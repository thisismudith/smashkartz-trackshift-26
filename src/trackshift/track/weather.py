"""C1-keyed, track-relative weather overlays (M33 / CP-06).

Weather arrives as a small session-level time series, whereas C1 has one row
per car/lap/segment.  This module keeps that distinction explicit: it writes a
versioned overlay and a caller joins it to C1 at the public boundary.  It never
rewrites a C1 Parquet file in place.

``wWD`` is the direction the wind comes *from*.  It is retained for audit but
is deliberately not a live model feature; models consume its heading-relative
head and cross projections instead.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from math import atan2, cos, degrees, isfinite, radians, sin
from pathlib import Path
from typing import Any, Iterable, Mapping

WEATHER_OVERLAY_SCHEMA_VERSION = "m33_segment_weather_overlay_v1"
WEATHER_RAW_FIELDS = ("wT", "wAT", "wTT", "wH", "wP", "wR", "wWD", "wWS")
WEATHER_VALUE_FIELDS = ("wAT", "wTT", "wH", "wP", "wR", "wWD", "wWS")
WEATHER_KEY_COLUMNS = (
    "year", "event", "session", "driver", "lap", "segment_id",
    "geometry_version", "boundary_hash",
)

# All columns written by build_weather_overlay.  Keeping this here makes a
# schema addition intentional and lets the script check it against M31.
WEATHER_OVERLAY_COLUMNS = (
    *WEATHER_KEY_COLUMNS,
    "segment_entry_session_time_s",
    "weather_sample_time_before_s",
    "weather_sample_time_after_s",
    "weather_available",
    "weather_alignment_status",
    "weather_missing_reason",
    "weather_extrapolated",
    "weather_post_final_sample",
    "air_temperature_c",
    "track_temperature_c",
    "humidity_pct",
    "air_pressure_hpa",
    "rain_observed",
    "wind_speed_mps",
    "wind_direction_deg",
    "wind_head_component_mps",
    "wind_cross_component_mps",
    "air_density_proxy",
    "wet_track_flag",
)


@dataclass(frozen=True)
class WeatherSamples:
    """Validated, time-sorted raw weather samples for one session."""

    samples: tuple[dict[str, Any], ...]
    reason: str | None = None
    source_path: Path | None = None


class WeatherOverlayError(ValueError):
    """Raised when an overlay cannot safely be keyed or joined to C1."""


def resolve_raw_session_dir(raw_root: Path, year: int | str, event: str, session: str) -> Path:
    """Resolve either supported raw layout, preferring ``tracinginsights``.

    Some local mirrors live under ``data/raw/tracinginsights/<year>`` while
    earlier downloads live directly under ``data/raw/<year>``.  Prefer the
    former when the requested session is present, then use the latter as a
    compatible fallback.  Returning the preferred candidate on a miss gives a
    useful, deterministic missing-file path to the manifest.
    """
    year_text = str(year)
    preferred = raw_root / "tracinginsights" / year_text / event / session
    fallback = raw_root / year_text / event / session
    if preferred.is_dir():
        return preferred
    if fallback.is_dir():
        return fallback
    return preferred if (raw_root / "tracinginsights" / year_text).is_dir() else fallback


def resolve_weather_path(raw_root: Path, year: int | str, event: str, session: str) -> Path:
    """Return the preferred existing weather file across both raw layouts."""
    year_text = str(year)
    candidates = (
        raw_root / "tracinginsights" / year_text / event / session / "weather.json",
        raw_root / year_text / event / session / "weather.json",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return resolve_raw_session_dir(raw_root, year, event, session) / "weather.json"


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _rain(value: Any) -> bool | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "nan"}):
        return None
    if isinstance(value, str):
        if value.strip().lower() in {"false", "no", "off", "0"}:
            return False
        if value.strip().lower() in {"true", "yes", "on", "1"}:
            return True
    return bool(value)


def load_weather_samples(path: Path) -> WeatherSamples:
    """Read the columnar ``weather.json`` shape without manufacturing samples."""
    if not path.exists():
        return WeatherSamples((), "MISSING_WEATHER_FILE", path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return WeatherSamples((), "MALFORMED_WEATHER_JSON", path)
    if not isinstance(payload, Mapping):
        return WeatherSamples((), "INVALID_WEATHER_LAYOUT", path)
    times = payload.get("wT")
    if not isinstance(times, list) or not times:
        return WeatherSamples((), "MISSING_WEATHER_TIME", path)

    rows: list[dict[str, Any]] = []
    for index, raw_time in enumerate(times):
        time_s = _number(raw_time)
        if time_s is None:
            continue
        row: dict[str, Any] = {"wT": time_s}
        for name in WEATHER_VALUE_FIELDS:
            values = payload.get(name)
            raw_value = values[index] if isinstance(values, list) and index < len(values) else None
            row[name] = _rain(raw_value) if name == "wR" else _number(raw_value)
        rows.append(row)
    if not rows:
        return WeatherSamples((), "NO_VALID_WEATHER_TIME", path)

    # The feed should be chronological. Sorting and resolving a duplicate by
    # keeping its latest listed reading is deterministic and avoids np.interp's
    # undefined duplicate-x behaviour.
    deduped: dict[float, dict[str, Any]] = {row["wT"]: row for row in rows}
    return WeatherSamples(tuple(deduped[key] for key in sorted(deduped)), None, path)


def _interpolate_direction_deg(before: float, after: float, fraction: float) -> float:
    """Interpolate a compass bearing across 0/360 by the short arc."""
    before_x, before_y = cos(radians(before)), sin(radians(before))
    after_x, after_y = cos(radians(after)), sin(radians(after))
    x = before_x + fraction * (after_x - before_x)
    y = before_y + fraction * (after_y - before_y)
    if x == 0.0 and y == 0.0:  # exact antipodes: deterministic linear fallback
        return (before + fraction * ((after - before + 180.0) % 360.0 - 180.0)) % 360.0
    return degrees(atan2(y, x)) % 360.0


def _missing_reason(values: Mapping[str, Any], base: str | None = None) -> str | None:
    missing = [name.upper() for name in WEATHER_VALUE_FIELDS if values.get(name) is None]
    parts = ([base] if base else []) + (["MISSING_" + "_".join(missing)] if missing else [])
    return "|".join(parts) if parts else None


def align_weather_to_time(samples: WeatherSamples, session_time_s: Any) -> dict[str, Any]:
    """Causally explicit weather alignment for one segment entry time.

    Before the first observation the first reading is held and marked
    ``weather_extrapolated``.  After the final observation the final reading is
    held but separately marked ``weather_post_final_sample``.  Interior
    continuous fields are interpolated; rain is a contemporaneous step value
    from the preceding sample, never a fractional boolean.
    """
    time_s = _number(session_time_s)
    blank = {name: None for name in WEATHER_VALUE_FIELDS}
    if time_s is None:
        return {
            **blank, "weather_available": False, "weather_alignment_status": "MISSING_SEGMENT_ENTRY_TIME",
            "weather_missing_reason": "MISSING_SEGMENT_ENTRY_TIME", "weather_extrapolated": False,
            "weather_post_final_sample": False, "weather_sample_time_before_s": None,
            "weather_sample_time_after_s": None,
        }
    if not samples.samples:
        return {
            **blank, "weather_available": False, "weather_alignment_status": "MISSING_WEATHER",
            "weather_missing_reason": samples.reason or "MISSING_WEATHER", "weather_extrapolated": False,
            "weather_post_final_sample": False, "weather_sample_time_before_s": None,
            "weather_sample_time_after_s": None,
        }

    first, last = samples.samples[0], samples.samples[-1]
    if time_s < first["wT"]:
        values = {name: first[name] for name in WEATHER_VALUE_FIELDS}
        return {
            **values, "weather_available": True, "weather_alignment_status": "PRE_FIRST_EXTRAPOLATED",
            "weather_missing_reason": _missing_reason(values), "weather_extrapolated": True,
            "weather_post_final_sample": False, "weather_sample_time_before_s": first["wT"],
            "weather_sample_time_after_s": first["wT"],
        }
    if time_s > last["wT"]:
        values = {name: last[name] for name in WEATHER_VALUE_FIELDS}
        return {
            **values, "weather_available": True, "weather_alignment_status": "POST_FINAL_HELD",
            "weather_missing_reason": _missing_reason(values), "weather_extrapolated": False,
            "weather_post_final_sample": True, "weather_sample_time_before_s": last["wT"],
            "weather_sample_time_after_s": last["wT"],
        }

    for row in samples.samples:
        if time_s == row["wT"]:
            values = {name: row[name] for name in WEATHER_VALUE_FIELDS}
            return {
                **values, "weather_available": True, "weather_alignment_status": "MATCHED_SAMPLE",
                "weather_missing_reason": _missing_reason(values), "weather_extrapolated": False,
                "weather_post_final_sample": False, "weather_sample_time_before_s": row["wT"],
                "weather_sample_time_after_s": row["wT"],
            }

    before, after = first, last
    for candidate, successor in zip(samples.samples, samples.samples[1:]):
        if candidate["wT"] < time_s < successor["wT"]:
            before, after = candidate, successor
            break
    fraction = (time_s - before["wT"]) / (after["wT"] - before["wT"])
    values: dict[str, Any] = {}
    for name in WEATHER_VALUE_FIELDS:
        if name == "wR":
            values[name] = before[name]
            continue
        if before[name] is None or after[name] is None:
            values[name] = None
        elif name == "wWD":
            values[name] = _interpolate_direction_deg(before[name], after[name], fraction)
        else:
            values[name] = before[name] + fraction * (after[name] - before[name])
    return {
        **values, "weather_available": True, "weather_alignment_status": "INTERPOLATED",
        "weather_missing_reason": _missing_reason(values), "weather_extrapolated": False,
        "weather_post_final_sample": False, "weather_sample_time_before_s": before["wT"],
        "weather_sample_time_after_s": after["wT"],
    }


def derive_track_relative_weather(weather: Mapping[str, Any], track_heading_deg: Any) -> dict[str, Any]:
    """Project observed wind onto travel direction and derive air-density proxy."""
    heading = _number(track_heading_deg)
    direction = _number(weather.get("wWD"))
    speed = _number(weather.get("wWS"))
    pressure = _number(weather.get("wP"))
    air_temp = _number(weather.get("wAT"))
    if heading is None or direction is None or speed is None:
        head = cross = None
    else:
        relative = radians(direction - heading)
        head, cross = speed * cos(relative), speed * sin(relative)
    density = None if pressure is None or air_temp is None or air_temp <= -273.15 else (
        pressure * 100.0 / (287.05 * (air_temp + 273.15))
    )
    rain = weather.get("wR")
    return {
        "wind_head_component_mps": head,
        "wind_cross_component_mps": cross,
        "air_density_proxy": density,
        "wet_track_flag": None if rain is None else bool(rain),
    }


def segment_entry_session_time(row: Mapping[str, Any]) -> float | None:
    """Return C1's causal segment-entry session time across C1 schema revisions."""
    explicit = _number(row.get("segment_entry_session_time_s"))
    if explicit is not None:
        return explicit
    lap_start, elapsed = _number(row.get("lap_start_session_s")), _number(row.get("entry_lap_elapsed_s"))
    if lap_start is not None and elapsed is not None:
        return lap_start + elapsed
    # Later C1 revisions may materialise entry time as session_time_s.  It is
    # intentionally only a fallback: the current lake's same-named lap field
    # is a lap-end timestamp and must not override the causal calculation.
    return _number(row.get("session_time_s"))


