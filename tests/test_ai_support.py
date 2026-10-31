"""AI incident commander and its guardrails, tested with a scripted stand-in model."""

import json
import random

from majhar.ai_support import PROBLEMS, STEPS, TOOL_SCHEMAS, AIDesk
from majhar.shop import ShopSim

SEAT = {"seat_taken", "assign_timeout", "seat_double_sold"}
PLAN = {
    "payment_unrecorded": ["record_missing_payment"],
    "no_ticket_sent": ["resend_confirmation"],
    "ticket_not_delivered": ["resend_ticket"],
    "refund_stuck": ["wait"],
    "ticket_gate_mismatch": ["repair_records"],
    **{k: ["move_to_equal_seat", "refund_if_no_equal_seat"] for k in SEAT},
}


class FakeCommander:
    """Plays a sensible incident commander: investigate, diagnose, fix, plan, write the message, done."""

    def __init__(self):
        self.calls = 0

    def _use(self, *calls, text="Looking at the incident."):
        return {
            "content": ([{"type": "text", "text": text}] if text else [])
            + [
                {"type": "tool_use", "id": f"t{self.calls}_{i}", "name": n, "input": a}
                for i, (n, a) in enumerate(calls)
            ],
            "stop_reason": "tool_use",
        }

    @staticmethod
    def needs(brief: str) -> list[str]:
        return [
            x["problem"] for x in json.loads(brief.split("NEEDS A PLAN:\n", 1)[1].split("\n\nALREADY PLANNED", 1)[0])
        ]

    @staticmethod
    def results(messages) -> dict:
        uses = [b for b in messages[-2]["content"] if b["type"] == "tool_use"]
        return {u["name"]: json.loads(r["content"]) for u, r in zip(uses, messages[-1]["content"])}

    def cause(self, k, p):
        mt = p["metrics"]
        if k == "payment_unrecorded":
            return "payment_webhook_drop"
        if k == "seat_taken":
            return (
                "hold_ttl_bug"
                if mt["hold_ttl_min"] < 5
                else (
                    "payment_webhook_drop"
                    if "payment_webhook_drop" in p["root_causes_already_fixed"]
                    else "no_platform_fault"
                )
            )
        if k == "no_ticket_sent":
            return (
                "confirmation_worker_down"
                if mt["confirm_consumers"] == 0
                else ("queue_overflow_drop" if mt["queue_dropped_3m"] > 0 else "no_platform_fault")
            )
        return {
            "assign_timeout": "seat_assign_timeout",
            "seat_double_sold": "duplicate_sale",
            "ticket_not_delivered": "ticket_render_bug",
            "refund_stuck": "refund_worker_failing",
            "ticket_gate_mismatch": "seat_sync_corruption",
        }[k]

    def plan(self, k, cause):
        return PLAN[k]

    def create(self, system, tools, messages):
        self.calls += 1
        assert tools == TOOL_SCHEMAS and "incident commander" in system
        last = messages[-1]["content"]
        if isinstance(last, str) and "NEEDS A PLAN" not in last:
            return {"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}
        if isinstance(last, str):
            self.todo = self.needs(last)
            return self._use(("read_platform", {}), ("check_inventory", {}))
        res = self.results(messages)
        if "read_platform" not in res:
            return self._use(("done", {"summary": "planned"}), text="All problems have a plan.")
        p, calls = res["read_platform"], []
        fixed = set(p["root_causes_already_fixed"])
        for k in self.todo:
            cause = self.cause(k, p)
            calls.append(("diagnose", {"problem": k, "cause": cause, "evidence": f"evidence for {k}"}))
            if cause != "no_platform_fault" and cause not in fixed:
                calls.append(("fix_root_cause", {"cause": cause, "evidence": f"evidence for {k}"}))
                fixed.add(cause)
            calls.append(("set_plan", {"problem": k, "steps": self.plan(k, cause), "reason": "standard remedy"}))
            calls.append(
                (
                    "write_customer_message",
                    {
                        "problem": k,
                        "english": "Hi {name}, we found a problem with your booking {booking} ({amount} SAR) and are fixing it now.",
                        "arabic": "مرحبًا {name}، وجدنا مشكلة في حجزك {booking} ({amount} ريال) ونعمل على حلها الآن.",
                    },
                )
            )
        return self._use(*calls, text="Gateway captured more than was recorded: the webhook is dropping orders.")


FakeClaude = FakeCommander


def shop():
    sim = ShopSim(seed=7, seats_per_section=120)
    return sim


def test_silent_payment_failure_is_found_fixed_explained_and_closed_by_the_ai():
    sim = shop()
    desk = AIDesk(sim, FakeCommander())
    sim.inject("payment_webhook_drop")
    b = sim.place_booking("Sara", "B", 1, "ar")
    desk.tick(8)
    c = desk.by_order[b["order_ids"][0]]
    assert (
        c.status == "resolved" and c.human_touches == 0 and "payment_webhook_drop" not in sim.faults and sim.truth_ok()
    )
    inc = desk.incidents["payment_webhook_drop"]
    assert inc["by"] == "AI" and inc["plan"] and inc["thoughts"] and inc["message"]["arabic"]
    assert any("مرحبًا Sara" in (n.get("text_ar") or "") for n in c.notes)


def test_a_rush_of_customers_costs_a_handful_of_model_calls():
    sim = shop()
    brain = FakeCommander()
    desk = AIDesk(sim, brain)
    sim.inject("payment_webhook_drop")
    sim.rush(60, random.Random(3))
    desk.tick(12)
    cs = list(desk.cases.values())
    assert len(cs) >= 30 and all(c.status == "resolved" for c in cs) and sim.truth_ok()
    assert brain.calls <= 6 and desk.ai_sessions <= 3


def test_one_approval_refunds_a_whole_booking():
    sim = shop()
    sim.sell_out("A", limit=117)
    desk = AIDesk(sim, FakeCommander())
    sim.inject("payment_webhook_drop")
    b = sim.place_booking("Sara", "A", 3, "ar")
    sim.sell_out("A")
    desk.tick(8)
    cs = [desk.by_order[i] for i in b["order_ids"]]
    hosts = [c for c in cs if c.proposal and not c.proposal.get("shared_with")]
    assert len(hosts) == 1 and hosts[0].proposal["amount_sar"] == 1350 and "BK-" in hosts[0].proposal["plan"]
    assert all(sim.orders[i].status == "PAID_NO_SEAT" for i in b["order_ids"])
    desk.approve(cs[-1].id)
    desk.tick(4)
    assert all(c.status == "resolved" and c.outcome == "refunded" for c in cs) and sim.truth_ok()
    public = [n for c in cs for n in c.notes if n["public"] and n["who"] == "agent"]
    assert sum(1 for n in public if n.get("key") == "refund_started") == 1


class Silent(FakeCommander):
    def create(self, system, tools, messages):
        self.calls += 1
        return {"content": [{"type": "text", "text": "I am not sure."}], "stop_reason": "end_turn"}


def test_no_plan_from_the_ai_without_backup_means_people_take_over():
    sim = shop()
    desk = AIDesk(sim, Silent(), fallback=False)
    sim.inject("payment_webhook_drop")
    b = sim.place_booking("Omar", "B")
    desk.tick(6)
    assert desk.by_order[b["order_ids"][0]].status == "escalated"


def test_an_ai_that_stops_early_is_reminded_then_the_playbook_covers_it():
    sim = shop()
    brain = Silent()
    desk = AIDesk(sim, brain)
    sim.inject("payment_webhook_drop")
    b = sim.place_booking("Omar", "B")
    desk.tick(8)
    c = desk.by_order[b["order_ids"][0]]
    assert brain.calls == 1 + AIDesk.REMINDERS, "the AI was reminded before anything else happened"
    assert c.status == "resolved" and "payment_unrecorded" in desk.backup_problems and not c.handover


class Lazy(FakeCommander):
    """Plans nothing on its first answer (says done), then plans properly once reminded."""

    def create(self, system, tools, messages):
        last = messages[-1]["content"]
        if isinstance(last, str) and "NEEDS A PLAN" in last:
            self.calls += 1
            self.todo = self.needs(last)
            return self._use(("done", {"summary": "nothing to do"}), text="Done.")
        texts = [last] if isinstance(last, str) else [b.get("text", "") for b in last if b.get("type") == "text"]
        if any(t.startswith("Not finished") for t in texts):
            self.calls += 1
            return self._use(("read_platform", {}), ("check_inventory", {}))
        return super().create(system, tools, messages)


def test_a_reminder_gets_the_ai_to_plan_what_it_skipped():
    sim = shop()
    desk = AIDesk(sim, Lazy())
    sim.inject("payment_webhook_drop")
    b = sim.place_booking("Omar", "B")
    desk.tick(8)
    assert desk.by_order[b["order_ids"][0]].status == "resolved"
    assert "payment_unrecorded" in desk.plans and not desk.backup_problems


class Quitter(FakeCommander):
    """Tries to hand every problem to people straight away."""

    def create(self, system, tools, messages):
        last = messages[-1]["content"]
        if isinstance(last, str) and "NEEDS A PLAN" in last:
            self.calls += 1
            self.todo = self.needs(last)
            return self._use(*[("escalate_problem", {"problem": k, "reason": "not sure"}) for k in self.todo])
        res = self.results(messages) if not isinstance(last, str) else {}
        if "escalate_problem" in res and res["escalate_problem"].get("not_yet"):
            self.calls += 1
            return self._use(("read_platform", {}), ("check_inventory", {}))
        return super().create(system, tools, messages)


def test_the_ai_must_try_a_plan_before_handing_customers_to_people():
    sim = shop()
    desk = AIDesk(sim, Quitter())
    sim.inject("payment_webhook_drop")
    b = sim.place_booking("Omar", "B")
    desk.tick(8)
    c = desk.by_order[b["order_ids"][0]]
    assert c.status == "resolved" and not desk.escalated_problems


class Down:
    def create(self, *a):
        raise ConnectionError("no network")


def test_ai_outage_without_backup_hands_tickets_to_people_instead_of_crashing():
    sim = shop()
    desk = AIDesk(sim, Down(), fallback=False)
    sim.inject("payment_webhook_drop")
    b = sim.place_booking("Omar", "B")
    desk.tick(6)
    assert desk.by_order[b["order_ids"][0]].status == "escalated" and desk.ai_errors


def test_ai_outage_switches_to_the_standard_playbook_and_customers_are_still_fixed():
    sim = shop()
    desk = AIDesk(sim, Down())
    sim.inject("payment_webhook_drop")
    b = sim.place_booking("Omar", "B")
    desk.tick(8)
    c = desk.by_order[b["order_ids"][0]]
    assert c.status == "resolved" and desk.ai_errors and desk.backup_problems
    assert any(n["text"].startswith("AI unavailable") for n in c.notes)
    assert all(i["by"] != "AI" for i in desk.incidents.values()), "the backup's work is never credited to the AI"


class Flaky(FakeCommander):
    """Fails once (a rate limit), then works: after the cool-down the AI is used again for new problems."""

    def __init__(self):
        super().__init__()
        self.failed = False

    def create(self, system, tools, messages):
        if not self.failed:
            self.failed = True
            raise RuntimeError("groq HTTP 429: rate limit")
        return super().create(system, tools, messages)


def test_after_the_cool_down_the_ai_takes_new_problems_again():
    sim = shop()
    desk = AIDesk(sim, Flaky())
    sim.inject("payment_webhook_drop")
    sim.place_booking("Omar", "B")
    desk.tick(desk.COOL_DOWN_MIN + 2)
    assert "payment_unrecorded" in desk.backup_problems
    sim.inject("ticket_render_bug")
    sim.place_booking("Reem", "C", lang="ar")
    desk.tick(6)
    assert desk.ai_calls > 0 and any(i["by"] == "AI" for i in desk.incidents.values())


def test_every_call_stays_small_however_long_the_session():
    sent = []

    class Counting(FakeCommander):
        def create(self, system, tools, messages):
            sent.append(len(messages))
            return super().create(system, tools, messages)

    sim = shop()
    desk = AIDesk(sim, Counting())
    for f in ("payment_webhook_drop", "ticket_render_bug", "confirmation_worker_down"):
        sim.inject(f)
    for i in range(6):
        sim.place_booking(f"Fan{i}", "BC"[i % 2], lang="ar")
    desk.tick(10)
    assert sent and max(sent) <= 2 * AIDesk.HISTORY_ROUNDS + 1


class Stubborn(FakeCommander):
    """Re-sends broken tickets without fixing the broken template: the plan cannot work."""

    def cause(self, k, p):
        return "no_platform_fault"


def test_a_plan_that_does_not_work_is_reviewed_then_handed_over():
    sim = shop()
    brain = Stubborn()
    desk = AIDesk(sim, brain, fallback=False)
    sim.inject("ticket_render_bug")
    for i in range(4):
        sim.place_booking(f"fan{i}", "B", 1, "ar")
    desk.tick(40)
    cs = list(desk.cases.values())
    assert cs and all(c.status in ("escalated", "resolved") for c in cs)
    assert any(c.status == "escalated" for c in cs) and desk.ai_sessions >= 2


def test_a_plan_that_keeps_failing_goes_to_the_playbook_not_to_people():
    sim = shop()
    desk = AIDesk(sim, Stubborn())
    sim.inject("ticket_render_bug")
    for i in range(4):
        sim.place_booking(f"fan{i}", "B", 1, "ar")
    for _ in range(40):
        desk.tick(1)
        for c in list(desk.cases.values()):
            if c.proposal:
                desk.approve(c.id)
    assert "ticket_not_delivered" in desk.backup_problems and desk.ai_sessions >= 2
    assert all(c.status == "resolved" for c in desk.cases.values())


def test_waiting_costs_no_model_calls():
    sim = shop()
    sim.sell_out("A", limit=119)
    brain = FakeCommander()
    desk = AIDesk(sim, brain)
    sim.inject("payment_webhook_drop")
    sim.place_booking("Sara", "A", 1)
    sim.sell_out("A")
    desk.tick(8)
    assert any(c.status == "awaiting_approval" for c in desk.cases.values())
    before = brain.calls
    desk.tick(6)
    assert brain.calls == before


def test_matches_the_rule_engine_on_generated_scenarios():
    """With a sensible model, the AI desk must make customers whole as reliably as the rule engine, with no false
    closures.
    """
    import eval_support as E
    from majhar.generate import generate

    whole = aff = false = viol = 0
    for s in generate(25, 5150)[:12]:
        r = E.run(s, "agent", backend=FakeCommander())
        whole, aff, false, viol = (
            whole + r["made_whole"],
            aff + r["affected"],
            false + r["false_closures"],
            viol + r["policy_violations"],
        )
    assert aff > 0 and whole / aff >= 0.95 and false == 0 and viol == 0


def test_tool_schemas_are_valid():
    names = [t["name"] for t in TOOL_SCHEMAS]
    assert len(names) == len(set(names)) and {
        "diagnose",
        "fix_root_cause",
        "set_plan",
        "write_customer_message",
    } <= set(names)
    for t in TOOL_SCHEMAS:
        assert t["input_schema"]["type"] == "object" and set(t["input_schema"]["required"]) <= set(
            t["input_schema"]["properties"]
        )
    assert set(PROBLEMS) == set(PLAN) and all(s in STEPS for v in PLAN.values() for s in v)
