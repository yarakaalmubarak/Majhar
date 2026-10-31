"""Model connections, tested against local servers that answer like the real providers."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from majhar.ai_support import TOOL_SCHEMAS, AIDesk
from majhar.llm_backends import PRESETS, OpenAICompatBackend, make_backend
from majhar.shop import ShopSim
from test_ai_support import FakeClaude


def back_to_blocks(msgs):
    """What the provider received, turned back into the agent's own message shape (to drive the scripted brain)."""
    system, out = msgs[0]["content"], []
    for m in msgs[1:]:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": json.loads(tc["function"]["arguments"]),
                }
                for tc in m.get("tool_calls", [])
            ]
            out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            r = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(r)
            else:
                out.append({"role": "user", "content": [r]})
    return system, out


def serve(handler_brain, fail_first=0):
    state = {"n": 0, "fail": fail_first, "seen": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["seen"].append((self.path, self.headers.get("Authorization"), body))
            if state["fail"] > 0:
                state["fail"] -= 1
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                return
            system, msgs = back_to_blocks(body["messages"])
            tools = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": {"required": [], **t["function"]["parameters"]},
                }
                for t in body["tools"]
            ]
            resp = handler_brain.create(system, tools, msgs)
            msg = {
                "role": "assistant",
                "content": " ".join(b["text"] for b in resp["content"] if b["type"] == "text") or None,
            }
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in resp["content"]
                if b["type"] == "tool_use"
            ]
            if calls:
                msg["tool_calls"] = calls
            data = json.dumps({"choices": [{"message": msg}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


class LooseBrain(FakeClaude):
    """The fake server rebuilds the tool list from the request, so hand the scripted brain the agent's own schemas."""

    def create(self, system, tools, messages):
        return super().create(system, TOOL_SCHEMAS, messages)


def test_conversion_round_trip():
    msgs, fns = OpenAICompatBackend.to_openai(
        "sys",
        TOOL_SCHEMAS,
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "ok"},
                    {"type": "tool_use", "id": "a1", "name": "read_order", "input": {}},
                ],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a1", "content": '{"x": 1}'}]},
        ],
    )
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "read_order" and msgs[3]["tool_call_id"] == "a1"
    assert all("required" not in f["function"]["parameters"] for f in fns if f["function"]["name"] == "read_order")
    out = OpenAICompatBackend.from_openai(
        {
            "choices": [
                {
                    "message": {
                        "content": "thinking",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "refund", "arguments": '{"reason": "no seat"}'}}
                        ],
                    }
                }
            ]
        }
    )
    assert out["content"][1] == {"type": "tool_use", "id": "c1", "name": "refund", "input": {"reason": "no seat"}}
    bad = OpenAICompatBackend.from_openai(
        {"choices": [{"message": {"tool_calls": [{"function": {"name": "wait", "arguments": "not json"}}]}}]}
    )
    assert bad["content"][0]["input"] == {} and bad["content"][0]["id"]


