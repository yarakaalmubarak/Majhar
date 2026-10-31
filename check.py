#!/usr/bin/env python3
"""Regression check: on the same scenarios, the current version must not do worse than the frozen first version."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = [
    ("seed 9090, normal rules", "support_v1/support.json", "support_dev.json"),
    ("seed 9090, every request declined", "support_v1/support_deny.json", "support_dev_deny.json"),
    ("seed 5150, every request declined", "support_deny_v1.json", "support_deny.json"),
]


def totals(path: str) -> dict:
    rows = [r for r in json.load(open(path)) if r["mode"] == "agent"]
    return {
        "made_whole": sum(r["made_whole"] for r in rows),
        "false_closures": sum(r["false_closures"] for r in rows),
        "policy_violations": sum(r["policy_violations"] for r in rows),
    }


def main() -> int:
    worse = []
    for name, old, new in PAIRS:
        base = totals(os.path.join(HERE, "results", old))
        now = totals(os.path.join(HERE, "results", new))
        print(f"{name:34} customers whole {base['made_whole']:>5} -> {now['made_whole']:>5}   "
              f"closed wrongly {base['false_closures']} -> {now['false_closures']}   "
              f"rule breaks {base['policy_violations']} -> {now['policy_violations']}")
        if now["made_whole"] < base["made_whole"]:
            worse.append(f"{name}: fewer customers made whole")
        if now["false_closures"] > base["false_closures"] or now["policy_violations"] > base["policy_violations"]:
            worse.append(f"{name}: more tickets closed wrongly or more rule breaks")
    if worse:
        print("FAILED:", "; ".join(worse))
        return 1
    print("OK: no result is worse than the frozen first version.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
