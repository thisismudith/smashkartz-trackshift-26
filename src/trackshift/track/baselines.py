"""C2 segment-baseline construction (M04 / Tanveer CP-09).

The output is a small set of reference tables, never per-row residuals.
Consumers compute ``current - median`` at use time, which keeps C2 from
accidentally becoming a retrospective feature matrix.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Any, Iterable, Mapping

__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "BASELINE_LEVELS",
    "BASELINE_METRICS",
    "BaselineBuildResult",
    "assign_year_group",
    "build_segment_baselines",
    "residual_at_use_time",
]

BASELINE_SCHEMA_VERSION = "c2_segment_baselines_v1"
BASELINE_LEVELS = ("driver", "team", "field")

# The aliases permit the current C1 schema's explicit OFFLINE_ONLY summaries.
# Baselines are offline reference artifacts, so they may use those summaries;
# the values are not copied into a live feature row by this module.
BASELINE_METRICS: dict[str, tuple[str, ...]] = {
    "segment_time_s": ("segment_time_s", "segment_time_s_offline"),
    "exit_speed_kmh": ("exit_speed_kmh", "exit_speed_kmh_offline"),
    "brake_onset_m": ("brake_onset_m", "brake_onset_distance_m"),
    "full_throttle_fraction": ("full_throttle_fraction", "full_throttle_fraction_offline"),
    "max_speed_kmh": ("max_speed_kmh", "max_speed_kmh_offline"),
}
BASELINE_METRIC_UNITS = {
    "segment_time_s": "s",
    "exit_speed_kmh": "km/h",
    "brake_onset_m": "m",
    "full_throttle_fraction": "ratio",
    "max_speed_kmh": "km/h",
}


@dataclass(frozen=True)
class BaselineBuildResult:
    """The three C2 tables and audit counts from their shared source filter."""

    driver: list[dict[str, Any]]
    team: list[dict[str, Any]]
    field: list[dict[str, Any]]
    c7_source: str
    excluded_rows_by_reason: dict[str, int]
    eligible_source_rows: int


def _number(value: Any) -> float | None:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return None
    return candidate if isfinite(candidate) else None


def _first_number(row: Mapping[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        candidate = _number(row.get(name))
        if candidate is not None:
            return candidate
    return None


def assign_year_group(year: Any, year_groups: Mapping[str, Iterable[int]] | None = None) -> str:
    """Return the explicit regulation-era group; never silently pool eras."""
    try:
        numeric_year = int(year)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"year {year!r} cannot be assigned to a baseline era") from exc
    groups = year_groups or {"drs_era": (2022, 2023, 2024, 2025), "2026": (2026,)}
    for label, years in groups.items():
        if numeric_year in {int(item) for item in years}:
            return str(label)
    raise ValueError(f"year {numeric_year} has no configured baseline year_group")


def _is_british_gp(row: Mapping[str, Any]) -> bool:
    circuit = str(row.get("circuit", "")).strip().lower().replace("-", "_").replace(" ", "_")
    event = str(row.get("event", "")).strip().lower().replace("-", "_").replace(" ", "_")
    return circuit in {"british", "great_britain", "gbr"} or event in {"british_grand_prix", "great_british_grand_prix"}


def _bridge_eligible(row: Mapping[str, Any], bridge: Mapping[str, Any]) -> bool:
    if str(row.get("track_status")).strip() != str(bridge["required_track_status"]):
        return False
    return row.get("is_accurate") is True if bridge.get("require_is_accurate", True) else True


def _filter_rows(
    rows: Iterable[Mapping[str, Any]], *, allow_legacy_c7_track_status_bridge: bool,
    bridge: Mapping[str, Any], include_british_gp: bool, year_groups: Mapping[str, Iterable[int]],
) -> tuple[list[dict[str, Any]], str, dict[str, int]]:
    source = [dict(row) for row in rows]
    has_c7 = ["normal_race_model_eligible" in row for row in source]
    if not all(has_c7) and not allow_legacy_c7_track_status_bridge:
        raise ValueError(
            "C1 rows lack materialised C7 normal_race_model_eligible; rerun with "
            "allow_legacy_c7_track_status_bridge=True only for the documented conservative bridge"
        )
    c7_source = "materialized_c7" if all(has_c7) else (
        "materialized_c7_plus_legacy_track_status_bridge" if any(has_c7) else "legacy_track_status_bridge_v1"
    )
    kept: list[dict[str, Any]] = []
    excluded = Counter()
    for row in source:
        if _is_british_gp(row) and not include_british_gp:
            excluded["BRITISH_GP_HELD_OUT"] += 1
            continue
        try:
            row["year_group"] = assign_year_group(row.get("year"), year_groups)
        except ValueError:
            excluded["UNSUPPORTED_YEAR"] += 1
            continue
        eligible = row.get("normal_race_model_eligible") is True if "normal_race_model_eligible" in row else _bridge_eligible(row, bridge)
        if not eligible:
            excluded["NOT_NORMAL_RACE_MODEL_ELIGIBLE"] += 1
            continue
        if not row.get("circuit"):
            excluded["MISSING_CIRCUIT"] += 1
            continue
        if not row.get("segment_id"):
            excluded["MISSING_SEGMENT_ID"] += 1
            continue
        kept.append(row)
    return kept, c7_source, dict(sorted(excluded.items()))


def _group_keys(level: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    base = (row["circuit"], row["year_group"], row["segment_id"])
    if level == "driver":
        return base + (row.get("driver"),)
    if level == "team":
        return base + (row.get("team"),)
    return base


def _key_columns(level: str) -> tuple[str, ...]:
    return ("circuit", "year_group", "segment_id") + ((level,) if level in {"driver", "team"} else ())


def _build_level(rows: list[dict[str, Any]], level: str, minimum_n: int) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if level in {"driver", "team"} and not row.get(level):
            # Identity is required for this level; it cannot be guessed from a
            # future roster or another table.
            continue
        groups[_group_keys(level, row)].append(row)
    output: list[dict[str, Any]] = []
    keys = _key_columns(level)
    for group_key in sorted(groups, key=lambda key: tuple(str(value) for value in key)):
        members = groups[group_key]
        row = {name: value for name, value in zip(keys, group_key)}
        row.update({
            "baseline_level": level,
            "n": len(members),
            "threshold_n": minimum_n,
            "baseline_valid": len(members) >= minimum_n,
            "schema_version": BASELINE_SCHEMA_VERSION,
            "provenance": "DERIVED",
        })
        unavailable: dict[str, dict[str, str]] = {}
        for metric, aliases in BASELINE_METRICS.items():
            values = [value for value in (_first_number(member, aliases) for member in members) if value is not None]
            row[f"{metric}_median"] = float(median(values)) if values else None
            row[f"{metric}_n"] = len(values)
            if not values:
                # API.md Quantity semantics: unavailable is a null value with
                # an explicit reason, not a zero-valued baseline.
                unavailable[metric] = {
                    "value": None,
                    "reason": f"C1 source has none of {', '.join(aliases)}",
                    "provenance": "DERIVED",
                    "unit": BASELINE_METRIC_UNITS[metric],
                }
        # `n` is the common source-row count that determines C2's threshold.
        # Individual metric n fields expose missing C1 channels without making
        # a group disappear or replacing a null median with zero.
        row["unavailable_metrics"] = unavailable
        output.append(row)
    return output


def build_segment_baselines(
    rows: Iterable[Mapping[str, Any]], *, minimum_samples: Mapping[str, int] | None = None,
    year_groups: Mapping[str, Iterable[int]] | None = None,
    legacy_c7_track_status_bridge: Mapping[str, Any] | None = None,
    allow_legacy_c7_track_status_bridge: bool = False,
    include_british_gp: bool = False,
) -> BaselineBuildResult:
    """Build C2 driver/team/field medians after the strict C7 eligibility gate."""
    thresholds = {"driver": 15, "team": 25, "field": 100, **(minimum_samples or {})}
    if any(level not in thresholds or int(thresholds[level]) < 1 for level in BASELINE_LEVELS):
        raise ValueError("minimum_samples must provide positive thresholds for driver, team and field")
    groups = year_groups or {"drs_era": (2022, 2023, 2024, 2025), "2026": (2026,)}
    bridge = {"required_track_status": "1", "require_is_accurate": True, **(legacy_c7_track_status_bridge or {})}
    eligible, c7_source, excluded = _filter_rows(
        rows,
        allow_legacy_c7_track_status_bridge=allow_legacy_c7_track_status_bridge,
        bridge=bridge,
        include_british_gp=include_british_gp,
        year_groups=groups,
    )
    return BaselineBuildResult(
        driver=_build_level(eligible, "driver", int(thresholds["driver"])),
        team=_build_level(eligible, "team", int(thresholds["team"])),
        field=_build_level(eligible, "field", int(thresholds["field"])),
        c7_source=c7_source,
        excluded_rows_by_reason=excluded,
        eligible_source_rows=len(eligible),
    )


def residual_at_use_time(value: Any, baseline_median: Any, *, baseline_valid: bool) -> float | None:
    """Compute a C2 residual only for a valid baseline, at consumer use time."""
    observed = _number(value)
    reference = _number(baseline_median)
    if not baseline_valid or observed is None or reference is None:
        return None
    return observed - reference
