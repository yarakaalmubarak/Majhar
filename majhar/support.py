"""Support desk: tickets, the refund policy, approvals and the rule-based workflow."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .sim import CONFIRMED, HOLD, PAID_NO_SEAT, PAYMENT_UNRECORDED, REFUND_REQUESTED, REFUNDED, SEATED, Sim
from .tools import Toolbox

ROOT_FIX = {
    "payment_webhook_drop": [("restart_payment_webhook", {}), ("replay_payments", {})],
    "seat_assign_timeout": [("restart_seat_assigner", {})],
    "confirmation_worker_down": [("restart_confirmation_worker", {}), ("replay_confirmations", {})],
    "queue_overflow_drop": [("scale_confirmation_queue", {}), ("replay_confirmations", {})],
    "refund_worker_failing": [("restart_refund_worker", {})],
    "duplicate_sale": [("enable_seat_uniqueness_check", {})],
    "hold_ttl_bug": [("set_hold_ttl", {"minutes": 10})],
    "seat_sync_corruption": [("rollback_seat_sync", {})],
    "ticket_render_bug": [],
}
PLATFORM_FOR = {"ticket_render_bug": ["rollback_ticket_template"], "seat_sync_corruption": ["repair_seat_records"]}
PLATFORM_PLAN = {
    "rollback_ticket_template": "roll the ticket template back to the last working version (affects every new ticket)",
    "repair_seat_records": "repair damaged ticket records from the primary database (bulk write)",
}
ISSUE_LABEL = {
    "payment_unrecorded": "Charged, but the order was never recorded",
    "assign_timeout": "Charged, but no seat was assigned",
    "seat_taken": "Charged, but the seat went to someone else",
    "seat_double_sold": "Seat sold twice",
    "no_ticket_sent": "Seat booked, but no ticket sent",
    "ticket_not_delivered": "Ticket failed to deliver",
    "refund_stuck": "Refund not arriving",
    "ticket_gate_mismatch": "Ticket record damaged (would fail at the gate)",
    "customer_report": "Customer reported a problem",
}
CAUSE_LABEL = {
    "payment_webhook_drop": "the payment webhook was dropping orders after taking the money",
    "seat_assign_timeout": "the seat-assignment service was timing out",
    "confirmation_worker_down": "the confirmation worker had stopped",
    "queue_overflow_drop": "the message queue was dropping confirmations under load",
    "refund_worker_failing": "the refund worker was not settling refunds",
    "duplicate_sale": "a database change allowed the same seat to be sold twice",
    "hold_ttl_bug": "seat holds were expiring before customers finished paying",
    "seat_sync_corruption": "a sync job was damaging ticket records",
    "ticket_render_bug": "a new ticket template fails for Arabic tickets",
}

MSG = {
    "opened": (
        "We can see a problem with your order {oid} ({amt} SAR). Case {cid} is open and our assistant is already "
        "working on it. You don't need to do anything.",
        "لاحظنا مشكلة في طلبك {oid} ({amt} ريال). تم فتح البلاغ {cid} ومساعدنا يعمل عليه الآن، ولا تحتاج لفعل أي شيء.",
    ),
    "restored": (
        "Fixed: your payment is recorded and your booking is confirmed. Seat {seat}. Your ticket is on its way.",
        "تم الإصلاح: سُجّلت دفعتك وتأكد حجزك. المقعد {seat}. تذكرتك في الطريق إليك.",
    ),
    "reassigned": (
        "Your original seat was no longer available, so we moved you to seat {seat} in the same section at no "
        "extra cost. Your ticket is on its way.",
        "مقعدك الأصلي لم يعد متاحًا، لذلك نقلناك إلى المقعد {seat} في نفس القسم دون أي تكلفة إضافية. تذكرتك في الطريق إليك.",
    ),
    "ticket_sent": ("Your ticket has been delivered. Enjoy the event!", "تم إرسال تذكرتك. نتمنى لك وقتًا ممتعًا!"),
    "refund_pending": (
        "Your seat is no longer available and there is no equal seat left. We have prepared a full refund of "
        "{amt} SAR; a team member is signing it off now.",
        "مقعدك لم يعد متاحًا ولا يوجد مقعد مماثل. جهّزنا استردادًا كاملًا بقيمة {amt} ريال، وأحد أعضاء الفريق يعتمده الآن.",
    ),
    "refund_started": (
        "A full refund of {amt} SAR has been approved. It will reach your card in 3 to 5 business days.",
        "تمت الموافقة على استرداد كامل بقيمة {amt} ريال، وسيصل إلى بطاقتك خلال 3 إلى 5 أيام عمل.",
    ),
    "refunded": (
        "Your refund of {amt} SAR has been sent to your card.",
        "تم إرسال مبلغ الاسترداد {amt} ريال إلى بطاقتك.",
    ),
    "escalated": (
        "A specialist is now handling your case personally. We will contact you shortly.",
        "يتولى أحد المختصين بلاغك الآن شخصيًا، وسنتواصل معك قريبًا.",
    ),
    "reopened": (
        "We noticed a new problem with your booking {oid} and have reopened case {cid}. We are on it.",
        "لاحظنا مشكلة جديدة في حجزك {oid} وأعدنا فتح البلاغ {cid}. نعمل عليها الآن.",
    ),
    "nothing_charged": (
        "We checked: your payment has not been taken, so you have not been charged. You can try again.",
        "تحققنا: لم يتم خصم أي مبلغ منك. يمكنك المحاولة مرة أخرى.",
    ),
}


@dataclass
class Policy:
    """What the agent may do on its own."""

    auto_refund_limit_sar: int = 300
    fix_patience_min: int = 6

    def decide(self, tool: str, preview: dict) -> tuple[str, str]:
        if tool == "reassign_seat":
            return "auto", "equal seat, same section, no money moves"
        if tool == "issue_refund":
            amt = preview.get("total_sar", 0)
            if amt <= self.auto_refund_limit_sar:
                return "auto", f"{amt} SAR is within the {self.auto_refund_limit_sar} SAR auto-refund limit"
            return "approval", f"{amt} SAR is above the {self.auto_refund_limit_sar} SAR auto-refund limit"
        return "approval", "platform-wide change: a human signs off"


@dataclass
class Case:
    id: str
    order_id: int
    customer: str
    lang: str
    amount: int
    source: str
    opened_t: int
    issue: str
    reason: str = ""
    status: str = "open"
    cause: str | None = None
    outcome: str | None = None
    notes: list[dict] = field(default_factory=list)
    proposal: dict | None = None
    human_touches: int = 0
    failed_tries: int = 0
    incident: str | None = None
    handover: str = ""
    auto_actions: int = 0
    resolved_t: int | None = None
    _sent: set = field(default_factory=set)


class SupportDesk:
    """The ticket system plus the agent that works it."""

    def __init__(self, sim: Sim, policy: Policy | None = None, autopilot: bool = True, reopen: bool = True):
        self.sim = sim
        self.tb = Toolbox(sim)
        self.policy = policy or Policy()
        self.autopilot = autopilot
        self.reopen = reopen
        self.cases: dict[str, Case] = {}
        self.by_order: dict[int, Case] = {}
        self.fixed: dict[str, int] = {}
        self.causes: dict[str, str] = {}
        self.feed: list[dict] = []
        self.platform: dict[str, str] = {}
        self.incidents: dict[str, dict] = {}
        self._snap_key, self._snap = None, {}
        self._n = 1001

    def _now(self) -> dict:
        return {"t": self.sim.t, "time": self.sim.clock()}

    def _log(self, case: Case | None, who: str, text: str, public: bool = False, key: str | None = None, **fmt) -> None:
        note = {**self._now(), "who": who, "text": text, "public": public}
        if key:
            en, ar = MSG[key]
            note.update(text=en.format(**fmt), text_ar=ar.format(**fmt), public=True, key=key)
        if case is not None:
            case.notes.append(note)
        self.feed.append({**note, "case": case.id if case else None})

    def _fmt(self, case: Case) -> dict:
        o = self.sim.orders[case.order_id]
        ref = self.booking(o.id)
        if ref and len(self.sim.bookings[ref]) > 1:
            os_ = [self.sim.orders[i] for i in self.sim.bookings[ref]]
            return {
                "oid": ref,
                "amt": sum(x.amount for x in os_),
                "cid": case.id,
                "seat": ", ".join(x.seat for x in os_),
            }
        return {"oid": o.id, "amt": o.amount, "cid": case.id, "seat": o.seat}

    def _tell(self, case: Case, key: str, **fmt) -> None:
        if key in case._sent:
            return
        case._sent.add(key)
        if any(key in sib._sent for sib in self.siblings(case)):
            return
        self._log(case, "agent", "", key=key, **{**self._fmt(case), **fmt})

    def snapshot(self) -> dict[int, str]:
        """Affected orders and why (the reconciliation feed)."""
        key = (self.sim.t, len(self.tb.actions), len(self.tb.calls), self.sim.next_id, len(self.sim.unrecorded))
        if key != self._snap_key:
            self._snap_key, self._snap = key, {o.id: r for o, r in self.sim.stranded()}
        return self._snap

    def booking(self, order_id: int) -> str | None:
        return getattr(self.sim, "booking_of", {}).get(order_id)

    def siblings(self, c: Case) -> list[Case]:
        """Other tickets for the same booking (a customer who bought several tickets in one checkout)."""
        ref = self.booking(c.order_id)
        if not ref:
            return []
        return [self.by_order[i] for i in self.sim.bookings.get(ref, []) if i != c.order_id and i in self.by_order]

    def incident(self, key: str, title: str, cause: str | None, evidence: str, by: str) -> dict:
        inc = self.incidents.get(key)
        if inc is None:
            inc = self.incidents[key] = {
                "id": f"INC-{len(self.incidents) + 1}",
                "key": key,
                "title": title,
                "cause": cause,
                "evidence": evidence,
                "plan": [],
                "message": None,
                "thoughts": [],
                "by": by,
                **self._now(),
            }
        return inc

    def free_seats(self) -> Counter:
        """The inventory system: free seats per section."""
        return Counter(s.section for s in self.sim.seats.values() if s.status == "free")

    def open_cases(self) -> list[Case]:
        return [c for c in self.cases.values() if c.status not in ("resolved", "escalated")]

    def _open(self, order_id: int, source: str, reason: str) -> Case:
        o = self.sim.orders[order_id]
        cid = f"CASE-{self._n}"
        self._n += 1
        c = Case(
            cid, order_id, o.customer, o.lang, o.amount, source, self.sim.t, ISSUE_LABEL.get(reason, reason), reason
        )
        self.cases[cid] = c
        self.by_order[order_id] = c
        how = "reconciliation alert (no complaint needed)" if source == "auto_detected" else "customer complaint"
        self._log(c, "system", f"Ticket opened from {how}: {c.issue}. Order {o.id}, seat {o.seat}, {o.amount} SAR.")
        self._tell(c, "opened")
        return c

    def report(self, order_id: int, text: str = "") -> Case:
        """A customer clicks 'Report a problem'."""
        c = self.by_order.get(order_id)
        if c is None:
            c = self._open(order_id, "customer_report", "customer_report")
        if text:
            self._log(c, "customer", text, public=True)
        if c.status in ("resolved", "escalated") and c.outcome != "nothing_charged":
            return c
        c.status = "open" if c.status == "resolved" else c.status
        return c

    def _intake(self, snap: dict[int, str]) -> None:
        for oid, reason in snap.items():
            c = self.by_order.get(oid)
            if c is None:
                self._open(oid, "auto_detected", reason)
            elif self.reopen and c.status == "resolved" and c.outcome != "refunded":
                c.status, c.outcome, c.reason, c.failed_tries = "open", None, reason, 0
                c.issue = ISSUE_LABEL.get(reason, reason)
                self._log(c, "system", f"Reopened: a new problem appeared after the ticket was closed ({c.issue}).")
                c._sent.discard("reopened")
                self._tell(c, "reopened")

    def tick(self, minutes: int = 1) -> None:
        for _ in range(minutes):
            self.tb.advance(1)
            self.step()

    def step(self) -> None:
        snap = self.snapshot()
        self._intake(snap)
        if self.autopilot:
            self.work(snap)

    def work(self, snap: dict[int, str] | None = None) -> None:
        snap = snap if snap is not None else self.snapshot()
        m = self.tb.call("get_metrics")
        self._raise_platform_proposals()
        for c in list(self.open_cases()):
            if c.status not in ("awaiting_approval", "resolved", "escalated"):
                self._work(c, snap, m)
                snap = self.snapshot()

    def _diagnose(self, case: Case, reason: str, m: dict) -> tuple[str | None, str]:
        """Root cause from evidence an engineer can see."""
        if reason == "payment_unrecorded":
            return "payment_webhook_drop", (
                f"payment ledger shows the charge, the order database does not; "
                f"{m['payments_unreconciled']} payments unreconciled platform-wide"
            )
        if reason == "assign_timeout":
            return "seat_assign_timeout", "order paid, seat assignment timed out"
        if reason == "seat_double_sold":
            return "duplicate_sale", f"{m['seats_oversold']} seats are sold to more than one customer"
        if reason == "seat_taken":
            if m["hold_ttl_min"] < 5:
                return "hold_ttl_bug", f"seat holds last only {m['hold_ttl_min']} min, shorter than checkout"
            if "payment_webhook_drop" in self.causes:
                return (
                    "payment_webhook_drop",
                    "the seat hold lapsed while the payment was stuck, and the seat was resold",
                )
            return None, "seat was resold before the payment was recorded"
        if reason == "no_ticket_sent":
            if m["confirm_consumers"] == 0:
                return "confirmation_worker_down", f"confirmation queue has {m['queue_depth']} messages and no consumer"
            if m["queue_dropped_3m"] > 0:
                return "queue_overflow_drop", f"broker dropped {m['queue_dropped_3m']} confirmations in 3 min"
            return None, "confirmation never sent"
        if reason == "ticket_not_delivered":
            if m["ticket_render_errors_3m"] > 0:
                return "ticket_render_bug", f"{m['ticket_render_errors_3m']} ticket render errors in 3 min"
            if case.failed_tries:
                return "ticket_render_bug", "re-sending this ticket failed: the ticket itself cannot be produced"
            return None, "ticket delivery failed once"
        if reason == "refund_stuck":
            return (
                "refund_worker_failing",
                f"{m['refunds_pending']} refunds pending, oldest {m['oldest_refund_age_min']} min",
            )
        if reason == "ticket_gate_mismatch":
            return "seat_sync_corruption", f"{m['sync_mismatches']} ticket records differ from the primary database"
        return None, reason

    def _fix_root(self, case: Case, cause: str, evidence: str) -> None:
        if cause not in self.causes:
            self.causes[cause] = evidence
            self.tb.call(
                "submit_diagnosis",
                {
                    "summary": "support desk",
                    "hypotheses": [{"cause": c, "confidence": 0.9, "evidence": e} for c, e in self.causes.items()],
                },
            )
        case.cause = cause
        if cause in self.fixed:
            self._log(
                case,
                "agent",
                f"Same root cause as an earlier ticket ({CAUSE_LABEL[cause]}), already fixed "
                f"{self.sim.t - self.fixed[cause]} min ago.",
            )
            case.incident = case.incident or self.incidents.get(cause, {}).get("id")
            return
        self.fixed[cause] = self.sim.t
        inc = self.incident(cause, CAUSE_LABEL[cause][0].upper() + CAUSE_LABEL[cause][1:], cause, evidence, "rules")
        inc["plan"].append(f"Fix the platform: {', '.join(t for t, _ in ROOT_FIX[cause]) or 'needs human sign-off'}")
        case.incident = inc["id"]
        self._log(case, "agent", f"Root cause: {CAUSE_LABEL[cause]}. Evidence: {evidence}.")
        for tool, args in ROOT_FIX[cause]:
            res = self.tb.call(tool, args)
            case.auto_actions += 1
            self._log(
                case,
                "agent",
                f"Fixed the platform so it stops happening to other customers: {tool} -> " f"{_brief(res)}",
            )
        for tool in PLATFORM_FOR.get(cause, ()):
            self.platform.setdefault(tool, "needed")

    def _raise_platform_proposals(self) -> None:
        """A platform-wide change is proposed once, on one ticket that needs it, and serves every ticket."""
        for tool, state in self.platform.items():
            if state != "needed":
                continue
            cause = next(c for c, tools in PLATFORM_FOR.items() if tool in tools)
            host = next((c for c in self.open_cases() if c.cause == cause and c.proposal is None), None)
            if host and self._propose(host, tool, {}, PLATFORM_PLAN[tool]) == "pending":
                self.platform[tool] = "proposed"

    def _platform_gate(self, c: Case, tool: str) -> bool:
        """True when this ticket must wait for (or has lost) a platform-wide change."""
        state = self.platform.get(tool)
        if state in ("needed", "proposed"):
            return True
        if state == "denied":
            self._escalate(c, f"the platform change it needs ({PLATFORM_PLAN[tool]}) was declined")
            return True
        return False

    def _refund_siblings(self, case: Case) -> list[Case]:
        """Tickets from the same booking that also need a refund now (no equal seat left for them either)."""
        free = self.free_seats()
        out = []
        for sib in self.siblings(case):
            so = self.sim.orders[sib.order_id]
            if (
                sib.status not in ("resolved", "escalated", "awaiting_approval", "refunding")
                and so.status == PAID_NO_SEAT
                and free[so.section] == 0
            ):
                out.append(sib)
        return out

    def _propose(self, case: Case, tool: str, args: dict, plan: str) -> str:
        linked = []
        if tool == "issue_refund":
            linked = self._refund_siblings(case)
            if linked:
                args = {"order_ids": [case.order_id] + [x.order_id for x in linked]}
                total = sum(self.sim.orders[i].amount for i in args["order_ids"])
                plan = f"full refund of {total} SAR for booking {self.booking(case.order_id)} ({len(args['order_ids'])} tickets)"
        pre = self.tb.call(tool, args, None)
        preview = pre.get("preview", pre)
        tier, why = self.policy.decide(tool, preview)
        if tier == "auto":
            res = self.tb.call(tool, args, True)
            case.auto_actions += 1
            self._log(case, "agent", f"{plan} (auto-approved: {why}) -> {_brief(res)}")
            for x in linked:
                x.status = "refunding"
                self._log(x, "agent", f"Refunded together with {case.id} (same booking): {plan}.")
            return "done"
        case.proposal = {
            "tool": tool,
            "args": args,
            "plan": plan,
            "preview": preview,
            "why": why,
            "amount_sar": preview.get("total_sar"),
            "linked": [x.id for x in linked],
            **self._now(),
        }
        case.status = "awaiting_approval"
        self._log(case, "agent", f"Needs a human: {plan}. Reason: {why}.")
        for x in linked:
            x.status, x.proposal = "awaiting_approval", {"shared_with": case.id, "plan": plan, **self._now()}
            self._log(x, "agent", f"Waiting for the same approval as {case.id} (same booking).")
        return "pending"

    def approve(self, case_id: str, by: str = "operator") -> Case:
        c = self.cases[case_id]
        p = c.proposal
        if p and p.get("shared_with"):
            return self.approve(p["shared_with"], by)
        if not p:
            return c
        res = self.tb.call(p["tool"], p["args"], True)
        c.human_touches += 1
        c.proposal = None
        c.status = "working"
        if p["tool"] in self.platform:
            self.platform[p["tool"]] = "approved"
        self._log(c, "human", f"Approved by {by}: {p['plan']} -> {_brief(res)}")
        if p["tool"] == "issue_refund":
            c.status = "refunding"
            self._tell(c, "refund_started")
            for lid in p.get("linked", []):
                x = self.cases[lid]
                x.proposal, x.status = None, "refunding"
                x.human_touches += 1
                self._log(x, "human", f"Approved by {by} together with {c.id}: {p['plan']}")
                self._tell(x, "refund_started")
        return c

    def deny(self, case_id: str, by: str = "operator") -> Case:
        c = self.cases[case_id]
        p = c.proposal
        if p and p.get("shared_with"):
            return self.deny(p["shared_with"], by)
        if not p:
            return c
        self.tb.call(p["tool"], p["args"], False)
        c.human_touches += 1
        c.proposal = None
        if p["tool"] in self.platform:
            self.platform[p["tool"]] = "denied"
        self._escalate(c, f"{by} declined: {p['plan']}")
        for lid in p.get("linked", []):
            x = self.cases[lid]
            x.proposal = None
            x.human_touches += 1
            self._escalate(x, f"{by} declined the refund for the whole booking ({c.id})")
        return c

    def _escalate(self, c: Case, why: str) -> None:
        c.status, c.outcome, c.resolved_t = "escalated", "escalated", self.sim.t
        c.handover = why
        self._log(c, "agent", f"Handed to a human specialist: {why}.")
        self._tell(c, "escalated")

    def _resolve(self, c: Case, outcome: str, key: str | None = None) -> None:
        c.status, c.outcome, c.resolved_t = "resolved", outcome, self.sim.t
        if key:
            self._tell(c, key)
        self._log(
            c,
            "agent",
            f"Verified with the order record and closed the ticket ({outcome}) after "
            f"{self.sim.t - c.opened_t} min.",
        )

    STALL_LIMIT = 3

    def _act(self, c: Case, tool: str, args: dict, what: str) -> bool:
        """Run a customer-level fix."""
        res = self.tb.call(tool, args)
        c.auto_actions += 1
        ok = isinstance(res, dict) and "error" not in res and bool(res.get("effective", True))
        c.failed_tries = 0 if ok else c.failed_tries + 1
        self._log(c, "agent", f"{what} -> {_brief(res)}")
        if c.failed_tries >= self.STALL_LIMIT:
            self._escalate(c, f"{tool} was tried {c.failed_tries} times without effect; retrying will not help")
            return False
        return ok

    def _work(self, c: Case, snap: dict[int, str], m: dict) -> None:
        o = self.sim.orders[c.order_id]
        reason = snap.get(o.id)
        if c.status == "open":
            c.status = "working"
            self._log(
                c,
                "agent",
                f"Investigating: order status {o.status}, payment ledger, inventory "
                f"({dict(self.free_seats())} free), platform health.",
            )
        if o.status == REFUNDED:
            return self._resolve(c, "refunded", "refunded")
        if reason is None and o.status == CONFIRMED and o.delivered and o.gate_ok:
            if c.cause is None and c.reason not in ("", "customer_report"):
                cause, _ = self._diagnose(c, c.reason, m)
                if cause in self.fixed:
                    c.cause = cause
                    c.incident = c.incident or self.incidents.get(cause, {}).get("id")
                    self._log(
                        c,
                        "agent",
                        f"Restored by the platform fix made for an earlier ticket " f"({CAUSE_LABEL[cause]}).",
                    )
            if c.outcome in (None, "restored", "reassigned"):
                key = "reassigned" if c.outcome == "reassigned" else ("restored" if c.cause else "ticket_sent")
                return self._resolve(c, c.outcome or ("restored" if c.cause else "ticket_sent"), key)
        if o.status == REFUND_REQUESTED and reason != "refund_stuck":
            c.status = "refunding"
            return
        if reason is None:
            if o.status == HOLD and c.source == "customer_report":
                return self._resolve(c, "nothing_charged", "nothing_charged")
            if o.status == PAYMENT_UNRECORDED and c.source == "customer_report":
                reason = "payment_unrecorded"
            else:
                return
        cause, evidence = self._diagnose(c, reason, m)
        if cause and cause not in self.fixed:
            self._fix_root(c, cause, evidence)
            if c.status == "awaiting_approval":
                return
            o = self.sim.orders[c.order_id]
            reason = self.snapshot().get(o.id, reason if o.status == PAYMENT_UNRECORDED else None)
            if reason in (None, "payment_unrecorded", "no_ticket_sent"):
                return
        elif cause:
            if c.cause is None:
                self._log(
                    c,
                    "agent",
                    f"Same root cause as an earlier ticket: {CAUSE_LABEL[cause]} (already fixed). "
                    f"Effect on this order: {evidence}.",
                )
            c.cause = c.cause or cause
            c.incident = c.incident or self.incidents.get(cause, {}).get("id")
            if self.sim.t - self.fixed[cause] > self.policy.fix_patience_min and reason not in (
                "assign_timeout",
                "seat_taken",
                "seat_double_sold",
            ):
                return self._escalate(
                    c, f"the fix for {cause} did not resolve this order within " f"{self.policy.fix_patience_min} min"
                )
        if reason == "payment_unrecorded":
            self._act(c, "replay_payments", {}, "Recorded the missing payment from the gateway ledger")
        elif reason in ("assign_timeout", "seat_taken", "seat_double_sold"):
            if self.free_seats()[o.section] > 0:
                before = o.seat
                if (
                    self._propose(
                        c,
                        "reassign_seat",
                        {"order_ids": [o.id]},
                        f"move the customer from {before} to an equal seat in section {o.section}",
                    )
                    == "done"
                ):
                    c.outcome = "reassigned"
            else:
                self._log(c, "agent", f"No free seat left in section {o.section}; the fair outcome is a full refund.")
                if self._propose(c, "issue_refund", {"order_ids": [o.id]}, f"full refund of {o.amount} SAR") == "done":
                    c.status = "refunding"
                    self._tell(c, "refund_started")
                else:
                    self._tell(c, "refund_pending")
        elif reason == "no_ticket_sent":
            self._act(c, "replay_confirmations", {}, "Re-sent the booking confirmation")
        elif reason == "ticket_not_delivered":
            if cause == "ticket_render_bug" and self._platform_gate(c, "rollback_ticket_template"):
                return
            self._act(c, "resend_tickets", {}, "Re-sent the ticket")
        elif reason == "ticket_gate_mismatch":
            if self._platform_gate(c, "repair_seat_records"):
                return
            if m["sync_mismatches"] > 0:
                self.platform["repair_seat_records"] = "needed"
        elif reason == "refund_stuck":
            pass

    def customer_view(self, order_id: int) -> dict:
        o = self.sim.orders[order_id]
        c = self.by_order.get(order_id)
        reason = self.snapshot().get(order_id)
        if o.status == REFUNDED:
            state, label = "refunded", "Refunded"
        elif o.status == REFUND_REQUESTED:
            state, label = "refunding", "Refund on its way"
        elif c and c.status == "escalated":
            state, label = "problem", "A specialist is handling this"
        elif o.status == CONFIRMED and o.delivered and o.gate_ok and reason is None:
            state, label = "confirmed", "Confirmed, ticket sent"
        elif o.status == HOLD:
            state, label = "processing", "Processing payment"
        elif o.status == PAYMENT_UNRECORDED and not c:
            state, label = "processing", "Payment received, confirming your booking"
        elif o.status in (SEATED, CONFIRMED) and not c and reason is None:
            state, label = "processing", "Booking confirmed, preparing your ticket"
        else:
            state, label = "problem", "Problem found, we are fixing it"
        msgs = [n for n in c.notes if n["public"]] if c else []
        return {
            "order_id": o.id,
            "seat": o.seat,
            "section": o.section,
            "amount_sar": o.amount,
            "lang": o.lang,
            "state": state,
            "label": label,
            "case": c.id if c else None,
            "case_status": c.status if c else None,
            "messages": msgs,
            "can_report": c is None or c.status in ("resolved",) and c.outcome == "nothing_charged",
        }

    def kpis(self) -> dict:
        cs = list(self.cases.values())
        done = [c for c in cs if c.status == "resolved"]
        auto = [c for c in done if c.human_touches == 0]
        refunds = [c for c in cs if c.outcome == "refunded" or c.status == "refunding"]
        kept = [c for c in done if c.outcome in ("restored", "reassigned", "ticket_sent")]
        return {
            "cases": len(cs),
            "open": len(self.open_cases()),
            "resolved": len(done),
            "awaiting_approval": sum(c.status == "awaiting_approval" for c in cs),
            "escalated": sum(c.status == "escalated" for c in cs),
            "resolved_without_human": len(auto),
            "autonomy_pct": round(100 * len(auto) / len(done)) if done else None,
            "mean_minutes_to_resolve": (
                round(sum(c.resolved_t - c.opened_t for c in done) / len(done), 1) if done else None
            ),
            "refunded_sar": sum(c.amount for c in refunds),
            "revenue_kept_sar": sum(c.amount for c in kept),
            "human_touches": sum(c.human_touches for c in cs),
        }


def _brief(res) -> str:
    if not isinstance(res, dict):
        return str(res)
    keep = {k: v for k, v in res.items() if k not in ("plan", "preview", "dry_run", "unneeded", "not_eligible")}
    return ", ".join(f"{k}={v}" for k, v in keep.items()) or "ok"
