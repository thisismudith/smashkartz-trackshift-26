"""Tests for the held-out demo-event guard (CP-01).

A guard that fails open is worse than no guard: it makes an unprotected pipeline
look protected. These tests therefore concentrate on the ways it could silently
let rows through — spelling variants, missing columns, wrong scope — rather than
just the happy path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.guards import (  # noqa: E402
    DEMO_EVENT,
    DemoEventLeak,
    DemoScope,
    assert_demo_held_out,
    is_demo_row,
    normalise_event,
)

CLEAN = [
    {"year": "2024", "event": "Monaco Grand Prix"},
    {"year": "2025", "event": "Italian Grand Prix"},
]
DEMO_ROW = {"year": "2026", "event": "British Grand Prix"}
HISTORIC_SILVERSTONE = {"year": "2023", "event": "British Grand Prix"}


# --------------------------------------------------------------- core behaviour
def test_clean_rows_pass():
    assert_demo_held_out(CLEAN, "clean split")


def test_demo_row_raises():
    with pytest.raises(DemoEventLeak):
        assert_demo_held_out(CLEAN + [DEMO_ROW], "leaky split")


def test_empty_input_passes():
    assert_demo_held_out([], "empty split")


def test_error_names_the_context_and_the_count():
    """The message has to be actionable from a log line alone."""
    with pytest.raises(DemoEventLeak) as exc:
        assert_demo_held_out(CLEAN + [DEMO_ROW, DEMO_ROW], "pass model DETECTION training")
    msg = str(exc.value)
    assert "pass model DETECTION training" in msg
    assert "2 of 4" in msg
    assert "section 40" in msg


# --------------------------------------------------------------- scope semantics
def test_default_scope_keeps_historic_silverstone_trainable():
    """2022-2025 British GP is DRS-era training data, not the held-out event.

    This is the difference from splits.py and the reason the scope is explicit.
    """
    assert_demo_held_out([HISTORIC_SILVERSTONE], "historic silverstone")
    assert is_demo_row(HISTORIC_SILVERSTONE) is False
    assert is_demo_row(DEMO_ROW) is True


def test_track_scope_holds_out_every_season():
    with pytest.raises(DemoEventLeak):
        assert_demo_held_out([HISTORIC_SILVERSTONE], "leave-one-track-out", scope=DemoScope.TRACK)
    assert is_demo_row(HISTORIC_SILVERSTONE, DemoScope.TRACK) is True


def test_track_scope_still_ignores_other_events():
    assert_demo_held_out(CLEAN, "other events", scope=DemoScope.TRACK)


# ------------------------------------------------------- spelling / type variants
@pytest.mark.parametrize(
    "event",
    [
        "British Grand Prix",
        "british grand prix",
        "british_grand_prix",
        "BRITISH GRAND PRIX",
        "  British   Grand  Prix  ",
    ],
)
def test_event_spelling_variants_all_caught(event):
    """Directory names, partition keys and config files disagree on spelling.

    A guard that matched the raw string would leak on an underscore.
    """
    with pytest.raises(DemoEventLeak):
        assert_demo_held_out([{"year": "2026", "event": event}], "spelling variant")


@pytest.mark.parametrize("year", [2026, "2026", " 2026 "])
def test_year_accepts_int_and_padded_string(year):
    """Parquet partitions give strings, DataFrames often give ints."""
    with pytest.raises(DemoEventLeak):
        assert_demo_held_out([{"year": year, "event": "British Grand Prix"}], "year type")


def test_near_miss_event_is_not_matched():
    """Do not over-match: only the actual demo event is held out."""
    assert_demo_held_out([{"year": "2026", "event": "Austrian Grand Prix"}], "near miss")


# --------------------------------------------------------------- DataFrame path
def test_dataframe_is_accepted():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(CLEAN + [DEMO_ROW])
    with pytest.raises(DemoEventLeak):
        assert_demo_held_out(df, "dataframe split")


def test_clean_dataframe_passes():
    pd = pytest.importorskip("pandas")
    assert_demo_held_out(pd.DataFrame(CLEAN), "clean dataframe")


def test_dataframe_missing_columns_is_refused_not_ignored():
    """An unguardable frame must not be reported as clean."""
    pd = pytest.importorskip("pandas")
    with pytest.raises(ValueError, match="missing"):
        assert_demo_held_out(pd.DataFrame({"lap": [1, 2]}), "no year/event")


def test_dataframe_with_integer_year_column():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame([{"year": 2026, "event": "British Grand Prix"}])
    with pytest.raises(DemoEventLeak):
        assert_demo_held_out(df, "integer year column")


# ------------------------------------------------- agreement with splits.py (C9)
def test_normalisation_matches_splits_module():
    """Both modules must fold event names identically, or one can leak where the
    other blocks."""
    from trackshift.data import splits

    for value in ["British Grand Prix", "british_grand_prix", "  BRITISH  grand prix "]:
        assert normalise_event(value) == splits._normalise_event(value)


def test_documented_difference_from_splits_still_holds():
    """splits.py implements the TRACK reading unconditionally; this module
    defaults to EVENT_YEAR.

    This test does not assert either is right. It pins the difference so that if
    someone changes one, this fails and forces the other to be reconsidered
    rather than silently diverging.
    """
    from trackshift.data import splits

    # splits.py rejects historic Silverstone...
    with pytest.raises(ValueError):
        splits.assert_training_or_calibration_eligible([HISTORIC_SILVERSTONE], "training")

    # ...while this module's default permits it.
    assert_demo_held_out([HISTORIC_SILVERSTONE], "default scope")

    # Under TRACK scope the two agree.
    with pytest.raises(DemoEventLeak):
        assert_demo_held_out([HISTORIC_SILVERSTONE], "track scope", scope=DemoScope.TRACK)


def test_both_modules_agree_on_the_demo_row_itself():
    """Whatever the scope disagreement, the actual demo event must be blocked by
    both."""
    from trackshift.data import splits

    with pytest.raises(ValueError):
        splits.assert_training_or_calibration_eligible([DEMO_ROW], "training")
    with pytest.raises(DemoEventLeak):
        assert_demo_held_out([DEMO_ROW], "demo row")


def test_demo_constant_matches_models_md():
    assert DEMO_EVENT == {"year": "2026", "event": "British Grand Prix"}
