"""Simulated platform: faults, symptoms and fixes."""

import pytest

from majhar.sim import Sim


def run(faults=(), arrivals=6, seed=1, seats=200, warm=5, impact=8):
    s = Sim(seed=seed, arrivals=arrivals, seats_per_section=seats)
    for _ in range(warm):
        s.tick()
    for f in faults:
        s.inject(f)
    for _ in range(impact):
        s.tick()
    return s


@pytest.mark.parametrize("arrivals", [6, 60])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_healthy_platform_has_no_stranded_customers_and_no_warnings(arrivals, seed):
    s = run(arrivals=arrivals, seed=seed, impact=25)
    m = s.metrics()
    assert m["stranded_customers"] == 0 and s.truth_ok()
    assert m["queue_depth"] == 0 and m["seats_oversold"] == 0
    assert not [l for l in s.logs if " WARN " in l or " ERROR " in l]


def test_simulation_is_deterministic_for_a_seed():
    assert run(["seat_assign_timeout"], seed=4).metrics() == run(["seat_assign_timeout"], seed=4).metrics()


def test_confirmation_worker_down_grows_queue_and_strands_customers():
    m = run(["confirmation_worker_down"]).metrics()
    assert m["confirm_consumers"] == 0 and m["queue_depth"] > 20
    assert m["stranded_by_reason"].get("no_ticket_sent", 0) > 0


def test_seat_assign_timeout_strands_paid_customers_without_a_seat():
    m = run(["seat_assign_timeout"]).metrics()
    assert m["stranded_by_reason"].get("assign_timeout", 0) > 5 and m["assign_errors_3m"] > 0


def test_hold_ttl_bug_releases_holds_and_strands_some_customers():
    s = run(["hold_ttl_bug"], seed=2)
    m = s.metrics()
    assert m["hold_ttl_min"] < 5 and m["holds_expired_3m"] > 0
    assert m["stranded_by_reason"].get("seat_taken", 0) > 0 and "hold_ttl_bug" in s.effects


def test_duplicate_sale_oversells_seats_when_holds_expire():
    oversold = [run(["hold_ttl_bug", "duplicate_sale"], seed=sd).metrics()["seats_oversold"] for sd in (1, 2, 3)]
    assert max(oversold) > 0


def test_duplicate_sale_alone_is_latent_and_harmless():
    s = run(["duplicate_sale"])
    assert s.metrics()["seats_oversold"] == 0 and "duplicate_sale" not in s.effects and s.truth_ok()


def test_ticket_render_bug_confirms_but_never_delivers_arabic_tickets():
    m = run(["ticket_render_bug"]).metrics()
    assert m["ticket_render_errors_3m"] > 0
    assert m["stranded_by_reason"].get("ticket_not_delivered", 0) > 0


def test_fixes_clear_faults_and_stop_new_damage():
    s = run(["seat_assign_timeout"])
    assert s.fix_seat_assigner()["effective"]
    before = s.metrics()["assign_errors_3m"]
    for _ in range(4):
        s.tick()
    assert s.metrics()["assign_errors_3m"] == 0 < before


def test_restarting_a_worker_expires_old_messages_so_replay_is_needed():
    s = run(["confirmation_worker_down"])
    r = s.fix_confirmation_worker()
    assert r["expired_messages"] > 0
    for _ in range(2):
        s.tick()
    assert s.metrics()["stranded_by_reason"].get("no_ticket_sent", 0) > 0
    assert s.replay_confirmations()["replayed"] > 0
    s.tick()
    s.tick()
    assert s.metrics()["stranded_customers"] == 0


def test_refunds_settle_when_worker_healthy_and_stick_when_failing():
    s = run(["seat_assign_timeout"])
    s.fix_seat_assigner()
    ids = [o.id for o, _ in s.stranded()][:3]
    for i in ids:
        o = s.orders[i]
        o.status, o.refund_at = "REFUND_REQUESTED", s.t
    s.tick()
    s.tick()
    assert all(s.orders[i].status == "REFUNDED" for i in ids)
    s2 = run(["seat_assign_timeout", "refund_worker_failing"])
    for o, _ in s2.stranded()[:3]:
        o.status, o.refund_at = "REFUND_REQUESTED", s2.t
    for _ in range(7):
        s2.tick()
    assert s2.metrics()["refunds_pending"] == 3
    assert s2.metrics()["stranded_by_reason"].get("refund_stuck") == 3
    assert any("settlement callback missing" in l for l in s2.logs)


