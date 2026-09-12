"""Deterministic, causal Practice-lap classification (M01 / CP-07).

The classifier accepts one metadata row per lap and returns a versioned overlay;
it never changes the 20 m telemetry lake.  Decisions for a lap use only that
lap and earlier laps from its own driver/session.  In particular, LONG_RUN does
not retrospectively label the first three laps after a fourth lap arrives.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import yaml

LAP_CLASSIFIER_SCHEMA_VERSION = "m01_practice_lap_class_v1"
LAP_CLASS_PROVENANCE = "DERIVED"
LAP_CLASSES = (
    "PUSH", "LONG_RUN", "COOLDOWN", "OUT_LAP", "IN_LAP", "INTERRUPTED", "INVALID", "UNKNOWN",
)
KEY_COLUMNS = ("year", "event", "session", "driver", "lap")
REQUIRED_INPUT_COLUMNS = (
    *KEY_COLUMNS,
    "lap_time_s",
    "tyre_life_laps",
    "pit_in_session_s",
    "pit_out_session_s",
    "is_accurate",
    "lap_deleted",
    "track_status",
)
OVERLAY_COLUMNS = (
    *KEY_COLUMNS,
    "practice_lap_class",
    "practice_lap_class_provenance",
    "practice_lap_class_reason",
    "practice_lap_classifier_version",
    "driver_session_best_lap_time_s",
)


class LapClassificationError(ValueError):
    """Raised when an M01 input or configuration violates its contract."""


@dataclass(frozen=True)
class LapClassificationConfig:
    """Validated M01 configuration loaded from ``lap_classification.yaml``."""

    schema_version: str
    classifier_version: str
    push_session_best_ratio_max: float
    push_tyre_life_laps_max: int
    cooldown_session_best_ratio_min_exclusive: float
    long_run_min_consecutive_laps: int
    long_run_lap_time_spread_ratio_max_exclusive: float
    long_run_max_unknown_bridge_laps: int
    precedence: tuple[str, ...]
    provenance: str = LAP_CLASS_PROVENANCE


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "lap_classification.yaml"


def load_lap_classification_config(path: Path | None = None) -> LapClassificationConfig:
    """Load and validate the versioned threshold/precedence configuration."""
    source = path or default_config_path()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise LapClassificationError(f"cannot read M01 configuration: {source}") from exc
    if not isinstance(raw, Mapping):
        raise LapClassificationError("M01 configuration must be a mapping")
    thresholds = raw.get("thresholds")
    if not isinstance(thresholds, Mapping):
        raise LapClassificationError("M01 configuration requires a thresholds mapping")
    precedence = tuple(raw.get("precedence") or ())
    if set(precedence) != set(LAP_CLASSES) or len(precedence) != len(LAP_CLASSES):
        raise LapClassificationError("M01 precedence must contain every label exactly once")
    if raw.get("provenance") != LAP_CLASS_PROVENANCE:
        raise LapClassificationError("M01 output provenance must be DERIVED")
    config = LapClassificationConfig(
        schema_version=str(raw.get("schema_version") or ""),
        classifier_version=str(raw.get("classifier_version") or ""),
        push_session_best_ratio_max=float(thresholds["push_session_best_ratio_max"]),
        push_tyre_life_laps_max=int(thresholds["push_tyre_life_laps_max"]),
        cooldown_session_best_ratio_min_exclusive=float(thresholds["cooldown_session_best_ratio_min_exclusive"]),
        long_run_min_consecutive_laps=int(thresholds["long_run_min_consecutive_laps"]),
        long_run_lap_time_spread_ratio_max_exclusive=float(thresholds["long_run_lap_time_spread_ratio_max_exclusive"]),
        long_run_max_unknown_bridge_laps=int((raw.get("long_run") or {})["max_unknown_bridge_laps"]),
        precedence=precedence,
    )
    if not config.schema_version or not config.classifier_version:
        raise LapClassificationError("M01 configuration needs schema_version and classifier_version")
    if not 1.0 < config.push_session_best_ratio_max:
        raise LapClassificationError("push ratio must exceed 1")
    if (
        config.long_run_min_consecutive_laps < 2
        or not 0 < config.long_run_lap_time_spread_ratio_max_exclusive < 1
        or config.long_run_max_unknown_bridge_laps != 1
    ):
        raise LapClassificationError("invalid M01 long-run thresholds")
    return config


def _is_set(value: Any) -> bool:
    return not pd.isna(value)


def _is_true(value: Any) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _is_green(status: Any) -> bool:
    """Return true only for an explicit, all-green concatenated status string."""
    if pd.isna(status):
        return False
    text = str(status)
    return bool(text) and all(char == "1" for char in text)


def _valid_positive_number(value: Any) -> bool:
    return not pd.isna(value) and float(value) > 0.0


def _strictly_above_ratio(value: float, reference: float, threshold: float) -> bool:
    """Compare a documented strict boundary without binary-float surprises."""
    ratio = value / reference
    return ratio > threshold and not math.isclose(ratio, threshold, rel_tol=1e-12, abs_tol=1e-12)


def _validate_laps(laps: pd.DataFrame) -> None:
    missing = sorted(set(REQUIRED_INPUT_COLUMNS) - set(laps.columns))
    if missing:
        raise LapClassificationError(f"M01 input missing required columns: {', '.join(missing)}")
    duplicated = laps.duplicated(list(KEY_COLUMNS), keep=False)
    if duplicated.any():
        keys = laps.loc[duplicated, list(KEY_COLUMNS)].head(3).to_dict("records")
        raise LapClassificationError(f"M01 input must contain one row per lap; duplicate keys include {keys}")


def _candidate_run_lap(row: Mapping[str, Any], preliminary: str) -> bool:
    return (
        preliminary == "UNKNOWN"
        and _valid_positive_number(row["lap_time_s"])
        and _valid_positive_number(row["tyre_life_laps"])
    )


def classify_practice_laps(
    laps: pd.DataFrame,
    config: LapClassificationConfig | None = None,
) -> pd.DataFrame:
    """Return one deterministic M01 label per supplied lap.

    Input must be a one-row-per-lap metadata table, normally reduced from the
    20 m lake. The output is only an overlay, keyed by the five stable lake
    identifiers. The input frame is never modified.
    """
    _validate_laps(laps)
    cfg = config or load_lap_classification_config()
    ordered = laps.loc[:, list(REQUIRED_INPUT_COLUMNS)].copy()
    ordered["_input_order"] = range(len(ordered))
    ordered = ordered.sort_values(list(KEY_COLUMNS), kind="stable")
    output: list[dict[str, Any]] = []

    for _, group in ordered.groupby(["year", "event", "session", "driver"], sort=False, dropna=False):
        running_best: float | None = None
        previous_pin = False
        previous_label: str | None = None
        run: list[tuple[int, float, float]] = []

        for row in group.to_dict("records"):
            lap_time = row["lap_time_s"]
            tyre_life = row["tyre_life_laps"]
            pin = _is_set(row["pit_in_session_s"])
            pout = _is_set(row["pit_out_session_s"])
            invalid = _is_true(row["lap_deleted"]) or not _is_true(row["is_accurate"])
            green = _is_green(row["track_status"])

            # The current lap is allowed to establish the best because M01 is
            # evaluated when that lap completes. Pit, invalid and interrupted
            # laps are deliberately excluded from this clean reference.
            best_candidate = (
                not invalid and green and not pin and not pout and not previous_pin
                and _valid_positive_number(lap_time)
            )
            if best_candidate:
                current_time = float(lap_time)
                running_best = current_time if running_best is None else min(running_best, current_time)

            # Pit evidence wins only for presentation/metadata reconciliation.
            # `_candidate_run_lap` accepts only the resulting UNKNOWN state, so
            # neither pit state can seed or bridge a long-run sequence.
            if pin:
                preliminary, reason = "IN_LAP", "pit_in_on_current_lap"
            elif pout or previous_pin:
                preliminary, reason = "OUT_LAP", "pit_out_on_current_lap" if pout else "previous_lap_had_pit_in"
            elif invalid:
                preliminary, reason = "INVALID", "lap_deleted_or_inaccurate"
            elif not green:
                preliminary, reason = "INTERRUPTED", "non_green_track_status"
            else:
                preliminary, reason = "UNKNOWN", "no_rule_matched"

            # Update the causal, same-driver/session candidate run. A
            # provisional UNKNOWN is the one permitted bridge only when that
            # very lap remains green, has increasing life and keeps the actual
            # run's spread below 3%. Every structural label resets immediately.
            # This is deliberately not a skip over an arbitrary slow lap.
            if _candidate_run_lap(row, preliminary):
                item = (int(row["lap"]), float(lap_time), float(tyre_life))
                contiguous = bool(run) and item[0] == run[-1][0] + 1
                increasing_life = bool(run) and item[2] > run[-1][2]
                if contiguous and increasing_life:
                    proposal = [*run, item]
                    times = [entry[1] for entry in proposal]
                    spread = (max(times) - min(times)) / min(times)
                    run = proposal if spread < cfg.long_run_lap_time_spread_ratio_max_exclusive else [item]
                else:
                    run = [item]
            else:
                run = []

            push = (
                preliminary == "UNKNOWN"
                and running_best is not None
                and _valid_positive_number(lap_time)
                and float(lap_time) <= running_best * cfg.push_session_best_ratio_max
                and _valid_positive_number(tyre_life)
                and float(tyre_life) <= cfg.push_tyre_life_laps_max
            )
            long_run = preliminary == "UNKNOWN" and len(run) >= cfg.long_run_min_consecutive_laps
            cooldown = (
                preliminary == "UNKNOWN"
                and running_best is not None
                and _valid_positive_number(lap_time)
                and _strictly_above_ratio(
                    float(lap_time), running_best, cfg.cooldown_session_best_ratio_min_exclusive,
                )
                and previous_label == "PUSH"
            )
            matches = {
                "INVALID": invalid,
                "INTERRUPTED": not green,
                "IN_LAP": pin,
                "OUT_LAP": pout or previous_pin,
                "PUSH": push,
                "LONG_RUN": long_run,
                "COOLDOWN": cooldown,
                "UNKNOWN": True,
            }
            label = next(label for label in cfg.precedence if matches[label])
            if label == "PUSH":
                reason = "within_causal_session_best_and_low_tyre_life"
            elif label == "LONG_RUN":
                reason = "causal_green_run_meets_length_life_and_spread"
            elif label == "COOLDOWN":
                reason = "slow_lap_immediately_after_push"

            output.append({
                **{key: row[key] for key in KEY_COLUMNS},
                "practice_lap_class": label,
                "practice_lap_class_provenance": cfg.provenance,
                "practice_lap_class_reason": reason,
                "practice_lap_classifier_version": cfg.classifier_version,
                "driver_session_best_lap_time_s": running_best,
                "_input_order": row["_input_order"],
            })
            previous_pin = pin
            previous_label = label

    result = pd.DataFrame(output).sort_values("_input_order", kind="stable").drop(columns="_input_order")
    if len(result) != len(laps) or result["practice_lap_class"].isna().any():
        raise AssertionError("M01 must emit exactly one non-null class per input lap")
    if not set(result["practice_lap_class"]).issubset(LAP_CLASSES):
        raise AssertionError("M01 emitted an unknown label")
    return result.loc[:, list(OVERLAY_COLUMNS)]


def label_counts(rows: pd.DataFrame) -> dict[str, int]:
    """Return a complete label histogram, including zero-count labels."""
    counts = rows["practice_lap_class"].value_counts()
    return {label: int(counts.get(label, 0)) for label in LAP_CLASSES}
