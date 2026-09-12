"""Causal M30 tyre-degradation and tyre-normalised pace overlay.

This module deliberately produces pace context, not a physical tyre sensor.
Every value uses only the current completed lap and preceding retained laps in
the same driver/session/circuit/compound/reconstructed stint.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Mapping

import pandas as pd
import yaml

from trackshift.race_context import enrich_race_context

TYRE_PACE_SCHEMA_VERSION = "m30_tyre_pace_overlay_v1"
TYRE_PACE_PROVENANCE = "DERIVED"
C1_KEY_COLUMNS = (
    "year", "event", "session", "driver", "lap", "segment_id", "geometry_version", "boundary_hash",
)
LAP_KEY_COLUMNS = ("year", "event", "session", "driver", "lap")
OVERLAY_COLUMNS = (
    *C1_KEY_COLUMNS,
    # Observed lap context is repeated deliberately so consumers can group the
    # immutable overlay without joining back to the source lake.
    "tyre_compound",
    "tyre_life_laps",
    "tyre_stint_id",
    "tyre_stint_reset_reason",
    "tyre_pace_retained_lap",
    "tyre_degradation_proxy_s",
    "tyre_degradation_proxy_reason",
    "tyre_degradation_proxy_provenance",
    "tyre_normalized_pace_s",
    "tyre_normalized_pace_reason",
    "tyre_normalized_pace_provenance",
    "tyre_pace_schema_version",
)


class TyrePaceError(ValueError):
    """Raised when C1/lake input does not meet the M30 contract."""


@dataclass(frozen=True)
class TyrePaceConfig:
    schema_version: str
    producer_version: str
    initial_baseline_retained_laps: int
    rolling_retained_laps: int
    normalized_minimum_prior_samples: int
    normalized_similar_tyre_life_laps: int
    fuel_context_status: str
    fuel_context_note: str


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "tyre_pace.yaml"


def load_tyre_pace_config(path: Path | None = None) -> TyrePaceConfig:
    source = path or default_config_path()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise TyrePaceError(f"cannot read M30 configuration: {source}") from exc
    if not isinstance(raw, Mapping) or raw.get("provenance") != TYRE_PACE_PROVENANCE:
        raise TyrePaceError("M30 configuration must be DERIVED and mapping-shaped")
    proxy = raw.get("proxy") or {}
    normalized = raw.get("normalised_pace") or {}
    fuel = raw.get("fuel_context") or {}
    config = TyrePaceConfig(
        schema_version=str(raw.get("schema_version") or ""),
        producer_version=str(raw.get("producer_version") or ""),
        initial_baseline_retained_laps=int(proxy["initial_baseline_retained_laps"]),
        rolling_retained_laps=int(proxy["rolling_retained_laps"]),
        normalized_minimum_prior_samples=int(normalized["minimum_prior_samples"]),
        normalized_similar_tyre_life_laps=int(normalized["similar_tyre_life_laps"]),
        fuel_context_status=str(fuel.get("status") or ""),
        fuel_context_note=str(fuel.get("note") or ""),
    )
    if (
        not config.schema_version or not config.producer_version
        or config.initial_baseline_retained_laps != 3
        or config.rolling_retained_laps != 5
        or config.normalized_minimum_prior_samples < 1
        or config.normalized_similar_tyre_life_laps < 0
        or config.fuel_context_status != "UNAVAILABLE_C5"
    ):
        raise TyrePaceError("invalid M30 configuration")
    return config


def _set(value: Any) -> bool:
    return not pd.isna(value)


def _true(value: Any) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _positive(value: Any) -> bool:
    return _set(value) and float(value) > 0.0


def _same(left: Any, right: Any) -> bool:
    """Equality where two source-null metadata values mean unchanged, not reset."""
    return (pd.isna(left) and pd.isna(right)) or left == right


def _green_state(status: Any) -> str:
    """C7 accepts a normalized state; raw concatenated status fails closed."""
    return "GREEN" if _set(status) and str(status) == "1" else "UNKNOWN"


def _pit_state(row: Mapping[str, Any]) -> str:
    pin, pout = _set(row.get("pit_in_session_s")), _set(row.get("pit_out_session_s"))
    if pin and pout:
        return "PIT_LANE"
    if pin:
        return "PIT_IN"
    if pout:
        return "PIT_OUT"
    return "ON_TRACK"


def derive_lap_c7_context(laps: pd.DataFrame) -> pd.DataFrame:
    """Use the portable C7 normalizer on one row per lap, fail-closed.

    C1 currently lacks materialized C7 fields and pit timestamps. The builder
    supplies them from the matching telemetry lake then calls C7 rather than
    duplicating race-control/pit transition semantics.
    """
    needed = {*LAP_KEY_COLUMNS, "lap_start_session_s", "track_status", "pit_in_session_s", "pit_out_session_s"}
    missing = sorted(needed - set(laps.columns))
    if missing:
        raise TyrePaceError(f"lap context missing columns: {', '.join(missing)}")
    rows = []
    for row in laps.loc[:, list(needed)].to_dict("records"):
        rows.append({
            **row,
            "session_time_s": row.get("lap_start_session_s"),
            "race_control_state": _green_state(row.get("track_status")),
            "pit_state": _pit_state(row),
        })
    return pd.DataFrame(enrich_race_context(rows, transition_guard_rows=1))


def _lap_reason(row: Mapping[str, Any]) -> str | None:
    if row.get("normal_race_model_eligible") is not True:
        return "C7_NORMAL_RACE_INELIGIBLE"
    if not _true(row.get("is_accurate")) or _true(row.get("lap_deleted")):
        return "INVALID_TIMING_METADATA"
    if not _positive(row.get("lap_time_s")):
        return "MISSING_OR_NONPOSITIVE_LAP_TIME"
    if not _positive(row.get("tyre_life_laps")):
        return "MISSING_OR_NONPOSITIVE_TYRE_LIFE"
    if not _set(row.get("tyre_compound")):
        return "MISSING_TYRE_COMPOUND"
    return None


def _boundary_reason(row: Mapping[str, Any], previous: Mapping[str, Any] | None) -> str | None:
    if previous is None:
        return "SESSION_START"
    if int(row["lap"]) != int(previous["lap"]) + 1:
        return "LAP_NUMBER_GAP"
    if _set(row.get("pit_in_session_s")) or _set(row.get("pit_out_session_s")):
        return "PIT_METADATA"
    if _set(previous.get("pit_in_session_s")) or _set(previous.get("pit_out_session_s")):
        return "PIT_METADATA"
    if row.get("race_control_transition_flag") is True or previous.get("race_control_transition_flag") is True:
        return "RACE_CONTROL_TRANSITION"
    if row.get("pit_transition_flag") is True or previous.get("pit_transition_flag") is True:
        return "PIT_TRANSITION"
    if not _same(row.get("stint"), previous.get("stint")):
        return "SOURCE_STINT_CHANGE"
    if not _same(row.get("tyre_compound"), previous.get("tyre_compound")):
        return "TYRE_COMPOUND_CHANGE"
    if not _same(row.get("tyre_is_new"), previous.get("tyre_is_new")):
        return "TYRE_NEWNESS_CHANGE"
    return None


def _validate_inputs(segments: pd.DataFrame, laps: pd.DataFrame) -> None:
    c1_missing = sorted(set(C1_KEY_COLUMNS) - set(segments.columns))
    lap_needed = {
        *LAP_KEY_COLUMNS, "lap_time_s", "tyre_life_laps", "tyre_compound", "tyre_is_new", "stint",
        "pit_in_session_s", "pit_out_session_s", "is_accurate", "lap_deleted", "track_status", "lap_start_session_s",
    }
    lap_missing = sorted(lap_needed - set(laps.columns))
    if c1_missing or lap_missing:
        raise TyrePaceError(f"M30 missing C1={c1_missing} lake={lap_missing}")
    if segments.duplicated(list(C1_KEY_COLUMNS)).any():
        raise TyrePaceError("M30 C1 input has duplicate segment keys")
    if laps.duplicated(list(LAP_KEY_COLUMNS)).any():
        raise TyrePaceError("M30 lake input has duplicate lap keys")


def build_tyre_pace_overlay(
    segments: pd.DataFrame,
    laps: pd.DataFrame,
    *,
    circuit: str,
    config: TyrePaceConfig | None = None,
) -> pd.DataFrame:
    """Build an immutable C1-keyed M30 overlay using no future observations."""
    _validate_inputs(segments, laps)
    cfg = config or load_tyre_pace_config()
    lap_context = derive_lap_c7_context(laps)
    joined_laps = laps.merge(
        lap_context[[*LAP_KEY_COLUMNS, "normal_race_model_eligible", "race_control_transition_flag", "pit_transition_flag"]],
        on=list(LAP_KEY_COLUMNS), validate="one_to_one",
    ).sort_values(["year", "event", "session", "driver", "lap"], kind="stable")
    grouped_segments = {
        key: group.sort_values("segment_id", kind="stable")
        for key, group in segments.groupby(list(LAP_KEY_COLUMNS), sort=False)
    }
    output: list[dict[str, Any]] = []

    for group_key, group in joined_laps.groupby(["year", "event", "session", "driver"], sort=False):
        previous: dict[str, Any] | None = None
        retained: list[dict[str, Any]] = []
        segment_history: dict[int, list[tuple[float, float]]] = {}
        stint_index = 0
        for lap_row in group.to_dict("records"):
            reason = _lap_reason(lap_row)
            boundary = _boundary_reason(lap_row, previous)
            # A timing-invalid lap is a conservative boundary: neither rolling
            # nor normalised-pace history crosses a known unusable observation.
            if reason == "INVALID_TIMING_METADATA" and boundary is None:
                boundary = "INVALID_TIMING_METADATA"
            if boundary is not None:
                stint_index += 1
                retained = []
                segment_history = {}
            stint_id = f"{lap_row['year']}_{circuit}_{lap_row['event']}_{lap_row['session']}_{lap_row['driver']}_S{stint_index:02d}"
            retained_lap = reason is None
            if retained_lap:
                retained.append(lap_row)
                initial = [float(item["lap_time_s"]) for item in retained[:cfg.initial_baseline_retained_laps]]
                if len(initial) < cfg.initial_baseline_retained_laps:
                    proxy, proxy_reason = None, "INSUFFICIENT_INITIAL_GREEN_LAPS"
                else:
                    rolling = [float(item["lap_time_s"]) for item in retained[-cfg.rolling_retained_laps:]]
                    proxy, proxy_reason = float(median(rolling) - median(initial)), None
            else:
                proxy, proxy_reason = None, reason

            segment_block = grouped_segments.get(tuple(lap_row[key] for key in LAP_KEY_COLUMNS))
            if segment_block is None:
                previous = lap_row
                continue
            life = float(lap_row["tyre_life_laps"]) if _positive(lap_row["tyre_life_laps"]) else None
            for segment in segment_block.to_dict("records"):
                segment_id = int(segment["segment_id"])
                value = segment.get("segment_time_s_offline")
                pace, pace_reason = None, None
                if not retained_lap:
                    pace_reason = reason
                elif not _positive(value):
                    pace_reason = "MISSING_SEGMENT_TIME"
                else:
                    history = segment_history.setdefault(segment_id, [])
                    similar = [time for prior_life, time in history if abs(prior_life - life) <= cfg.normalized_similar_tyre_life_laps]
                    if len(similar) < cfg.normalized_minimum_prior_samples:
                        pace_reason = "INSUFFICIENT_CAUSAL_SIMILAR_LIFE_HISTORY"
                    else:
                        pace = float(float(value) - median(similar))
                    # Append only after calculating the current residual; this
                    # makes the normalizer invariant to future laps/stints.
                    history.append((life, float(value)))
                output.append({
                    **{key: segment[key] for key in C1_KEY_COLUMNS},
                    "tyre_compound": lap_row.get("tyre_compound"),
                    "tyre_life_laps": lap_row.get("tyre_life_laps"),
                    "tyre_stint_id": stint_id,
                    "tyre_stint_reset_reason": boundary,
                    "tyre_pace_retained_lap": retained_lap,
                    "tyre_degradation_proxy_s": proxy,
                    "tyre_degradation_proxy_reason": proxy_reason,
                    "tyre_degradation_proxy_provenance": TYRE_PACE_PROVENANCE,
                    "tyre_normalized_pace_s": pace,
                    "tyre_normalized_pace_reason": pace_reason,
                    "tyre_normalized_pace_provenance": TYRE_PACE_PROVENANCE,
                    "tyre_pace_schema_version": TYRE_PACE_SCHEMA_VERSION,
                })
            previous = lap_row
    result = pd.DataFrame(output)
    if len(result) != len(segments):
        raise AssertionError("M30 output must remain one-to-one with C1 segments")
    return result.loc[:, list(OVERLAY_COLUMNS)]
