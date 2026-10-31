"""Statistics, markdown tables and the code fingerprint used by the evaluation reports."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform

HERE = os.path.dirname(os.path.abspath(__file__))
FINGERPRINT_FILES = ["sim.py", "tools.py", "scenarios.py", "generate.py", "shop.py", "support.py", "ai_support.py"]


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join("-" if c is None else str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - a) / d), min(1.0, (c + a) / d)


def pct_ci(k: int, n: int) -> str:
    if n == 0:
        return "-"
    lo, hi = wilson(k, n)
    f = (lambda x: f"{100 * x:.1f}".rstrip("0").rstrip(".")) if n >= 200 else (lambda x: f"{100 * x:.0f}")
    return f"{k}/{n} ({100 * k / n:.0f}%, CI {f(lo)}-{f(hi)}%)"


def fingerprint() -> str:
    """Short hash of the source files that determine the results."""
    h = hashlib.sha256()
    for f in FINGERPRINT_FILES:
        with open(os.path.join(HERE, f), "rb") as fh:
            h.update(f.encode() + b"\0" + fh.read())
    return h.hexdigest()[:12]


def provenance(**extra) -> dict:
    return {"code_fingerprint": fingerprint(), "python": platform.python_version(), **extra}


def header(title: str, **extra) -> list[str]:
    p = provenance(**extra)
    meta = ", ".join(f"{k}={v}" for k, v in p.items())
    return [f"# {title}\n", f"*Reproducible: deterministic simulator, fixed seeds. {meta}*\n"]


def write_reports(out_dir: str, stem: str, md: str, rows: list[dict]) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{stem}.md"), "w") as f:
        f.write(md)
    with open(os.path.join(out_dir, f"{stem}.json"), "w") as f:
        json.dump([{k: v for k, v in r.items() if k not in ("events",)} for r in rows], f, indent=1)