def test_ai_agent_runs_through_an_openai_compatible_provider_with_rate_limits():
    srv, state = serve(LooseBrain(), fail_first=2)
    try:
        b = OpenAICompatBackend("ollama", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", retries=3)
        sim = ShopSim(seed=7, seats_per_section=40)
        desk = AIDesk(sim, b)
        sim.inject("payment_webhook_drop")
        o = sim.place_order("Sara", "B", "ar")
        desk.tick(10)
        c = desk.by_order[o.id]
        assert c.status == "resolved" and sim.truth_ok() and not desk.ai_errors
        path, auth, body = state["seen"][-1]
        assert path == "/v1/chat/completions" and body["model"] == "qwen2.5:7b" and auth is None
    finally:
        srv.shutdown()


def test_keys_are_sent_and_missing_keys_are_explained(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        OpenAICompatBackend("gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k123")
    b = OpenAICompatBackend("gemini")
    assert (
        b.key == "k123"
        and b.base.startswith("https://generativelanguage.googleapis.com")
        and b.model == PRESETS["gemini"][2]
    )
    monkeypatch.setenv("MAJHAR_MODEL", "gemini-x")
    assert OpenAICompatBackend("gemini").model == "gemini-x"


def test_auto_choice_and_clear_error_when_nothing_is_available(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "MAJHAR_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("majhar.llm_backends._ollama_up", lambda *a: False)
    with pytest.raises(RuntimeError, match="aistudio"):
        make_backend()
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert make_backend().provider == "groq"


def test_provider_down_means_the_standard_playbook_takes_over():
    b = OpenAICompatBackend("ollama", base_url="http://127.0.0.1:9/v1", retries=0, timeout=2)
    sim = ShopSim(seed=7, seats_per_section=40)
    desk = AIDesk(sim, b)
    sim.inject("payment_webhook_drop")
    o = sim.place_order("Omar", "B")
    desk.tick(8)
    assert desk.by_order[o.id].status == "resolved" and "cannot reach" in desk.ai_errors[-1] and desk.backup_problems
    desk2 = AIDesk(ShopSim(seed=7, seats_per_section=40), b, fallback=False)
    desk2.sim.inject("payment_webhook_drop")
    o2 = desk2.sim.place_order("Omar", "B")
    desk2.tick(6)
    assert desk2.by_order[o2.id].status == "escalated"


def _limit_server(code_body):
    """A provider that answers every call with a fixed status and body (to test rate-limit handling)."""
    state = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            state["n"] += 1
            code, body = code_body(state["n"])
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def test_daily_limit_fails_at_once_with_a_clear_way_forward():
    from majhar.llm_backends import DailyLimitReached

    srv, state = _limit_server(
        lambda n: (
            429,
            {
                "error": {
                    "message": "Rate limit reached for model `x` "
                    "on tokens per day (TPD): Limit 200000, Used 199500. Please try again in 7m12s."
                }
            },
        )
    )
    try:
        b = OpenAICompatBackend("ollama", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", model="x")
        with pytest.raises(DailyLimitReached, match="MAJHAR_MODEL"):
            b.create("s", TOOL_SCHEMAS, [{"role": "user", "content": "hi"}])
        assert state["n"] == 1, "no point waiting for a daily limit"
    finally:
        srv.shutdown()


def test_groq_daily_limit_switches_to_the_next_free_model():
    from majhar.llm_backends import DailyLimitReached

    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body["model"])
            if body["model"] != "openai/gpt-oss-20b":
                code, out = 429, {
                    "error": {"message": f"Rate limit reached for model `{body['model']}` on tokens per day (TPD)"}
                }
            else:
                code, out = 200, {
                    "choices": [{"message": {"role": "assistant", "content": "OK"}}],
                    "usage": {"total_tokens": 10},
                }
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b = OpenAICompatBackend("groq", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", api_key="k")
        assert b.model == "openai/gpt-oss-120b"
        r = b.create("s", TOOL_SCHEMAS, [{"role": "user", "content": "hi"}])
        assert r["content"][0]["text"] == "OK" and b.model == "openai/gpt-oss-20b" and b.model_changes
        assert seen == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
        b.exhausted.append("openai/gpt-oss-20b")
        b.exhausted.append("qwen/qwen3.8-27b")
        b.model = "qwen/qwen3.8-27b"
        with pytest.raises(DailyLimitReached):
            b.create("s", TOOL_SCHEMAS, [{"role": "user", "content": "hi"}])
    finally:
        srv.shutdown()


def test_per_minute_limit_waits_then_counts_tokens(monkeypatch):
    slept = []
    monkeypatch.setattr("majhar.llm_backends.time.sleep", lambda s: slept.append(s))
    ok = {"choices": [{"message": {"role": "assistant", "content": "fine"}}], "usage": {"total_tokens": 1234}}
    srv, state = _limit_server(
        lambda n: (
            (429, {"error": {"message": "tokens per minute (TPM): Please try again in 2.5s."}}) if n == 1 else (200, ok)
        )
    )
    try:
        b = OpenAICompatBackend(
            "groq", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", api_key="k", model="openai/gpt-oss-120b"
        )
        r = b.create("s", TOOL_SCHEMAS, [{"role": "user", "content": "hi"}])
        assert r["content"][0]["text"] == "fine" and b.tokens_used == 1234 and b.calls == 1
        assert slept and 2.5 <= slept[0] <= 3.5
    finally:
        srv.shutdown()


def test_wait_time_parsing():
    from majhar.llm_backends import _wait_seconds

    assert _wait_seconds(None, "Please try again in 1m2.5s.", 1) == 62.5
    assert _wait_seconds(None, "Please try again in 7.3s", 1) == 7.3
    assert _wait_seconds({"Retry-After": "4"}, "", 1) == 4.0
    assert _wait_seconds(None, "busy", 9) == 9


from majhar.llm_backends import GeminiBackend


def gemini_back_to_blocks(body):
    msgs, ids = [], {}
    for i, c in enumerate(body["contents"]):
        if c["role"] == "model":
            blocks = []
            for j, p in enumerate(c["parts"]):
                if "functionCall" in p:
                    bid = f"x{i}_{j}"
                    ids.setdefault(i, []).append(bid)
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": bid,
                            "name": p["functionCall"]["name"],
                            "input": p["functionCall"].get("args", {}),
                        }
                    )
                elif "text" in p:
                    blocks.append({"type": "text", "text": p["text"]})
            msgs.append({"role": "assistant", "content": blocks})
        else:
            fr = [p for p in c["parts"] if "functionResponse" in p]
            if fr:
                prev = ids.get(i - 1, [])
                msgs.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": prev[k],
                                "content": json.dumps(p["functionResponse"]["response"]),
                            }
                            for k, p in enumerate(fr)
                        ],
                    }
                )
            else:
                msgs.append({"role": "user", "content": c["parts"][0]["text"]})
    return body["systemInstruction"]["parts"][0]["text"], msgs


