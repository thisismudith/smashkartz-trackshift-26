"""Tests for lap-metadata joining from laptimes.json.

This existed as a silent failure: laptimes.json is columnar, the loader expected
a list of records, and the type check returned an all-None dict with no error. So
every tyre, position, track-status and lap-time value in the lake was null while
the pipeline reported success.

A silent null is the worst failure mode here, because downstream code treats the
column as "not available for this season" rather than "broken". These tests pin
the shapes and the sentinel handling so it cannot recur.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.raw_loader import (  # noqa: E402
    METADATA_ALIASES,
    RawLap,
    _clean,
    _records,
    load_lap_metadata,
)


# ------------------------------------------------------------------ _records
def test_records_handles_columnar_dict():
    """The real laptimes.json shape: a dict of equal-length lists."""
    payload = {"lap": [1, 2, 3], "compound": ["SOFT", "SOFT", "MEDIUM"]}
    rows = _records(payload, "laptimes")
    assert len(rows) == 3
    assert rows[2] == {"lap": 3, "compound": "MEDIUM"}


def test_records_handles_list_of_records():
    payload = [{"lap": 1}, {"lap": 2}]
    assert _records(payload, "laptimes") == [{"lap": 1}, {"lap": 2}]


def test_records_handles_list_under_a_key():
    payload = {"laptimes": [{"lap": 1}]}
    assert _records(payload, "laptimes") == [{"lap": 1}]


def test_records_broadcasts_scalars_alongside_columns():
    """A session-wide constant must reach every row, not be dropped."""
    payload = {"lap": [1, 2], "team": "Ferrari"}
    rows = _records(payload, "laptimes")
    assert [r["team"] for r in rows] == ["Ferrari", "Ferrari"]


def test_records_pads_ragged_columns_rather_than_truncating():
    """Losing rows silently would repeat the original bug in a new form."""
    payload = {"lap": [1, 2, 3], "compound": ["SOFT"]}
    rows = _records(payload, "laptimes")
    assert len(rows) == 3
    assert rows[1]["compound"] is None


@pytest.mark.parametrize("payload", [{}, [], {"laptimes": []}, "text", 42, None])
def test_records_returns_empty_for_unusable_input(payload):
    assert _records(payload, "laptimes") == []


# -------------------------------------------------------------------- _clean
@pytest.mark.parametrize("value", [None, "None", "", "nan", "NaN"])
def test_clean_maps_sentinels_to_none(value):
    """The source writes the string 'None', which is truthy and would read as a
    real value."""
    assert _clean(value) is None


@pytest.mark.parametrize("value", [0, 0.0, False, "0", "MEDIUM", 5524.292])
def test_clean_preserves_real_values_including_falsey_ones(value):
    """0 and False are meaningful here (lap 0 offsets, is_accurate False)."""
    assert _clean(value) == value


# ------------------------------------------------------- against the real data
RACE = ROOT / "data" / "raw" / "tracinginsights" / "2026" / "British Grand Prix" / "Race" / "HAM"
requires_mirror = pytest.mark.skipif(
    not (RACE / "laptimes.json").exists(),
    reason="raw mirror not present; see CHECKPOINTS_TANVEER.md CP-01",
)


@requires_mirror
def test_real_laptimes_is_columnar():
    """Pins the assumption this fix rests on. If upstream switches to records,
    _records still handles it, but the change should be visible."""
    payload = json.loads((RACE / "laptimes.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "laptimes" not in payload
    assert isinstance(payload.get("lap"), list)


@requires_mirror
def test_real_metadata_is_not_all_null():
    """The regression itself: this returned every field as None."""
    md = load_lap_metadata(RawLap("2026", "British Grand Prix", "Race", "HAM", 10, RACE / "10_tel.json"))
    populated = {k: v for k, v in md.items() if v is not None}
    assert populated, "metadata join produced nothing -- the columnar bug is back"
    # More than half the canonical fields should resolve on a normal race lap.
    assert len(populated) > len(METADATA_ALIASES) / 2


@requires_mirror
def test_real_metadata_core_fields():
    md = load_lap_metadata(RawLap("2026", "British Grand Prix", "Race", "HAM", 10, RACE / "10_tel.json"))
    assert md["tyre_compound"] in {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"}
    assert isinstance(md["tyre_life_laps"], (int, float))
    assert isinstance(md["stint"], int)
    assert md["lap_time_s"] > 0
    assert md["team"]
    assert md["track_status"] is not None


@requires_mirror
def test_metadata_is_per_lap_not_the_first_row():
    """A columnar transpose done wrong yields the same row for every lap."""
    a = load_lap_metadata(RawLap("2026", "British Grand Prix", "Race", "HAM", 10, RACE / "10_tel.json"))
    b = load_lap_metadata(RawLap("2026", "British Grand Prix", "Race", "HAM", 30, RACE / "30_tel.json"))
    assert a["lap_time_s"] != b["lap_time_s"] or a["tyre_life_laps"] != b["tyre_life_laps"]


@requires_mirror
def test_pit_lap_reports_a_pit_time_and_others_do_not():
    """Pit entry/exit drives the C7 eligibility gate, so it has to be real."""
    pit = load_lap_metadata(RawLap("2026", "British Grand Prix", "Race", "HAM", 23, RACE / "23_tel.json"))
    green = load_lap_metadata(RawLap("2026", "British Grand Prix", "Race", "HAM", 10, RACE / "10_tel.json"))
    assert pit["pit_in_session_s"] is not None
    assert green["pit_in_session_s"] is None
    assert green["pit_out_session_s"] is None


@requires_mirror
def test_missing_laptimes_file_returns_all_none_not_an_exception():
    md = load_lap_metadata(RawLap("2026", "Nowhere", "Race", "XXX", 1, ROOT / "does" / "not" / "exist" / "1_tel.json"))
    assert set(md) == set(METADATA_ALIASES)
    assert all(v is None for v in md.values())


@requires_mirror
def test_unknown_lap_number_returns_all_none():
    md = load_lap_metadata(RawLap("2026", "British Grand Prix", "Race", "HAM", 9999, RACE / "9999_tel.json"))
    assert all(v is None for v in md.values())
