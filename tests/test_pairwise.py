"""CP-04 causal M06 pairwise-feature tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.features.api import build_battle_episodes, build_pairwise_features  # noqa: E402


def battle_row(index: int, *, gap_s: float | None = 1.0, gap_m: float = 80.0,
               speed_kmh: float = 250.0, battle_id: str = "B1", **extra):
    return {
        "year": 2026, "event": "EVT", "session": "Sprint", "lap": 1,
        "segment_id": index, "segment_index": index, "battle_id": battle_id,
        "attacker": "HAM", "defender": "ANT", "entry_distance_m": index * 100.0,
        "entry_lap_elapsed_s": index * 2.0, "lap_start_session_s": 100.0,
        "entry_speed_kmh": speed_kmh, "gap_ahead_m_entry": gap_m,
        "time_gap_entry_s": gap_s, "tyre_compound": "MEDIUM", "tyre_life_laps": 8.0,
        "normal_race_model_eligible": True, "race_control_transition_flag": False,
        "pit_transition_flag": False,
        **extra,
    }


def defender_row(index: int, *, speed_kmh: float = 240.0, life: float = 12.0, **extra):
    return {
        "year": 2026, "event": "EVT", "session": "Sprint", "lap": 1,
        "segment_id": index, "driver": "ANT", "entry_speed_kmh": speed_kmh,
        "tyre_compound": "HARD", "tyre_life_laps": life, **extra,
    }


def build(rows, defenders=None):
    defenders = defenders or [defender_row(row["segment_id"]) for row in rows]
    return build_pairwise_features(rows, defenders)


def test_time_and_distance_gaps_remain_distinct():
    output = build([battle_row(1, gap_s=0.8, gap_m=63.0)]).rows[0]
    assert output["time_gap_entry_s"] == 0.8
    assert output["distance_gap_entry_m"] == 63.0
    missing_time = build([battle_row(1, gap_s=None, gap_m=63.0)]).rows[0]
    assert missing_time["time_gap_entry_s"] is None
    quantity = missing_time["unavailable_quantities"]["time_gap_entry_s"]
    assert quantity["unit"] == "s" and "not substituted" in quantity["reason"]


def test_relative_speed_sign_is_positive_when_attacker_is_faster():
    output = build([battle_row(1, speed_kmh=252.0)], [defender_row(1, speed_kmh=234.0)]).rows[0]
    assert output["relative_speed_to_ahead_mps"] == 5.0


def test_trailing_window_uses_current_and_preceding_same_battle_rows_only():
    rows = [
        battle_row(1, gap_s=1.2, speed_kmh=244.0),
        battle_row(2, gap_s=1.0, speed_kmh=248.0),
        battle_row(3, gap_s=0.8, speed_kmh=252.0),
        battle_row(4, gap_s=0.6, speed_kmh=256.0),
    ]
    defenders = [defender_row(index, speed_kmh=240.0) for index in range(1, 5)]
    output = build(rows, defenders).rows
    # At row three the future 4th row cannot affect the mean.
    assert output[2]["relative_speed_to_ahead_trailing_mean_3_mps"] == pytest.approx((4.0 + 8.0 + 12.0) / 3.6 / 3.0)
    # At row four the first row has rolled out of the fixed trailing window.
    assert output[3]["time_gap_entry_trailing_mean_3_s"] == pytest.approx(0.8)


def test_every_rolling_live_feature_is_truncation_invariant():
    rows = [battle_row(index, gap_s=1.4 - index / 10, gap_m=90 - index * 3, speed_kmh=240 + index * 4) for index in range(1, 6)]
    defenders = [defender_row(index, speed_kmh=235 + index) for index in range(1, 6)]
    full = build(rows, defenders).rows
    truncated = build(rows[:3], defenders[:3]).rows
    rolling = [
        "relative_speed_to_ahead_trailing_mean_3_mps",
        "time_gap_entry_trailing_mean_3_s",
        "distance_gap_entry_trailing_mean_3_m",
        "gap_rate_ahead_trailing_mean_3_s_per_s",
    ]
    for name in rolling:
        assert full[2][name] == truncated[2][name], name


@pytest.mark.parametrize("transition_field", ["pit_transition_flag", "race_control_transition_flag"])
def test_history_never_crosses_battle_or_c7_transition_boundary(transition_field):
    first = battle_row(1, gap_s=1.0, speed_kmh=270.0, battle_id="B1")
    transition = battle_row(2, gap_s=0.8, speed_kmh=275.0, battle_id="B1", **{transition_field: True})
    after_transition = battle_row(3, gap_s=0.7, speed_kmh=245.0, battle_id="B1")
    second_battle = battle_row(4, gap_s=0.9, speed_kmh=246.0, battle_id="B2")
    defenders = [defender_row(1, speed_kmh=240.0), defender_row(2, speed_kmh=240.0), defender_row(3, speed_kmh=240.0), defender_row(4, speed_kmh=240.0)]
    result = build([first, transition, after_transition, second_battle], defenders)
    assert result.excluded_rows_by_reason == {"C7_TRANSITION_ROW": 1}
    by_battle = {row["battle_id"]: row for row in result.rows}
    assert by_battle["B2"]["relative_speed_to_ahead_trailing_mean_3_mps"] == pytest.approx((246.0 - 240.0) / 3.6)
    after = next(row for row in result.rows if row["segment_index"] == 3)
    assert after["relative_speed_to_ahead_trailing_mean_3_mps"] == pytest.approx((245.0 - 240.0) / 3.6)


def test_missing_c2_c5_values_are_explicit_api_quantities_not_defaults():
    source = battle_row(1, brake_fraction_offline=0.9)
    output = build([source]).rows[0]
    unavailable = output["unavailable_quantities"]
    for name in (
        "baseline_residual_delta_s", "fuel_load_delta_kg_est", "fuel_load_delta_uncertainty_kg",
        "ers_energy_delta_kj_est", "ers_energy_delta_uncertainty_kj",
    ):
        assert output[name] is None
        assert unavailable[name]["value"] is None
        assert unavailable[name]["reason"]
        assert unavailable[name]["provenance"] in {"DERIVED", "INFERRED", "SIMULATED"}
    # A whole-segment brake fraction is only known after segment completion.
    assert output["braking_intensity_delta"] is None
    assert "OFFLINE_ONLY" in unavailable["braking_intensity_delta"]["reason"]


def test_real_c8_builder_rows_are_accepted_by_the_public_pairwise_boundary():
    c1_rows = [
        {**battle_row(1), "driver": "HAM", "driver_ahead": "ANT"},
        {**defender_row(1), "driver_ahead": None, "normal_race_model_eligible": True,
         "race_control_transition_flag": False, "pit_transition_flag": False, "entry_distance_m": 100.0,
         "entry_lap_elapsed_s": 2.0, "gap_ahead_m_entry": None},
    ]
    c8 = build_battle_episodes(c1_rows)
    output = build_pairwise_features(c8.battle_rows, c1_rows)
    assert len(c8.battle_rows) == len(output.rows) == 1
    assert output.rows[0]["battle_id"] == c8.battle_rows[0]["battle_id"]
