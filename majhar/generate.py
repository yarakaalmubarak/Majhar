"""Seeded generator of test scenarios: random combinations of the nine fault types, crowd sizes and seat counts."""

from __future__ import annotations

import random

from .scenarios import Scenario
from .sim import FAULT_IDS

ARRIVALS = [6, 6, 12, 25, 40, 60]
SEATS = [15, 60, 200, 200]
N_FAULTS = [0] * 1 + [1] * 3 + [2] * 3 + [3] * 2 + [4] * 1


def generate(n: int = 100, seed: int = 2026) -> list[Scenario]:
    rng = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        k = rng.choice(N_FAULTS)
        faults = rng.sample(FAULT_IDS, k)
        arrivals, seats = rng.choice(ARRIVALS), rng.choice(SEATS)
        name = (
            f"{k} fault{'s' if k != 1 else ''}, {arrivals}/min, {seats} seats/section"
            if k
            else f"healthy, {arrivals}/min, {seats} seats/section"
        )
        out.append(
            Scenario(
                f"u{i:03d}", name, faults, arrivals=arrivals, seats_per_section=seats, held_out=True, note="generated"
            )
        )
    return out
