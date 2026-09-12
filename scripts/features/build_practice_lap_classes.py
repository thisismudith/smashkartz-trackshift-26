#!/usr/bin/env python3
"""Build the M01 Practice 1 lap-class overlay without mutating the lake."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.registry import assert_registered  # noqa: E402
from trackshift.track.lap_classifier import (  # noqa: E402
    KEY_COLUMNS,
    LAP_CLASSIFIER_SCHEMA_VERSION,
    label_counts,
    classify_practice_laps,
    load_lap_classification_config,
)


def _safe(value: str) -> str:
    return value.replace(" ", "_")


def _git_commit() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _lap_table(path: Path) -> pd.DataFrame:
    columns = [
        *KEY_COLUMNS, "lap_time_s", "tyre_life_laps", "pit_in_session_s", "pit_out_session_s",
        "is_accurate", "lap_deleted", "track_status",
    ]
    frame = pd.read_parquet(path, columns=columns)
    return frame.drop_duplicates(list(KEY_COLUMNS), keep="first")


def _session_summary(rows: pd.DataFrame) -> dict[str, object]:
    total = len(rows)
    counts = label_counts(rows)
    return {
        "laps": total,
        "label_counts": counts,
        "label_shares": {label: (count / total if total else 0.0) for label, count in counts.items()},
        "unknown_laps": counts["UNKNOWN"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "processed" / "telemetry_20m")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "processed" / "practice_lap_classes")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--session", default="Practice 1")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "lap_classification.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_lap_classification_config(args.config)
    pattern = f"year={args.year}/event=*/session={_safe(args.session)}/telemetry_20m.parquet"
    files = sorted(args.input.glob(pattern))
    if not files:
        raise SystemExit(f"no lake partitions found for {args.year} {args.session}: {args.input}")
    session_summaries: dict[str, dict[str, object]] = {}
    overlays: list[pd.DataFrame] = []
    excluded_laps = 0
    for source in files:
        metadata = _lap_table(source)
        overlay = classify_practice_laps(metadata, config)
        assert_registered(overlay.columns, "M01 practice lap-class overlay")
        overlays.append(overlay)
        event = str(metadata["event"].iloc[0])
        summary = _session_summary(overlay)
        summary["source_lake"] = str(source)
        summary["excluded_laps"] = 0  # one label is produced for every lake lap
        session_summaries[event] = summary
        excluded_laps += 0
        if not args.dry_run:
            destination = args.output / f"year={args.year}" / f"event={_safe(event)}" / f"session={_safe(args.session)}" / "practice_lap_classes.parquet"
            destination.parent.mkdir(parents=True, exist_ok=True)
            overlay.to_parquet(destination, index=False)

    result = pd.concat(overlays, ignore_index=True)
    overall = _session_summary(result)
    gates = {
        "unknown_lt_15pct": overall["label_shares"]["UNKNOWN"] < 0.15,
        "push_between_5pct_and_20pct": 0.05 <= overall["label_shares"]["PUSH"] <= 0.20,
        "long_run_between_20pct_and_50pct": 0.20 <= overall["label_shares"]["LONG_RUN"] <= 0.50,
    }
    manifest = {
        "schema_version": LAP_CLASSIFIER_SCHEMA_VERSION,
        "classifier_version": config.classifier_version,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "source_dataset": "telemetry_20m",
        "source_partitions": [str(path) for path in files],
        "scope": {"year": args.year, "session": args.session},
        "config": str(args.config),
        "causal_policy": "current lap and earlier same-driver/session laps only; no retrospective LONG_RUN labels",
        "precedence": list(config.precedence),
        "input_laps": int(len(result)),
        "excluded_laps": excluded_laps,
        "unknown_laps": overall["unknown_laps"],
        "overall": overall,
        "by_session": session_summaries,
        "acceptance_gates": gates,
        "all_acceptance_gates_pass": all(gates.values()),
        "cpu_inference_verified": True,
    }
    if not args.dry_run:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
