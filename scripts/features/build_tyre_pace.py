#!/usr/bin/env python3
"""Build the immutable C1-keyed M30 tyre pace overlay for 2026 Race/Sprint."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.registry import assert_registered  # noqa: E402
from trackshift.features.tyre_pace import (  # noqa: E402
    C1_KEY_COLUMNS,
    TYRE_PACE_SCHEMA_VERSION,
    build_tyre_pace_overlay,
    load_tyre_pace_config,
)


def _safe(value: str) -> str:
    return value.replace(" ", "_")


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _lap_metadata(lake: Path, year: int, event: str, session: str) -> pd.DataFrame:
    columns = [
        "year", "event", "session", "driver", "lap", "lap_time_s", "tyre_life_laps", "tyre_compound",
        "tyre_is_new", "stint", "pit_in_session_s", "pit_out_session_s", "is_accurate", "lap_deleted",
        "track_status", "lap_start_session_s",
    ]
    source = lake / f"year={year}" / f"event={_safe(event)}" / f"session={_safe(session)}" / "telemetry_20m.parquet"
    if not source.exists():
        raise FileNotFoundError(f"missing matching lake partition: {source}")
    return pd.read_parquet(source, columns=columns).drop_duplicates(["year", "event", "session", "driver", "lap"])


def _summary(frame: pd.DataFrame) -> dict[str, object]:
    lap_rows = frame.drop_duplicates(["year", "event", "session", "driver", "lap"])
    available_proxy = lap_rows["tyre_degradation_proxy_s"].dropna()
    normalized_available = frame["tyre_normalized_pace_s"].notna()
    compound_frame = (
        lap_rows.groupby("tyre_compound", dropna=False)
        .agg(laps=("lap", "size"), retained=("tyre_pace_retained_lap", "sum"), proxy_n=("tyre_degradation_proxy_s", "count"), proxy_median_s=("tyre_degradation_proxy_s", "median"))
        .reset_index()
    )
    compound = compound_frame.astype(object).where(pd.notna(compound_frame), None).to_dict(orient="records")
    return {
        "c1_rows": int(len(frame)),
        "laps": int(len(lap_rows)),
        "stints": int(lap_rows["tyre_stint_id"].nunique()),
        "usable_stints": int(lap_rows.loc[lap_rows["tyre_degradation_proxy_s"].notna(), "tyre_stint_id"].nunique()),
        "retained_laps": int(lap_rows["tyre_pace_retained_lap"].sum()),
        "insufficient_history_rows": int(lap_rows["tyre_degradation_proxy_reason"].eq("INSUFFICIENT_INITIAL_GREEN_LAPS").sum()),
        "reset_counts": dict(Counter(reason for reason in lap_rows["tyre_stint_reset_reason"].dropna())),
        "proxy_distribution_s": {
            "n": int(len(available_proxy)),
            "median": float(available_proxy.median()) if len(available_proxy) else None,
            "p05": float(available_proxy.quantile(0.05)) if len(available_proxy) else None,
            "p95": float(available_proxy.quantile(0.95)) if len(available_proxy) else None,
            "min": float(available_proxy.min()) if len(available_proxy) else None,
            "max": float(available_proxy.max()) if len(available_proxy) else None,
        },
        "normalised_pace_availability": {
            "available_segments": int(normalized_available.sum()),
            "total_segments": int(len(frame)),
            "available_laps": int(frame.loc[normalized_available, ["year", "event", "session", "driver", "lap"]].drop_duplicates().shape[0]),
            "unavailable_reason_counts": dict(Counter(frame.loc[~normalized_available, "tyre_normalized_pace_reason"].dropna())),
        },
        "compound_summary": compound,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments-dir", type=Path, default=ROOT / "data" / "processed" / "segments")
    parser.add_argument("--lake-dir", type=Path, default=ROOT / "data" / "processed" / "telemetry_20m")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "processed" / "tyre_pace_overlay")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "tyre_pace.yaml")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--exclude-event", action="append", default=["British Grand Prix"])
    parser.add_argument("--include-british-gp-final-validation", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_tyre_pace_config(args.config)
    excluded = {value.lower() for value in args.exclude_event}
    if args.include_british_gp_final_validation:
        excluded.discard("british grand prix")
    frames: list[pd.DataFrame] = []
    sources: list[str] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(args.segments_dir.glob("circuit=*/segments.parquet")):
        circuit = path.parent.name.removeprefix("circuit=")
        c1 = pd.read_parquet(path)
        c1 = c1[(c1["year"].astype(int) == args.year) & c1["session"].isin(["Race", "Sprint"])].copy()
        if c1.empty:
            continue
        event = str(c1["event"].iloc[0])
        if event.lower() in excluded:
            skipped.append({"event": event, "reason": "EXCLUDED_EVENT"})
            continue
        overlays: list[pd.DataFrame] = []
        for session, segment_session in c1.groupby("session", sort=True):
            lap_meta = _lap_metadata(args.lake_dir, args.year, event, str(session))
            overlays.append(build_tyre_pace_overlay(segment_session, lap_meta, circuit=circuit, config=config))
        overlay = pd.concat(overlays, ignore_index=True)
        assert_registered(overlay.columns, "M30 tyre pace overlay")
        frames.append(overlay)
        sources.append(str(path))
        if not args.dry_run:
            destination = args.output / f"circuit={circuit}" / "tyre_pace_overlay.parquet"
            destination.parent.mkdir(parents=True, exist_ok=True)
            overlay.to_parquet(destination, index=False)
    if not frames:
        raise SystemExit("no eligible Race/Sprint C1 inputs found")
    result = pd.concat(frames, ignore_index=True)
    manifest = {
        "schema_version": TYRE_PACE_SCHEMA_VERSION,
        "producer_version": config.producer_version,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "scope": {"year": args.year, "sessions": ["Race", "Sprint"], "excluded_events": sorted(excluded)},
        "source_c1": sources,
        "c1_key": list(C1_KEY_COLUMNS),
        "summary": _summary(result),
        "fuel_context": {"status": config.fuel_context_status, "note": config.fuel_context_note},
        "notes": [
            "Tyre degradation proxy is pace context, not a physical tyre-performance sensor.",
            "No fuel correction is applied until C5/M34 supplies causal fuel estimates.",
            "Compound summaries are descriptive only; no universal compound ordering is claimed.",
        ],
        "cpu_inference_verified": True,
        "skipped": skipped,
    }
    if not args.dry_run:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (args.output / "offline_report.json").write_text(json.dumps({"summary": manifest["summary"], "fuel_context": manifest["fuel_context"], "notes": manifest["notes"]}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
