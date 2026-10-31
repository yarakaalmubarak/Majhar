"""Connections to language model providers (Groq, Gemini, Ollama, OpenAI, Anthropic)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

PRESETS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-3.8-flash"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "ollama": ("http://localhost:11434/v1", None, "qwen2.5:7b"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
}
ORDER = ["anthropic", "groq", "gemini", "openai", "ollama"]
DAILY_FALLBACKS = {"groq": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]}
UA = {"User-Agent": "Majhar/1.0 (+https://github.com) python", "Accept": "application/json"}


QUOTES = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb`"


def clean_key(key: str | None) -> str | None:
    """A key exactly as the provider issued it, even if it was pasted with extras: a whole `export NAME="..."` line,
    straight or curly quotes (Notes and Word turn " into “ ”), spaces or line breaks.
    """
    if key is None:
        return None
    k = key.strip()
    m = re.search(r"(gsk_[A-Za-z0-9]+|AIza[0-9A-Za-z_\-]+|AQ\.[0-9A-Za-z_\-\.]+|sk-[0-9A-Za-z_\-]+)", k)
    if m:
        return m.group(1)
    if "=" in k and k.split("=", 1)[0].strip().lower().startswith("export"):
        k = k.split("=", 1)[1]
    k = "".join(ch for ch in k if ch not in QUOTES and not ch.isspace())
    if any(ord(ch) > 127 for ch in k):
        raise RuntimeError(
            "the API key contains characters that are not part of a key (for example curly quotes). "
            "Copy the key again from the provider's website and paste only the key."
        )
    return k or None


def loosen(fns: list[dict]) -> list[dict]:
    """The same tools with the allowed values written into the descriptions instead of enforced by the schema."""
    out = json.loads(json.dumps(fns))
    for f in out:
        params = f["function"]["parameters"]
        params.pop("required", None)
        for name, prop in (params.get("properties") or {}).items():
            vals = prop.pop("enum", None) or (prop.get("items") or {}).pop("enum", None)
            if vals:
                prop["description"] = (prop.get("description", "") + f" Allowed values: {', '.join(vals)}.").strip()
    return out


class DailyLimitReached(RuntimeError):
    """The provider's daily quota for this model is used up."""


def _wait_seconds(headers, detail: str, default: float) -> float:
    """How long a 429 asks us to wait: the Retry-After header, or 'try again in 1m2.5s' / 'in 7.3s' in the message."""
    try:
        if headers is not None and headers.get("Retry-After"):
            return float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?", detail)
    if m and any(m.groups()):
        h, mi, se = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + se
    return default


class OpenAICompatBackend:
    """Any server with an OpenAI-style /chat/completions endpoint (Gemini, Groq, Ollama, OpenAI...)."""

    MAX_WAIT = 65

    def __init__(
        self,
        provider: str = "gemini",
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = 90,
        retries: int = 4,
    ):
        base, key_env, default_model = PRESETS.get(provider, (None, None, None))
        self.provider = provider
        self.base = (base_url or os.environ.get("MAJHAR_BASE_URL") or base or "").rstrip("/")
        self.key = clean_key(api_key if api_key is not None else (os.environ.get(key_env) if key_env else None))
        self.model = model or os.environ.get("MAJHAR_MODEL") or default_model
        self.timeout, self.retries = timeout, retries
        self.tokens_used = 0
        self.calls = 0
        self.loose_tools = False
        self.exhausted: list[str] = []
        self.model_changes: list[str] = []
        self.low_reasoning = "gpt-oss" in (self.model or "")
        if not self.base:
            raise ValueError(f"unknown provider '{provider}' and no base URL")
        if key_env and not self.key:
            raise RuntimeError(f"{key_env} is not set")

    @staticmethod
    def to_openai(system: str, tools: list[dict], messages: list[dict]) -> tuple[list[dict], list[dict]]:
        out = [{"role": "system", "content": system}]
        for m in messages:
            c = m["content"]
            if isinstance(c, str):
                out.append({"role": m["role"], "content": c})
            elif m["role"] == "assistant":
                text = "\n".join(b["text"] for b in c if b["type"] == "text").strip()
                calls = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                        },
                    }
                    for b in c
                    if b["type"] == "tool_use"
                ]
                msg = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                for b in c:
                    if b["type"] == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b["content"])})
                    elif b["type"] == "text":
                        out.append({"role": "user", "content": b["text"]})
        fns = []
        for t in tools:
            params = dict(t["input_schema"])
            if not params.get("required"):
                params.pop("required", None)
            fns.append(
                {
                    "type": "function",
                    "function": {"name": t["name"], "description": t["description"], "parameters": params},
                }
            )
        return out, fns

    @staticmethod
    def from_openai(resp: dict) -> dict:
        msg = resp["choices"][0]["message"]
        blocks = []
        if msg.get("content"):
            blocks.append({"type": "text", "text": msg["content"]})
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id") or f"call_{i}_{int(time.time() * 1000)}",
                    "name": fn.get("name", ""),
                    "input": args if isinstance(args, dict) else {},
                }
            )
        return {
            "content": blocks or [{"type": "text", "text": "(no output)"}],
            "stop_reason": "tool_use" if msg.get("tool_calls") else "end_turn",
        }

    PREFER = [
        "llama-3.3-70b",
        "llama-4",
        "gpt-oss-120b",
        "qwen",
        "llama-3.1-70b",
        "gpt-oss",
        "llama",
        "gpt-4o-mini",
        "gemini",
    ]

    def available_models(self) -> list[str]:
        req = urllib.request.Request(
            f"{self.base}/models", headers={**UA, **({"Authorization": f"Bearer {self.key}"} if self.key else {})}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]

    def _pick_model(self) -> bool:
        """Pick a capable model from the provider's list when the configured one is not available."""
        try:
            ids = self.available_models()
        except Exception:
            return False
        for want in self.PREFER:
            for mid in ids:
                if (
                    want in mid
                    and "guard" not in mid
                    and "whisper" not in mid
                    and "tts" not in mid
                    and mid != self.model
                ):
                    self.model = mid
                    return True
        return False

    def create(self, system: str, tools: list[dict], messages: list[dict]) -> dict:
        msgs, fns = self.to_openai(system, tools, messages)
        body = json.dumps({"model": self.model, "messages": msgs, "tools": fns, "temperature": 0.2}).encode()
        headers = {"Content-Type": "application/json", **UA}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        delay, switched, attempt, waits, tool_retries = 2.0, False, 0, 0, 0
        while True:
            payload = {
                "model": self.model,
                "messages": msgs,
                "tools": loosen(fns) if self.loose_tools else fns,
                "temperature": 0.2 + 0.2 * tool_retries,
            }
            if self.low_reasoning:
                payload["reasoning_effort"] = "low"
            req = urllib.request.Request(f"{self.base}/chat/completions", json.dumps(payload).encode(), headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                self.calls += 1
                self.tokens_used += int((data.get("usage") or {}).get("total_tokens") or 0)
                return self.from_openai(data)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:400]
                low = detail.lower()
                if (
                    e.code == 400
                    and (
                        "tool call validation" in low or "tool_use_failed" in low or "failed to call a function" in low
                    )
                    and tool_retries < 3
                ):
                    tool_retries += 1
                    self.loose_tools = True
                    continue
                if e.code == 400 and self.low_reasoning and "reasoning" in low:
                    self.low_reasoning = False
                    continue
                if e.code in (400, 404) and "model" in low and not switched:
                    switched = True
                    if self._pick_model():
                        self.low_reasoning = "gpt-oss" in self.model
                        continue
                if e.code == 429 and ("per day" in low or "(tpd)" in low or "(rpd)" in low):
                    if self.model not in self.exhausted:
                        self.exhausted.append(self.model)
                    nxt = next((m for m in DAILY_FALLBACKS.get(self.provider, []) if m not in self.exhausted), None)
                    if nxt:
                        self.model_changes.append(f"{self.model} reached its daily limit; switched to {nxt}")
                        self.model, self.low_reasoning, waits = nxt, "gpt-oss" in nxt, 0
                        continue
                    raise DailyLimitReached(
                        f"{self.provider} daily limit reached for {', '.join(self.exhausted)}. It resets within 24 hours. "
                        f"To keep going now, use another provider or model, e.g. export MAJHAR_MODEL=<model>"
                    ) from None
                if e.code == 429 and waits < 6:
                    waits += 1
                    wait = _wait_seconds(e.headers, detail, delay)
                    if wait > self.MAX_WAIT:
                        raise RuntimeError(
                            f"{self.provider} HTTP 429: asked to wait {round(wait)} s: {detail}"
                        ) from None
                    time.sleep(wait + 0.5)
                    delay *= 2
                    continue
                if e.code in (500, 502, 503, 504) and attempt < self.retries:
                    attempt += 1
                    time.sleep(delay)
                    delay *= 2
                    continue
                hint = " (blocked by the provider's security filter)" if "1010" in detail else ""
                raise RuntimeError(f"{self.provider} HTTP {e.code}{hint}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < self.retries:
                    attempt += 1
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError(f"cannot reach {self.provider} at {self.base}: {getattr(e, 'reason', e)}") from None


class GeminiBackend:
    """Google Gemini through its native API (generateContent + function calling)."""

    BASE = "https://generativelanguage.googleapis.com/v1beta"
    FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash"]

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 60,
        retries: int = 3,
    ):
        self.provider = "gemini"
        self.key = clean_key(
            api_key if api_key is not None else (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        )
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.key = self.key.strip().strip('"').strip("'")
        self.base = (base_url or os.environ.get("MAJHAR_GEMINI_BASE") or self.BASE).rstrip("/")
        self.model = model or os.environ.get("MAJHAR_MODEL") or self.FALLBACK_MODELS[0]
        self._explicit_model = bool(model or os.environ.get("MAJHAR_MODEL"))
        self.tokens_used = 0
        self.calls = 0
        self.timeout, self.retries = timeout, retries

    @staticmethod
    def _schema(p: dict) -> dict | None:
        props = p.get("properties") or {}
        if not props:
            return None
        out = {"type": "object", "properties": props}
        if p.get("required"):
            out["required"] = p["required"]
        return out

    @classmethod
    def to_gemini(cls, system: str, tools: list[dict], messages: list[dict]) -> dict:
        names: dict[str, str] = {}
        contents = []
        for m in messages:
            c = m["content"]
            if isinstance(c, str):
                contents.append({"role": "user" if m["role"] == "user" else "model", "parts": [{"text": c}]})
            elif m["role"] == "assistant":
                parts = []
                for b in c:
                    if b["type"] == "text" and b["text"].strip():
                        parts.append(b.get("_raw") or {"text": b["text"]})
                    elif b["type"] == "tool_use":
                        names[b["id"]] = b["name"]
                        parts.append(
                            b.get("_raw") or {"functionCall": {"name": b["name"], "args": b.get("input") or {}}}
                        )
                contents.append({"role": "model", "parts": parts or [{"text": "(thinking)"}]})
            else:
                parts = []
                for b in c:
                    if b["type"] == "tool_result":
                        try:
                            resp = json.loads(b["content"]) if isinstance(b["content"], str) else b["content"]
                        except json.JSONDecodeError:
                            resp = {"result": b["content"]}
                        if not isinstance(resp, dict):
                            resp = {"result": resp}
                        parts.append(
                            {"functionResponse": {"name": names.get(b["tool_use_id"], "tool"), "response": resp}}
                        )
                    elif b["type"] == "text":
                        parts.append({"text": b["text"]})
                contents.append({"role": "user", "parts": parts})
        decls = []
        for t in tools:
            d = {"name": t["name"], "description": t["description"]}
            sch = cls._schema(t["input_schema"])
            if sch:
                d["parameters"] = sch
            decls.append(d)
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "tools": [{"functionDeclarations": decls}],
            "generationConfig": {"temperature": 0.2},
        }

    @staticmethod
    def from_gemini(resp: dict) -> dict:
        cands = resp.get("candidates") or []
        if not cands:
            reason = (resp.get("promptFeedback") or {}).get("blockReason", "no answer")
            return {
                "content": [{"type": "text", "text": f"(model returned nothing: {reason})"}],
                "stop_reason": "end_turn",
            }
        blocks = []
        for i, part in enumerate((cands[0].get("content") or {}).get("parts") or []):
            if "functionCall" in part:
                fc = part["functionCall"]
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": fc.get("id") or f"g{i}_{int(time.time() * 1000)}",
                        "name": fc.get("name", ""),
                        "input": fc.get("args") or {},
                        "_raw": part,
                    }
                )
            elif part.get("text") and not part.get("thought"):
                blocks.append({"type": "text", "text": part["text"], "_raw": part})
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return {"content": blocks or [{"type": "text", "text": "(no output)"}], "stop_reason": stop}

    def _post(self, model: str, body: bytes) -> dict:
        req = urllib.request.Request(
            f"{self.base}/models/{model}:generateContent",
            body,
            {"Content-Type": "application/json", "x-goog-api-key": self.key, **UA},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def create(self, system: str, tools: list[dict], messages: list[dict]) -> dict:
        body = json.dumps(self.to_gemini(system, tools, messages)).encode()
        delay = 2.0
        for attempt in range(self.retries + 1):
            try:
                data = self._post(self.model, body)
                self.calls += 1
                self.tokens_used += int((data.get("usageMetadata") or {}).get("totalTokenCount") or 0)
                return self.from_gemini(data)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:400]
                suggested = re.search(r"use models/([\w.\-]+)", detail)
                if e.code == 404 and suggested and suggested.group(1) != self.model and attempt < self.retries:
                    self.model = suggested.group(1)
                    continue
                if e.code == 404 and not self._explicit_model and self.model in self.FALLBACK_MODELS:
                    i = self.FALLBACK_MODELS.index(self.model)
                    if i + 1 < len(self.FALLBACK_MODELS):
                        self.model = self.FALLBACK_MODELS[i + 1]
                        continue
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(min(float(e.headers.get("Retry-After") or delay), 30))
                    delay *= 2
                    continue
                hint = ""
                if e.code in (400, 401, 403) and ("key" in detail.lower() or "auth" in detail.lower()):
                    hint = " (check GEMINI_API_KEY: copy it again from AI Studio, no spaces)"
                raise RuntimeError(f"gemini HTTP {e.code}{hint}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if "CERTIFICATE_VERIFY_FAILED" in str(getattr(e, "reason", e)):
                    raise RuntimeError(
                        "Python cannot verify HTTPS certificates. On a Mac run: "
                        'open "/Applications/Python 3.12/Install Certificates.command"'
                    ) from None
                if attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError(f"cannot reach gemini at {self.base}: {getattr(e, 'reason', e)}") from None
        raise RuntimeError("unreachable")


def _ollama_up(base: str = "http://localhost:11434") -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/api/tags", headers=UA), timeout=1.5):
            return True
    except Exception:
        return False


def make_backend(provider: str | None = None):
    """The model behind the agent."""
    provider = provider or os.environ.get("MAJHAR_PROVIDER")
    if provider is None:
        for p in ORDER:
            if p == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
                provider = p
            elif p in PRESETS and PRESETS[p][1] and os.environ.get(PRESETS[p][1]):
                provider = p
            elif p == "ollama" and _ollama_up():
                provider = p
            if provider:
                break
    if provider is None:
        raise RuntimeError(
            "no AI model found. Set GEMINI_API_KEY (free at https://aistudio.google.com), or start Ollama, "
            "or set ANTHROPIC_API_KEY / GROQ_API_KEY / OPENAI_API_KEY"
        )
    if provider == "anthropic":
        from .agents import AnthropicBackend

        b = AnthropicBackend()
        b.provider = "anthropic"
        return b
    if provider == "gemini":
        return GeminiBackend()
    if provider == "ollama" and not _ollama_up():
        raise RuntimeError("Ollama is not running. Install it from https://ollama.com, then: ollama pull qwen2.5:7b")
    return OpenAICompatBackend(provider)
