"""Tests for the lake batch driver's scoping logic (CP-04).

The build itself is the single-session builder, already tested. What is new here
is deciding *which* sessions to build, and getting that wrong silently builds the
wrong scope -- expensive at 279 sessions and invisible until much later.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "data"))

from build_lake import (  # noqa: E402
    CORE_SESSIONS_2026,
    CORE_SESSIONS_HISTORIC,
    SessionJob,
    discover_sessions,
    safe_name,
)


@pytest.fixture
def mirror(tmp_path: Path) -> Path:
    """A miniature raw mirror: two seasons, two events, several sessions."""
    layout = {
        ("2026", "British Grand Prix"): ["Practice 1", "Qualifying", "Race", "Sprint"],
        ("2026", "Monaco Grand Prix"): ["Practice 1", "Qualifying", "Race"],
        ("2024", "British Grand Prix"): ["Qualifying", "Race"],
        ("2024", "Monaco Grand Prix"): ["Qualifying", "Race"],
    }
    for (year, event), sessions in layout.items():
        for session in sessions:
            driver = tmp_path / year / event / session / "HAM"
            driver.mkdir(parents=True)
            (driver / "1_tel.json").write_text("{}", encoding="utf-8")
    # A session directory with no drivers has nothing to build.
    (tmp_path / "2026" / "British Grand Prix" / "Empty Session").mkdir(parents=True)
    return tmp_path


def labels(jobs: list[SessionJob]) -> set[str]:
    return {j.label for j in jobs}


def test_discovers_core_sessions_for_a_year(mirror):
    jobs = discover_sessions(mirror, ("2026",), None, None, [])
    assert "2026 British Grand Prix / Race" in labels(jobs)
    assert "2026 Monaco Grand Prix / Qualifying" in labels(jobs)


def test_practice_1_is_2026_only_by_policy(mirror):
    """Section 8: Practice is not in the historical core scope."""
    assert "Practice 1" in CORE_SESSIONS_2026
    assert "Practice 1" not in CORE_SESSIONS_HISTORIC
    jobs = discover_sessions(mirror, ("2024",), None, None, [])
    assert not [j for j in jobs if j.session == "Practice 1"]


def test_event_filter(mirror):
    jobs = discover_sessions(mirror, ("2026",), ["British Grand Prix"], None, [])
    assert {j.event for j in jobs} == {"British Grand Prix"}


def test_event_filter_is_case_insensitive(mirror):
    jobs = discover_sessions(mirror, ("2026",), ["british grand prix"], None, [])
    assert jobs and {j.event for j in jobs} == {"British Grand Prix"}


def test_session_filter(mirror):
    jobs = discover_sessions(mirror, ("2026",), None, ["Race"], [])
    assert {j.session for j in jobs} == {"Race"}


def test_exclude_event_removes_it(mirror):
    jobs = discover_sessions(mirror, ("2026",), None, None, ["British Grand Prix"])
    assert "British Grand Prix" not in {j.event for j in jobs}
    assert "Monaco Grand Prix" in {j.event for j in jobs}


def test_exclude_beats_include_for_the_same_event(mirror):
    """Ambiguous input must fail safe: excluded wins, so nothing unwanted builds."""
    jobs = discover_sessions(mirror, ("2026",), ["British Grand Prix"], None, ["British Grand Prix"])
    assert jobs == []


def test_session_with_no_drivers_is_skipped(mirror):
    jobs = discover_sessions(mirror, ("2026",), None, ["Empty Session"], [])
    assert jobs == []


def test_missing_year_is_not_an_error(mirror):
    assert discover_sessions(mirror, ("2022",), None, None, []) == []


def test_multiple_years(mirror):
    jobs = discover_sessions(mirror, ("2024", "2026"), None, ["Race"], [])
    assert {j.year for j in jobs} == {"2024", "2026"}


def test_discovery_is_deterministically_ordered(mirror):
    a = [j.label for j in discover_sessions(mirror, ("2024", "2026"), None, None, [])]
    b = [j.label for j in discover_sessions(mirror, ("2024", "2026"), None, None, [])]
    assert a == b == sorted(a, key=lambda s: (s.split()[0], s))


def test_partition_path_matches_the_builder_naming(mirror):
    """Must agree with build_phase2_dataset.py, or moved Parquet lands in the
    wrong partition."""
    job = SessionJob("2026", "British Grand Prix", "Sprint Qualifying")
    assert job.partition == Path("year=2026") / "event=British_Grand_Prix" / "session=Sprint_Qualifying"


def test_safe_name_replaces_spaces_only():
    assert safe_name("British Grand Prix") == "British_Grand_Prix"
    assert safe_name("Race") == "Race"
