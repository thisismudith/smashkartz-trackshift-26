"""CP-13 tyre, weather and geometry context on M07 opportunities.

The properties that matter are that a missing source yields a null rather than
a default, that the emitted schema is stable across events so the table loads as
one frame, and that geometry is resolved at the checkpoint's own distance rather
than per lap.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.features.opportunity_context import (
    CONTEXT_BOOL_FIELDS,
    CONTEXT_FIELDS,
    CONTEXT_NUMERIC_FIELDS,
    GEOMETRY_FIELDS,
    build_lap_context,
    build_segment_geometry,
    coerce_context_dtypes,
    pair_context,
)


def segments(rows=None):
    rows = rows or [
        # VER, lap 1: two segments tiling 0-1000 m, SOFT, 2 laps old.
        dict(session="Race", driver="VER", lap=1, start_distance_m=0.0,
             end_distance_m=500.0, corner_type="SLOW_LEFT", sector="S1",
             tyre_compound="SOFT", tyre_life_laps=2.0, team="Red Bull"),
        dict(session="Race", driver="VER", lap=1, start_distance_m=500.0,
             end_distance_m=1000.0, corner_type="FAST_RIGHT", sector="S2",
             tyre_compound="SOFT", tyre_life_laps=2.0, team="Red Bull"),
        # HAM, lap 1: HARD, 20 laps old.
        dict(session="Race", driver="HAM", lap=1, start_distance_m=0.0,
             end_distance_m=500.0, corner_type="SLOW_LEFT", sector="S1",
             tyre_compound="HARD", tyre_life_laps=20.0, team="Ferrari"),
    ]
    return pd.DataFrame(rows)


def weather(rows=None):
    rows = rows or [
        dict(session="Race", driver="VER", lap=1, wind_head_component_mps=2.0,
             wind_cross_component_mps=1.0, track_temperature_c=40.0, wet_track_flag=False),
        dict(session="Race", driver="VER", lap=1, wind_head_component_mps=4.0,
             wind_cross_component_mps=3.0, track_temperature_c=42.0, wet_track_flag=False),
    ]
    return pd.DataFrame(rows)


# --- lap context ------------------------------------------------------------

def test_tyre_state_is_taken_at_lap_entry_for_both_cars():
    context = build_lap_context(segments())
    out = pair_context(context, session="Race", lap=1,
                       attacker="VER", defender_code="HAM")
    assert out["attacker_tyre_compound"] == "SOFT"
    assert out["defender_tyre_compound"] == "HARD"
    assert out["attacker_tyre_life_laps"] == 2.0
    assert out["defender_tyre_life_laps"] == 20.0
    assert out["tyre_life_delta_laps"] == -18.0
    assert out["tyre_compound_pair"] == "SOFT|HARD"


def test_weather_is_averaged_over_the_lap_and_shared_by_both_cars():
    context = build_lap_context(segments(), weather())
    out = pair_context(context, session="Race", lap=1,
                       attacker="VER", defender_code="HAM")
    # The two cars are metres apart; weather is a property of the track.
    assert out["wind_head_component_mps"] == pytest.approx(3.0)
    assert out["wind_cross_component_mps"] == pytest.approx(2.0)
    assert out["track_temperature"] == pytest.approx(41.0)
    assert out["wet_track_flag"] is False


def test_a_lap_that_saw_rain_at_all_is_a_wet_lap():
    wet = weather()
    wet.loc[1, "wet_track_flag"] = True
    context = build_lap_context(segments(), wet)
    out = pair_context(context, session="Race", lap=1, attacker="VER", defender_code="HAM")
    assert out["wet_track_flag"] is True


def test_an_unknown_defender_contributes_nulls_never_the_attackers_values():
    """A fabricated compound would be a silent claim about a car's strategy."""
    context = build_lap_context(segments(), weather())
    out = pair_context(context, session="Race", lap=1,
                       attacker="VER", defender_code="NOBODY")
    assert out["defender_tyre_compound"] is None
    assert out["defender_tyre_life_laps"] is None
    assert out["tyre_life_delta_laps"] is None
    assert out["tyre_compound_pair"] is None
    assert out["attacker_tyre_compound"] == "SOFT"   # attacker unaffected


