"""Portable C7 race-context normalisation and normal-race eligibility."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

RACE_CONTROL_STATES = {
    "GREEN", "YELLOW", "DOUBLE_YELLOW", "VSC", "SC", "RED", "UNKNOWN",
}
PIT_STATES = {"ON_TRACK", "PIT_IN", "PIT_LANE", "PIT_OUT", "UNKNOWN"}

_RACE_CONTROL_ALIASES = {
    "1": "GREEN", "GREEN": "GREEN", "CLEAR": "GREEN", "ALL CLEAR": "GREEN",
    "2": "YELLOW", "YELLOW": "YELLOW", "3": "DOUBLE_YELLOW",
    "DOUBLE YELLOW": "DOUBLE_YELLOW", "DOUBLE_YELLOW": "DOUBLE_YELLOW",
    "4": "SC", "SC": "SC", "SAFETY CAR": "SC", "SAFETY_CAR": "SC",
    "5": "RED", "RED": "RED", "RED FLAG": "RED", "RED_FLAG": "RED",
    "6": "VSC", "VSC": "VSC", "VIRTUAL SAFETY CAR": "VSC",
    "VIRTUAL_SAFETY_CAR": "VSC",
}
_PIT_ALIASES = {
    "ON TRACK": "ON_TRACK", "ON_TRACK": "ON_TRACK", "TRACK": "ON_TRACK",
    "PIT IN": "PIT_IN", "PIT_IN": "PIT_IN", "IN": "PIT_IN",
    "PIT LANE": "PIT_LANE", "PIT_LANE": "PIT_LANE", "PIT": "PIT_LANE",
    "PIT OUT": "PIT_OUT", "PIT_OUT": "PIT_OUT", "OUT": "PIT_OUT",
}


def _text(value: Any) -> str:
    return "" if value is None else " ".join(str(value).strip().upper().replace("-", " ").replace("_", " ").split())


def _first(row: dict[str, Any], names: Iterable[str]) -> Any:
    return next((row[name] for name in names if row.get(name) is not None), None)


def normalize_race_control_state(value: Any) -> str:
    """Map source-specific control status to the strict C7 enum.

    Unknown, missing, and unsupported source values deliberately remain UNKNOWN.
    """
    return _RACE_CONTROL_ALIASES.get(_text(value), "UNKNOWN")


def normalize_pit_state(value: Any) -> str:
    """Map source-specific pit status to the strict C7 enum."""
    return _PIT_ALIASES.get(_text(value), "UNKNOWN")


def _race_control_for_row(row: dict[str, Any]) -> str:
    if row.get("safety_car_active") is True or row.get("safety_car") is True:
        return "SC"
    if row.get("virtual_safety_car_active") is True or row.get("virtual_safety_car") is True:
        return "VSC"
    return normalize_race_control_state(
        _first(row, ("normalized_race_control_state", "race_control_state", "track_status", "status"))
    )


def _pit_for_row(row: dict[str, Any]) -> str:
    explicit = _first(row, ("pit_state", "pit_status"))
    if explicit is not None:
        return normalize_pit_state(explicit)
    if row.get("pit_in") is True:
        return "PIT_IN"
    if row.get("in_pit_lane") is True or row.get("pit_stop") is True:
        return "PIT_LANE"
    if row.get("pit_out") is True:
        return "PIT_OUT"
    # Phase 2 has no measured pit state when no source value is supplied.
    return "UNKNOWN"


def _row_time(row: dict[str, Any]) -> float | None:
    value = row.get("session_time_s")
    if value is None and row.get("lap_start_session_s") is not None and row.get("lap_elapsed_s") is not None:
        value = float(row["lap_start_session_s"]) + float(row["lap_elapsed_s"])
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _group_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(name) for name in ("year", "event", "session", "driver"))


def enrich_race_context(rows: Iterable[dict[str, Any]], transition_guard_rows: int = 1) -> list[dict[str, Any]]:
    """Add deterministic C7 fields without dropping or resampling input rows.

    A control or pit-state change marks the preceding, changed, and following
    rows by default.  Those rows are ineligible, so any later rolling feature
    builder has an explicit hard boundary on both sides of the transition.
    """
    if transition_guard_rows < 0:
        raise ValueError("transition_guard_rows must be non-negative")
    result = [dict(row) for row in rows]
    grouped: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for index, row in enumerate(result):
        grouped[_group_key(row)].append(index)

    for indices in grouped.values():
        indices.sort(key=lambda index: (_row_time(result[index]) is None, _row_time(result[index]) or 0.0, index))
        control = [_race_control_for_row(result[index]) for index in indices]
        pit = [_pit_for_row(result[index]) for index in indices]
        raw_control_transition = [False] * len(indices)
        raw_pit_transition = [False] * len(indices)
        for position in range(1, len(indices)):
            raw_control_transition[position] = control[position] != control[position - 1]
            raw_pit_transition[position] = pit[position] != pit[position - 1]
        for position, index in enumerate(indices):
            row = result[index]
            row["normalized_race_control_state"] = control[position]
            row["pit_state"] = pit[position]
            row["safety_car_active"] = control[position] == "SC"
            row["virtual_safety_car_active"] = control[position] == "VSC"
            row["race_control_transition_flag"] = any(
                raw_control_transition[candidate]
                for candidate in range(max(0, position - transition_guard_rows), min(len(indices), position + transition_guard_rows + 1))
            )
            row["pit_transition_flag"] = any(
                raw_pit_transition[candidate]
                for candidate in range(max(0, position - transition_guard_rows), min(len(indices), position + transition_guard_rows + 1))
            )

        elapsed = 0.0
        previous_time: float | None = None
        for position, index in enumerate(indices):
            row = result[index]
            current_time = _row_time(row)
            ordinary_green = control[position] == "GREEN" and not row["race_control_transition_flag"]
            if not ordinary_green:
                elapsed = 0.0
                row["green_flag_elapsed_s"] = 0.0
            else:
                if previous_time is not None and current_time is not None:
                    elapsed += max(0.0, current_time - previous_time)
                row["green_flag_elapsed_s"] = elapsed
            previous_time = current_time
            row["normal_race_model_eligible"] = (
                control[position] == "GREEN"
                and pit[position] == "ON_TRACK"
                and not row["safety_car_active"]
                and not row["virtual_safety_car_active"]
                and not row["race_control_transition_flag"]
                and not row["pit_transition_flag"]
            )
    return result
