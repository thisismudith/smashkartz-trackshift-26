"""M01 deterministic Practice-lap classifier tests; CPU-only by design."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.track.lap_classifier import (  # noqa: E402
    LAP_CLASSES,
    LAP_CLASS_PROVENANCE,
    classify_practice_laps,
    load_lap_classification_config,
)


def _rows(specs, *, event="Test GP", session="Practice 1", driver="AAA"):
    rows = []
    for lap, spec in enumerate(specs, start=1):
        rows.append({
            "year": 2026, "event": event, "session": session, "driver": driver, "lap": lap,
            "lap_time_s": spec.get("time", 100.0), "tyre_life_laps": spec.get("life", 6),
            "pit_in_session_s": spec.get("pin"), "pit_out_session_s": spec.get("pout"),
            "is_accurate": spec.get("accurate", True), "lap_deleted": spec.get("deleted", False),
            "track_status": spec.get("status", "1"),
        })
    return rows


def _labels(rows):
    return classify_practice_laps(pd.DataFrame(rows))["practice_lap_class"].tolist()


def test_every_label_is_emitted_once_and_all_rows_have_one_valid_label():
    rows = _rows([
        {"time": 100, "life": 6},  # reference / RACE_PACE (in band, worn tyre)
        {"time": 112, "life": 6},  # UNKNOWN: 108-115% belongs to no rule
        {"time": 107, "life": 5},  # PUSH at the inclusive 107% boundary
        {"time": 116, "life": 6},  # COOLDOWN immediately after PUSH
        {"time": 100, "life": 7, "pout": 400},  # OUT_LAP
        {"time": 100, "life": 8, "pin": 500},  # IN_LAP
        {"time": 100, "life": 9},  # OUT_LAP after prior pin
        {"time": 100, "life": 10, "accurate": False},  # INVALID
        {"time": 100, "life": 11, "status": "12"},  # INTERRUPTED
    ])
    # A separate driver provides a causal four-lap LONG_RUN without a future
    # backfill. The first three are RACE_PACE: in band, on a worn tyre, and not
    # yet inside a qualifying run.
    rows += _rows([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 102, "life": 8}, {"time": 102, "life": 9},
    ], driver="BBB")
    output = classify_practice_laps(pd.DataFrame(rows))
    assert set(LAP_CLASSES) <= set(output["practice_lap_class"])
    assert len(output) == len(rows)
    assert output["practice_lap_class"].notna().all()
    assert set(output["practice_lap_class"]).issubset(LAP_CLASSES)
    assert set(output["practice_lap_class_provenance"]) == {LAP_CLASS_PROVENANCE}


def test_pit_evidence_precedes_metadata_quality_then_invalid_and_interrupted():
    labels = _labels(_rows([
        {"time": 100, "life": 1, "pin": 1, "pout": 1, "accurate": False, "status": "12"},
        {"time": 100, "life": 2, "pout": 2, "status": "12"},
        {"time": 100, "life": 3, "pin": 3, "pout": 3},
        {"time": 100, "life": 4, "pout": 4},
    ]))
    assert labels == ["IN_LAP", "OUT_LAP", "IN_LAP", "OUT_LAP"]


def test_out_and_in_lap_reconciliation_uses_pin_pout_and_previous_pin():
    rows = _rows([
        {"time": 100, "life": 6, "pout": 10},
        {"time": 101, "life": 7, "pin": 20},
        {"time": 102, "life": 8},
    ])
    assert _labels(rows) == ["OUT_LAP", "IN_LAP", "OUT_LAP"]


def test_invalid_and_interrupted_handling_breaks_a_long_run():
    rows = _rows([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 101, "life": 8, "accurate": False},
        {"time": 101, "life": 9, "status": "167"},
        {"time": 101, "life": 10}, {"time": 101, "life": 11},
        {"time": 101, "life": 12},
    ])
    labels = _labels(rows)
    assert labels[2:4] == ["INVALID", "INTERRUPTED"]
    assert "LONG_RUN" not in labels


def test_push_relaxation_keeps_107_valid_and_is_inclusive_at_108_percent():
    labels = _labels(_rows([
        {"time": 100, "life": 6},
        {"time": 107, "life": 5},
        {"time": 108, "life": 5},
        {"time": 108.001, "life": 5},
    ]))
    assert labels == ["RACE_PACE", "PUSH", "PUSH", "UNKNOWN"]


def test_long_run_boundary_requires_four_laps_strictly_increasing_life_and_spread_below_3_percent():
    labels = _labels(_rows([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 102, "life": 8}, {"time": 102, "life": 9},
        {"time": 103, "life": 10},  # exactly 3%; resets rather than qualifies
    ]))
    assert labels[:3] == ["RACE_PACE", "RACE_PACE", "RACE_PACE"]
    assert labels[3] == "LONG_RUN"
    # Exactly 3% resets the run; the lap is still in band on a worn tyre.
    assert labels[4] == "RACE_PACE"


def test_push_wins_over_an_overlapping_long_run_by_configured_precedence():
    labels = _labels(_rows([
        {"time": 100, "life": 1}, {"time": 101, "life": 2},
        {"time": 101, "life": 3}, {"time": 101, "life": 4},
    ]))
    assert labels[-1] == "PUSH"


def test_cooldown_is_only_immediately_after_push_and_strictly_above_115_percent():
    labels = _labels(_rows([
        {"time": 100, "life": 6}, {"time": 107, "life": 5},
        {"time": 115, "life": 6}, {"time": 116, "life": 7},
        {"time": 116, "life": 8},
    ]))
    assert labels[1] == "PUSH"
    assert labels[2:] == ["UNKNOWN", "UNKNOWN", "UNKNOWN"]


def test_long_runs_do_not_leak_across_drivers_or_sessions():
    rows = []
    rows += _rows([{ "time": 100, "life": 6}, {"time": 101, "life": 7}, {"time": 102, "life": 8}], driver="AAA")
    rows += _rows([{ "time": 102, "life": 9}], driver="BBB")
    rows += _rows([{ "time": 102, "life": 9}], driver="AAA", session="Practice 1b")
    output = classify_practice_laps(pd.DataFrame(rows))
    assert "LONG_RUN" not in set(output["practice_lap_class"])


def test_long_run_is_not_retroactively_backfilled_from_a_future_lap():
    prefix = _rows([
        {"time": 100, "life": 6}, {"time": 101, "life": 7}, {"time": 102, "life": 8},
    ])
    extended = [*prefix, *_rows([{ "time": 102, "life": 9}])[0:1]]
    # The appended helper row must retain lap 4, rather than restart at one.
    extended[-1]["lap"] = 4
    assert _labels(prefix) == _labels(extended)[:3] == ["RACE_PACE", "RACE_PACE", "RACE_PACE"]
    assert _labels(extended)[3] == "LONG_RUN"


def test_one_unknown_bridge_is_allowed_only_when_its_actual_values_hold_run_conditions():
    # Laps 1--3 are provisional UNKNOWN while a causal run is forming. Lap 3
    # is the one bridge into lap 4: it is green, adjacent, increasing in life,
    # and its 101 s time keeps the complete actual run within 3%.
    labels = _labels(_rows([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 101, "life": 8}, {"time": 102, "life": 9},
    ]))
    assert labels == ["RACE_PACE", "RACE_PACE", "RACE_PACE", "LONG_RUN"]
    # A slow unknown-looking lap cannot be bridged: its actual time destroys
    # the spread and starts a fresh candidate sequence.
    labels = _labels(_rows([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 105, "life": 8}, {"time": 101, "life": 9},
    ], driver="BBB"))
    assert "LONG_RUN" not in labels


def test_configuration_carries_exact_required_thresholds_and_complete_precedence():
    config = load_lap_classification_config()
    assert config.push_session_best_ratio_max == pytest.approx(1.08)
    assert config.push_tyre_life_laps_max == 5
    assert config.long_run_min_consecutive_laps == 4
    assert config.long_run_lap_time_spread_ratio_max_exclusive == pytest.approx(0.03)
    assert config.long_run_max_unknown_bridge_laps == 1
    assert config.precedence[:4] == ("IN_LAP", "OUT_LAP", "INVALID", "INTERRUPTED")
    assert set(config.precedence) == set(LAP_CLASSES)


def test_module_import_is_cpu_only():
    module = importlib.import_module("trackshift.track.lap_classifier")
    # v2 adds RACE_PACE. The version is pinned here on purpose: the label set is
    # a contract, and widening it must be a deliberate edit in two places.
    assert module.LAP_CLASSIFIER_SCHEMA_VERSION == "m01_practice_lap_class_v2"
    assert "torch" not in sys.modules


def test_race_pace_covers_in_band_laps_on_a_tyre_older_than_the_push_limit():
    """The section 9 gap this label closes.

    A lap inside the PUSH time band but on a tyre past the PUSH life limit is
    ordinary race-pace running. Before RACE_PACE existed it fell to UNKNOWN,
    which claimed the lap's character could not be determined when both the
    lap time and the tyre life say plainly what it is.
    """
    labels = _labels(_rows([
        {"time": 100, "life": 5},    # PUSH: in band, fresh tyre
        {"time": 104, "life": 9},    # RACE_PACE: in band, worn tyre
        {"time": 112, "life": 10},   # UNKNOWN: 108-115% dead band
        {"time": 130, "life": 11},   # >115% but not after a PUSH -> UNKNOWN
    ]))
    assert labels == ["PUSH", "RACE_PACE", "UNKNOWN", "UNKNOWN"]


def test_race_pace_never_outranks_a_qualifying_long_run():
    """Precedence: sustained consistent running is the stronger evidence."""
    labels = _labels(_rows([
        {"time": 100, "life": 6}, {"time": 101, "life": 7},
        {"time": 101, "life": 8}, {"time": 102, "life": 9},
    ]))
    assert labels[3] == "LONG_RUN", "a lap inside a qualifying run must not read as RACE_PACE"


def test_race_pace_requires_a_worn_tyre_not_merely_a_slow_lap():
    """A fresh-tyre lap in band is PUSH; RACE_PACE must not shadow it."""
    labels = _labels(_rows([{"time": 100, "life": 1}, {"time": 103, "life": 2}]))
    assert labels == ["PUSH", "PUSH"]