def serve_gemini(brain, fail_first=0, missing_model=None, suggest=None):
    state = {"fail": fail_first, "seen": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["seen"].append(
                (self.path, self.headers.get("x-goog-api-key"), self.headers.get("Authorization"), body)
            )
            if missing_model and missing_model in self.path:
                self.send_response(404)
                self.end_headers()
                msg = (
                    f"This model is no longer available. Please update your code to use models/{suggest} instead."
                    if suggest
                    else "model not found"
                )
                self.wfile.write(json.dumps({"error": {"code": 404, "message": msg}}).encode())
                return
            if state["fail"] > 0:
                state["fail"] -= 1
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                return
            system, msgs = gemini_back_to_blocks(body)
            resp = brain.create(system, TOOL_SCHEMAS, msgs)
            parts = [{"text": b["text"]} for b in resp["content"] if b["type"] == "text"] + [
                {"functionCall": {"name": b["name"], "args": b["input"]}, "thoughtSignature": "sig123"}
                for b in resp["content"]
                if b["type"] == "tool_use"
            ]
            data = json.dumps({"candidates": [{"content": {"role": "model", "parts": parts}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def test_gemini_native_with_new_AQ_style_key_rate_limits_and_retired_model(monkeypatch):
    monkeypatch.delenv("MAJHAR_MODEL", raising=False)
    srv, state = serve_gemini(LooseBrain(), fail_first=1, missing_model="gemini-3.8-flash")
    try:
        b = GeminiBackend(
            api_key=' "AQ.test-key" ', base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1beta", retries=3
        )
        sim = ShopSim(seed=7, seats_per_section=40)
        desk = AIDesk(sim, b)
        sim.inject("payment_webhook_drop")
        o = sim.place_order("Sara", "B", "ar")
        desk.tick(10)
        assert desk.by_order[o.id].status == "resolved" and sim.truth_ok() and not desk.ai_errors
        path, key, auth, body = state["seen"][-1]
        assert key == "AQ.test-key" and auth is None
        assert path.endswith("/models/gemini-flash-latest:generateContent") and b.model == "gemini-flash-latest"
        decl = {d["name"]: d for d in body["tools"][0]["functionDeclarations"]}
        assert "parameters" not in decl["read_platform"] and decl["set_plan"]["parameters"]["required"] == [
            "problem",
            "steps",
            "reason",
        ]
        sent_back = [p for c in body["contents"] if c["role"] == "model" for p in c["parts"] if "functionCall" in p]
        assert sent_back and all(p.get("thoughtSignature") == "sig123" for p in sent_back)
    finally:
        srv.shutdown()


def test_gemini_errors_are_explained(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiBackend()
    out = GeminiBackend.from_gemini({"promptFeedback": {"blockReason": "SAFETY"}})
    assert "SAFETY" in out["content"][0]["text"]


def test_gemini_follows_googles_suggested_replacement_model(monkeypatch):
    monkeypatch.delenv("MAJHAR_MODEL", raising=False)
    srv, state = serve_gemini(LooseBrain(), missing_model="old-model", suggest="gemini-9-flash")
    try:
        b = GeminiBackend(
            model="old-model", api_key="AQ.k", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1beta"
        )
        from majhar.ai_support import SYSTEM

        out = b.create(SYSTEM, TOOL_SCHEMAS, [{"role": "user", "content": "hi"}])
        assert b.model == "gemini-9-flash" and out["content"]
    finally:
        srv.shutdown()


def test_unknown_model_is_replaced_by_one_the_provider_actually_has():
    brain = LooseBrain()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = json.dumps(
                {"data": [{"id": "whisper-large-v3"}, {"id": "llama-guard-4"}, {"id": "llama-3.3-70b-versatile"}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if body["model"] != "llama-3.3-70b-versatile":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error": {"message": "The model `old` does not exist"}}')
                return
            data = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b = OpenAICompatBackend(
            "groq",
            model="old-retired-model",
            base_url=f"http://127.0.0.1:{srv.server_address[1]}/openai/v1",
            api_key="gsk_x",
        )
        out = b.create("s", TOOL_SCHEMAS, [{"role": "user", "content": "hi"}])
        assert b.model == "llama-3.3-70b-versatile" and out["content"][0]["text"] == "OK"
    finally:
        srv.shutdown()


def test_requests_do_not_use_the_default_python_signature():
    srv, state = serve(LooseBrain())
    try:
        b = OpenAICompatBackend("groq", base_url=f"http://127.0.0.1:{srv.server_address[1]}/openai/v1", api_key="gsk_x")
        captured = {}
        import urllib.request as ur

        orig = ur.urlopen

        def spy(req, timeout=None):
            captured["ua"] = req.get_header("User-agent")
            return orig(req, timeout=timeout)

        ur.urlopen = spy
        try:
            from majhar.ai_support import SYSTEM

            b.create(SYSTEM, TOOL_SCHEMAS, [{"role": "user", "content": "hi"}])
        finally:
            ur.urlopen = orig
        assert captured["ua"].startswith("Majhar/") and "urllib" not in captured["ua"]
    finally:
        srv.shutdown()


def test_pasted_keys_are_cleaned():
    from majhar.llm_backends import clean_key

    key = "gsk_" + "a1B2" * 13
    for pasted in (
        key,
        f'"{key}"',
        f"\u201c{key}\u201d",
        f"export GROQ_API_KEY=\u201c{key}\u201d",
        f"exportGROQ_API_KEY=\u201c{key}\u201d",
        f"  {key}\n",
        f"export GROQ_API_KEY='{key}'",
    ):
        assert clean_key(pasted) == key, pasted
    assert clean_key("AQ.Ab8RN6_x-y") == "AQ.Ab8RN6_x-y"
    assert clean_key("\u201cplainkey\u201d") == "plainkey"
    with pytest.raises(RuntimeError, match="curly quotes"):
        clean_key("ke\u00e9y")
    b = OpenAICompatBackend("groq", api_key=f"export GROQ_API_KEY=\u201c{key}\u201d")
    assert b.key == key


def test_a_rejected_tool_call_is_retried_with_loose_schemas_and_the_agent_carries_on():
    """Groq rejects a whole answer when the model fills a tool call against the schema (HTTP 400 'tool call validation
    failed').
    """
    from test_ai_support import FakeCommander
    from majhar.ai_support import TOOL_SCHEMAS as TS

    brain, seen = FakeCommander(), []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            strict = "enum" in json.dumps(body["tools"])
            seen.append(strict)
            if strict:
                code, out = 400, {
                    "error": {
                        "message": "Tool call validation failed: parameters for tool set_plan did not "
                        "match schema: errors: [missing properties: 'steps']",
                        "code": "tool_use_failed",
                    }
                }
            else:
                assert all("required" not in t["function"]["parameters"] for t in body["tools"])
                system, msgs = back_to_blocks(body["messages"])
                resp = brain.create(system, TS, msgs)
                msg = {
                    "role": "assistant",
                    "content": " ".join(b["text"] for b in resp["content"] if b["type"] == "text") or None,
                }
                calls = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                    }
                    for b in resp["content"]
                    if b["type"] == "tool_use"
                ]
                if calls:
                    msg["tool_calls"] = calls
                code, out = 200, {"choices": [{"message": msg}]}
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b = OpenAICompatBackend("groq", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", api_key="k")
        sim = ShopSim(seed=7, seats_per_section=40)
        desk = AIDesk(sim, b)
        sim.inject("payment_webhook_drop")
        o = sim.place_order("Omar", "B")
        desk.tick(6)
        assert desk.by_order[o.id].status == "resolved" and not desk.backup_problems and not desk.ai_errors
        assert seen[0] is True and b.loose_tools and seen[-1] is False
    finally:
        srv.shutdown()


def test_loosen_moves_allowed_values_into_descriptions():
    from majhar.llm_backends import loosen

    _, fns = OpenAICompatBackend.to_openai("s", TOOL_SCHEMAS, [])
    loose = loosen(fns)
    sp = next(f for f in loose if f["function"]["name"] == "set_plan")["function"]["parameters"]
    assert "enum" not in json.dumps(loose) and "required" not in sp
    assert "record_missing_payment" in sp["properties"]["steps"]["description"]
    assert "enum" in json.dumps(fns), "the original schemas are not changed"
