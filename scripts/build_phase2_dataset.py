#!/usr/bin/env python3
"""Build a validated, distance-resampled Phase 2 telemetry dataset."""
from __future__ import annotations
import argparse, csv, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trackshift import SCHEMA_VERSION
from trackshift.data.registry import assert_registered
from trackshift.raw_loader import discover_laps, load_lap_metadata, load_raw_lap
from trackshift.resample import CONTINUOUS, DISCRETE, resample_lap

def safe_name(value: str) -> str: return value.replace(" ", "_")
def write_csv(path: Path, rows: list[dict]) -> None:
    keys = sorted({key for row in rows for key in row})
    if not keys: keys = ["year", "event", "session", "driver", "lap", "source_path", "validation_status", "rejection_code", "rejection_reason", "source_rows", "output_rows", "output_file_path"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys); writer.writeheader(); writer.writerows(rows)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True); parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--year", required=True); parser.add_argument("--event", required=True); parser.add_argument("--session", required=True)
    parser.add_argument("--drivers", nargs="+"); parser.add_argument("--laps", nargs="+", type=int); parser.add_argument("--max-laps-per-driver", type=int)
    parser.add_argument("--spacing-m", type=float, default=20.0); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--allow-unregistered", action="store_true",
                        help="Write columns that are not in the feature registry. Escape hatch for "
                             "local experiments only; a committed build must never need it (section 46).")
    args = parser.parse_args()
    if args.spacing_m <= 0: parser.error("--spacing-m must be positive")
    raw_laps = discover_laps(args.raw_root, args.year, args.event, args.session, args.drivers, args.laps, args.max_laps_per_driver)
    manifest, rejected, quality, resampled_rows = [], [], [], []
    for raw_lap in raw_laps:
        payload, result = load_raw_lap(raw_lap)
        base = {"year":raw_lap.year, "event":raw_lap.event, "session":raw_lap.session, "driver":raw_lap.driver, "lap":raw_lap.lap, "source_path":str(raw_lap.path)}
        item = {**base, "validation_status":result.status, "rejection_code":result.rejection_code, "rejection_reason":result.rejection_reason, "source_rows":result.source_rows, "output_rows":0, "output_file_path":""}
        quality.append({**base, "validation_status":result.status, "rejection_code":result.rejection_code, **result.metrics})
        if result.status == "ACCEPTED":
            rows = resample_lap(payload["tel"], {key:base[key] for key in ("year", "event", "session", "driver", "lap")}, load_lap_metadata(raw_lap), str(raw_lap.path), result.source_rows, args.spacing_m)
            item["output_rows"] = len(rows); resampled_rows.extend(rows)
        else: rejected.append(item)
        manifest.append(item)
    output_file = args.output_root / f"year={args.year}" / f"event={safe_name(args.event)}" / f"session={safe_name(args.session)}" / "telemetry_20m.parquet"
    for item in manifest:
        if item["output_rows"]: item["output_file_path"] = str(output_file)
    if args.dry_run:
        print(json.dumps({"discovered_laps":len(raw_laps), "accepted_laps":sum(item["validation_status"] == "ACCEPTED" for item in manifest), "rejected_laps":len(rejected)}, indent=2)); return 0
    try:
        import pandas as pd
        import pyarrow  # noqa: F401
    except ImportError:
        raise SystemExit("Parquet output requires pandas and pyarrow. Install them with: pip install pandas pyarrow")
    # Registration is a gate, not a lint: a dataset that writes an unregistered
    # column is undocumented output, and the registry silently drifting from the
    # real schema is the failure this check exists to prevent (M31, section 46).
    if resampled_rows and not args.allow_unregistered:
        assert_registered(sorted({key for row in resampled_rows for key in row}),
                          f"telemetry_20m build {args.year} {args.event} {args.session}")
    args.output_root.mkdir(parents=True, exist_ok=True)
    if resampled_rows:
        output_file.parent.mkdir(parents=True, exist_ok=True); pd.DataFrame(resampled_rows).to_parquet(output_file, index=False)
    write_csv(args.output_root / "lap_manifest.csv", manifest); write_csv(args.output_root / "rejected_laps.csv", rejected); write_csv(args.output_root / "quality_summary.csv", quality)
    run_manifest = {"schema_version":SCHEMA_VERSION, "created_utc":datetime.now(timezone.utc).isoformat(), "command_arguments":vars(args), "source_roots":[str(args.raw_root)], "discovered_laps":len(raw_laps), "accepted_laps":sum(item["validation_status"] == "ACCEPTED" for item in manifest), "rejected_laps":len(rejected), "resampling_policy":{"spacing_m":args.spacing_m, "continuous_linear":list(CONTINUOUS), "discrete_preceding_zero_order_hold":list(DISCRETE)}}
    (args.output_root / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"discovered_laps":len(raw_laps), "accepted_laps":run_manifest["accepted_laps"], "rejected_laps":len(rejected), "resampled_rows":len(resampled_rows), "output":str(output_file) if resampled_rows else None}, indent=2))
    return 0
if __name__ == "__main__": raise SystemExit(main())