def test_a_missing_defender_code_is_handled_without_raising():
    context = build_lap_context(segments())
    out = pair_context(context, session="Race", lap=1, attacker="VER", defender_code=None)
    assert out["defender_tyre_compound"] is None


def test_the_emitted_schema_is_stable_when_no_weather_exists():
    """One weatherless circuit must not change the table's columns.

    The M07 loader refuses partitions that disagree on their columns -- the
    right guard -- so an event with no CP-06 overlay has to emit the same keys
    with nulls, not a narrower row.
    """
    with_weather = pair_context(build_lap_context(segments(), weather()),
                                session="Race", lap=1, attacker="VER", defender_code="HAM")
    without = pair_context(build_lap_context(segments(), None),
                           session="Race", lap=1, attacker="VER", defender_code="HAM")
    assert set(with_weather) == set(without) == set(CONTEXT_FIELDS)
    assert without["track_temperature"] is None
    assert without["wind_head_component_mps"] is None


def test_an_entirely_unknown_lap_still_emits_every_key():
    out = pair_context(build_lap_context(segments()), session="Race", lap=99,
                       attacker="VER", defender_code="HAM")
    assert set(out) == set(CONTEXT_FIELDS)
    assert all(value is None for value in out.values())


# --- geometry ---------------------------------------------------------------

def test_geometry_is_resolved_at_the_checkpoint_distance_not_per_lap():
    """Resolving per lap would attach a straight's geometry to a corner."""
    geometry = build_segment_geometry(segments())
    assert geometry.at("Race", "VER", 1, 100.0)["corner_type"] == "SLOW_LEFT"
    assert geometry.at("Race", "VER", 1, 700.0)["corner_type"] == "FAST_RIGHT"


def test_geometry_boundaries_and_the_final_segment():
    geometry = build_segment_geometry(segments())
    assert geometry.at("Race", "VER", 1, 500.0)["sector"] == "S2"
    # Past the final segment's end (resampling can stop short of the line).
    assert geometry.at("Race", "VER", 1, 1200.0)["corner_type"] == "FAST_RIGHT"
    # Before the first segment starts there is no segment.
    assert geometry.at("Race", "VER", 1, -50.0)["corner_type"] is None


def test_geometry_for_an_unknown_key_returns_every_field_as_null():
    geometry = build_segment_geometry(segments())
    out = geometry.at("Race", "NOBODY", 1, 100.0)
    assert set(out) == set(GEOMETRY_FIELDS)
    assert all(value is None for value in out.values())


# --- dtypes -----------------------------------------------------------------

def test_context_dtypes_survive_a_partition_with_no_weather():
    """An all-null column types as object and poisons the concatenation.

    The feature selector reads dtype as the strong categorical signal, so one
    weatherless circuit would otherwise turn track temperature and both wind
    components into categoricals across every event.
    """
    populated = coerce_context_dtypes(pd.DataFrame([{
        **{f: None for f in CONTEXT_FIELDS},
        "track_temperature": 40.0, "wind_head_component_mps": 2.0,
        "wind_cross_component_mps": 1.0, "wet_track_flag": False,
    }]))
    empty = coerce_context_dtypes(pd.DataFrame([{f: None for f in CONTEXT_FIELDS}]))

    for name in CONTEXT_NUMERIC_FIELDS:
        assert empty[name].dtype == "float64", name
    for name in CONTEXT_BOOL_FIELDS:
        assert str(empty[name].dtype) == "boolean", name

    combined = pd.concat([populated, empty], ignore_index=True)
    for name in CONTEXT_NUMERIC_FIELDS:
        assert combined[name].dtype.kind == "f", f"{name} became {combined[name].dtype}"


def test_an_absent_wet_flag_stays_unknown_rather_than_reading_as_dry():
    empty = coerce_context_dtypes(pd.DataFrame([{f: None for f in CONTEXT_FIELDS}]))
    assert empty["wet_track_flag"].isna().all()
    assert not (empty["wet_track_flag"] == False).any()  # noqa: E712
