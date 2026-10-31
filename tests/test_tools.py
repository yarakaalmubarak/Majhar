"""Agent tools: access rules, dry runs and approvals."""

import pytest

from majhar.sim import Sim
from majhar.tools import ALL_CAUSES, Toolbox


def incident(faults, seed=1):
    s = Sim(seed=seed)
    for _ in range(5):
        s.tick()
    for f in faults:
        s.inject(f)
    for _ in range(8):
        s.tick()
    return s, Toolbox(s)


def diagnose(tb, cause="seat_assign_timeout"):
    return tb.call("submit_diagnosis", {"hypotheses": [{"cause": cause, "confidence": 0.9, "evidence": "e"}]})


def test_tool_schemas_are_wellformed():
    _, tb = incident([])
    for t in tb.anthropic_tools():
        assert t["name"] and t["description"]
        sch = t["input_schema"]
        assert sch["type"] == "object" and set(sch["required"]) <= set(sch["properties"])
    assert {"get_logs", "get_metrics", "submit_diagnosis", "verify_recovery", "declare_recovered"} <= {
        t["name"] for t in tb.anthropic_tools()
    }


def test_actions_are_locked_until_a_diagnosis_is_submitted_but_reads_are_not():
    s, tb = incident(["seat_assign_timeout"])
    assert "error" in tb.call("restart_seat_assigner")
    assert "seat_assign_timeout" in s.faults
    assert "lines" in tb.call("get_logs", {"min_level": "ERROR"})
    diagnose(tb)
    assert tb.call("restart_seat_assigner")["effective"] is True
    assert "seat_assign_timeout" not in s.faults


def test_risky_tools_dry_run_first_and_change_nothing_until_approved():
    s, tb = incident(["seat_assign_timeout"])
    diagnose(tb)
    tb.call("restart_seat_assigner")
    before, n_actions = s.metrics()["stranded_customers"], len(tb.actions)
    r = tb.call("reassign_seat", {"reason": "assign_timeout"})
    assert r["status"] == "needs_approval" and r["preview"]["would_reassign"] > 0
    assert s.metrics()["stranded_customers"] == before and len(tb.actions) == n_actions
    d = tb.call("reassign_seat", {"reason": "assign_timeout"}, approved=False)
    assert d["status"] == "denied_by_human" and s.metrics()["stranded_customers"] == before
    ok = tb.call("reassign_seat", {"reason": "assign_timeout"}, approved=True)
    assert ok["reassigned"] > 0
    tb.advance(3)
    assert s.metrics()["stranded_customers"] == 0


def test_unneeded_actions_are_flagged():
    s, tb = incident([])
    diagnose(tb, "no_incident")
    tb.call("restart_confirmation_worker")
    tb.call("set_hold_ttl", {"minutes": 10})
    assert [a["unneeded"] for a in tb.actions] == [1, 1]


def test_refund_ignores_customers_who_are_not_stranded():
    s, tb = incident(["seat_assign_timeout"])
    diagnose(tb)
    healthy = next(o.id for o in s.orders.values() if o.status == "CONFIRMED")
    r = tb.call("issue_refund", {"order_ids": [healthy]}, approved=True)
    assert r["refunds_queued"] == 0 and r["not_eligible"] == 1 and tb.actions[-1]["unneeded"] == 1
    assert s.orders[healthy].status == "CONFIRMED"


def test_declare_recovered_is_gated_on_verification():
    s, tb = incident(["confirmation_worker_down"])
    diagnose(tb, "confirmation_worker_down")
    r = tb.call("declare_recovered", {"summary": "done"})
    assert r["accepted"] is False and "confirmation_worker_consuming" in r["failing_checks"] and tb.finished is None
    tb.call("restart_confirmation_worker")
    tb.call("replay_confirmations")
    tb.call("wait", {"minutes": 4})
    assert tb.call("verify_recovery")["healthy"] is True
    assert tb.call("declare_recovered", {"summary": "ok"})["accepted"] is True and tb.finished == "recovered"


def test_diagnosis_validation():
    _, tb = incident([])
    assert "error" in tb.call("submit_diagnosis", {"hypotheses": []})
    assert "error" in tb.call(
        "submit_diagnosis", {"hypotheses": [{"cause": "gremlins", "confidence": 1, "evidence": "x"}]}
    )
    assert "no_incident" in ALL_CAUSES and diagnose(tb, "no_incident")["recorded"]