def test_latent_fault_does_not_count_against_truth():
    s = run(["refund_worker_failing"])
    assert s.truth_ok()


def _loud(s):
    return [l for l in s.logs if " WARN " in l or " ERROR " in l]


def test_payment_webhook_drop_is_silent_and_only_visible_as_disagreeing_counters():
    s = run(["payment_webhook_drop"])
    m = s.metrics()
    assert _loud(s) == []
    assert m["payments_unreconciled"] > 5 and m["gateway_captured_3m"] > m["paid_3m"]
    assert m["stranded_by_reason"].get("payment_unrecorded", 0) > 0
    assert not s.truth_ok() and "payment_webhook_drop" in s.effects


def test_payment_webhook_fix_stops_new_drops_and_replay_books_the_old_ones():
    s = run(["payment_webhook_drop"])
    assert s.fix_payment_webhook()["effective"]
    for _ in range(3):
        s.tick()
    left = s.metrics()["payments_unreconciled"]
    assert left > 0
    r = s.replay_payments()
    assert r["recorded"] == left
    for _ in range(6):
        s.tick()
    m = s.metrics()
    assert m["payments_unreconciled"] == 0 and m["stranded_by_reason"].get("payment_unrecorded", 0) == 0
    assert set(m["stranded_by_reason"]) <= {"seat_taken", "no_ticket_sent"}


def test_replay_without_the_fix_leaves_the_fault_active_and_new_drops_appear():
    s = run(["payment_webhook_drop"])
    s.replay_payments()
    for _ in range(3):
        s.tick()
    assert s.metrics()["payments_unreconciled"] > 0 and not s.truth_ok()


def test_seat_sync_corruption_is_slow_quiet_and_keeps_growing_until_rolled_back():
    s = run(["seat_sync_corruption"], impact=4)
    early = s.metrics()["sync_mismatches"]
    for _ in range(6):
        s.tick()
    later = s.metrics()["sync_mismatches"]
    assert 0 < early < later <= early + 6 * 4
    loud = _loud(s)
    assert loud and all("seat-sync: replica checksum drift" in l and " WARN " in l for l in loud)
    assert s.metrics()["stranded_by_reason"].get("ticket_gate_mismatch", 0) > 0


def test_seat_sync_needs_the_job_stopped_and_then_the_rows_repaired():
    s = run(["seat_sync_corruption"])
    s.repair_records()
    for _ in range(4):
        s.tick()
    assert s.metrics()["sync_mismatches"] > 0
    assert s.fix_seat_sync()["effective"]
    for _ in range(3):
        s.tick()
    frozen = s.metrics()["sync_mismatches"]
    for _ in range(3):
        s.tick()
    assert s.metrics()["sync_mismatches"] == frozen > 0
    assert s.repair_records()["repaired"] == frozen
    for _ in range(4):
        s.tick()
    assert s.truth_ok() and s.metrics()["sync_mismatches"] == 0


def test_queue_overflow_drop_is_latent_at_normal_load():
    s = run(["queue_overflow_drop"], arrivals=6, impact=20)
    assert s.truth_ok() and "queue_overflow_drop" not in s.effects and s.metrics()["queue_dropped_3m"] == 0


def test_queue_overflow_drop_loses_work_in_a_spike_without_a_single_error_line():
    s = run(["queue_overflow_drop"], arrivals=40)
    m = s.metrics()
    assert m["queue_dropped_3m"] > 0 and m["stranded_by_reason"].get("no_ticket_sent", 0) > 20
    assert m["confirm_consumers"] == 1 and m["queue_depth"] <= 50
    assert _loud(s) == []
    assert not s.truth_ok() and "queue_overflow_drop" in s.effects


def test_queue_overflow_needs_the_limit_removed_and_the_lost_confirmations_replayed():
    s = run(["queue_overflow_drop"], arrivals=40)
    assert s.fix_queue_limit()["effective"]
    for _ in range(4):
        s.tick()
    assert s.metrics()["stranded_by_reason"].get("no_ticket_sent", 0) > 0
    assert s.replay_confirmations()["replayed"] > 0
    for _ in range(5):
        s.tick()
    assert s.metrics()["stranded_customers"] == 0 and s.truth_ok()
