#!/usr/bin/env python3
"""Checks that the configured language model answers."""

import argparse
import sys
import time

from majhar.llm_backends import make_backend

ap = argparse.ArgumentParser()
ap.add_argument("--provider", default=None)
a = ap.parse_args()
try:
    b = make_backend(a.provider)
except Exception as e:
    print(f"NOT READY: {e}")
    sys.exit(1)
print(f"Asking {getattr(b, 'provider', '?')} (model {b.model}) to say OK ... (can take up to a minute)")
t = time.time()
try:
    out = b.create(
        "You are a test. Reply with the single word OK.",
        [{"name": "wait", "description": "do nothing", "input_schema": {"type": "object", "properties": {}}}],
        [{"role": "user", "content": "Say OK"}],
    )
except Exception as e:
    print(f"FAILED after {time.time() - t:.0f}s: {e}")
    sys.exit(1)
text = " ".join(x.get("text", "") for x in out["content"] if x["type"] == "text") or str(out["content"])
print(f"WORKS: the model answered in {time.time() - t:.0f}s: {text[:80]}")
print(f"Model in use: {b.model}. You can now run:  python3 app.py")