def test_log_filtering_and_bad_calls_do_not_crash():
    s, tb = incident(["seat_assign_timeout"])
    errs = tb.call("get_logs", {"min_level": "ERROR", "last_n": 500})["lines"]
    assert errs and all(" ERROR " in l for l in errs)
    assert "error" in tb.call("nope")
    assert "error" in tb.call("get_order", {"order_id": 1})
    diagnose(tb)
    assert "error" in tb.call("reassign_seat", {}, approved=True)
    assert "error" in tb.call("set_hold_ttl", {"minutes": 999})


def incident_at(faults, arrivals=6):
    s = Sim(seed=1, arrivals=arrivals)
    for _ in range(5):
        s.tick()
    for f in faults:
        s.inject(f)
    for _ in range(8):
        s.tick()
    return s, Toolbox(s)


@pytest.mark.parametrize(
    "fault,arrivals,failing",
    [
        ("payment_webhook_drop", 6, "payments_reconciled"),
        ("seat_sync_corruption", 6, "no_corrupted_records"),
        ("queue_overflow_drop", 40, "no_dropped_confirmations_3m"),
    ],
)
def test_verify_recovery_catches_each_quiet_failure_that_service_health_checks_miss(fault, arrivals, failing):
    s, tb = incident_at([fault], arrivals)
    v = tb.call("verify_recovery")
    assert not v["healthy"] and failing in v["failing"]
    assert v["checks"]["confirmation_worker_consuming"] and v["checks"]["no_seat_assign_errors_3m"]


def test_repair_seat_records_is_risky_dry_runs_first_and_changes_nothing_until_approved():
    s, tb = incident_at(["seat_sync_corruption"])
    diagnose(tb, "seat_sync_corruption")
    assert tb.kind("repair_seat_records") == "risky"
    r = tb.call("repair_seat_records")
    assert r["status"] == "needs_approval" and r["preview"]["would_repair"] == s.metrics()["sync_mismatches"] > 0
    assert s.metrics()["sync_mismatches"] > 0 and not tb.actions
    assert tb.call("repair_seat_records", approved=False)["status"] == "denied_by_human"
    assert s.metrics()["sync_mismatches"] > 0


def test_quiet_failure_tools_are_locked_until_a_diagnosis_is_submitted():
    s, tb = incident_at(["payment_webhook_drop"])
    for name in ("restart_payment_webhook", "replay_payments", "rollback_seat_sync", "scale_confirmation_queue"):
        assert "submit_diagnosis first" in tb.call(name)["error"]
    assert "payment_webhook_drop" in s.faults


def test_refund_never_touches_a_customer_whose_only_problem_is_a_bad_gate_record():
    s, tb = incident_at(["seat_sync_corruption"])
    diagnose(tb, "seat_sync_corruption")
    r = tb.call("issue_refund", {"reason": "ticket_gate_mismatch"}, approved=True)
    assert r["refunds_queued"] == 0 and r["effective"] is False


def test_payment_replay_then_reassign_covers_everyone_whose_seat_was_resold():
    s, tb = incident_at(["payment_webhook_drop", "hold_ttl_bug"])
    diagnose(tb, "payment_webhook_drop")
    tb.call("restart_payment_webhook")
    assert tb.call("replay_payments")["recorded"] > 0
    tb.call("set_hold_ttl", {"minutes": 10})
    r = tb.call("reassign_seat", {"reason": "seat_taken"}, approved=True)
    assert r.get("reassigned", 0) > 0
    tb.advance(5)
    left = s.metrics()["stranded_by_reason"]
    assert set(left) <= {"seat_taken"}
    tb.call("reassign_seat", {"reason": "seat_taken"}, approved=True)
    tb.advance(5)
    assert s.metrics()["stranded_customers"] == 0 and s.truth_ok()


def test_escalation_hand_off_lists_what_was_seen_tried_and_still_broken():
    s, tb = incident_at(["seat_assign_timeout"])
    diagnose(tb)
    tb.call("restart_seat_assigner")
    tb.call("escalate_to_human", {"reason": "reassign needs a human"})
    h = tb.handoff_report
    assert tb.finished == "escalated" and tb.escalated_by == "agent" and h["reason"] == "reassign needs a human"
    assert h["likely_causes"][0]["cause"] == "seat_assign_timeout"
    assert h["actions_tried"][0]["tool"] == "restart_seat_assigner" and h["customers_still_affected"] > 0
    assert "no_stranded_customers" in h["still_failing_checks"]
    assert not any(c["tool"] == "verify_recovery" for c in tb.calls)
