#!/usr/bin/env python3
"""Materialise C7 race context onto C1 segment rows.

The C1 segment producer intentionally keeps its input immutable and does not
carry lap-level pit timestamps.  This small adapter joins the matching
telemetry-20m lap metadata, calls the public C7 normaliser, and writes a
separate segment table for downstream model builders.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.features.tyre_pace import derive_lap_c7_context  # noqa: E402

DEFAULT_SEGMENTS = ROOT / "data" / "processed" / "segments"
DEFAULT_LAKE = ROOT / "data" / "processed" / "telemetry_20m"


def _safe(value: str) -> str:
    return str(value).replace(" ", "_")


def _load_lap_metadata(lake: Path, year: str, event: str, session: str):
    import pandas as pd

    path = lake / f"year={year}" / f"event={_safe(event)}" / f"session={_safe(session)}" / "telemetry_20m.parquet"
    if not path.exists():
        raise ValueError(f"matching telemetry-20m partition is absent: {path}")
    columns = [
        "year", "event", "session", "driver", "lap", "track_status",
        "lap_start_session_s", "pit_in_session_s", "pit_out_session_s",
    ]
    frame = pd.read_parquet(path, columns=columns).drop_duplicates(["year", "event", "session", "driver", "lap"])
    return frame


def _load_segments(path: Path, circuit: str, year: str, event: str, session: str):
    import pandas as pd

    source = path / f"circuit={circuit}" / "segments.parquet"
    if not source.exists():
        raise ValueError(f"C1 segment artifact is absent: {source}")
    frame = pd.read_parquet(source)
    for key, value in (("year", year), ("event", event), ("session", session)):
        frame = frame[frame[key].astype(str) == str(value)]
    if frame.empty:
        raise ValueError("selected C1 segment input is empty")
    return frame, source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments-dir", type=Path, default=DEFAULT_SEGMENTS)
    parser.add_argument("--lake-dir", type=Path, default=DEFAULT_LAKE)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--circuit", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()

    c1, source = _load_segments(args.segments_dir, args.circuit, args.year, args.event, args.session)
    lap_metadata = _load_lap_metadata(args.lake_dir, args.year, args.event, args.session)
    c7 = derive_lap_c7_context(lap_metadata)
    c7_columns = [
        "year", "event", "session", "driver", "lap", "pit_state",
        "normalized_race_control_state", "safety_car_active", "virtual_safety_car_active",
        "race_control_transition_flag", "pit_transition_flag", "green_flag_elapsed_s",
        "normal_race_model_eligible",
    ]
    output = c1.merge(c7[c7_columns], on=["year", "event", "session", "driver", "lap"], how="left", validate="many_to_one")
    output_dir = args.output_dir / f"circuit={args.circuit}"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "segments.parquet"
    output.to_parquet(destination, index=False)
    manifest: dict[str, Any] = {
        "schema_version": "c7_race_context_materialized_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_c1": str(source),
        "source_telemetry_20m": str(args.lake_dir / f"year={args.year}" / f"event={_safe(args.event)}" / f"session={_safe(args.session)}" / "telemetry_20m.parquet"),
        "output": str(destination),
        "row_count": len(output),
        "normal_race_model_eligible_rows": int(output["normal_race_model_eligible"].eq(True).sum()),
        "normal_race_model_ineligible_rows": int(output["normal_race_model_eligible"].ne(True).sum()),
        "c7_source": "telemetry_20m_lap_metadata_v1",
        "transition_guard_rows": 1,
    }
    (args.output_dir / "race_context_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
