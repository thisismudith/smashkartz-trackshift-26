"""Focused CP-03 tests for immediate-ahead pairing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.features.pairing import (  # noqa: E402
    CLOSE_FOLLOWING_CRITERION,
    assign_immediate_ahead_pairs,
    build_session_roster,
)


def row(driver: str, ahead: str | None, gap: float | None, **extra):
    return {
        "year": 2026, "event": "EVT", "session": "Race", "lap": 1,
        "segment_id": 1, "entry_distance_m": 20.0,
        "driver": driver, "driver_ahead": ahead, "gap_ahead_m_entry": gap,
        "normal_race_model_eligible": True,
        "pit_state": "ON_TRACK", "race_control_transition_flag": False,
        "pit_transition_flag": False,
        **extra,
    }


def test_only_immediate_observed_ahead_is_paired_not_every_driver_pair():
    rows = assign_immediate_ahead_pairs([
        row("HAM", "ANT", 40.0), row("ANT", "VER", 50.0), row("VER", None, None),
    ])
    assert [entry["pair_key"] for entry in rows] == ["HAM->ANT", "ANT->VER", None]
    assert {entry["pair_key"] for entry in rows if entry["pair_key"]} == {"HAM->ANT", "ANT->VER"}
    assert rows[2]["pairing_exclusion_reason"] == "MISSING_DRIVER_AHEAD"


def test_self_pair_is_never_eligible_and_distance_criterion_is_explicit():
    paired = assign_immediate_ahead_pairs([row("HAM", "HAM", 10.0)])[0]
    assert paired["pairing_eligible"] is False
    assert paired["pairing_exclusion_reason"] == "SELF_PAIR"
    assert paired["close_following_criterion"] == CLOSE_FOLLOWING_CRITERION


def test_same_session_driver_number_resolves_to_the_static_roster_identity():
    roster = build_session_roster([
        {"year": 2026, "event": "EVT", "session": "Race", "driver": "HAM", "driver_number": "44"},
        {"year": 2026, "event": "EVT", "session": "Race", "driver": "ANT", "driver_number": "12"},
    ])
    paired = assign_immediate_ahead_pairs(
        [row("HAM", None, 20.0, driver_ahead_number="12")], session_roster=roster
    )[0]
    assert paired["defender"] == "ANT"
    assert paired["defender_resolution"] == "SESSION_ROSTER"
    assert paired["pairing_eligible"] is True


def test_unknown_driver_number_is_audited_not_invented():
    paired = assign_immediate_ahead_pairs([
        row("HAM", None, 20.0, driver_ahead_number="12"),
    ])[0]
    assert paired["defender"] is None
    assert paired["pairing_exclusion_reason"] == "UNKNOWN_DRIVER_NUMBER"


def test_ambiguous_reused_driver_number_is_not_model_ready():
    roster = build_session_roster([
        {"year": 2026, "event": "EVT", "session": "Race", "driver": "ANT", "driver_number": "12"},
        {"year": 2026, "event": "EVT", "session": "Race", "driver": "BEA", "driver_number": "12"},
    ])
    paired = assign_immediate_ahead_pairs(
        [row("HAM", None, 20.0, driver_ahead_number="12")], session_roster=roster
    )[0]
    assert paired["defender"] is None
    assert paired["pairing_exclusion_reason"] == "AMBIGUOUS_DRIVER_NUMBER"


def test_race_position_or_later_rows_never_fill_an_unknown_ahead_number():
    source = [
        row("HAM", None, 20.0, driver_ahead_number="99", race_position=2),
        row("ANT", None, None, driver_number="12", race_position=1),
    ]
    paired = assign_immediate_ahead_pairs(source)[0]
    assert paired["defender"] is None
    assert paired["pairing_exclusion_reason"] == "UNKNOWN_DRIVER_NUMBER"
