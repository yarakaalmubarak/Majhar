"""Tools available to the agent: read the platform state, diagnose, and apply safe or approved fixes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable

from .sim import FAULT_IDS, MSG_TTL, REFUND_REQUESTED, Sim

REASONS = [
    "assign_timeout",
    "seat_taken",
    "seat_double_sold",
    "no_ticket_sent",
    "ticket_not_delivered",
    "refund_stuck",
    "payment_unrecorded",
    "ticket_gate_mismatch",
]
REASSIGNABLE = {"assign_timeout", "seat_taken", "seat_double_sold"}
NOT_REFUNDABLE = {"refund_stuck", "ticket_gate_mismatch"}
LEVELS = {"INFO": 0, "WARN": 1, "ERROR": 2}
ALL_CAUSES = FAULT_IDS + ["no_incident"]


@dataclass
class Tool:
    name: str
    kind: str
    description: str
    properties: dict
    required: list
    fn: Callable


class Toolbox:
    def __init__(self, sim: Sim, require_diagnosis: bool = True, gate_declare: bool = True, disabled=()):
        self.sim = sim
        self.require_diagnosis, self.gate_declare = require_diagnosis, gate_declare
        self.disabled = frozenset(disabled)
        self._n_calls = 0
        self.actions: list[dict] = []
        self.calls: list[dict] = []
        self.diagnosis: dict | None = None
        self.diagnosis_t: int | None = None
        self.first_action_t: int | None = None
        self.finished: str | None = None
        self.final_summary = ""
        self.escalated_by: str | None = None
        self.handoff_report: dict | None = None
        self._tools = {t.name: t for t in self._build()}

    def handoff(self, reason: str) -> dict:
        """Summary for the support team when a case is handed over: what was seen, tried and is still open."""
        v = self._tools["verify_recovery"].fn({}, False)
        m = self.sim.metrics()
        diag = (self.diagnosis or {}).get("hypotheses", [])
        return {
            "reason": reason,
            "likely_causes": [{"cause": h["cause"], "confidence": h["confidence"]} for h in diag],
            "actions_tried": [
                {"tool": a["tool"], "args": a["args"], "effective": a["effective"], "denied": a["denied"]}
                for a in self.actions
            ][-12:],
            "still_failing_checks": v["failing"],
            "customers_still_affected": m["stranded_customers"],
            "stranded_by_reason": m["stranded_by_reason"],
            "refunds_pending": m["refunds_pending"],
        }

    def escalate(self, reason: str, by: str = "agent") -> dict:
        self.finished, self.final_summary, self.escalated_by = "escalated", reason, by
        self.handoff_report = self.handoff(reason)
        return {"escalated": True}

    def force_escalate(self, reason: str) -> dict:
        """Called by the agent runtime's guard (stalled, denied twice, out of steps), not by the model."""
        self.calls.append({"t": self.sim.t, "tool": "escalate_to_human", "kind": "finish"})
        return self.escalate(reason, "guard")

    def advance(self, minutes: int = 1) -> None:
        for _ in range(minutes):
            self.sim.tick()

    def anthropic_tools(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description + (" [REQUIRES HUMAN APPROVAL]" if t.kind == "risky" else ""),
                "input_schema": {"type": "object", "properties": t.properties, "required": t.required},
            }
            for t in self._tools.values()
            if t.name not in self.disabled
        ]

    def kind(self, name: str) -> str | None:
        t = self._tools.get(name)
        return t.kind if t else None

    def call(self, name: str, args: dict | None = None, approved: bool | None = None) -> dict:
        """Execute a tool."""
        args = args or {}
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown tool '{name}'"}
        if name in self.disabled:
            return {
                "error": f"ToolDisabled: '{name}' is switched off by operator policy. Do not retry; choose another way or escalate."
            }
        self._n_calls += 1
        self.calls.append({"t": self.sim.t, "tool": name, "kind": tool.kind})
        if tool.kind in ("safe", "risky") and self.require_diagnosis and self.diagnosis is None:
            return {"error": "Action locked: call submit_diagnosis first (ranked likely causes with evidence)."}
        try:
            if tool.kind == "risky":
                if approved is None:
                    return {"status": "needs_approval", "preview": tool.fn(args, True)}
                if not approved:
                    self._record(name, args, {"effective": False, "unneeded": 0}, denied=True)
                    return {"status": "denied_by_human", "note": "Operator declined. Propose an alternative."}
                res = tool.fn(args, False)
            else:
                res = tool.fn(args, False)
        except (KeyError, TypeError, ValueError) as e:
            return {"error": f"{type(e).__name__}: {e}"}
        if tool.kind in ("safe", "risky"):
            self._record(name, args, res)
        return res

    def _record(self, name: str, args: dict, res: dict, denied: bool = False) -> None:
        if self.first_action_t is None:
            self.first_action_t = self.sim.t
        effective = bool(res.get("effective", True))
        unneeded = int(res.get("unneeded", 0 if effective else 1))
        self.actions.append(
            {
                "t": self.sim.t,
                "tool": name,
                "args": args,
                "effective": effective,
                "unneeded": unneeded,
                "denied": denied,
            }
        )

    def _select(self, a: dict) -> tuple[list, list]:
        stranded = self.sim.stranded()
        ids, reason = a.get("order_ids"), a.get("reason")
        if ids:
            ids = [int(i) for i in ids]
            by_id = {o.id: (o, r) for o, r in stranded}
            return [by_id[i] for i in ids if i in by_id], [i for i in ids if i not in by_id]
        if reason:
            if reason not in REASONS:
                raise ValueError(f"reason must be one of {REASONS}")
            return [(o, r) for o, r in stranded if r == reason], []
        raise ValueError("provide order_ids or reason")

    def _build(self) -> list[Tool]:
        sim = self.sim
        int_list = {"type": "array", "items": {"type": "integer"}}

        def get_logs(a, d):
            n = max(1, min(int(a.get("last_n", 40)), 200))
            lines = sim.logs
            lvl = a.get("min_level")
            if lvl:
                lines = [l for l in lines if LEVELS.get(l.split()[1], 0) >= LEVELS[lvl]]
            if a.get("contains"):
                lines = [l for l in lines if a["contains"].lower() in l.lower()]
            return {"lines": lines[-n:], "returned": len(lines[-n:]), "matching_total": len(lines)}

        def get_metrics(a, d):
            return sim.metrics()

        def get_change_history(a, d):
            return {"changes": sim.changes}

        def list_stranded(a, d):
            s = sim.stranded()
            if a.get("reason"):
                s = [(o, r) for o, r in s if r == a["reason"]]
            lim = max(1, min(int(a.get("limit", 15)), 50))
            return {
                "total": len(s),
                "by_reason": dict(Counter(r for _, r in s)),
                "sample": [
                    {
                        "order_id": o.id,
                        "seat": o.seat,
                        "amount_sar": o.amount,
                        "status": o.status,
                        "reason": r,
                        "lang": o.lang,
                        "minutes_since_payment": sim.t - (o.paid_at or sim.t),
                    }
                    for o, r in s[:lim]
                ],
            }

        def get_order(a, d):
            o = sim.orders[int(a["order_id"])]
            seat = sim.seats[o.seat]
            return {
                "order_id": o.id,
                "status": o.status,
                "seat": o.seat,
                "seat_sold_to": seat.sold_to,
                "amount_sar": o.amount,
                "lang": o.lang,
                "delivered": o.delivered,
                "reason": o.reason,
                "gate_record_matches_primary": o.gate_ok,
            }

        def verify_recovery(a, d):
            m = sim.metrics()
            checks = {
                "confirmation_worker_consuming": m["confirm_consumers"] == 1,
                "confirmation_queue_ok": m["queue_depth"] <= 50,
                "no_seat_assign_errors_3m": m["assign_errors_3m"] == 0,
                "no_seat_conflicts_3m": m["seat_conflicts_3m"] == 0,
                "no_hold_expiries_3m": m["holds_expired_3m"] == 0,
                "hold_ttl_sane": m["hold_ttl_min"] >= 5,
                "no_oversold_seats": m["seats_oversold"] == 0,
                "no_ticket_render_errors_3m": m["ticket_render_errors_3m"] == 0,
                "no_refunds_pending": m["refunds_pending"] == 0,
                "payments_reconciled": m["payments_unreconciled"] == 0 and m["gateway_captured_3m"] <= m["paid_3m"],
                "no_dropped_confirmations_3m": m["queue_dropped_3m"] == 0,
                "no_corrupted_records": m["sync_mismatches"] == 0,
                "no_new_record_corruption_3m": m["sync_corrupted_3m"] == 0,
                "no_stranded_customers": m["stranded_customers"] == 0,
            }
            return {
                "healthy": all(checks.values()),
                "checks": checks,
                "failing": [k for k, v in checks.items() if not v],
                "time": m["time"],
            }

        def wait(a, d):
            n = max(1, min(int(a.get("minutes", 2)), 10))
            self.advance(n)
            return {"waited_minutes": n, "time": sim.clock()}

        def submit_diagnosis(a, d):
            hyp = a.get("hypotheses") or []
            if not hyp:
                raise ValueError("hypotheses must be a non-empty list (use cause 'no_incident' if nothing is wrong)")
            clean = []
            for h in hyp:
                if h["cause"] not in ALL_CAUSES:
                    raise ValueError(f"cause must be one of {ALL_CAUSES}")
                clean.append(
                    {
                        "cause": h["cause"],
                        "confidence": float(h.get("confidence", 0.5)),
                        "evidence": str(h.get("evidence", "")),
                    }
                )
            self.diagnosis = {"summary": a.get("summary", ""), "hypotheses": clean, "t": sim.t}
            self.diagnosis_t = sim.t if self.diagnosis_t is None else self.diagnosis_t
            return {"recorded": True, "actions_unlocked": True}

        def restart_confirmation_worker(a, d):
            return sim.fix_confirmation_worker()

        def restart_seat_assigner(a, d):
            return sim.fix_seat_assigner()

        def restart_refund_worker(a, d):
            return sim.fix_refund_worker()

        def set_hold_ttl(a, d):
            return sim.set_hold_ttl(int(a["minutes"]))

        def enable_seat_uniqueness_check(a, d):
            return sim.enable_unique_check()

        def replay_confirmations(a, d):
            return sim.replay_confirmations()

        def resend_tickets(a, d):
            return sim.resend_tickets()

        def restart_payment_webhook(a, d):
            return sim.fix_payment_webhook()

        def replay_payments(a, d):
            return sim.replay_payments()

        def rollback_seat_sync(a, d):
            return sim.fix_seat_sync()

        def scale_confirmation_queue(a, d):
            return sim.fix_queue_limit()

        def reassign_seat(a, dry):
            sel, missing = self._select(a)
            ok = [(o, r) for o, r in sel if r in REASSIGNABLE]
            skipped = [(o, r) for o, r in sel if r not in REASSIGNABLE]
            free = Counter(s.section for s in sim.seats.values() if s.status == "free")
            if dry:
                return {
                    "dry_run": True,
                    "would_reassign": len(ok),
                    "free_seats_available": sum(free.values()),
                    "not_eligible": len(skipped) + len(missing),
                    "plan": "move each customer to a free seat in the same section, else any section; "
                    "then queue the confirmation",
                }
            done = nofree = 0
            for o, r in ok:
                cands = [s for s in sim.seats.values() if s.status == "free" and s.section == o.section] or [
                    s for s in sim.seats.values() if s.status == "free"
                ]
                if not cands:
                    nofree += 1
                    continue
                old = sim.seats[o.seat]
                if o.id in old.sold_to:
                    old.sold_to.remove(o.id)
                new = cands[0]
                new.status, new.holder, new.sold_to = "sold", None, [o.id]
                o.seat, o.section = new.id, new.section
                sim._seated(o)
                done += 1
            sim.log("INFO", "ops-console", f"reassigned {done} customers to new seats ({nofree} had no free seat)")
            unneeded = len(skipped) + len(missing)
            return {
                "reassigned": done,
                "no_free_seat": nofree,
                "not_eligible": unneeded,
                "effective": done > 0,
                "unneeded": unneeded,
            }

        def issue_refund(a, dry):
            sel, missing = self._select(a)
            ok = [(o, r) for o, r in sel if r not in NOT_REFUNDABLE and o.status != REFUND_REQUESTED]
            skipped = len(sel) - len(ok) + len(missing)
            if dry:
                return {
                    "dry_run": True,
                    "would_refund": len(ok),
                    "total_sar": sum(o.amount for o, _ in ok),
                    "not_eligible": skipped,
                    "plan": "queue a full refund for each customer",
                }
            for o, _ in ok:
                o.status, o.refund_at = REFUND_REQUESTED, sim.t
                o.reason = ""
                sim.refunds_requested += 1
            sim.log("INFO", "ops-console", f"queued {len(ok)} refunds")
            return {
                "refunds_queued": len(ok),
                "total_sar": sum(o.amount for o, _ in ok),
                "not_eligible": skipped,
                "effective": len(ok) > 0,
                "unneeded": skipped,
            }

        def rollback_ticket_template(a, dry):
            if dry:
                return {
                    "dry_run": True,
                    "plan": "roll ticket-template v7 back to v6 for ALL new tickets",
                    "risk": "affects every ticket rendered after the rollback",
                }
            return sim.rollback_template()

        def repair_seat_records(a, dry):
            n = sim._corrupted()
            if dry:
                return {
                    "dry_run": True,
                    "would_repair": n,
                    "source_of_truth": "primary seat_assignments table",
                    "plan": "overwrite every damaged ticket-gate row with the primary copy",
                    "risk": "bulk write to live gate records; only worthwhile once the job that damages them has been stopped",
                }
            return sim.repair_records()

        def declare_recovered(a, d):
            v = verify_recovery({}, False)
            if self.gate_declare and not v["healthy"]:
                return {
                    "accepted": False,
                    "failing_checks": v["failing"],
                    "hint": "Fix the failing checks (wait a few minutes if they are 3m-window metrics) or escalate_to_human.",
                }
            self.finished, self.final_summary = "recovered", a.get("summary", "")
            return {"accepted": True}

        def escalate_to_human(a, d):
            return self.escalate(a.get("reason", ""), "agent")

        no = {}
        return [
            Tool(
                "get_logs",
                "read",
                "Read platform logs (newest last). Filter by min_level (INFO/WARN/ERROR) or substring.",
                {
                    "last_n": {"type": "integer"},
                    "min_level": {"type": "string", "enum": list(LEVELS)},
                    "contains": {"type": "string"},
                },
                [],
                get_logs,
            ),
            Tool(
                "get_metrics",
                "read",
                "Current platform metrics: queues, consumers, 3-minute windows, payment-gateway vs recorded payments, broker drops, replica checksum mismatches, oversold seats, refunds, stranded customers.",
                no,
                [],
                get_metrics,
            ),
            Tool("get_change_history", "read", "Recent deploys, migrations and rollbacks.", no, [], get_change_history),
            Tool(
                "list_stranded",
                "read",
                "Customers who paid but have no valid outcome yet, grouped by reason.",
                {"reason": {"type": "string", "enum": REASONS}, "limit": {"type": "integer"}},
                [],
                list_stranded,
            ),
            Tool(
                "get_order", "read", "Details of one order.", {"order_id": {"type": "integer"}}, ["order_id"], get_order
            ),
            Tool(
                "verify_recovery",
                "read",
                "Run the recovery checks. Healthy only when every check passes.",
                no,
                [],
                verify_recovery,
            ),
            Tool(
                "wait",
                "read",
                "Let platform time pass (1-10 minutes) so 3-minute windows refresh.",
                {"minutes": {"type": "integer"}},
                [],
                wait,
            ),
            Tool(
                "submit_diagnosis",
                "read",
                "Record your ranked likely root causes with confidence (0-1) and evidence. Required before any action. Re-submit when you learn more. Use cause 'no_incident' if nothing is wrong.",
                {
                    "summary": {"type": "string"},
                    "hypotheses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "cause": {"type": "string", "enum": ALL_CAUSES},
                                "confidence": {"type": "number"},
                                "evidence": {"type": "string"},
                            },
                            "required": ["cause", "confidence", "evidence"],
                        },
                    },
                },
                ["hypotheses"],
                submit_diagnosis,
            ),
            Tool(
                "restart_confirmation_worker",
                "safe",
                f"Restart the confirmation worker. Queued messages older than {MSG_TTL} minutes expire on restart (use replay_confirmations afterwards).",
                no,
                [],
                restart_confirmation_worker,
            ),
            Tool("restart_seat_assigner", "safe", "Restart the seat-assigner service.", no, [], restart_seat_assigner),
            Tool(
                "restart_refund_worker",
                "safe",
                "Restart the refund worker and re-submit unsettled refund requests.",
                no,
                [],
                restart_refund_worker,
            ),
            Tool(
                "set_hold_ttl",
                "safe",
                "Set the seat-hold timeout in minutes (normal is 10).",
                {"minutes": {"type": "integer"}},
                ["minutes"],
                set_hold_ttl,
            ),
            Tool(
                "enable_seat_uniqueness_check",
                "safe",
                "Re-create the unique index on seat assignments so a seat cannot be sold twice.",
                no,
                [],
                enable_seat_uniqueness_check,
            ),
            Tool(
                "replay_confirmations",
                "safe",
                "Re-queue confirmations for seated customers whose message is missing from the queue.",
                no,
                [],
                replay_confirmations,
            ),
            Tool(
                "resend_tickets",
                "safe",
                "Re-render and resend tickets that were confirmed but not delivered.",
                no,
                [],
                resend_tickets,
            ),
            Tool(
                "restart_payment_webhook",
                "safe",
                "Restart the payment-webhook consumer (clears its dedupe cache). Stops new payments being dropped; payments already dropped need replay_payments.",
                no,
                [],
                restart_payment_webhook,
            ),
            Tool(
                "replay_payments",
                "safe",
                "Reconcile with the payment-gateway ledger: record payments that were captured but are missing from the order database, then assign them seats.",
                no,
                [],
                replay_payments,
            ),
            Tool(
                "rollback_seat_sync",
                "safe",
                "Roll the seat-sync job back to its previous version. Stops it damaging ticket-gate records; does NOT repair rows already damaged.",
                no,
                [],
                rollback_seat_sync,
            ),
            Tool(
                "scale_confirmation_queue",
                "safe",
                "Remove the confirmation broker's per-minute publish limit (scale it out). Messages already discarded are not recovered; use replay_confirmations.",
                no,
                [],
                scale_confirmation_queue,
            ),
            Tool(
                "repair_seat_records",
                "risky",
                "Repair damaged ticket-gate records from the primary database (bulk write).",
                no,
                [],
                repair_seat_records,
            ),
            Tool(
                "reassign_seat",
                "risky",
                "Move stranded customers (reason assign_timeout / seat_taken / seat_double_sold) to free seats. Give order_ids OR a reason for bulk.",
                {"order_ids": int_list, "reason": {"type": "string", "enum": REASONS}},
                [],
                reassign_seat,
            ),
            Tool(
                "issue_refund",
                "risky",
                "Refund stranded customers in full. Give order_ids OR a reason for bulk. Use when no seat can be found.",
                {"order_ids": int_list, "reason": {"type": "string", "enum": REASONS}},
                [],
                issue_refund,
            ),
            Tool(
                "rollback_ticket_template",
                "risky",
                "Roll the ticket template back to the previous version.",
                no,
                [],
                rollback_ticket_template,
            ),
            Tool(
                "declare_recovered",
                "finish",
                "Declare the platform recovered. Rejected unless every verify_recovery check passes.",
                {"summary": {"type": "string"}},
                ["summary"],
                declare_recovered,
            ),
            Tool(
                "escalate_to_human",
                "finish",
                "Give up and hand over to a human with a clear reason.",
                {"reason": {"type": "string"}},
                ["reason"],
                escalate_to_human,
            ),
        ]