def build_weather_overlay(segments, samples: WeatherSamples):
    """Build one M33 overlay frame from C1 rows and one session's weather."""
    import pandas as pd

    missing_keys = [key for key in WEATHER_KEY_COLUMNS if key not in segments.columns]
    if missing_keys:
        raise WeatherOverlayError(f"C1 rows lack deterministic weather-overlay key(s): {', '.join(missing_keys)}")
    if "track_heading_deg" not in segments.columns:
        raise WeatherOverlayError("C1 rows lack track_heading_deg required for wind projection")

    rows: list[dict[str, Any]] = []
    for source in segments.to_dict(orient="records"):
        entry_time = segment_entry_session_time(source)
        aligned = align_weather_to_time(samples, entry_time)
        derived = derive_track_relative_weather(aligned, source.get("track_heading_deg"))
        rows.append({
            **{key: source[key] for key in WEATHER_KEY_COLUMNS},
            "segment_entry_session_time_s": entry_time,
            "weather_sample_time_before_s": aligned["weather_sample_time_before_s"],
            "weather_sample_time_after_s": aligned["weather_sample_time_after_s"],
            "weather_available": aligned["weather_available"],
            "weather_alignment_status": aligned["weather_alignment_status"],
            "weather_missing_reason": aligned["weather_missing_reason"],
            "weather_extrapolated": aligned["weather_extrapolated"],
            "weather_post_final_sample": aligned["weather_post_final_sample"],
            "air_temperature_c": aligned["wAT"],
            "track_temperature_c": aligned["wTT"],
            "humidity_pct": aligned["wH"],
            "air_pressure_hpa": aligned["wP"],
            "rain_observed": aligned["wR"],
            "wind_speed_mps": aligned["wWS"],
            "wind_direction_deg": aligned["wWD"],
            **derived,
        })
    result = pd.DataFrame(rows, columns=WEATHER_OVERLAY_COLUMNS)
    if result.duplicated(list(WEATHER_KEY_COLUMNS)).any():
        raise WeatherOverlayError("C1 source contains duplicate weather-overlay keys")
    return result


