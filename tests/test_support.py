"""Support desk workflow, refund policy, approvals and the demo server."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from majhar.shop import ShopSim
from majhar.support import Policy, SupportDesk


def shop(**kw):
    sim = ShopSim(seed=7, seats_per_section=40, **kw)
    return sim, SupportDesk(sim)


def test_normal_purchase_needs_no_ticket():
    sim, desk = shop()
    o = sim.place_order("Sara", "B")
    desk.tick(4)
    assert desk.customer_view(o.id)["state"] == "confirmed" and not desk.cases


def test_silent_payment_drop_is_found_fixed_and_closed_without_a_human():
    sim, desk = shop()
    sim.inject("payment_webhook_drop")
    o = sim.place_order("Omar", "B")
    desk.tick(2)
    assert desk.customer_view(o.id)["state"] == "processing"
    desk.tick(5)
    c = desk.by_order[o.id]
    assert c.source == "auto_detected" and c.status == "resolved" and c.outcome == "restored"
    assert c.human_touches == 0 and c.cause == "payment_webhook_drop"
    assert desk.customer_view(o.id)["state"] == "confirmed" and sim.truth_ok()
    assert "payment_webhook_drop" not in sim.faults


def test_one_root_cause_many_tickets_one_fix():
    sim, desk = shop()
    sim.inject("payment_webhook_drop")
    orders = [sim.place_order(f"c{i}", "C") for i in range(6)]
    desk.tick(8)
    assert all(desk.by_order[o.id].outcome == "restored" for o in orders)
    assert sum(a["tool"] == "restart_payment_webhook" for a in desk.tb.actions) == 1


def test_big_refund_waits_for_the_button_then_completes():
    sim, desk = shop()
    sim.sell_out("A", limit=39)
    sim.inject("payment_webhook_drop")
    o = sim.place_order("Sara", "A", "ar")
    desk.tick(1)
    sim.sell_out("A")
    desk.tick(5)
    c = desk.by_order[o.id]
    assert c.status == "awaiting_approval" and c.proposal["tool"] == "issue_refund" and c.proposal["amount_sar"] == 450
    assert sim.orders[o.id].status != "REFUND_REQUESTED"
    desk.approve(c.id)
    desk.tick(3)
    assert c.status == "resolved" and c.outcome == "refunded" and c.human_touches == 1 and sim.truth_ok()
    msgs = desk.customer_view(o.id)["messages"]
    assert any("450" in m["text_ar"] for m in msgs if m.get("text_ar"))


def test_small_refund_is_automatic():
    sim, desk = shop()
    sim.sell_out("C", limit=39)
    sim.inject("payment_webhook_drop")
    o = sim.place_order("Lama", "C")
    desk.tick(1)
    sim.sell_out("C")
    desk.tick(6)
    c = desk.by_order[o.id]
    assert c.outcome == "refunded" and c.human_touches == 0


def test_policy_limit_is_respected_and_configurable():
    p = Policy(auto_refund_limit_sar=500)
    assert p.decide("issue_refund", {"total_sar": 450})[0] == "auto"
    assert Policy().decide("issue_refund", {"total_sar": 450})[0] == "approval"
    assert Policy().decide("reassign_seat", {})[0] == "auto"
    assert Policy().decide("rollback_ticket_template", {})[0] == "approval"


def test_declined_refund_goes_to_a_human_not_silence():
    sim, desk = shop()
    sim.sell_out("A", limit=39)
    sim.inject("payment_webhook_drop")
    o = sim.place_order("Sara", "A")
    desk.tick(1)
    sim.sell_out("A")
    desk.tick(5)
    c = desk.by_order[o.id]
    desk.deny(c.id)
    assert c.status == "escalated" and c.human_touches == 1
    assert desk.customer_view(o.id)["state"] == "problem"


def test_customer_complaint_before_any_charge():
    sim, desk = shop()
    o = sim.place_order("Faisal", "B")
    c = desk.report(o.id, "nothing happened")
    desk.work()
    assert c.source == "customer_report" and c.outcome == "nothing_charged"


def test_customer_complaint_speeds_up_the_fix():
    sim, desk = shop()
    sim.inject("payment_webhook_drop")
    o = sim.place_order("Reem", "B")
    desk.tick(1)
    desk.report(o.id, "I paid and got nothing")
    desk.work()
    assert desk.by_order[o.id].cause == "payment_webhook_drop" and "payment_webhook_drop" not in sim.faults


def test_seat_moved_to_equal_seat_when_original_is_gone():
    sim, desk = shop()
    sim.inject("payment_webhook_drop")
    o = sim.place_order("Hessa", "B")
    desk.tick(1)
    taken = sim.seats[o.seat]
    taken.status, taken.holder, taken.sold_to = "sold", None, [99999]
    desk.tick(6)
    c = desk.by_order[o.id]
    assert c.outcome == "reassigned" and sim.orders[o.id].section == "B" and c.human_touches == 0


def test_demo_server_end_to_end():
    import app

    srv = app.make_server(0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def post(path, body):
        r = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r).read())

    def get(path):
        return json.loads(urllib.request.urlopen(base + path).read())

    try:
        app.SESSION.reset()
        app.SESSION.running = False
        for page in ("/", "/shop", "/desk"):
            assert urllib.request.urlopen(base + page).status == 200
        post("/api/demo", {"action": "rush"})
        b = post("/api/buy", {"customer": "Sara", "section": "A", "qty": 3, "lang": "ar"})
        assert b["amount_sar"] == 1350 and len(b["order_ids"]) == 3
        s = get("/api/shop?customer=Sara")
        assert s["bookings"][0]["booking"] == b["booking"] and s["bookings"][0]["state"] == "processing"
        post("/api/demo", {"action": "grab_vip"})
        for _ in range(6):
            post("/api/demo", {"action": "minute"})
        d = get("/api/desk")
        top = d["groups"][0]
        assert top["status"] == "awaiting_approval" and top["tickets"] == 3 and top["amount"] == 1350
        assert d["kpis"]["customers"] > 5 and d["incidents"] and d["counts"]["awaiting_approval"] == 1
        det = get("/api/case?id=" + top["case"])
        assert det["proposal"]["amount_sar"] == 1350 and len(det["tickets"]) == 3
        post("/api/approve", {"case_id": top["case"]})
        for _ in range(3):
            post("/api/demo", {"action": "minute"})
        s = get("/api/shop?customer=Sara")
        assert s["bookings"][0]["state"] == "refunded" and s["bookings"][0]["messages"]
        with pytest.raises(urllib.error.HTTPError) as e:
            post("/api/buy", {"customer": "X", "section": "Z"})
        assert e.value.code == 400
    finally:
        srv.shutdown()
        app.SESSION.reset()


def _crowd(faults, minutes=30, approve=True, arrivals=12):
    from majhar.sim import Sim

    sim = Sim(seed=3, arrivals=arrivals, seats_per_section=200)
    for _ in range(5):
        sim.tick()
    for f in faults:
        sim.inject(f)
    desk = SupportDesk(sim)
    for _ in range(minutes):
        desk.tick(1)
        for c in desk.cases.values():
            if c.proposal and sim.t - c.proposal["t"] >= 2:
                (desk.approve if approve else desk.deny)(c.id)
    return sim, desk


def test_platform_change_is_proposed_once_and_serves_every_ticket():
    sim, desk = _crowd(["ticket_render_bug"])
    props = [n for c in desk.cases.values() for n in c.notes if n["who"] == "human"]
    assert len(props) == 1 and desk.platform["rollback_ticket_template"] == "approved"
    assert all(c.status == "resolved" for c in desk.cases.values()) and sim.truth_ok()


def test_declined_platform_change_hands_affected_tickets_to_a_human():
    sim, desk = _crowd(["ticket_render_bug"], approve=False)
    assert desk.platform["rollback_ticket_template"] == "denied"
    stuck = [c for c in desk.cases.values() if c.reason == "ticket_not_delivered"]
    assert stuck and all(c.status in ("escalated", "resolved") for c in stuck)
    assert not [c for c in desk.cases.values() if c.status not in ("resolved", "escalated", "awaiting_approval")]


def test_two_platform_changes_on_the_same_incident_do_not_overwrite_each_other():
    sim, desk = _crowd(["ticket_render_bug", "seat_sync_corruption", "confirmation_worker_down"], minutes=40)
    assert desk.platform == {"rollback_ticket_template": "approved", "repair_seat_records": "approved"}
    assert not desk.open_cases() and sim.truth_ok()


def test_a_fix_that_keeps_failing_is_handed_over_not_retried_forever():
    sim, desk = shop()
    o = sim.place_order("Turki", "B")
    c = desk._open(o.id, "auto_detected", "no_ticket_sent")
    desk.tb.call(
        "submit_diagnosis",
        {"hypotheses": [{"cause": "confirmation_worker_down", "confidence": 0.5, "evidence": "test"}]},
    )
    for _ in range(3):
        desk._act(c, "replay_confirmations", {}, "Re-sent the booking confirmation")
    assert c.status == "escalated" and any("3 times" in n["text"] for n in c.notes)


def test_agent_never_reads_hidden_fault_flags():
    import inspect
    from majhar import support

    src = inspect.getsource(support)
    assert "sim.faults" not in src and "_corrupted(" not in src and "truth_ok" not in src


def test_customer_hurt_again_after_closure_gets_the_ticket_reopened():
    sim, desk = shop()
    o = sim.place_order("Dana", "B")
    c = desk._open(o.id, "auto_detected", "no_ticket_sent")
    desk.tick(4)
    assert c.status == "resolved"
    sim.orders[o.id].gate_ok, sim.orders[o.id].corrupted_at = False, sim.t
    desk.tick(5)
    assert any("Reopened" in n["text"] for n in c.notes) and any(n.get("key") == "reopened" for n in c.notes)
    off = SupportDesk(sim, reopen=False)
    assert off.reopen is False
