#!/usr/bin/env python3
"""Web server for the live demo and the testing page."""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from majhar.ai_support import AIDesk
from majhar.llm_backends import make_backend
from majhar.shop import ShopSim
from majhar.sim import CONFIRMED, PRICES, REFUNDED
from majhar.support import CAUSE_LABEL, SupportDesk

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTS = {"A": "VIP · front rows", "B": "Standard", "C": "Economy"}
BACKEND = None
BACKEND_ERROR = "not started"
SECONDS_PER_MINUTE = 2.0
RUSH_FANS = 60
STATE_ORDER = {"problem": 0, "processing": 1, "refunding": 2, "refunded": 3, "confirmed": 4}
BUSY_MINUTES = 6
BUSY_ARRIVALS = 12
SEATS = 200
FAILURES = {
    "payments": ("Payments taken but orders lost", ["payment_webhook_drop"]),
    "seats": ("Seat service timing out", ["seat_assign_timeout"]),
    "emails": ("Ticket emails stopped", ["confirmation_worker_down"]),
    "arabic": ("Arabic e-tickets failing", ["ticket_render_bug"]),
    "holds": ("Seat holds expiring too early", ["hold_ttl_bug"]),
    "records": ("Ticket records damaged", ["seat_sync_corruption"]),
    "combo": ("Two at once: payments lost + Arabic e-tickets", ["payment_webhook_drop", "ticket_render_bug"]),
}
RULES = {
    0: "Strict: every refund needs a person",
    300: "Normal: refunds up to 300 SAR are automatic",
    1000: "Relaxed: refunds up to 1,000 SAR are automatic",
}
LOG_FILE = os.path.join(HERE, "logs", "majhar.log")
LOG_TO_FILE = False


