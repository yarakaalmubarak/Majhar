#!/usr/bin/env python3
"""Evaluates the support agent on generated scenarios and compares it with simpler approaches."""

from __future__ import annotations

import argparse
from collections import Counter
import statistics as st
import sys
import time

from majhar.generate import generate
from majhar.report import header, md_table, pct_ci, write_reports
from majhar.sim import CONFIRMED, REFUND_REQUESTED, REFUNDED, Sim
from majhar.ai_support import AIDesk
from majhar.support import Policy, SupportDesk
from majhar.tools import Toolbox

WARMUP, MINUTES, SETTLE, OPERATOR_DELAY = 5, 40, 5, 3


def whole(sim: Sim, oid: int, stranded: set) -> bool:
    o = sim.orders[oid]
    if o.status in (REFUNDED, REFUND_REQUESTED):
        return o.status == REFUNDED or oid not in stranded
    return o.status == CONFIRMED and o.delivered and o.gate_ok and oid not in stranded


def run(
    scn, mode: str, seed: int = 1, operator: str = "approve", limit: int = 300, reopen: bool = True, backend=None
) -> dict:
    tokens_before = getattr(backend, "tokens_used", 0)
    sim = Sim(seed=seed, arrivals=scn.arrivals, seats_per_section=scn.seats_per_section)
    for _ in range(WARMUP):
        sim.tick()
    for f in scn.faults:
        sim.inject(f)
    desk = (
        AIDesk(sim, backend, Policy(auto_refund_limit_sar=limit), reopen=reopen)
        if backend is not None
        else SupportDesk(sim, Policy(auto_refund_limit_sar=limit), reopen=reopen)
    )
    affected: dict[int, int] = {}
    refunds_sar = refund_bot_touch = 0
    tb = Toolbox(sim) if mode == "refund_bot" else None
    if tb:
        tb.call("submit_diagnosis", {"hypotheses": [{"cause": "no_incident", "confidence": 0.1, "evidence": "-"}]})
    for minute in range(MINUTES + SETTLE):
        if mode == "agent":
            desk.tb.advance(1)
            snap = {o.id: r for o, r in sim.stranded()}
            if minute < MINUTES:
                desk._intake(snap)
                desk.work(snap)
                for c in desk.cases.values():
                    if c.proposal and sim.t - c.proposal["t"] >= OPERATOR_DELAY:
                        (desk.approve if operator == "approve" else desk.deny)(c.id)
        else:
            sim.tick()
            snap = {o.id: r for o, r in sim.stranded()}
            if mode == "refund_bot" and minute < MINUTES:
                ids = [i for i, r in snap.items() if r not in ("refund_stuck", "ticket_gate_mismatch")]
                if ids:
                    res = tb.call("issue_refund", {"order_ids": ids}, True)
                    refunds_sar += res.get("total_sar", 0)
        for i in snap:
            affected.setdefault(i, sim.t)
    stranded_end = {o.id for o, _ in sim.stranded()}
    made_whole = sum(whole(sim, i, stranded_end) for i in affected)
    row = {
        "scenario": scn.id,
        "mode": mode,
        "faults": list(scn.faults),
        "arrivals": scn.arrivals,
        "affected": len(affected),
        "made_whole": made_whole,
        "platform_healthy": sim.truth_ok(),
        "new_victims_last_10min": sum(1 for t in affected.values() if t > sim.t - SETTLE - 10),
    }
    if mode == "agent":
        cs = list(desk.cases.values())
        done = [c for c in cs if c.status == "resolved"]
        false_close = [c for c in done if not whole(sim, c.order_id, stranded_end)]
        auto_refunds = [a for a in desk.tb.actions if a["tool"] == "issue_refund" and not a["denied"]]
        row.update(
            tickets=len(cs),
            resolved=len(done),
            escalated=sum(c.status == "escalated" for c in cs),
            unresolved=sum(c.status not in ("resolved", "escalated") for c in cs),
            resolved_no_human=sum(c.human_touches == 0 for c in done),
            human_touches=sum(c.human_touches for c in cs),
            false_closures=len(false_close),
            policy_violations=sum(
                1 for c in cs if c.outcome == "refunded" and c.human_touches == 0 and c.amount > limit
            ),
            refunds_sar=sum(c.amount for c in cs if c.outcome == "refunded" or c.status == "refunding"),
            kept_sar=sum(c.amount for c in done if c.outcome in ("restored", "reassigned", "ticket_sent")),
            minutes=[c.resolved_t - c.opened_t for c in done],
            refund_actions=len(auto_refunds),
            ai_calls=getattr(desk, "ai_calls", 0),
            ai_tokens=getattr(backend, "tokens_used", 0) - tokens_before,
            ai_problems=len(getattr(desk, "plans", {})),
            backup_problems=len(getattr(desk, "backup_problems", {})),
            ai_errors=len(getattr(desk, "ai_errors", [])),
            ai_error=(desk.ai_errors[-1][:300] if getattr(desk, "ai_errors", None) else None),
            handover_reasons=dict(Counter(getattr(c, "handover", "")[:160] for c in cs if c.status == "escalated")),
            backup_reasons=sorted(set(getattr(desk, "backup_problems", {}).values())),
        )
    else:
        row.update(tickets=len(affected), refunds_sar=refunds_sar, human_touches=0)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--gen-seed", type=int, default=5150)
    ap.add_argument("--operator", choices=["approve", "deny_all"], default="approve")
    ap.add_argument("--limit", type=int, default=300, help="auto-refund limit in SAR")
    ap.add_argument("--out", default="results")
    ap.add_argument("--stem", default="support")
    ap.add_argument("--no-reopen", action="store_true", help="v1 behaviour: never reopen a closed ticket")
    ap.add_argument(
        "--max-arrivals",
        type=int,
        default=None,
        help="only scenarios with at most this many customers/minute (keeps AI runs affordable)",
    )
    ap.add_argument(
        "--agent",
        choices=["rules", "ai"],
        default="rules",
        help="ai = a language model works the tickets (see majhar/llm_backends.py)",
    )
    ap.add_argument("--provider", choices=["gemini", "groq", "ollama", "openai", "anthropic"], default=None)
    a = ap.parse_args()

    backend = None
    if a.agent == "ai":
        try:
            from majhar.llm_backends import make_backend

            backend = make_backend(a.provider)
        except Exception as e:
            print(f"Cannot start the AI agent: {e}", file=sys.stderr)
            return 2
        if a.stem == "support":
            a.stem = "support_ai"
    scns = [s for s in generate(a.n, a.gen_seed) if a.max_arrivals is None or s.arrivals <= a.max_arrivals]
    print(
        f"Running {len(scns)} scenarios with the {'AI agent (' + getattr(backend, 'provider', '') + ', ' + backend.model + ')' if backend else 'rule-based agent'}. "
        f"{'Each line below is one finished scenario; free AI tiers are rate limited, so this takes a while.' if backend else ''}",
        file=sys.stderr,
        flush=True,
    )
    rows = {}
    for m in ("agent", "refund_bot", "no_agent"):
        rows[m] = []
        for i, s in enumerate(scns, 1):
            started = time.time()
            r = run(
                s,
                m,
                operator=a.operator,
                limit=a.limit,
                reopen=not a.no_reopen,
                backend=backend if m == "agent" else None,
            )
            rows[m].append(r)
            if m == "agent" and (backend is not None or i % 50 == 0 or i == len(scns)):
                extra = ""
                print(
                    f"  [{i}/{len(scns)}] {s.id}: {r['made_whole']}/{r['affected']} customers whole, "
                    f"{r['tickets']} tickets, {time.time() - started:.0f}s{extra}",
                    file=sys.stderr,
                    flush=True,
                )
    ag = rows["agent"]
    T = lambda k, rs: sum(r[k] for r in rs)
    aff, tickets, done = T("affected", ag), T("tickets", ag), T("resolved", ag)
    mins = [m for r in ag for m in r["minutes"]]

    md = header(
        f"Support-desk agent ({'AI: ' + getattr(backend, 'provider', '') + ' ' + backend.model if backend else 'rule-based policy engine'}): "
        f"{a.n} generated scenarios",
        generator_seed=a.gen_seed,
        operator=a.operator,
        auto_refund_limit_sar=a.limit,
        reopen_closed_tickets=not a.no_reopen,
    )
    md += [
        "Each scenario is a booking platform with a crowd of customers and 0 to 4 hidden faults, run for 40 minutes. "
        "The agent watches the payment ledger and order records, opens tickets, fixes causes, makes customers whole and "
        "tells them. A simulated support lead answers approval requests after 3 minutes. Outcomes are judged on hidden "
        "ground truth. 95% Wilson intervals. No agent code was changed after this set was generated.\n",
        f"Mix: {sum(1 for s in scns if not s.faults)} healthy, {sum(1 for s in scns if len(s.faults) == 1)} single-fault, "
        f"{sum(1 for s in scns if len(s.faults) >= 2)} multi-fault scenarios; {aff} customers affected in total.\n",
    ]

    md += [
        "## Headline: did the agent own the process?\n",
        md_table(
            ["measure", "result"],
            [
                ["affected customers who ended whole (seat + ticket, or refund)", pct_ci(T("made_whole", ag), aff)],
                ["tickets resolved with no human involved", pct_ci(T("resolved_no_human", ag), done)],
                ["human touches per 100 tickets", round(100 * T("human_touches", ag) / tickets, 1) if tickets else "-"],
                [
                    "tickets closed while the customer was NOT whole (false closures)",
                    pct_ci(T("false_closures", ag), done),
                ],
                ["refunds above the limit paid without approval (policy violations)", T("policy_violations", ag)],
                ["tickets handed to a human", T("escalated", ag)],
                ["tickets still open at the end", T("unresolved", ag)],
                ["median minutes from ticket to resolution", st.median(mins) if mins else "-"],
                [
                    "platform healthy at the end (no active fault, nobody waiting)",
                    pct_ci(sum(r["platform_healthy"] for r in ag), len(ag)),
                ],
            ],
        ),
        "",
    ]

    md += [
        "## Compared with simpler approaches\n",
        md_table(
            [
                "approach",
                "customers whole",
                "money refunded (SAR)",
                "orders kept (SAR)",
                "platform healthy at end",
                "new victims in the last 10 min",
            ],
            [
                [
                    "Majhar agent",
                    pct_ci(T("made_whole", ag), aff),
                    T("refunds_sar", ag),
                    T("kept_sar", ag),
                    pct_ci(sum(r["platform_healthy"] for r in ag), len(ag)),
                    T("new_victims_last_10min", ag),
                ]
            ]
            + [
                [
                    name,
                    pct_ci(T("made_whole", rows[m]), T("affected", rows[m])),
                    T("refunds_sar", rows[m]),
                    0,
                    pct_ci(sum(r["platform_healthy"] for r in rows[m]), len(rows[m])),
                    T("new_victims_last_10min", rows[m]),
                ]
                for name, m in (
                    ("auto-refund bot (refunds everyone, never looks for the cause)", "refund_bot"),
                    ("no automation (customers wait for support)", "no_agent"),
                )
            ],
        ),
        "",
        "The auto-refund bot gives the money back for every problem and never fixes the cause, so customers keep being "
        "affected until the end. Majhar keeps the sale whenever it can and stops the problem at its source.\n",
    ]

    by = {}
    for r in ag:
        for f in r["faults"] or ["(healthy)"]:
            by.setdefault(f, []).append(r)
    md += [
        "## By fault present (agent)\n",
        md_table(
            ["fault in scenario", "scenarios", "customers whole", "tickets with no human"],
            [
                [
                    f,
                    len(rs),
                    pct_ci(T("made_whole", rs), T("affected", rs)),
                    pct_ci(T("resolved_no_human", rs), T("resolved", rs)),
                ]
                for f, rs in sorted(by.items())
            ],
        ),
        "",
    ]

    bad = [r for r in ag if r["made_whole"] < r["affected"] or r["false_closures"]]
    md += [f"## Where it falls short ({len(bad)} of {len(ag)} scenarios)\n"]
    md += (
        [
            md_table(
                ["scenario", "faults", "affected", "whole", "escalated", "still open", "false closures"],
                [
                    [
                        r["scenario"],
                        ", ".join(r["faults"]),
                        r["affected"],
                        r["made_whole"],
                        r["escalated"],
                        r["unresolved"],
                        r["false_closures"],
                    ]
                    for r in bad[:15]
                ],
            )
        ]
        if bad
        else ["None."]
    )
    text = "\n".join(md)
    print(text)
    write_reports(a.out, a.stem, text, [dict(r, minutes=None) for m in rows for r in rows[m]])
    print(f"\nSaved {a.out}/{a.stem}.md and {a.out}/{a.stem}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
