"""CP-07 tests for the practice-lap acceptance audit.

The audit exists to answer one question the manifest cannot: is the sustained
running population real, or is the gate being met by counting laps that are not
in any run? These tests pin the arithmetic that answers it, because the whole
value of the audit is that its numbers can be trusted against the classifier's.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.track.lap_classifier import (  # noqa: E402
    classify_practice_laps,
    load_lap_classification_config,
)

_SPEC = importlib.util.spec_from_file_location(
    "cp07_audit", ROOT / "scripts" / "features" / "audit_practice_lap_classes.py")
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


def _laps(specs, *, driver="AAA", session="Practice 1"):
    rows = []
    for lap, spec in enumerate(specs, start=1):
        rows.append({
            "year": 2026, "event": "Test GP", "session": session, "driver": driver,
            "lap": lap, "lap_time_s": spec.get("time", 100.0),
            "tyre_life_laps": spec.get("life", 6),
            "pit_in_session_s": spec.get("pin"), "pit_out_session_s": spec.get("pout"),
            "is_accurate": spec.get("accurate", True),
            "lap_deleted": spec.get("deleted", False),
            "track_status": spec.get("status", "1"),
        })
    return rows


def _classified(rows):
    """Metadata joined to its emitted class, the shape the audit consumes."""
    frame = pd.DataFrame(rows)
    overlay = classify_practice_laps(frame)
    return frame.merge(overlay, on=["year", "event", "session", "driver", "lap"])


@pytest.fixture
def cfg():
    return load_lap_classification_config()


def test_race_pace_inside_a_completed_run_is_counted_as_sustained(cfg):
    """The three head laps of a four-lap run are exactly what the gate revision
    claimed RACE_PACE was. When that is true, the audit must agree."""
    joined = _classified(_laps([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 101, "life": 8}, {"time": 102, "life": 9},
    ]))
    placement = audit.run_analysis(joined, cfg)["race_pace_placement"]
    assert placement["inside_a_run_that_reached_long_run"] == 3
    assert placement["isolated_single_lap"] == 0
    assert placement["share_inside_a_qualifying_run"] == pytest.approx(1.0)


def test_isolated_race_pace_laps_are_not_counted_as_sustained(cfg):
    """The finding that failed CP-07.

    A lap inside the time band on a worn tyre is RACE_PACE on its own evidence,
    but a slow lap either side blows the 3% spread and leaves it in a run of
    one. 898 of the season's 2,074 RACE_PACE laps are in exactly this position,
    which is why they cannot be counted toward sustained running.
    """
    joined = _classified(_laps([
        {"time": 100, "life": 10},   # RACE_PACE, run of one
        {"time": 130, "life": 11},   # slow: breaks the spread, restarts the run
        {"time": 104, "life": 12},   # RACE_PACE, run of one again
    ]))
    placement = audit.run_analysis(joined, cfg)["race_pace_placement"]
    assert placement["isolated_single_lap"] == 2
    assert placement["inside_a_run_that_reached_long_run"] == 0
    assert placement["share_inside_a_qualifying_run"] == pytest.approx(0.0)


def test_a_structural_boundary_ends_a_run_and_is_recorded_as_the_reason(cfg):
    joined = _classified(_laps([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 101, "life": 8, "pin": 20},
        {"time": 101, "life": 9}, {"time": 102, "life": 10},
    ]))
    rejections = audit.run_analysis(joined, cfg)["candidate_run_rejections"]
    assert rejections.get("STRUCTURAL_BOUNDARY", 0) >= 1


def test_lap_time_spread_ends_a_run_and_is_recorded_separately(cfg):
    """Spread and boundary failures mean different things: one is the 3% rule
    biting, the other is the session's own structure. They must not merge."""
    joined = _classified(_laps([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 130, "life": 8}, {"time": 131, "life": 9},
    ]))
    rejections = audit.run_analysis(joined, cfg)["candidate_run_rejections"]
    assert rejections.get("LAP_TIME_SPREAD_EXCEEDED", 0) >= 1
    assert "STRUCTURAL_BOUNDARY" not in rejections


def test_head_lap_count_matches_the_causal_ceiling_claim(cfg):
    """A run of n >= 4 contributes exactly (min_len - 1) unlabellable head laps.
    The checkpoint claimed ~2,000 across the season; this pins the unit so the
    season figure is arithmetic rather than assertion."""
    joined = _classified(_laps([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 101, "life": 8}, {"time": 102, "life": 9},
        {"time": 102, "life": 10},
    ]))
    result = audit.run_analysis(joined, cfg)
    assert result["head_laps_unlabellable_by_causality"] == (
        cfg.long_run_min_consecutive_laps - 1)


def test_unknown_reasons_separate_the_dead_band_from_missing_inputs(cfg):
    """`no_rule_matched` covers several causes. Collapsing them would hide
    whether UNKNOWN is genuine ambiguity or a fixable input gap."""
    joined = _classified(_laps([
        {"time": 100, "life": 6},     # RACE_PACE, sets the running best
        {"time": 112, "life": 7},     # UNKNOWN: 108-115% dead band
        {"time": 130, "life": 8},     # UNKNOWN: slow, not after a PUSH
    ]))
    reasons = audit.unknown_reasons(joined, cfg)
    assert reasons["DEAD_BAND_108_TO_115_PCT"] == 1
    assert reasons["SLOW_BUT_NOT_AFTER_PUSH"] == 1


def test_runs_do_not_leak_across_drivers(cfg):
    """Same laps, two drivers: neither forms a run, so no lap is sustained."""
    rows = _laps([{"time": 100, "life": 6}, {"time": 101, "life": 7}], driver="AAA")
    rows += _laps([{"time": 101, "life": 8}, {"time": 102, "life": 9}], driver="BBB")
    joined = _classified(rows)
    placement = audit.run_analysis(joined, cfg)["race_pace_placement"]
    assert placement["inside_a_run_that_reached_long_run"] == 0


def test_field_quality_shares_are_fractions_of_the_lap_count(cfg):
    joined = _classified(_laps([
        {"time": 100, "life": 6},
        {"time": 101, "life": 7, "accurate": False},
        {"time": 101, "life": 8, "status": "4"},
        {"time": 101, "life": 9, "pin": 40},
    ]))
    quality = audit.field_quality(joined)
    assert quality["laps"] == 4
    assert quality["is_accurate_false"] == pytest.approx(0.25)
    assert quality["track_status_green"] == pytest.approx(0.75)
    assert quality["pit_in_present"] == pytest.approx(0.25)
    assert all(0.0 <= v <= 1.0 for k, v in quality.items() if k != "laps")
