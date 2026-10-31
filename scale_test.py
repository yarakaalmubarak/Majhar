#!/usr/bin/env python3
"""Measures how many AI calls the agent needs as the number of affected customers grows."""

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests"))

from majhar.ai_support import AIDesk
from majhar.report import header, md_table
from majhar.shop import ShopSim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None)
    ap.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 200, 800])
    a = ap.parse_args()
    if a.provider:
        from majhar.llm_backends import make_backend

        backend_for = lambda: make_backend(a.provider)
        who = f"{a.provider} model"
    else:
        from test_ai_support import FakeCommander

        backend_for = FakeCommander
        who = "scripted stand-in model"
    rows = []
    for n in a.sizes:
        sim = ShopSim(seed=11, seats_per_section=max(200, n * 2))
        b = backend_for()
        desk = AIDesk(sim, b)
        sim.inject("payment_webhook_drop")
        sim.rush(n, random.Random(n))
        t0 = time.time()
        desk.tick(15)
        cs = list(desk.cases.values())
        whole = sum(c.status == "resolved" for c in cs)
        rows.append(
            [
                n,
                len(cs),
                f"{whole}/{len(cs)}",
                desk.ai_calls,
                desk.ai_sessions,
                f"{time.time() - t0:.1f}s",
                sim.truth_ok(),
            ]
        )
        print(f"  {n} fans: {len(cs)} tickets, {whole} solved, {desk.ai_calls} AI calls", file=sys.stderr, flush=True)
    md = header(f"Scale: AI calls vs affected customers ({who})")
    md += [
        "A rush of fans checks out while payments are silently dropped. One AI session plans the whole incident; automation "
        "applies the plan to every ticket.\n",
        md_table(
            ["fans in the rush", "tickets", "solved", "AI calls", "AI sessions", "wall time", "platform healthy"], rows
        ),
        "",
    ]
    text = "\n".join(md)
    print(text)
    os.makedirs("results", exist_ok=True)
    stem = "scale" if not a.provider else f"scale_{a.provider}"
    with open(f"results/{stem}.md", "w") as f:
        f.write(text)
    import json

    with open(f"results/{stem}.json", "w") as f:
        json.dump(
            [{"fans": r[0], "tickets": r[1], "solved": int(r[2].split("/")[0]), "ai_calls": r[3]} for r in rows],
            f,
            indent=1,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
