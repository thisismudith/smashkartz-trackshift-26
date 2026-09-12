"""Focused M33 / CP-06 weather-overlay tests; no GPU dependencies required."""
from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.registry import dataset, feature  # noqa: E402
from trackshift.track.weather import (  # noqa: E402
    WeatherSamples,
    align_weather_to_time,
    build_weather_overlay,
    derive_track_relative_weather,
    join_weather_overlay,
    load_weather_samples,
    resolve_weather_path,
)


def _samples() -> WeatherSamples:
    return WeatherSamples((
        {"wT": 10.0, "wAT": 20.0, "wTT": 30.0, "wH": 50.0, "wP": 1013.0, "wR": False, "wWD": 0.0, "wWS": 4.0},
        {"wT": 20.0, "wAT": 22.0, "wTT": 32.0, "wH": 60.0, "wP": 1015.0, "wR": True, "wWD": 20.0, "wWS": 6.0},
    ))


def _segments(times=(10.0, 15.0, 20.0)) -> pd.DataFrame:
    return pd.DataFrame({
        "year": [2026] * len(times), "event": ["Australian Grand Prix"] * len(times),
        "session": ["Race"] * len(times), "driver": ["AAA"] * len(times), "lap": [1] * len(times),
        "segment_id": list(range(1, len(times) + 1)), "geometry_version": ["test-v1"] * len(times),
        "boundary_hash": ["abc"] * len(times), "track_heading_deg": [0.0] * len(times),
        "segment_entry_session_time_s": list(times),
    })


def test_air_density_proxy_is_realistic():
    weather = derive_track_relative_weather({"wAT": 25.0, "wP": 1013.0, "wWD": 0.0, "wWS": 4.0, "wR": False}, 0.0)
    assert 1.0 <= weather["air_density_proxy"] <= 1.35


def test_wind_projection_preserves_speed_magnitude():
    weather = derive_track_relative_weather({"wAT": 20.0, "wP": 1013.0, "wWD": 37.0, "wWS": 8.25, "wR": False}, 121.0)
    assert weather["wind_head_component_mps"] ** 2 + weather["wind_cross_component_mps"] ** 2 == pytest.approx(8.25 ** 2, abs=1e-6)


def test_heading_equal_to_wind_from_means_headwind():
    weather = derive_track_relative_weather({"wAT": 20.0, "wP": 1013.0, "wWD": 75.0, "wWS": 7.0, "wR": False}, 75.0)
    assert weather["wind_head_component_mps"] == pytest.approx(7.0)
    assert weather["wind_cross_component_mps"] == pytest.approx(0.0, abs=1e-12)


def test_heading_changes_create_expected_wind_sign_changes_around_lap():
    values = [derive_track_relative_weather({"wAT": 20.0, "wP": 1013.0, "wWD": 0.0, "wWS": 5.0, "wR": False}, heading)
              for heading in (0.0, 90.0, 180.0, 270.0)]
    assert [math.copysign(1, item["wind_head_component_mps"]) for item in values] == [1.0, 1.0, -1.0, -1.0]
    assert values[1]["wind_cross_component_mps"] < 0.0
    assert values[3]["wind_cross_component_mps"] > 0.0


def test_interpolation_and_pre_first_extrapolation_are_explicit():
    first = align_weather_to_time(_samples(), 5.0)
    middle = align_weather_to_time(_samples(), 15.0)
    final = align_weather_to_time(_samples(), 25.0)
    assert first["wAT"] == 20.0
    assert first["weather_extrapolated"] is True
    assert first["weather_post_final_sample"] is False
    assert middle["wAT"] == pytest.approx(21.0)
    assert middle["wWS"] == pytest.approx(5.0)
    assert middle["wR"] is False  # a boolean is held, never linearly interpolated
    assert middle["weather_alignment_status"] == "INTERPOLATED"
    assert final["wAT"] == 22.0
    assert final["weather_post_final_sample"] is True


def test_missing_weather_is_explicit_and_never_fabricated(tmp_path):
    samples = load_weather_samples(tmp_path / "absent-weather.json")
    overlay = build_weather_overlay(_segments(), samples)
    assert set(overlay["weather_alignment_status"]) == {"MISSING_WEATHER"}
    assert set(overlay["weather_missing_reason"]) == {"MISSING_WEATHER_FILE"}
    assert not overlay["weather_available"].any()
    for column in ("air_temperature_c", "wind_speed_mps", "wind_head_component_mps", "air_density_proxy", "wet_track_flag"):
        assert overlay[column].isna().all(), column


def test_missing_raw_channel_has_an_explicit_reason_not_a_silent_nan():
    incomplete = WeatherSamples((
        {"wT": 10.0, "wAT": None, "wTT": 30.0, "wH": 50.0, "wP": 1013.0, "wR": False, "wWD": 0.0, "wWS": 4.0},
    ))
    overlay = build_weather_overlay(_segments((10.0,)), incomplete)
    assert pd.isna(overlay.loc[0, "air_temperature_c"])
    assert "MISSING_WAT" in overlay.loc[0, "weather_missing_reason"]
    assert pd.isna(overlay.loc[0, "air_density_proxy"])


def test_public_join_boundary_is_one_to_one_and_does_not_mutate_c1():
    c1 = _segments((10.0, 15.0))
    overlay = build_weather_overlay(c1, _samples())
    joined = join_weather_overlay(c1, overlay)
    assert "wind_head_component_mps" in joined
    assert "wind_head_component_mps" not in c1
    assert len(joined) == len(c1)


def test_raw_weather_loader_and_preferred_tracinginsights_layout(tmp_path):
    preferred = tmp_path / "tracinginsights" / "2026" / "Elsewhere GP" / "Race"
    fallback = tmp_path / "2026" / "Elsewhere GP" / "Race"
    preferred.mkdir(parents=True)
    fallback.mkdir(parents=True)
    preferred_weather = preferred / "weather.json"
    fallback_weather = fallback / "weather.json"
    preferred_weather.write_text(json.dumps({"wT": [1], "wAT": [20], "wTT": [25], "wH": [50], "wP": [1013], "wR": [0], "wWD": [90], "wWS": [3]}), encoding="utf-8")
    fallback_weather.write_text("{}", encoding="utf-8")
    assert resolve_weather_path(tmp_path, 2026, "Elsewhere GP", "Race") == preferred_weather
    assert load_weather_samples(preferred_weather).samples[0]["wWS"] == 3.0


def test_raw_wind_bearing_is_not_live_safe_but_components_are():
    assert feature("wind_direction_deg")["provenance"] == "OBSERVED"
    assert feature("wind_direction_deg")["live_safe"] is False
    assert feature("wind_speed_mps")["live_safe"] is False
    assert feature("wind_head_component_mps")["live_safe"] is True
    assert feature("wind_cross_component_mps")["provenance"] == "DERIVED"
    assert dataset("segment_weather_overlay")["schema_version"] == "m33_segment_weather_overlay_v1"


def test_weather_module_import_is_cpu_only():
    module = importlib.import_module("trackshift.track.weather")
    assert module.WEATHER_OVERLAY_SCHEMA_VERSION == "m33_segment_weather_overlay_v1"
    assert "torch" not in sys.modules
