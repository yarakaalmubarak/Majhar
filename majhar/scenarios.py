"""A test scenario: which faults are active and how busy the shop is."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    id: str
    name: str
    faults: list[str] = field(default_factory=list)
    arrivals: int = 6
    seats_per_section: int = 200
    held_out: bool = False
    note: str = ""
