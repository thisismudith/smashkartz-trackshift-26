"""Immediate-ahead pairing for C8 battle extraction (M05).

The public telemetry identifies only the car immediately ahead.  This module
keeps that limitation intact: it resolves that one observation when possible
and never manufactures a Cartesian product of drivers.  Every source row is
returned as an audit row, including rows that cannot become model input.

Close following is intentionally distance-only.  ``gap_ahead_m_entry`` (or its
telemetry-level predecessor ``gap_ahead_m``) must be at most
``CLOSE_FOLLOWING_MAX_DISTANCE_M``.  A time-gap is not substituted here because
the C1 builder does not yet produce a reliable entry time-gap for every row.
"""
from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import Any, Iterable, Mapping

__all__ = [
    "CLOSE_FOLLOWING_MAX_DISTANCE_M",
    "CLOSE_FOLLOWING_CRITERION",
    "build_session_roster",
    "assign_immediate_ahead_pairs",
    "pair_immediate_ahead",
]

# One explicit, configurable criterion.  100 m is deliberately a conservative
# distance-only candidate window, not a proxy for a one-second FIA threshold.
CLOSE_FOLLOWING_MAX_DISTANCE_M = 100.0
CLOSE_FOLLOWING_CRITERION = "gap_ahead_m_entry <= close_following_max_distance_m"

_DIRECT_AHEAD_FIELDS = ("driver_ahead", "driver_ahead_driver", "ahead_driver")
_AHEAD_NUMBER_FIELDS = ("driver_ahead_number", "DriverAhead")
_CAR_NUMBER_FIELDS = ("driver_number", "car_number", "driver_car_number", "dNum")
_GAP_FIELDS = ("gap_ahead_m_entry", "gap_ahead_m")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "0", "0.0"}:
        return None
    return text.upper()


def _number_key(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    try:
        numeric = float(text)
    except ValueError:
        return text
    return str(int(numeric)) if numeric.is_integer() else text


def _first_value(row: dict[str, Any], fields: tuple[str, ...]) -> Any:
    return next((row[field] for field in fields if row.get(field) is not None), None)


def _session_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """Canonical key for static same-session identity metadata."""
    return tuple(str(row.get(field)).strip() for field in ("year", "event", "session"))


def build_session_roster(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, str | None]]:
    """Build the static ``(year, event, session, driver_number) -> driver`` roster.

    The input is identity metadata only: its driver and driver-number columns
    are read without consulting position, ahead observations, chronological
    order, pass outcomes, or any other race dynamics.  ``None`` deliberately
    marks a reused/ambiguous car number; an absent key means the number is
    unknown for that session.
    """
    candidates: dict[tuple[Any, Any, Any], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in rows:
        driver = _text(row.get("driver"))
        number = _number_key(_first_value(row, _CAR_NUMBER_FIELDS))
        if driver is not None and number is not None:
            candidates[_session_key(row)][number].add(driver)
    return {
        session: {number: next(iter(drivers)) if len(drivers) == 1 else None for number, drivers in by_number.items()}
        for session, by_number in candidates.items()
    }


def _ahead_driver(
    row: dict[str, Any], roster: Mapping[tuple[str, str, str], Mapping[str, str | None]]
) -> tuple[str | None, str | None]:
    """Resolve the observed ahead identity, never from position or sequence."""
    for field in _DIRECT_AHEAD_FIELDS:
        direct = _text(row.get(field))
        if direct is not None:
            return direct, "DIRECT_AHEAD"
    number = next((_number_key(row.get(field)) for field in _AHEAD_NUMBER_FIELDS if _number_key(row.get(field)) is not None), None)
    if number is None:
        return None, "MISSING_DRIVER_AHEAD"
    session = roster.get(_session_key(row))
    if session is None or number not in session:
        return None, "UNKNOWN_DRIVER_NUMBER"
    defender = session[number]
    if defender is None:
        return None, "AMBIGUOUS_DRIVER_NUMBER"
    return defender, "SESSION_ROSTER"


def _distance_gap_m(row: dict[str, Any]) -> float | None:
    value = _first_value(row, _GAP_FIELDS)
    try:
        gap = float(value)
    except (TypeError, ValueError):
        return None
    return gap if isfinite(gap) and gap >= 0.0 else None


def assign_immediate_ahead_pairs(
    rows: Iterable[dict[str, Any]], *, close_following_max_distance_m: float = CLOSE_FOLLOWING_MAX_DISTANCE_M,
    session_roster: Mapping[tuple[str, str, str], Mapping[str, str | None]] | None = None,
) -> list[dict[str, Any]]:
    """Annotate every C1/C7 row with an immediate-ahead pairing verdict.

    ``pairing_eligible`` means that the row is a normal-race, non-transition,
    close-following observation with a resolved *non-self* immediate defender.
    Rows with ``pairing_eligible == false`` remain in the returned audit table,
    with an explicit ``pairing_exclusion_reason``.  No time gap is read or
    compared by this function.
    """
    if close_following_max_distance_m <= 0:
        raise ValueError("close_following_max_distance_m must be positive")

    source_rows = [dict(row) for row in rows]
    roster = session_roster if session_roster is not None else build_session_roster(source_rows)
    audit_rows: list[dict[str, Any]] = []

    for row in source_rows:
        attacker = _text(row.get("driver"))
        defender, defender_resolution = _ahead_driver(row, roster)
        gap_m = _distance_gap_m(row)
        audit = dict(row)
        audit.update(
            {
                "attacker": attacker,
                "defender": defender,
                "defender_resolution": defender_resolution,
                "gap_ahead_m_for_pairing": gap_m,
                "close_following_criterion": CLOSE_FOLLOWING_CRITERION,
                "close_following_max_distance_m": float(close_following_max_distance_m),
                "close_following": False,
                "pairing_eligible": False,
                "pairing_exclusion_reason": None,
                "pair_key": None,
            }
        )

        if row.get("race_control_transition_flag") is True:
            audit["pairing_exclusion_reason"] = "RACE_CONTROL_TRANSITION"
        elif row.get("pit_transition_flag") is True:
            audit["pairing_exclusion_reason"] = "PIT_TRANSITION"
        elif row.get("normal_race_model_eligible") is not True:
            audit["pairing_exclusion_reason"] = "NOT_NORMAL_RACE_MODEL_ELIGIBLE"
        elif attacker is None:
            audit["pairing_exclusion_reason"] = "MISSING_ATTACKER"
        elif defender is None:
            audit["pairing_exclusion_reason"] = defender_resolution
        elif defender == attacker:
            audit["pairing_exclusion_reason"] = "SELF_PAIR"
        elif gap_m is None:
            audit["pairing_exclusion_reason"] = "MISSING_DISTANCE_GAP"
        else:
            audit["pair_key"] = f"{attacker}->{defender}"
            audit["close_following"] = gap_m <= close_following_max_distance_m
            if audit["close_following"]:
                audit["pairing_eligible"] = True
            else:
                audit["pairing_exclusion_reason"] = "NOT_CLOSE_FOLLOWING"
        audit_rows.append(audit)
    return audit_rows


pair_immediate_ahead = assign_immediate_ahead_pairs
