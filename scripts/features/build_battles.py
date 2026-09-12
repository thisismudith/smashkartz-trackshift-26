#!/usr/bin/env python3
"""Build C8 battle episodes from local C1 segments and a static lake roster.

Examples:
    .venv/bin/python scripts/features/build_battles.py \\
      --circuit british --year 2026 --event "British Grand Prix" --session Sprint \\
      --derive-c7-from-c1-track-status

The normal production path expects C7 fields already attached to C1 rows.  The
explicit ``--derive-c7-from-c1-track-status`` switch is a conservative bridge
for the current local CP-05 output, which predates that materialisation: only
accurate rows whose status is exactly green (``"1"``) are admitted.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.features.api import (  # noqa: E402
    CLOSE_FOLLOWING_CRITERION,
    CLOSE_FOLLOWING_MAX_DISTANCE_M,
    build_battle_episodes,
    build_session_roster,
)

SEGMENTS = ROOT / "data" / "processed" / "segments"
TELEMETRY = ROOT / "data" / "processed" / "telemetry_20m"
OUT = ROOT / "data" / "processed" / "battle_episodes"


def _clean(value: Any) -> Any:
    """Convert pandas/NumPy nulls and scalars to portable JSON values."""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _records(frame: Any) -> list[dict[str, Any]]:
    return [{key: _clean(value) for key, value in row.items()} for row in frame.to_dict(orient="records")]


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise ValueError("JSON input must be an array of objects")
        return [dict(row) for row in payload]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _filter(frame: Any, args: argparse.Namespace) -> Any:
    if args.year is not None:
        frame = frame[frame["year"].astype(str) == str(args.year)]
    for field in ("event", "session"):
        value = getattr(args, field)
        if value is not None:
            frame = frame[frame[field] == value]
    return frame


def _load_segments(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.input is not None:
        return _load_json_rows(args.input)
    import pandas as pd

    paths = sorted(args.segments_dir.glob("circuit=*/segments.parquet"))
    if args.circuit:
        paths = [path for path in paths if path.parent.name == f"circuit={args.circuit}"]
    if not paths:
        raise ValueError(f"no C1 segment files found under {args.segments_dir}")
    frame = _filter(pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True), args)
    return _records(frame)


def _session_path(telemetry_dir: Path, row: dict[str, Any]) -> Path:
    return (
        telemetry_dir
        / f"year={row['year']}"
        / f"event={str(row['event']).replace(' ', '_')}"
        / f"session={str(row['session']).replace(' ', '_')}"
        / "telemetry_20m.parquet"
    )


def _load_roster_rows(rows: Iterable[dict[str, Any]], telemetry_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    import pandas as pd

    paths = sorted({_session_path(telemetry_dir, row) for row in rows})
    roster_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for path in paths:
        if not path.exists():
            missing.append(str(path))
            continue
        frame = pd.read_parquet(path, columns=["year", "event", "session", "driver", "driver_number"])
        roster_rows.extend(_records(frame))
    return roster_rows, missing


def _green_track_status(value: Any) -> bool:
    return str(value).strip() == "1"


def _materialize_legacy_c7(rows: list[dict[str, Any]], enabled: bool) -> str:
    """Use supplied C7, or explicitly derive a conservative local fallback."""
    missing = [row for row in rows if "normal_race_model_eligible" not in row]
    if not missing:
        return "provided_c7"
    if not enabled:
        raise ValueError(
            "C1 rows lack normal_race_model_eligible. Materialize C7 first, or "
            "pass --derive-c7-from-c1-track-status for the explicit conservative local fallback."
        )
    for row in rows:
        normal = _green_track_status(row.get("track_status")) and row.get("is_accurate") is True
        row.setdefault("normal_race_model_eligible", normal)
        row.setdefault("normalized_race_control_state", "GREEN" if normal else "UNKNOWN")
        row.setdefault("pit_state", "ON_TRACK" if normal else "UNKNOWN")
        row.setdefault("race_control_transition_flag", False)
        row.setdefault("pit_transition_flag", False)
    return "derived_from_c1_track_status_v1"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, help="C1/C7 rows as JSONL or JSON array; bypasses --segments-dir")
    parser.add_argument("--segments-dir", type=Path, default=SEGMENTS)
    parser.add_argument("--telemetry-dir", type=Path, default=TELEMETRY)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--circuit", help="C1 circuit directory key, for example british")
    parser.add_argument("--year")
    parser.add_argument("--event")
    parser.add_argument("--session")
    parser.add_argument("--close-following-max-distance-m", type=float, default=CLOSE_FOLLOWING_MAX_DISTANCE_M)
    parser.add_argument("--derive-c7-from-c1-track-status", action="store_true")
    args = parser.parse_args()

    rows = _load_segments(args)
    if not rows:
        raise ValueError("selected C1 input has no rows")
    c7_source = _materialize_legacy_c7(rows, args.derive_c7_from_c1_track_status)
    roster_rows, missing_roster_sources = _load_roster_rows(rows, args.telemetry_dir)
    roster = build_session_roster(roster_rows)
    result = build_battle_episodes(
        rows,
        close_following_max_distance_m=args.close_following_max_distance_m,
        session_roster=roster,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(args.output_dir / "battle_episodes.jsonl", result.episodes)
    _write_jsonl(args.output_dir / "battle_segment_rows.jsonl", result.battle_rows)
    _write_jsonl(args.output_dir / "pairing_audit.jsonl", result.audit_rows)
    excluded = Counter(
        row["pairing_exclusion_reason"] for row in result.audit_rows if row.get("pairing_exclusion_reason") is not None
    )
    bounded = Counter(episode["bounded_by"] for episode in result.episodes)
    resolved = sum(
        row.get("defender_resolution") in {"DIRECT_AHEAD", "SESSION_ROSTER"}
        for row in result.audit_rows
    )
    manifest = {
        "schema_version": "c8_battle_episodes_v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_row_count": len(result.audit_rows),
        "c7_gate_source": c7_source,
        "roster_source_row_count": len(roster_rows),
        "missing_roster_sources": missing_roster_sources,
        "eligible_rows": sum(row["pairing_eligible"] for row in result.audit_rows),
        "resolved_immediate_ahead_pairs": resolved,
        "excluded_rows_by_reason": dict(sorted(excluded.items())),
        "battle_count": len(result.episodes),
        "battle_row_count": len(result.battle_rows),
        "bounded_by_distribution": dict(sorted(bounded.items())),
        "pass_count": bounded["PASS"],
        "pair_switch_count": bounded["PAIR_SWITCH"],
        "close_following_criterion": CLOSE_FOLLOWING_CRITERION,
        "close_following_max_distance_m": args.close_following_max_distance_m,
    }
    (args.output_dir / "battle_manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
