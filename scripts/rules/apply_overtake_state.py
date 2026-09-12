#!/usr/bin/env python3
"""Materialise CP-10 Overtake state overlays from the 20 m lake.

The primary output is a keyed lake overlay rather than a rewrite of the lake,
so CP-10 cannot silently alter C1/C7-owned columns.  A second keyed segment
overlay is emitted when the existing C1 segment table is available.  State
transitions are evaluated at each 20 m row before any segment aggregation;
this preserves Detection/Activation order inside one strategic segment.

The British Grand Prix is held out by default.  The only opt-in is
``--include-british-final-replay`` and is intentionally named for final replay
or validation use, not development.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.registry import assert_registered  # noqa: E402
from trackshift.rules.config import load_event_rules  # noqa: E402
from trackshift.rules.state_machine import (  # noqa: E402
    ACTIVE,
    ARMED,
    DISABLED,
    NOT_ARMED,
    configured_line_provenance,
    eligible,
    resolved_zones,
    step,
)

LAKE = ROOT / "data" / "processed" / "telemetry_20m"
SEGMENTS = ROOT / "data" / "processed" / "segments"
OUT = ROOT / "data" / "processed" / "overtake_state"
RAW = ROOT / "data" / "raw"
BRITISH_EVENT = "British Grand Prix"
KEYS = ["year", "event", "session", "driver", "lap", "distance_m"]
STATE_COLUMNS = [
    "overtake_state", "overtake_eligible", "overtake_unavailable_reason",
    "historical_drs_eligible", "historical_drs_open",
]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _message_records(path: Path) -> list[dict[str, Any]]:
    """Read the raw/extracted RCM columnar shape without changing it."""
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, Mapping) and "messages" in payload:
        messages = payload["messages"]
        return [dict(row) for row in messages if isinstance(row, Mapping)] if isinstance(messages, list) else []
    if isinstance(payload, Mapping) and "rcm" in payload:
        payload = payload["rcm"]
    if not isinstance(payload, Mapping):
        return []
    columns = {key: value for key, value in payload.items() if isinstance(value, list)}
    if not columns:
        return []
    count = max(len(values) for values in columns.values())
    return [{key: (values[index] if index < len(values) else None) for key, values in columns.items()}
            for index in range(count)]


def _session_records(source: Path, session: str) -> list[dict[str, Any]]:
    """Read one session from either an extracted artifact or raw RCM file."""
    if source.suffix != ".json":
        return _message_records(source / session / "rcm.json")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    sessions = payload.get("sessions") if isinstance(payload, Mapping) else None
    if isinstance(sessions, Mapping):
        doc = sessions.get(session) or {}
        messages = doc.get("messages") if isinstance(doc, Mapping) else None
        return [dict(row) for row in messages if isinstance(row, Mapping)] if isinstance(messages, list) else []
    return _message_records(source)


def _timestamp_s(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is not None:
        return numeric
    if not isinstance(value, str):
        return None
    try:
        # Python handles six fractional digits; raw RCM carries nanoseconds.
        clean = value.replace("Z", "+00:00")
        if "." in clean:
            head, tail = clean.split(".", 1)
            fraction = tail[:6]
            suffix = tail[9:] if len(tail) >= 9 else ""
            clean = f"{head}.{fraction}{suffix}"
        return datetime.fromisoformat(clean).timestamp()
    except ValueError:
        return None


def _control_messages(records: Iterable[Mapping[str, Any]], year: str) -> list[dict[str, Any]]:
    """Keep only observed enable/disable messages for the relevant regulation era."""
    enabled = "OVERTAKE ENABLED" if str(year) == "2026" else "DRS ENABLED"
    disabled = "OVERTAKE DISABLED" if str(year) == "2026" else "DRS DISABLED"
    result: list[dict[str, Any]] = []
    for record in records:
        text = str(record.get("message", record.get("msg", "")) or "").upper()
        if enabled not in text and disabled not in text:
            continue
        result.append({
            "kind": "enabled" if enabled in text else "disabled",
            "time": record.get("time"),
            "lap": _number(record.get("lap")),
            "message": text,
        })
    return result


def align_race_control_messages(frame, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Convert observed RCM timestamps into the lake's session-second clock.

    RCM timestamps are wall-clock ISO strings while C1/lake timing is elapsed
    session seconds.  The raw source supplies a message lap number but no
    session-start epoch, so the offset is derived only from the matching lap's
    observed lake interval.  It is recorded as derived alignment, never as an
    observed timestamp conversion with invented precision.
    """
    candidates: list[float] = []
    for message in messages:
        # Numeric RCM times are already session-relative; never reinterpret
        # them as Unix seconds and manufacture an offset.
        if _number(message.get("time")) is not None:
            continue
        stamp = _timestamp_s(message.get("time"))
        lap = message.get("lap")
        if stamp is None or lap is None:
            continue
        same_lap = frame[frame["lap"].astype(float).eq(float(lap))]
        if same_lap.empty:
            continue
        row_time = same_lap["lap_start_session_s"].astype(float) + same_lap["lap_elapsed_s"].astype(float)
        candidates.append(stamp - (float(row_time.min()) + float(row_time.max())) / 2.0)
    offset = sorted(candidates)[len(candidates) // 2] if candidates else None
    aligned: list[dict[str, Any]] = []
    for message in messages:
        numeric_time = _number(message.get("time"))
        stamp = _timestamp_s(message.get("time"))
        if numeric_time is not None:
            session_time = numeric_time
        elif stamp is not None and offset is not None:
            session_time = stamp - offset
        else:
            # Missing temporal alignment must not create a control transition.
            session_time = None
        aligned.append({**message, "session_time_s": session_time})
    aligned.sort(key=lambda message: (message["session_time_s"] is None, message["session_time_s"] or float("inf")))
    return aligned, {
        "method": "lap_marker_midpoint_v1" if offset is not None else "unavailable",
        "session_epoch_unix_s": offset,
        "anchor_messages": len(candidates),
        "unaligned_messages": sum(message["session_time_s"] is None for message in aligned),
    }


def _gap_s(row: Mapping[str, Any]) -> float | None:
    direct = _number(row.get("gap_ahead_s"))
    if direct is not None:
        return direct
    distance = _number(row.get("gap_ahead_m"))
    speed_kmh = _number(row.get("speed_kmh"))
    if distance is None or speed_kmh is None or speed_kmh <= 0:
        return None
    return distance / (speed_kmh / 3.6)


def _usable_lines(rules: Mapping[str, Any]) -> bool:
    # resolved_zones fills the lap's single Detection Line into each zone, which
    # is how the 2026 notes describe it; reading raw zones here would report no
    # usable line even once the event-level value is configured.
    zones = resolved_zones(rules)
    # DERIVED_TELEMETRY is permitted because a Detection Line measured from the
    # pit-entry crossing is a measurement with an FIA-cited rule behind it, not
    # a proxy standing in for one.
    permitted_tiers = {"RULE_FIA", "DERIVED_TELEMETRY", "PROXY_HISTORICAL_DRS"}
    return bool(zones) and all(
        isinstance(zone, Mapping)
        and _number((zone.get("detection_line_m") or {}).get("value")) is not None
        and (zone.get("detection_line_m") or {}).get("value_source") in permitted_tiers
        and _number((zone.get("activation_line_m") or {}).get("value")) is not None
        and (zone.get("activation_line_m") or {}).get("value_source") in permitted_tiers
        for zone in zones
    )


def _session_time(row: Mapping[str, Any]) -> float | None:
    start, elapsed = _number(row.get("lap_start_session_s")), _number(row.get("lap_elapsed_s"))
    return None if start is None or elapsed is None else start + elapsed


def apply_2026(frame, rules: Mapping[str, Any], control_messages: list[dict[str, Any]]):
    """Apply state transitions per driver in causal lap/distance order."""
    import pandas as pd

    line_available = _usable_lines(rules)
    result = frame[KEYS].copy()
    for column in STATE_COLUMNS:
        result[column] = None
    unavailable_reason = (
        "required Detection Line unavailable: historical DRS active-zone derivation "
        "supplies Activation/zone-end proxies only; no authoritative historical "
        "Detection Line is configured"
    )
    # Race control can only be applied where a message actually lands on the
    # session clock. With none aligned, `disabled` below would stay True for
    # every row and the session would be written out as DISABLED end to end --
    # a positive OBSERVED_RCM claim that race control withdrew Overtake, which
    # is a different thing from "we could not tell". Barcelona and Hungary 2026
    # both read OVERTAKE ENABLED from lap ~2 to the flag, and both came out
    # 100% DISABLED before this check existed.
    aligned_messages = [m for m in control_messages if m.get("session_time_s") is not None]
    race_control_known = bool(aligned_messages)
    undetermined_reason = (
        f"race-control state undetermined: {len(control_messages)} Overtake message(s) "
        "present but none could be aligned to session time, so no ENABLED message "
        "can be applied. Recorded as unknown rather than DISABLED, which would "
        "assert a withdrawal the message feed does not support."
    )

    unavailable_line_rows = 0
    disabled_rows = 0
    undetermined_rows = 0

    for _, group in frame.groupby(["driver"], sort=False):
        group = group.sort_values(["lap", "distance_m"], kind="stable")
        state = DISABLED  # fail closed until a genuinely observed ENABLED message.
        prior_lap: Any = None
        previous_position: float | None = None
        previous_gap: float | None = None
        previous_disabled = True
        message_index = 0
        disabled = True
        for index, row in group.iterrows():
            row_time = _session_time(row)
            while (message_index < len(control_messages)
                   and control_messages[message_index].get("session_time_s") is not None
                   and row_time is not None
                   and float(control_messages[message_index]["session_time_s"]) <= row_time):
                disabled = control_messages[message_index]["kind"] == "disabled"
                message_index += 1
            lap = row["lap"]
            if lap != prior_lap:
                previous_position, previous_gap = 0.0, None
                prior_lap = lap
                # A zone lives inside a lap: crossing the line ends any armed or
                # active state, exactly as SESSION_END does. Without this, a zone
                # whose configured end sits beyond the telemetry lap length can
                # never be exited and the car stays ACTIVE for the rest of the
                # session. Two Tier-C proxy zones are in that position today
                # (Monaco 3300 m of a 3284 m lap, Australian zone 4), but the
                # rule is right regardless of how those are later corrected.
                if state in {ARMED, ACTIVE}:
                    state = NOT_ARMED
            current_position = _number(row.get("distance_m"))
            current_gap = _gap_s(row)
            control = {
                "overtake_disabled": disabled and not previous_disabled,
                "overtake_enabled": (not disabled) and previous_disabled,
                "previous_position_m": previous_position,
                "previous_gap_s": previous_gap,
            }
            if not race_control_known:
                # Fail closed on the *action* (no state, so nothing downstream
                # may treat this as eligible) without failing dishonest on the
                # *label*.
                undetermined_rows += 1
                result.at[index, "overtake_unavailable_reason"] = undetermined_reason
            elif disabled or line_available:
                state = step(state, current_position, current_gap, control, rules)
                result.at[index, "overtake_state"] = state
                result.at[index, "overtake_eligible"] = eligible(state)
                if state == DISABLED:
                    disabled_rows += 1
            else:
                # An enabled car without a configured line cannot be assigned a
                # 2026 state safely.  Do not manufacture a line or consult DRS.
                unavailable_line_rows += 1
                result.at[index, "overtake_unavailable_reason"] = unavailable_reason
            previous_disabled = disabled
            previous_position, previous_gap = current_position, current_gap

    return result, {
        "line_available": line_available,
        "unavailable_line_rows": unavailable_line_rows,
        "disabled_rows": disabled_rows,
        "race_control_undetermined_rows": undetermined_rows,
        "race_control_known": race_control_known,
        "race_control_messages": len(control_messages),
        "race_control_messages_aligned": len(aligned_messages),
    }


def apply_historical(frame, control_messages: list[dict[str, Any]]):
    """Historical-only DRS covariates; 2026 Overtake fields remain null."""
    result = frame[KEYS].copy()
    result["overtake_state"] = None
    result["overtake_eligible"] = None
    result["overtake_unavailable_reason"] = None
    result["historical_drs_eligible"] = None
    result["historical_drs_open"] = None
    for _, group in frame.groupby(["driver"], sort=False):
        group = group.sort_values(["lap", "distance_m"], kind="stable")
        message_index, enabled = 0, False
        for index, row in group.iterrows():
            row_time = _session_time(row)
            while (message_index < len(control_messages)
                   and control_messages[message_index].get("session_time_s") is not None
                   and row_time is not None
                   and float(control_messages[message_index]["session_time_s"]) <= row_time):
                enabled = control_messages[message_index]["kind"] == "enabled"
                message_index += 1
            # This intentionally uses the historical DRS field only in the
            # historical branch. It is an observed covariate, not a 2026 rule.
            open_observed = bool(row.get("drs_open") is True or _number(row.get("drs_open")) not in (None, 0.0))
            value = bool(enabled and open_observed)
            result.at[index, "historical_drs_open"] = value
            result.at[index, "historical_drs_eligible"] = value
    return result


def _event_display(event_key: str) -> str:
    rules = load_event_rules(event_key)
    return str(rules.get("event_display"))


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write_segment_overlay(overlay, event_rules: Mapping[str, Any], output: Path) -> list[str]:
    """Write C1-keyed entry snapshots without mutating the C1 table."""
    import pandas as pd

    circuit = event_rules.get("circuit")
    path = SEGMENTS / f"circuit={circuit}" / "segments.parquet"
    if not circuit or not path.exists():
        return []
    segments = pd.read_parquet(path)
    value_rows = overlay.rename(columns={"distance_m": "entry_distance_m"})
    keys = ["year", "event", "session", "driver", "lap", "entry_distance_m"]
    available = [column for column in keys if column in segments.columns and column in value_rows.columns]
    if len(available) != len(keys):
        return []
    merged = segments[[*keys, "segment_id"]].merge(value_rows[[*keys, *STATE_COLUMNS]], on=keys, how="left")
    destination = output / "segments" / f"circuit={circuit}" / "overtake_state.parquet"
    destination.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(destination, index=False)
    return [str(destination.relative_to(ROOT))]


def main() -> int:
    global SEGMENTS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", default="2026", choices=["2022", "2023", "2024", "2025", "2026"])
    parser.add_argument("--event", action="append", help="Event config key; repeat to select several")
    parser.add_argument("--lake", type=Path, default=LAKE)
    parser.add_argument("--segments", type=Path, default=SEGMENTS)
    parser.add_argument("--raw-root", type=Path, default=RAW)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--include-british-final-replay", action="store_true",
                        help="Allow British GP only for final replay/final validation, never development.")
    args = parser.parse_args()

    SEGMENTS = args.segments
    requested = args.event or [path.stem for path in (ROOT / "config" / "rules" / "2026").glob("*.yaml") if path.stem != "common"]
    manifests: list[dict[str, Any]] = []
    for event_key in sorted(set(requested)):
        rules = load_event_rules(event_key)
        event_display = _event_display(event_key)
        if event_display == BRITISH_EVENT and not args.include_british_final_replay:
            manifests.append({"event": event_key, "excluded": True, "reason": "British GP held out by default"})
            continue
        safe_event = event_display.replace(" ", "_")
        files = sorted(args.lake.glob(f"year={args.year}/event={safe_event}/session=*/telemetry_20m.parquet"))
        if not files:
            manifests.append({"event": event_key, "skipped": True, "reason": "no local lake rows"})
            continue
        import pandas as pd
        frame = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
        frame = frame[frame["session"].isin(["Race", "Sprint"])].copy()
        if frame.empty:
            manifests.append({"event": event_key, "skipped": True, "reason": "no Race/Sprint rows"})
            continue
        if str(args.year) == "2026":
            source = ROOT / str((rules.get("race_control") or {}).get("overtake_windows_source", ""))
        else:
            source = args.raw_root / str(args.year) / event_display
        session_overlays = []
        session_control: dict[str, Any] = {}
        info = {"line_available": _usable_lines(rules) if str(args.year) == "2026" else None,
                "unavailable_line_rows": 0, "disabled_rows": 0,
                # Carried per event so an undetermined session is visible in the
                # manifest rather than only in the absence of DISABLED rows.
                "race_control_undetermined_rows": 0,
                "race_control_messages": 0,
                "race_control_messages_aligned": 0,
                "sessions_with_undetermined_race_control": []}
        for session, session_frame in frame.groupby("session", sort=True):
            records = _session_records(source, str(session))
            messages = _control_messages(records, args.year)
            aligned, alignment = align_race_control_messages(session_frame, messages)
            if str(args.year) == "2026":
                session_overlay, session_info = apply_2026(session_frame, rules, aligned)
                info["unavailable_line_rows"] += session_info["unavailable_line_rows"]
                info["disabled_rows"] += session_info["disabled_rows"]
                info["race_control_undetermined_rows"] += session_info["race_control_undetermined_rows"]
                info["race_control_messages"] += session_info["race_control_messages"]
                info["race_control_messages_aligned"] += session_info["race_control_messages_aligned"]
                if not session_info["race_control_known"] and session_info["race_control_messages"]:
                    info["sessions_with_undetermined_race_control"].append(str(session))
            else:
                session_overlay = apply_historical(session_frame, aligned)
            session_overlays.append(session_overlay)
            session_control[str(session)] = {"messages": len(messages), "alignment": alignment}
        overlay = pd.concat(session_overlays, ignore_index=True)
        assert_registered(overlay.columns, f"M20 Overtake lake overlay {event_key}")
        written: list[str] = []
        for session, session_overlay in overlay.groupby("session", sort=True):
            destination = args.output / "lake" / f"year={args.year}" / f"event={safe_event}" / f"session={str(session).replace(' ', '_')}" / "overtake_state.parquet"
            destination.parent.mkdir(parents=True, exist_ok=True)
            session_overlay.to_parquet(destination, index=False)
            written.append(str(destination.relative_to(ROOT)))
        segment_paths = _write_segment_overlay(overlay, rules, args.output) if str(args.year) == "2026" else []
        state_counts = {str(key): int(value) for key, value in overlay["overtake_state"].value_counts(dropna=False).items()}
        manifests.append({
            "event": event_key,
            "event_display": event_display,
            "year": args.year,
            "rows": int(len(overlay)),
            "state_counts": state_counts,
            "overtake_eligible_rows": int(overlay["overtake_eligible"].fillna(False).sum()),
            "historical_drs_nonnull_rows": int(overlay["historical_drs_open"].notna().sum()),
            "race_control_source": str(source.relative_to(ROOT)) if source.is_absolute() and str(source).startswith(str(ROOT)) else str(source),
            "race_control": session_control,
            "line_provenance": configured_line_provenance(rules) if str(args.year) == "2026" else [],
            "rules_version": (rules.get("regulation_snapshot") or {}).get("encoded_configuration_version"),
            "source_data": [str(path.relative_to(ROOT)) for path in files],
            "excluded_unavailable": info,
            "written": written,
            "segment_overlay": segment_paths,
        })
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "m20_overtake_state_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "british_gp_policy": "excluded by default; --include-british-final-replay required",
        "events": manifests,
    }
    path = args.output / "run_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