class Session:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache: dict = {}
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.sim = ShopSim(seed=7, seats_per_section=SEATS)
            self.sim.sell_out("A", limit=SEATS - 3)
            self.sim.protected = {"A"}
            self.sim.sell_out("B", limit=40)
            self.sim.sell_out("C", limit=20)
            self.desk = AIDesk(self.sim, BACKEND) if BACKEND is not None else SupportDesk(self.sim)
            self.running = True
            self.rng = random.Random(7)
            self.rushes = 0
            self.failure = "payments"
            self.busy_until = None
            self.seen: dict[str, dict] = {}
            self.errors_seen = 0
            self.file_seq = 0
            self.sim.note(
                "INFO",
                "demo",
                f"demo started: agent = {self._agent_name()}, "
                f"rules = {RULES.get(self.desk.policy.auto_refund_limit_sar, '')}",
            )
            self._write_log(header=True)

    def _agent_name(self) -> str:
        return (
            f"AI ({getattr(BACKEND, 'provider', '')}: {getattr(BACKEND, 'model', '')})"
            if BACKEND is not None
            else "rule-based backup"
        )

    def _sync_events(self) -> None:
        """Add the agent's new decisions to the system log and write new entries to the log file."""
        d, sim = self.desk, self.sim
        for inc in d.incidents.values():
            s = self.seen.setdefault(inc["id"], {"open": False, "plan": 0, "thoughts": 0})
            who = "AI" if inc.get("by") == "AI" else "AGENT"
            if not s["open"]:
                s["open"] = True
                sim.note(who, "incident", f"{inc['id']} opened: {inc['title']}. Evidence: {inc['evidence']}")
            for th in inc.get("thoughts", [])[s["thoughts"] :]:
                sim.note(who, "reasoning", str(th.get("text", th) if isinstance(th, dict) else th)[:400])
            s["thoughts"] = len(inc.get("thoughts", []))
            for step in inc.get("plan", [])[s["plan"] :]:
                sim.note(who, "plan", f"{inc['id']}: {step}")
            s["plan"] = len(inc.get("plan", []))
        changes = getattr(BACKEND, "model_changes", [])
        for ch in changes[getattr(self, "changes_seen", 0) :]:
            sim.note("WARN", "ai-model", ch)
            print("AI model:", ch)
        self.changes_seen = len(changes)
        errs = getattr(d, "ai_errors", [])
        for e in errs[self.errors_seen :]:
            sim.note("ERROR", "ai-model", str(e)[:300])
        self.errors_seen = len(errs)
        self._write_log()

    def _write_log(self, header: bool = False) -> None:
        if not LOG_TO_FILE:
            return
        try:
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                if header:
                    f.write(f"\n===== demo reset {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                for e in self.sim.feed:
                    if e["seq"] > self.file_seq:
                        f.write(f"{e['time']} {e['level']:<8} {e['src']}: {e['text']}\n")
                        self.file_seq = e["seq"]
        except OSError:
            pass

    def tick(self) -> None:
        with self.lock:
            if self.running:
                self._minute()

    def _minute(self) -> None:
        self.desk.tick(1)
        if self.busy_until is not None and self.sim.t >= self.busy_until:
            self.sim.arrivals, self.busy_until = 0, None
            self.sim.note("TEST", "demo", "rush is over: no new customers")
        self._sync_events()

    def buy(self, customer: str, section: str, qty: int, lang: str) -> dict:
        with self.lock:
            customer = (customer or "Guest").strip()[:30] or "Guest"
            if section not in PRICES:
                raise ValueError("unknown ticket type")
            b = self.sim.place_booking(customer, section, int(qty or 1), "ar" if lang == "ar" else "en")
            if b is None:
                raise ValueError(f"not enough {PRODUCTS[section]} seats left")
            self.sim.note(
                "CUSTOMER",
                "shop",
                f"{customer} paid {b['amount_sar']:,} SAR for {len(b['order_ids'])} "
                f"{PRODUCTS[section]} ticket(s), booking {b['booking']}",
            )
            self._write_log()
            return b

    def report(self, booking: str, text: str) -> None:
        with self.lock:
            if self.sim.bookings.get(booking):
                self.sim.note("CUSTOMER", "shop", f"booking {booking} reported a problem: {(text or '')[:120]}")
            for oid in self.sim.bookings.get(booking, []):
                self.desk.report(oid, text or "I paid but I have not received my tickets.")
                text = ""
            self._sync_events()

    def decide(self, case_id: str, ok: bool, by: str = "support lead") -> None:
        with self.lock:
            c = self.desk.cases.get(case_id)
            if c is None:
                raise ValueError("unknown ticket")
            p = c.proposal or {}
            what = p.get("plan") or p.get("tool") or "the proposal"
            (self.desk.approve if ok else self.desk.deny)(case_id, by)
            self.sim.note("HUMAN", by.replace(" ", "-"), f"{'approved' if ok else 'declined'} on {case_id}: {what}")
            self._sync_events()

    def demo(self, action: str, failure: str | None = None, limit=None) -> None:
        with self.lock:
            if action in ("rush", "break_payments", "break"):
                key = failure or self.failure
                if key not in FAILURES:
                    raise ValueError(f"unknown failure {key}")
                self.failure = key
                label, faults = FAILURES[key]
                for f in faults:
                    self.sim.inject(f)
                self.sim.note("TEST", "demo", f"failure injected: {label} (the agent is not told what it is)")
                if action == "rush":
                    self.rushes += 1
                    n = self.sim.rush(RUSH_FANS, self.rng)
                    self.sim.arrivals, self.busy_until = BUSY_ARRIVALS, self.sim.t + BUSY_MINUTES
                    self.sim.note(
                        "TEST",
                        "demo",
                        f"rush hour: {RUSH_FANS} fans checked out at once ({n} tickets), "
                        f"then {BUSY_ARRIVALS} new fans a minute for {BUSY_MINUTES} minutes",
                    )
            elif action == "rules":
                limit = int(limit)
                if limit not in RULES:
                    raise ValueError("unknown rule")
                self.desk.policy.auto_refund_limit_sar = limit
                self.sim.note("RULES", "policy", f"company rules changed: {RULES[limit]}")
            elif action in ("grab_vip", "sell_out_vip"):
                self.sim.sell_out("A")
            elif action == "pause":
                self.running = False
            elif action == "play":
                self.running = True
            elif action == "minute":
                self._minute()
                return
            elif action == "reset":
                self.reset()
                return
            else:
                raise ValueError(f"unknown action {action}")
            self._sync_events()

    def _read(self, key, build):
        if self.lock.acquire(timeout=0.25):
            try:
                self.cache[key] = build()
            finally:
                self.lock.release()
        return self.cache.get(key) or {"busy": True}

    def shop_state(self, customer: str) -> dict:
        return self._read(("shop", customer), lambda: self._shop_state(customer))

    def desk_state(self) -> dict:
        return self._read("desk", self._desk_state)

    def case_detail(self, case_id: str) -> dict:
        return self._read(("case", case_id), lambda: self._case_detail(case_id))

    def _booking_view(self, ref: str) -> dict:
        views = [self.desk.customer_view(i) for i in self.sim.bookings[ref]]
        o0 = self.sim.orders[self.sim.bookings[ref][0]]
        states = [v["state"] for v in views]
        if all(s == "confirmed" for s in states):
            state = "confirmed"
        elif all(s == "refunded" for s in states):
            state = "refunded"
        else:
            state = min(states, key=lambda s: STATE_ORDER.get(s, 0))
        msgs, seen = [], set()
        for v in views:
            for n in v["messages"]:
                k = (n["time"], n["text"])
                if k not in seen:
                    seen.add(k)
                    msgs.append({**n, "t": n["t"]})
        msgs.sort(key=lambda n: n["t"])
        cases = [v["case"] for v in views if v["case"]]
        return {
            "booking": ref,
            "section": o0.section,
            "type": PRODUCTS[o0.section],
            "lang": o0.lang,
            "tickets": [{"order_id": v["order_id"], "seat": v["seat"], "state": v["state"]} for v in views],
            "amount_sar": sum(v["amount_sar"] for v in views),
            "state": state,
            "messages": msgs,
            "case": cases[0] if cases else None,
            "can_report": not cases and state in ("processing", "problem"),
        }

    def _shop_state(self, customer: str) -> dict:
        inv = self.sim.inventory()
        refs = []
        for oid in self.sim.shoppers.get(customer, []):
            r = self.sim.booking_of.get(oid)
            if r and r not in refs:
                refs.append(r)
        return {
            "time": self.sim.clock(),
            "running": self.running,
            "products": [{"section": s, "name": PRODUCTS[s], "price": PRICES[s], "left": inv[s]} for s in PRICES],
            "bookings": [self._booking_view(r) for r in reversed(refs)],
        }

    def _group_status(self, cs) -> str:
        st = [c.status for c in cs]
        for s in ("awaiting_approval", "escalated", "working", "open", "refunding"):
            if s in st:
                return s
        return "resolved"

    def _desk_state(self) -> dict:
        d, sim = self.desk, self.sim
        groups: dict[str, list] = {}
        for c in d.cases.values():
            groups.setdefault(d.booking(c.order_id) or c.id, []).append(c)
        rows = []
        for key, cs in groups.items():
            cs.sort(key=lambda c: c.id)
            host = next((c for c in cs if c.proposal and not c.proposal.get("shared_with")), cs[0])
            rows.append(
                {
                    "key": key,
                    "case": host.id,
                    "customer": host.customer,
                    "lang": host.lang,
                    "tickets": len(cs),
                    "amount": sum(c.amount for c in cs),
                    "issue": host.issue,
                    "status": self._group_status(cs),
                    "source": host.source,
                    "incident": host.incident,
                    "human": any(c.human_touches for c in cs),
                    "opened_t": min(c.opened_t for c in cs),
                    "minutes": max((c.resolved_t if c.resolved_t is not None else sim.t) - c.opened_t for c in cs),
                }
            )
        rows.sort(key=lambda r: (r["status"] != "awaiting_approval", -r["opened_t"], r["key"]))
        counts = {
            s: sum(1 for r in rows if r["status"] == s)
            for s in ("awaiting_approval", "working", "refunding", "resolved", "escalated")
        }
        counts["working"] += sum(1 for r in rows if r["status"] == "open")
        incs = []
        for inc in d.incidents.values():
            mine = [c for c in d.cases.values() if c.incident == inc["id"]]
            incs.append(
                {
                    **{k: inc[k] for k in ("id", "title", "evidence", "plan", "message", "by", "time")},
                    "thoughts": inc["thoughts"][-4:],
                    "tickets": len(mine),
                    "resolved": sum(1 for c in mine if c.status == "resolved"),
                    "waiting_human": sum(1 for c in mine if c.status in ("awaiting_approval", "escalated")),
                }
            )
        ai = isinstance(d, AIDesk)
        k = d.kpis()
        k["customers"] = len({c.customer for c in d.cases.values()})
        return {
            "time": sim.clock(),
            "running": self.running,
            "kpis": k,
            "groups": rows[:80],
            "total_groups": len(rows),
            "counts": counts,
            "incidents": incs,
            "mode": (f"{getattr(d.backend, 'provider', 'ai')} · {getattr(d.backend, 'model', '')}" if ai else None),
            "ai": ai,
            "ai_calls": getattr(d, "ai_calls", 0),
            "ai_sessions": getattr(d, "ai_sessions", 0),
            "ai_error": (d.ai_errors[-1] if ai and d.ai_errors else (None if ai else BACKEND_ERROR)),
            "payments_broken": "payment_webhook_drop" in sim.faults,
            "rushes": self.rushes,
            "vip_left": sim.inventory()["A"],
            "policy": {"auto_refund_limit_sar": d.policy.auto_refund_limit_sar},
            "failure": self.failure,
            "failures": [{"key": k, "label": v[0]} for k, v in FAILURES.items()],
            "rules": [{"limit": k, "label": v} for k, v in RULES.items()],
            "log": list(sim.feed)[-120:],
            "rush_on": self.busy_until is not None,
        }

    def _case_detail(self, case_id: str) -> dict:
        d = self.desk
        c = d.cases.get(case_id)
        if c is None:
            return {"error": "unknown ticket"}
        group = [c] + d.siblings(c)
        group.sort(key=lambda x: x.id)
        notes, seen = [], set()
        for x in group:
            for n in x.notes:
                k = (n["t"], n["who"], n["text"])
                if k not in seen:
                    seen.add(k)
                    notes.append({**n, "case": x.id})
        notes.sort(key=lambda n: n["t"])
        host = next((x for x in group if x.proposal and not x.proposal.get("shared_with")), None)
        ref = d.booking(c.order_id)
        return {
            "case": c.id,
            "booking": ref,
            "customer": c.customer,
            "lang": c.lang,
            "tickets": [
                {
                    "case": x.id,
                    "order_id": x.order_id,
                    "seat": self.sim.orders[x.order_id].seat,
                    "amount": x.amount,
                    "status": x.status,
                    "outcome": x.outcome,
                }
                for x in group
            ],
            "amount": sum(x.amount for x in group),
            "issue": c.issue,
            "status": self._group_status(group),
            "source": c.source,
            "incident": c.incident,
            "cause": (
                CAUSE_LABEL.get(c.cause, "")
                if c.cause
                else next((i["title"] for i in d.incidents.values() if i["id"] == c.incident), "")
            ),
            "opened": self._clock(min(x.opened_t for x in group)),
            "proposal": ({**host.proposal, "case": host.id} if host else None),
            "notes": notes[-60:],
        }

    def _clock(self, t: int) -> str:
        m = self.sim.start_minutes + t
        return f"{(m // 60) % 24:02d}:{m % 60:02d}"


SESSION = Session()


def _agg(rows: list[dict]) -> dict:
    T = lambda k: sum(r.get(k) or 0 for r in rows)
    return {
        "scenarios": len(rows),
        "affected": T("affected"),
        "whole": T("made_whole"),
        "tickets": T("tickets"),
        "resolved": T("resolved"),
        "no_human": T("resolved_no_human"),
        "human_touches": T("human_touches"),
        "false_closures": T("false_closures"),
        "policy_violations": T("policy_violations"),
        "escalated": T("escalated"),
        "refunded_sar": T("refunds_sar"),
        "kept_sar": T("kept_sar"),
        "healthy": sum(1 for r in rows if r.get("platform_healthy")),
        "new_victims": T("new_victims_last_10min"),
    }


def results_summary() -> dict:
    res = os.path.join(HERE, "results")

    def load(stem):
        try:
            with open(os.path.join(res, stem + ".json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    out = {}
    main_rows = load("support")
    for mode in ("agent", "refund_bot", "no_agent"):
        out[mode] = _agg([r for r in main_rows if r["mode"] == mode])
    out["variants"] = {
        stem: _agg([r for r in load(stem) if r["mode"] == "agent"])
        for stem in ("support_limit0", "support_limit1000", "support_deny", "support_deny_v1")
    }
    out["scale"] = load("scale")
    try:
        md = open(os.path.join(res, "support.md")).read()
        out["fingerprint"] = re.search(r"code_fingerprint=(\w+)", md).group(1)
        out["median_minutes"] = re.search(r"median minutes from ticket to resolution \| ([\d.]+)", md).group(1)
        out["mix"] = re.search(r"Mix: ([^\n]+)", md).group(1)
    except (OSError, AttributeError):
        pass
    out["tests"] = _test_count()
    try:
        out["live_runs"] = list(reversed(json.load(open(os.path.join(res, "live_runs.json")))))[:20]
        for r in out["live_runs"]:
            r.pop("rows", None)
    except (OSError, ValueError):
        out["live_runs"] = []
    out["ai"] = BACKEND is not None
    return out


_TESTS: list[int] = []


def _test_count() -> int:
    """How many automated tests the project has (asks pytest once; counts test functions if pytest is not installed)."""
    if _TESTS:
        return _TESTS[0]
    n = 0
    try:
        import subprocess
        import sys

        p = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=30,
        )
        m = re.search(r"(\d+) tests? collected", p.stdout)
        n = int(m.group(1)) if m else 0
    except Exception:
        n = 0
    if not n:
        for f in glob.glob(os.path.join(HERE, "tests", "test_*.py")):
            n += len(re.findall(r"^\s*def test_", open(f).read(), re.M))
    _TESTS.append(n)
    return n


class LiveTest:
    """'Run a fresh test now': new scenarios nobody has seen, run with the agent that is live (the AI if connected)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False}

    def start(self, n: int, limit: int = 300) -> dict:
        if limit not in RULES:
            raise ValueError("unknown rule")
        with self.lock:
            if self.state.get("running"):
                return self.state
            seed = random.randint(10000, 99999)
            self.state = {
                "running": True,
                "n": n,
                "seed": seed,
                "done": 0,
                "rows": [],
                "started": time.time(),
                "limit": limit,
                "rules": RULES[limit],
                "agent": (
                    f"AI ({getattr(BACKEND, 'provider', '')}: {getattr(BACKEND, 'model', '')})"
                    if BACKEND
                    else "rule-based backup"
                ),
            }
        threading.Thread(target=self._run, args=(n, seed, limit), daemon=True).start()
        return self.state

    def _run(self, n: int, seed: int, limit: int = 300) -> None:
        import eval_support as E
        from majhar.generate import generate

        try:
            for scn in generate(n, seed):
                r = E.run(scn, "agent", backend=BACKEND, limit=limit)
                row = {
                    "scenario": scn.id,
                    "faults": scn.faults,
                    "arrivals": scn.arrivals,
                    "affected": r["affected"],
                    "ai_calls": r.get("ai_calls", 0),
                    "policy_violations": r["policy_violations"],
                    "whole": r["made_whole"],
                    "tickets": r["tickets"],
                    "no_human": r["resolved_no_human"],
                    "resolved": r["resolved"],
                    "false_closures": r["false_closures"],
                    "escalated": r["escalated"],
                    "minutes": statistics.median(r["minutes"]) if r["minutes"] else None,
                    "ai_tokens": r.get("ai_tokens", 0),
                    "ai_problems": r.get("ai_problems", 0),
                    "backup_problems": r.get("backup_problems", 0),
                    "ai_errors": r.get("ai_errors", 0),
                    "ai_error": r.get("ai_error"),
                    "handover_reasons": r.get("handover_reasons", {}),
                    "backup_reasons": r.get("backup_reasons", []),
                }
                with self.lock:
                    self.state["rows"].append(row)
                    self.state["done"] += 1
        except Exception as e:
            with self.lock:
                self.state["error"] = f"{type(e).__name__}: {e}"
        with self.lock:
            self.state["running"] = False
            self.state["seconds"] = round(time.time() - self.state["started"], 1)
            self._save(dict(self.state))

    @staticmethod
    def _save(st: dict) -> None:
        """Every test run on this computer is kept, so the results you show are the ones you produced."""
        rows = st.get("rows", [])
        T = lambda k: sum(r.get(k) or 0 for r in rows)
        rec = {
            "when": time.strftime("%Y-%m-%d %H:%M"),
            "agent": st["agent"],
            "seed": st["seed"],
            "scenarios": len(rows),
            "rules": st.get("rules", RULES[300]),
            "affected": T("affected"),
            "whole": T("whole"),
            "resolved": T("resolved"),
            "no_human": T("no_human"),
            "false_closures": T("false_closures"),
            "policy_violations": T("policy_violations"),
            "escalated": T("escalated"),
            "ai_calls": T("ai_calls"),
            "ai_tokens": T("ai_tokens"),
            "ai_problems": T("ai_problems"),
            "backup_problems": T("backup_problems"),
            "ai_errors": T("ai_errors"),
            "seconds": st.get("seconds"),
            "error": st.get("error"),
            "rows": rows,
        }
        path = os.path.join(HERE, "results", "live_runs.json")
        try:
            runs = json.load(open(path)) if os.path.exists(path) else []
        except ValueError:
            runs = []
        runs.append(rec)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(runs, f, indent=1, ensure_ascii=False)
        with open(os.path.join(HERE, "results", "live_runs.md"), "w") as f:
            f.write(
                "# Test runs on this computer\n\nEach row is a run of fresh scenarios (random seed) by the live agent, "
                "judged on the simulator's hidden truth.\n\n| when | agent | rules | seed | scenarios | customers affected | made whole | "
                "solved with no person | closed while not fixed | over-limit refunds without approval | AI calls | AI tokens | "
                "problems planned by the AI | problems on the backup playbook (AI unavailable) | time |\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
            )
            for r in runs:
                f.write(
                    f"| {r['when']} | {r['agent']} | {r.get('rules', RULES[300])} | {r['seed']} | {r['scenarios']} | {r['affected']} | {r['whole']} | "
                    f"{r['no_human']}/{r['resolved']} | {r['false_closures']} | {r['policy_violations']} | {r['ai_calls']} | "
                    f"{r.get('ai_tokens', 0)} | {r.get('ai_problems', 0)} | {r.get('backup_problems', 0)} | "
                    f"{r['seconds']}s |\n"
                )

    def get(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state, default=str))


LIVE = LiveTest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8", code)

    def _page(self, name):
        with open(os.path.join(HERE, "web", name), "rb") as f:
            self._send(f.read(), "text/html; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        pages = {"/": "app.html", "/shop": "shop.html", "/desk": "desk.html", "/results": "results.html"}
        if u.path in pages:
            return self._page(pages[u.path])
        if u.path == "/api/shop":
            return self._json(SESSION.shop_state(q.get("customer", ["Sara"])[0]))
        if u.path == "/api/desk":
            return self._json(SESSION.desk_state())
        if u.path == "/api/case":
            return self._json(SESSION.case_detail(q.get("id", [""])[0]))
        if u.path == "/api/results":
            return self._json(results_summary())
        if u.path == "/api/livetest":
            return self._json(LIVE.get())
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}")
            p = urlparse(self.path).path
            if p == "/api/buy":
                return self._json(
                    SESSION.buy(b.get("customer"), b.get("section"), b.get("qty", 1), b.get("lang", "en"))
                )
            if p == "/api/report":
                SESSION.report(b["booking"], b.get("text", ""))
            elif p == "/api/approve":
                SESSION.decide(b["case_id"], True, b.get("by", "support lead"))
            elif p == "/api/deny":
                SESSION.decide(b["case_id"], False, b.get("by", "support lead"))
            elif p == "/api/demo":
                SESSION.demo(b.get("action", ""), b.get("failure"), b.get("limit"))
            elif p == "/api/livetest":
                return self._json(LIVE.start(max(3, min(int(b.get("n", 10)), 50)), int(b.get("limit", 300))))
            else:
                return self._json({"error": "not found"}, 404)
            self._json({"ok": True})
        except (ValueError, KeyError, TypeError) as e:
            self._json({"error": str(e)}, 400)


def clock_loop(stop: threading.Event) -> None:
    while not stop.wait(SECONDS_PER_MINUTE):
        SESSION.tick()


def make_server(port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--rules", action="store_true", help="run without the AI (rule-based fallback)")
    ap.add_argument("--provider", choices=["groq", "gemini", "ollama", "openai", "anthropic"], default=None)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = ap.parse_args()
    if args.rules:
        BACKEND_ERROR = "started with --rules"
    else:
        try:
            BACKEND = make_backend(args.provider)
        except Exception as e:
            BACKEND, BACKEND_ERROR = None, f"{type(e).__name__}: {e}"
            print(f"AI NOT CONNECTED: {e}\n  Running the rule-based fallback for now.")
    LOG_TO_FILE = True
    SESSION.reset()
    print(
        "Agent:",
        f"AI ({getattr(BACKEND, 'provider', 'ai')}, model {BACKEND.model})" if BACKEND else "rule-based fallback",
    )
    try:
        srv = make_server(args.port)
    except OSError:
        print(
            f"\nPort {args.port} is already in use: Majhar is probably still running in another Terminal window.\n"
            f"Close that window, or run this and then start again:\n    lsof -ti:{args.port} | xargs kill\n"
        )
        raise SystemExit(1)
    stop = threading.Event()
    threading.Thread(target=clock_loop, args=(stop,), daemon=True).start()
    print(f"Majhar: http://127.0.0.1:{args.port}   (Ctrl+C to stop)")
    print(f"System log is also saved to {os.path.relpath(LOG_FILE, HERE)}")
    if not args.no_browser:
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop.set()
        print("\nbye")
