"""Demo controls: failure menu, refund rules and the system log."""

import json
import threading
import urllib.request

import pytest

import app
from majhar.ai_support import AIDesk
from test_ai_support import FakeCommander


def _run_story(session, failure, minutes=40, then=None):
    """Rush hour with the chosen failure, then let the desk work; a support lead approves after 3 minutes."""
    session.demo("rush", failure)
    if then:
        then()
    asked = {}
    for i in range(minutes):
        session._minute()
        for c in list(session.desk.cases.values()):
            if c.status == "awaiting_approval":
                asked.setdefault(c.id, i)
                if i - asked[c.id] >= 3:
                    session.decide(c.id, True)
    return session.desk


@pytest.mark.parametrize("failure", list(app.FAILURES))
@pytest.mark.parametrize("brain", ["rules", "ai"])
def test_every_failure_in_the_menu_is_found_and_every_customer_is_fixed(failure, brain, monkeypatch):
    monkeypatch.setattr(app, "BACKEND", FakeCommander() if brain == "ai" else None)
    s = app.Session()
    s.running = False
    assert isinstance(s.desk, AIDesk) == (brain == "ai")
    desk = _run_story(s, failure)
    cases = list(desk.cases.values())
    assert cases, "the failure must hurt someone, or the demo shows nothing"
    assert all(c.status == "resolved" for c in cases), sorted({c.status for c in cases})
    assert not s.sim.stranded(), "the hidden truth agrees: nobody is left without seat, ticket or refund"
    assert desk.incidents, "an incident with a root cause is recorded"


def test_the_log_records_the_failure_the_agent_and_the_human(monkeypatch):
    monkeypatch.setattr(app, "BACKEND", FakeCommander())
    s = app.Session()
    s.running = False
    _run_story(s, "payments", then=lambda: (s.buy("Sara", "A", 3, "ar"), s.demo("grab_vip")))
    lv = {e["level"] for e in s.sim.feed}
    srcs = {e["src"] for e in s.sim.feed}
    assert {"TEST", "AI", "HUMAN", "CUSTOMER"} <= lv, lv
    assert "ops-console" in srcs, "the agent's platform fix is in the log"
    assert any("failure injected" in e["text"] for e in s.sim.feed)
    assert not any("failure injected" in line or "approved on" in line for line in s.sim.logs)


def test_changing_the_rules_changes_what_needs_a_person(monkeypatch):
    monkeypatch.setattr(app, "BACKEND", None)
    s = app.Session()
    s.running = False
    s.demo("rules", limit=1000)
    assert s.desk.policy.auto_refund_limit_sar == 1000
    s.demo("rush", "payments")
    b = s.buy("Sara", "A", 2, "en")
    s.demo("grab_vip")
    for _ in range(10):
        s._minute()
    sara = [s.desk.by_order[o] for o in b["order_ids"]]
    assert all(c.outcome == "refunded" and c.human_touches == 0 for c in sara)
    assert s.desk.kpis()["awaiting_approval"] == 0
    assert any(e["level"] == "RULES" for e in s.sim.feed)
    with pytest.raises(ValueError):
        s.demo("rules", limit=12345)
    with pytest.raises(ValueError):
        s.demo("rush", "not-a-failure")


def test_strict_rules_send_every_refund_to_a_person(monkeypatch):
    monkeypatch.setattr(app, "BACKEND", None)
    s = app.Session()
    s.running = False
    s.demo("rules", limit=0)
    s.demo("rush", "payments")
    s.buy("Lama", "C", 1, "en")
    s.demo("grab_vip")
    s.sim.sell_out("C")
    s.sim.sell_out("B")
    for _ in range(8):
        s._minute()
    refunds = [c for c in s.desk.cases.values() if c.proposal and c.proposal.get("tool") == "issue_refund"]
    assert refunds, "with no free seats left, refunds are needed and each one waits for a person"
    assert all(c.status == "awaiting_approval" for c in refunds)


def test_desk_api_exposes_menu_rules_and_log(monkeypatch):
    monkeypatch.setattr(app, "BACKEND", None)
    app.SESSION.reset()
    app.SESSION.running = False
    srv = app.make_server(0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def post(path, body):
        r = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r).read())

    try:
        post("/api/demo", {"action": "rules", "limit": 0})
        post("/api/demo", {"action": "rush", "failure": "arabic"})
        for _ in range(4):
            post("/api/demo", {"action": "minute"})
        d = json.loads(urllib.request.urlopen(base + "/api/desk").read())
        assert d["failure"] == "arabic" and len(d["failures"]) == len(app.FAILURES)
        assert d["policy"]["auto_refund_limit_sar"] == 0 and len(d["rules"]) == 3
        assert d["log"] and any(e["level"] == "TEST" for e in d["log"])
    finally:
        srv.shutdown()
        app.SESSION.reset()


def test_the_last_vip_seats_stay_for_the_story_during_the_rush(monkeypatch):
    """The steady crowd after a rush buys Standard and Economy only, so Sara can still buy the last 3 VIP seats."""
    monkeypatch.setattr(app, "BACKEND", None)
    s = app.Session()
    s.running = False
    for failure in ("payments", "combo"):
        s.reset()
        s.running = False
        s.demo("rush", failure)
        for _ in range(app.BUSY_MINUTES + 1):
            s._minute()
        assert s.sim.inventory()["A"] == 3
        b = s.buy("Sara", "A", 3, "ar")
        assert b["amount_sar"] == 1350
