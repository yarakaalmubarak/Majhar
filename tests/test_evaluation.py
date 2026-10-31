"""The evaluation tooling: scenario generator, statistics and report helpers."""

import json
import os

import pytest

from majhar.generate import generate
from majhar.report import fingerprint, header, md_table, pct_ci, wilson, write_reports
from majhar.sim import FAULT_IDS


def test_generator_is_deterministic_and_seed_sensitive():
    a, b, c = generate(50, 2026), generate(50, 2026), generate(50, 7)
    key = lambda s: (s.id, s.faults, s.arrivals, s.seats_per_section)
    assert [key(s) for s in a] == [key(s) for s in b]
    assert [s.faults for s in a] != [s.faults for s in c]


def test_generated_scenarios_are_valid_and_varied():
    scns = generate(300, 2026)
    assert len({s.id for s in scns}) == 300
    for s in scns:
        assert s.held_out and 0 <= len(s.faults) <= 4
        assert len(set(s.faults)) == len(s.faults) and set(s.faults) <= set(FAULT_IDS)
    assert {len(s.faults) for s in scns} == {0, 1, 2, 3, 4}
    assert {f for s in scns for f in s.faults} == set(FAULT_IDS)
    assert len({s.arrivals for s in scns}) >= 4 and len({s.seats_per_section for s in scns}) >= 3
    assert 0.03 < sum(not s.faults for s in scns) / 300 < 0.25


def test_wilson_interval():
    lo, hi = wilson(50, 100)
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60
    assert wilson(0, 20)[0] == 0.0 and 0.15 < wilson(0, 20)[1] < 0.17
    assert wilson(20, 20)[1] == pytest.approx(1.0) and wilson(0, 0) == (0.0, 0.0)
    assert "CI 0-16%" in pct_ci(0, 20)


def test_fingerprint_changes_when_source_changes(tmp_path, monkeypatch):
    from majhar import report

    fp = fingerprint()
    assert len(fp) == 12 and fp == fingerprint()
    for f in report.FINGERPRINT_FILES:
        (tmp_path / f).write_text(open(os.path.join(report.HERE, f)).read())
    monkeypatch.setattr(report, "HERE", str(tmp_path))
    assert report.fingerprint() == fp
    (tmp_path / "support.py").write_text((tmp_path / "support.py").read_text() + "\n# changed\n")
    assert report.fingerprint() != fp


def test_report_helpers(tmp_path):
    assert md_table(["a", "b"], [[1, None]]).splitlines()[-1] == "| 1 | - |"
    assert fingerprint() in "\n".join(header("T", n=3))
    write_reports(str(tmp_path), "x", "# hi", [{"a": 1, "events": [1, 2, 3]}])
    assert (tmp_path / "x.md").read_text() == "# hi"
    assert json.loads((tmp_path / "x.json").read_text()) == [{"a": 1}]
