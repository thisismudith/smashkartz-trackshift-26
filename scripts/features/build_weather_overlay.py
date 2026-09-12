#!/usr/bin/env python3
"""Build the non-mutating C1 weather overlay for one circuit (M33 / CP-06).

Example (the non-demo local validation path):
    .venv/bin/python scripts/features/build_weather_overlay.py --circuit australian --year 2026 --session Race --output /tmp/weather-overlay
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.registry import assert_registered  # noqa: E402
from trackshift.track.weather import (  # noqa: E402
    WEATHER_OVERLAY_SCHEMA_VERSION,
    build_weather_overlay,
    load_weather_samples,
    resolve_weather_path,
)

SEGMENTS = ROOT / "data" / "processed" / "segments"
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "weather_overlay"
BRITISH_EVENT = "British Grand Prix"


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--circuit", required=True, help="C1 circuit key, for example australian")
    parser.add_argument("--year", default="2026")
    parser.add_argument("--session", action="append", help="Session name; repeat to select several (default: all in C1)")
    parser.add_argument("--segments", type=Path, default=SEGMENTS)
    parser.add_argument("--raw-root", type=Path, default=RAW)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--include-british-final-validation", action="store_true",
                        help="Allow the held-out British GP only for deterministic final validation.")
    args = parser.parse_args()

    import pandas as pd

    segment_path = args.segments / f"circuit={args.circuit}" / "segments.parquet"
    if not segment_path.exists():
        parser.error(f"C1 table not found: {segment_path}")
    c1 = pd.read_parquet(segment_path)
    c1 = c1[c1["year"].astype(str).eq(str(args.year))].copy()
    if args.session:
        c1 = c1[c1["session"].isin(args.session)].copy()
    if c1.empty:
        parser.error("no C1 rows for requested year/session")

    events = sorted(c1["event"].dropna().unique())
    if len(events) != 1:
        parser.error(f"one circuit table must resolve to one event in the requested scope, got {events}")
    event = str(events[0])
    if event == BRITISH_EVENT and not args.include_british_final_validation:
        parser.error("British GP is held out; pass --include-british-final-validation only for final deterministic validation")

    output_rows = []
    session_manifest = []
    for session, segment_rows in c1.groupby("session", sort=True):
        weather_path = resolve_weather_path(args.raw_root, args.year, event, str(session))
        samples = load_weather_samples(weather_path)
        overlay = build_weather_overlay(segment_rows.copy(), samples)
        output_rows.append(overlay)
        session_manifest.append({
            "session": str(session), "rows": int(len(overlay)), "weather_samples": len(samples.samples),
            "weather_source": str(weather_path), "weather_load_reason": samples.reason,
            "pre_first_extrapolated_rows": int(overlay["weather_extrapolated"].sum()),
            "post_final_held_rows": int(overlay["weather_post_final_sample"].sum()),
            "missing_weather_rows": int((~overlay["weather_available"]).sum()),
        })
    result = pd.concat(output_rows, ignore_index=True)
    assert_registered(result.columns, "M33 weather overlay")
    destination = args.output / f"circuit={args.circuit}" / "weather_overlay.parquet"
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(destination, index=False)
    manifest = {
        "schema_version": WEATHER_OVERLAY_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(), "git_commit": _git_commit(),
        "circuit": args.circuit, "year": int(args.year), "event": event,
        "input_c1": str(segment_path), "sessions": session_manifest,
        "rows": int(len(result)), "pre_first_extrapolated_rows": int(result["weather_extrapolated"].sum()),
        "post_final_held_rows": int(result["weather_post_final_sample"].sum()),
        "missing_weather_rows": int((~result["weather_available"]).sum()),
        "join_key": ["year", "event", "session", "driver", "lap", "segment_id", "geometry_version", "boundary_hash"],
        "raw_root_policy": "prefer data/raw/tracinginsights/<year>, otherwise data/raw/<year>",
        "british_gp_policy": "held out by default; explicit deterministic-final-validation switch required",
    }
    (args.output / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
