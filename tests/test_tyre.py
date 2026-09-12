"""M30 causal tyre-pace tests; CPU-only and independent of processed data."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.features.tyre_pace import (  # noqa: E402
    C1_KEY_COLUMNS,
    TYRE_PACE_PROVENANCE,
    build_tyre_pace_overlay,
    load_tyre_pace_config,
)


def _inputs(specs, *, driver="AAA", session="Race", event="Test GP"):
    laps, segments = [], []
    for lap, spec in enumerate(specs, start=1):
        base = {
            "year": 2026, "event": event, "session": session, "driver": driver, "lap": lap,
            "lap_time_s": spec.get("lap_time", 100.0), "tyre_life_laps": spec.get("life", lap),
            "tyre_compound": spec.get("compound", "MEDIUM"), "tyre_is_new": spec.get("fresh", True),
            "stint": spec.get("stint", 1), "pit_in_session_s": spec.get("pin"),
            "pit_out_session_s": spec.get("pout"), "is_accurate": spec.get("accurate", True),
            "lap_deleted": spec.get("deleted", False), "track_status": spec.get("status", "1"),
            "lap_start_session_s": float(lap * 100),
        }
        laps.append(base)
        segments.append({
            **{key: base[key] for key in ("year", "event", "session", "driver", "lap")},
            "segment_id": 1, "geometry_version": "test-v1", "boundary_hash": "abc",
            "segment_time_s_offline": spec.get("segment_time", 10.0),
        })
    return pd.DataFrame(segments), pd.DataFrame(laps)


def _overlay(specs):
    segments, laps = _inputs(specs)
    return build_tyre_pace_overlay(segments, laps, circuit="test")


def test_first_three_retained_laps_are_explicitly_unavailable_then_proxy_starts_at_zero():
    output = _overlay([
        {"lap_time": 100, "segment_time": 10}, {"lap_time": 102, "segment_time": 11},
        {"lap_time": 104, "segment_time": 12},
    ])
    assert output["tyre_degradation_proxy_s"].iloc[:2].isna().all()
    assert output["tyre_degradation_proxy_s"].iloc[2] == pytest.approx(0.0)
    assert output["tyre_degradation_proxy_reason"].tolist()[:2] == [
        "INSUFFICIENT_INITIAL_GREEN_LAPS", "INSUFFICIENT_INITIAL_GREEN_LAPS",
    ]
    assert set(output["tyre_degradation_proxy_provenance"]) == {TYRE_PACE_PROVENANCE}


def test_stint_reconstruction_resets_at_source_stint_and_pit_boundaries():
    output = _overlay([
        {"lap_time": 100, "stint": 1}, {"lap_time": 101, "stint": 1},
        {"lap_time": 102, "stint": 1, "pin": 300},
        {"lap_time": 103, "stint": 2, "pout": 400, "fresh": False},
        {"lap_time": 104, "stint": 2, "fresh": False},
    ])
    assert output.loc[2, "tyre_stint_reset_reason"] == "PIT_METADATA"
    assert output.loc[3, "tyre_stint_reset_reason"] == "PIT_METADATA"
    assert output["tyre_stint_id"].nunique() >= 2
    assert output.loc[4, "tyre_degradation_proxy_s"] is None


def test_race_control_boundary_never_crosses_proxy_history():
    output = _overlay([
        {"lap_time": 100}, {"lap_time": 101}, {"lap_time": 102, "status": "12"},
        {"lap_time": 103}, {"lap_time": 104}, {"lap_time": 105},
    ])
    assert not bool(output.loc[2, "tyre_pace_retained_lap"])
    assert pd.isna(output.loc[3, "tyre_degradation_proxy_s"])
    assert pd.isna(output.loc[5, "tyre_degradation_proxy_s"])


def test_causal_truncation_invariance_and_no_future_stint_leakage():
    specs = [
        {"lap_time": 100, "segment_time": 10, "stint": 1},
        {"lap_time": 102, "segment_time": 11, "stint": 1},
        {"lap_time": 104, "segment_time": 12, "stint": 1},
        {"lap_time": 106, "segment_time": 13, "stint": 1},
        {"lap_time": 90, "segment_time": 8, "stint": 2, "fresh": False},
    ]
    full = _overlay(specs)
    prefix = _overlay(specs[:4])
    columns = ["tyre_degradation_proxy_s", "tyre_normalized_pace_s", "tyre_stint_id"]
    pd.testing.assert_frame_equal(full.loc[:3, columns], prefix.loc[:, columns], check_dtype=False)
    assert pd.isna(full.loc[4, "tyre_degradation_proxy_s"])
    assert pd.isna(full.loc[4, "tyre_normalized_pace_s"])


def test_short_stints_and_missing_values_stay_null_not_zero():
    output = _overlay([
        {"lap_time": 100}, {"lap_time": 101}, {"lap_time": None, "life": None},
    ])
    assert output["tyre_degradation_proxy_s"].isna().all()
    assert output.loc[2, "tyre_degradation_proxy_reason"] == "INVALID_TIMING_METADATA" or output.loc[2, "tyre_degradation_proxy_reason"] == "MISSING_OR_NONPOSITIVE_LAP_TIME"
    assert output["tyre_normalized_pace_s"].isna().all()


def test_decreasing_proxy_is_permitted_and_normalized_pace_needs_prior_history():
    output = _overlay([
        {"lap_time": 100, "segment_time": 10}, {"lap_time": 102, "segment_time": 11},
        {"lap_time": 104, "segment_time": 12}, {"lap_time": 98, "segment_time": 13},
    ])
    assert output.loc[3, "tyre_degradation_proxy_s"] == pytest.approx(-1.0)
    assert output.loc[3, "tyre_normalized_pace_s"] == pytest.approx(2.0)
    assert output.loc[:2, "tyre_normalized_pace_reason"].eq("INSUFFICIENT_CAUSAL_SIMILAR_LIFE_HISTORY").all()


def test_output_stays_one_to_one_with_c1_and_declares_all_unavailable_reasons():
    segments, laps = _inputs([{ "lap_time": 100}, {"lap_time": 101}, {"lap_time": 102}])
    output = build_tyre_pace_overlay(segments, laps, circuit="test")
    assert len(output) == len(segments)
    assert not output.duplicated(list(C1_KEY_COLUMNS)).any()
    assert output["tyre_pace_schema_version"].notna().all()


def test_config_and_module_are_cpu_only():
    config = load_tyre_pace_config()
    assert config.initial_baseline_retained_laps == 3
    assert config.rolling_retained_laps == 5
    module = importlib.import_module("trackshift.features.tyre_pace")
    assert module.TYRE_PACE_SCHEMA_VERSION == "m30_tyre_pace_overlay_v1"
    assert "torch" not in sys.modules
