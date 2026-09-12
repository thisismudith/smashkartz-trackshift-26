"""C8 dynamic battle-episode extraction (M05, CP-03).

Episodes are directed: ``attacker -> defender``.  An episode starts only on a
distance-close, immediate-ahead C1/C7 observation and is forcibly closed by
pair/pass changes and C7 pit/race-control boundaries.  The complete pairing
audit is retained separately so missing ahead telemetry never becomes a made-up
rival or gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

from .pairing import CLOSE_FOLLOWING_MAX_DISTANCE_M, assign_immediate_ahead_pairs

__all__ = [
    "BATTLE_SCHEMA_VERSION",
    "BATTLE_BOUNDARIES",
    "BattleBuildResult",
    "build_battle_episodes",
    "extract_battle_episodes",
]

BATTLE_SCHEMA_VERSION = "c8_battle_episodes_v2"
BATTLE_BOUNDARIES = {
    "PASS",
    "PAIR_SWITCH",
    "RACE_CONTROL_TRANSITION",
    "PIT_TRANSITION",
    "SESSION_END",
}


@dataclass(frozen=True)
class BattleBuildResult:
    """JSON-ready C8 outputs plus the non-model audit rows."""

    episodes: list[dict[str, Any]]
    battle_rows: list[dict[str, Any]]
    audit_rows: list[dict[str, Any]]


def _numeric(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _sort_key(row: dict[str, Any], index: int) -> tuple[Any, ...]:
    """Deterministic per-driver order without using a future-derived feature."""
    lap = _numeric(row.get("lap"))
    segment = _numeric(row.get("segment_id"))
    distance = _numeric(row.get("entry_distance_m"))
    elapsed = _numeric(row.get("entry_lap_elapsed_s"))
    return (
        lap is None, lap if lap is not None else float("inf"),
        segment is None, segment if segment is not None else float("inf"),
        distance is None, distance if distance is not None else float("inf"),
        elapsed is None, elapsed if elapsed is not None else float("inf"),
        index,
    )


def _session_driver_key(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return tuple(row.get(field) for field in ("year", "event", "session", "attacker"))


def _snapshot_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """The contemporaneous C1 point used for pass-direction evidence."""
    return tuple(row.get(field) for field in ("year", "event", "session", "lap", "segment_id", "entry_distance_m"))


def _token(value: Any) -> str:
    """Stable ID token; event/session abbreviations are accepted when supplied."""
    text = str(value if value is not None else "UNKNOWN").strip()
    return "_".join(part for part in "".join(ch if ch.isalnum() else " " for ch in text).split() if part) or "UNKNOWN"


def _event_token(row: dict[str, Any]) -> str:
    return _token(row.get("event_code") or row.get("event_abbreviation") or row.get("event"))


def _truthy(row: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return any(row.get(field) is True for field in fields)


def _closing_rate_mps(row: dict[str, Any]) -> float | None:
    """Use an already-derived causal rate only; CP-03 does not derive M06."""
    for field in ("closing_rate_ahead_mps", "relative_speed_to_ahead_mps"):
        value = _numeric(row.get(field))
        if value is not None:
            return value
    return None


def _time_gap_s(row: dict[str, Any]) -> float | None:
    """Return a documented ahead time gap only when the source actually has one."""
    for field in ("time_gap_ahead_s_entry", "gap_ahead_s_entry", "time_gap_entry_s", "gap_ahead_s"):
        value = _numeric(row.get(field))
        if value is not None and value >= 0.0:
            return value
    return None


def _episode_summary(active: dict[str, Any], bounded_by: str) -> dict[str, Any]:
    if bounded_by not in BATTLE_BOUNDARIES:
        raise ValueError(f"unknown battle boundary {bounded_by!r}")
    members = active["members"]
    gaps = [row["gap_ahead_m_for_pairing"] for row in members if row.get("gap_ahead_m_for_pairing") is not None]
    time_gaps = [_time_gap_s(row) for row in members]
    time_gaps = [gap for gap in time_gaps if gap is not None]
    rates = [_closing_rate_mps(row) for row in members]
    rates = [rate for rate in rates if rate is not None]
    durations = [_numeric(row.get("segment_time_s_offline")) for row in members]
    durations = [duration for duration in durations if duration is not None and duration >= 0.0]
    explicit_attempt = any(_truthy(row, ("pass_attempted", "pass_attempted_observed")) for row in members)
    first, last = members[0], members[-1]
    return {
        "year": first.get("year"),
        "event": first.get("event"),
        "session": first.get("session"),
        "attacker": active["attacker"],
        "defender": active["defender"],
        "start_lap": first.get("lap"),
        "end_lap": last.get("lap"),
        "duration_segments": len(members),
        "duration_s": sum(durations) if durations else None,
        "minimum_distance_gap_m": min(gaps) if gaps else None,
        # The C1 lake currently lacks this field, so it remains null rather
        # than converting metres using a speed assumption.
        "minimum_time_gap_s": min(time_gaps) if time_gaps else None,
        "maximum_closing_rate_mps": max(rates) if rates else None,
        "detection_opportunities": sum(
            1
            for row in members
            if _truthy(row, ("detection_opportunity", "is_detection_opportunity", "at_detection_line"))
            or row.get("decision_checkpoint") == "DETECTION"
        ),
        "pass_attempted": explicit_attempt or bounded_by == "PASS",
        "pass_completed": bounded_by == "PASS",
        "bounded_by": bounded_by,
        "normal_race_only": True,
        "provenance": "DERIVED",
        "_members": members,
        "_start_order": _sort_key(first, active["start_index"]),
    }


def build_battle_episodes(
    rows: Iterable[dict[str, Any]], *, close_following_max_distance_m: float = CLOSE_FOLLOWING_MAX_DISTANCE_M,
    session_roster: Mapping[tuple[str, str, str], Mapping[str, str | None]] | None = None,
) -> BattleBuildResult:
    """Produce C8 summaries and model-ready battle rows from local C1/C7 rows.

    A pass is recognised only from contemporaneous observed reversal (the old
    defender now directly observes the old attacker ahead) or an explicit pass
    completion flag.  A missing ahead observation is audited and closes an
    existing sequence as ``PAIR_SWITCH`` rather than being filled from position
    data or a guessed rival.
    """
    audit_rows = assign_immediate_ahead_pairs(
        rows,
        close_following_max_distance_m=close_following_max_distance_m,
        session_roster=session_roster,
    )
    indexed = list(enumerate(audit_rows))
    by_driver: dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = {}
    for index, row in indexed:
        by_driver.setdefault(_session_driver_key(row), []).append((index, row))
    for stream in by_driver.values():
        stream.sort(key=lambda item: _sort_key(item[1], item[0]))

    observed_pairs = {
        (_snapshot_key(row), row.get("attacker"), row.get("defender"))
        for row in audit_rows
        if row.get("attacker") is not None and row.get("defender") is not None
    }
    drafts: list[dict[str, Any]] = []

    for stream in by_driver.values():
        active: dict[str, Any] | None = None
        for source_index, row in stream:
            boundary: str | None = None
            if row.get("race_control_transition_flag") is True:
                boundary = "RACE_CONTROL_TRANSITION"
            elif row.get("pit_transition_flag") is True:
                boundary = "PIT_TRANSITION"
            elif row.get("normal_race_model_eligible") is not True:
                # Ineligible C7 rows are never model rows. A pit/control state
                # without its guard flag is still a hard C7 boundary.
                boundary = (
                    "PIT_TRANSITION"
                    if row.get("pit_state") in {"PIT_IN", "PIT_LANE", "PIT_OUT"}
                    else "RACE_CONTROL_TRANSITION"
                )

            if boundary is not None:
                if active is not None:
                    drafts.append(_episode_summary(active, boundary))
                    active = None
                continue

            relation = (row.get("attacker"), row.get("defender"))
            same_relation = active is not None and relation == (active["attacker"], active["defender"])
            is_pair_change = active is not None and not same_relation
            if is_pair_change:
                reverse_observed = (
                    _snapshot_key(row), active["defender"], active["attacker"]
                ) in observed_pairs
                explicit_pass = _truthy(row, ("pass_completed", "pass_completed_observed"))
                drafts.append(_episode_summary(active, "PASS" if reverse_observed or explicit_pass else "PAIR_SWITCH"))
                active = None

            if active is not None and same_relation:
                if row.get("pairing_eligible"):
                    active["members"].append(row)
                # A battle may start only close, but an unchanged directed
                # relation does not get a fictional new boundary merely because
                # it briefly exceeds the distance window.
                continue

            if row.get("pairing_eligible"):
                active = {
                    "attacker": row["attacker"],
                    "defender": row["defender"],
                    "members": [row],
                    "start_index": source_index,
                }

        if active is not None:
            drafts.append(_episode_summary(active, "SESSION_END"))

    # Number each directed pair chronologically.  The pair is in the ID, so a
    # counter scoped to the pair is both stable and globally unique in-session.
    drafts.sort(key=lambda row: (
        row.get("year"), _event_token(row), _token(row.get("session")),
        _token(row.get("attacker")), _token(row.get("defender")), row["_start_order"],
    ))
    counters: dict[tuple[Any, ...], int] = {}
    battle_rows: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for draft in drafts:
        counter_key = (
            draft.get("year"), _event_token(draft), draft.get("session"),
            draft.get("attacker"), draft.get("defender"),
        )
        counters[counter_key] = counters.get(counter_key, 0) + 1
        battle_id = "_".join(
            (
                _token(draft.get("year")), _event_token(draft), _token(draft.get("session")),
                _token(draft.get("attacker")), _token(draft.get("defender")),
                f"Battle{counters[counter_key]:02d}",
            )
        )
        members = draft.pop("_members")
        draft.pop("_start_order")
        draft["battle_id"] = battle_id
        episodes.append(draft)
        for segment_index, member in enumerate(members, start=1):
            battle_rows.append({
                **member,
                "battle_id": battle_id,
                "segment_index": segment_index,
            })

    return BattleBuildResult(episodes=episodes, battle_rows=battle_rows, audit_rows=audit_rows)


extract_battle_episodes = build_battle_episodes