def join_weather_overlay(segments, weather_overlay):
    """Join a validated M33 overlay to C1 without modifying either input frame."""
    missing = [key for key in WEATHER_KEY_COLUMNS if key not in segments.columns or key not in weather_overlay.columns]
    if missing:
        raise WeatherOverlayError(f"cannot join C1 and weather overlay; missing key(s): {', '.join(missing)}")
    if weather_overlay.duplicated(list(WEATHER_KEY_COLUMNS)).any():
        raise WeatherOverlayError("weather overlay has duplicate deterministic join keys")
    weather_fields = [field for field in WEATHER_OVERLAY_COLUMNS if field not in WEATHER_KEY_COLUMNS]
    return segments.merge(weather_overlay[[*WEATHER_KEY_COLUMNS, *weather_fields]], on=list(WEATHER_KEY_COLUMNS), how="left", validate="one_to_one")


def load_weather_overlay(path: Path):
    """Load a persisted overlay and enforce its public join-key contract."""
    import pandas as pd

    frame = pd.read_parquet(path)
    missing = [field for field in WEATHER_OVERLAY_COLUMNS if field not in frame.columns]
    if missing:
        raise WeatherOverlayError(f"weather overlay is not {WEATHER_OVERLAY_SCHEMA_VERSION}: missing {', '.join(missing)}")
    if frame.duplicated(list(WEATHER_KEY_COLUMNS)).any():
        raise WeatherOverlayError("weather overlay has duplicate deterministic join keys")
    return frame


__all__ = [
    "WEATHER_KEY_COLUMNS", "WEATHER_OVERLAY_COLUMNS", "WEATHER_OVERLAY_SCHEMA_VERSION", "WeatherOverlayError",
    "WeatherSamples", "align_weather_to_time", "build_weather_overlay", "derive_track_relative_weather",
    "join_weather_overlay", "load_weather_overlay", "load_weather_samples", "resolve_raw_session_dir",
    "resolve_weather_path", "segment_entry_session_time",
]
