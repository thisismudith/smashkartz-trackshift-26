"""CP-09 tests for C2 segment baselines."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.track.api import build_segment_baselines, residual_at_use_time  # noqa: E402


def row(value: float, *, year: int = 2026, driver: str = "HAM", team: str = "Ferrari",
        circuit: str = "monaco", eligible: bool = True, **extra):
    return {
        "year": year, "event": "Monaco Grand Prix", "circuit": circuit, "segment_id": 7,
        "driver": driver, "team": team, "normal_race_model_eligible": eligible,
        "segment_time_s": value, "exit_speed_kmh": 200.0 + value,
        "brake_onset_m": 300.0 + value, "full_throttle_fraction": 0.5 + value / 100,
        "max_speed_kmh": 250.0 + value,
        **extra,
    }


def build(rows, **kwargs):
    return build_segment_baselines(
        rows,
        minimum_samples={"driver": 3, "team": 4, "field": 5},
        **kwargs,
    )


def test_medians_counts_and_validity_flags_are_retained():
    rows = [row(value) for value in (1.0, 2.0, 3.0)] + [
        row(4.0, driver="LEC"), row(5.0, driver="LEC"),
    ]
    result = build(rows)
    ham = next(item for item in result.driver if item["driver"] == "HAM")
    team = next(item for item in result.team if item["team"] == "Ferrari")
    field = result.field[0]
    assert ham["segment_time_s_median"] == 2.0
    assert ham["segment_time_s_n"] == 3
    assert ham["n"] == 3 and ham["baseline_valid"] is True
    assert team["n"] == 5 and team["baseline_valid"] is True
    assert field["n"] == 5 and field["baseline_valid"] is True
    # LEC's thin row remains inspectable instead of disappearing.
    lec = next(item for item in result.driver if item["driver"] == "LEC")
    assert lec["n"] == 2 and lec["baseline_valid"] is False


def test_c7_eligibility_is_applied_before_every_group():
    result = build([row(1.0), row(100.0, eligible=False)])
    field = result.field[0]
    assert field["segment_time_s_median"] == 1.0
    assert field["n"] == 1
    assert result.excluded_rows_by_reason == {"NOT_NORMAL_RACE_MODEL_ELIGIBLE": 1}
    assert result.c7_source == "materialized_c7"


def test_regulation_eras_are_never_pooled():
    result = build([row(10.0, year=2025), row(30.0, year=2026)])
    assert {(item["year_group"], item["segment_time_s_median"]) for item in result.field} == {
        ("drs_era", 10.0), ("2026", 30.0),
    }


def test_british_gp_is_held_out_unless_the_explicit_final_replay_option_is_used():
    british = row(10.0, circuit="british", event="British Grand Prix")
    development = build([british])
    assert development.field == []
    assert development.excluded_rows_by_reason == {"BRITISH_GP_HELD_OUT": 1}
    final_replay = build([british], include_british_gp=True)
    assert len(final_replay.field) == 1


def test_legacy_track_status_bridge_is_explicit_and_conservative():
    legacy_green = row(2.0)
    del legacy_green["normal_race_model_eligible"]
    legacy_green.update({"track_status": "1", "is_accurate": True})
    legacy_compound = dict(legacy_green, track_status="12")
    with pytest.raises(ValueError, match="allow_legacy"):
        build([legacy_green])
    result = build([legacy_green, legacy_compound], allow_legacy_c7_track_status_bridge=True)
    assert result.c7_source == "legacy_track_status_bridge_v1"
    assert result.eligible_source_rows == 1
    assert result.excluded_rows_by_reason == {"NOT_NORMAL_RACE_MODEL_ELIGIBLE": 1}


def test_missing_c1_metric_is_null_and_explained_without_fake_values():
    source = row(2.0)
    del source["brake_onset_m"]
    baseline = build([source]).field[0]
    assert baseline["brake_onset_m_median"] is None
    assert baseline["brake_onset_m_n"] == 0
    unavailable = baseline["unavailable_metrics"]["brake_onset_m"]
    assert unavailable["value"] is None
    assert unavailable["unit"] == "m" and unavailable["reason"]


def test_residuals_are_computed_at_use_time_and_center_on_the_driver_median():
    result = build([row(value) for value in (1.0, 2.0, 3.0)])
    baseline = result.driver[0]
    residuals = [residual_at_use_time(value, baseline["segment_time_s_median"], baseline_valid=baseline["baseline_valid"])
                 for value in (1.0, 2.0, 3.0)]
    assert residuals == [-1.0, 0.0, 1.0]
    assert sorted(residuals)[1] == 0.0
    assert not any("residual" in name for name in baseline)
    assert residual_at_use_time(2.0, 2.0, baseline_valid=False) is None

def test_baselines_write_to_parquet_when_no_metric_is_missing(tmp_path):
    """The case that used to break the write: every C1 metric resolves.

    unavailable_metrics is then empty for every row, and Arrow cannot write a
    struct with no child fields. This is the normal case once C1 is complete,
    so it must be the one that is exercised.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_baselines", ROOT / "scripts" / "features" / "build_baselines.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = build([row(value) for value in (1.0, 2.0, 3.0, 4.0, 5.0)])
    assert all(not item["unavailable_metrics"] for item in result.field), (
        "fixture no longer exercises the empty case"
    )

    written = module._write_table(tmp_path, "field", result.field)

    import pandas as pd

    frame = pd.read_parquet(written)
    assert len(frame) == len(result.field)
    assert frame["unavailable_metrics"].tolist() == ["{}"] * len(frame)


def test_parquet_write_survives_a_partially_missing_metric(tmp_path):
    """And the mixed case still records which metric was absent, and why."""
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location(
        "build_baselines", ROOT / "scripts" / "features" / "build_baselines.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rows = []
    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        source = row(value)
        del source["brake_onset_m"]
        rows.append(source)
    result = build(rows)

    import pandas as pd

    frame = pd.read_parquet(module._write_table(tmp_path, "field", result.field))
    decoded = json.loads(frame["unavailable_metrics"].iloc[0])
    assert "brake_onset_m" in decoded
    assert decoded["brake_onset_m"]["unit"] == "m"
    assert decoded["brake_onset_m"]["reason"]
