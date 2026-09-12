#!/usr/bin/env python3
"""Build C2 segment-baseline tables from C1 segments.

British GP is excluded by default because it is the held-out demo event.  The
only inclusion switch is intentionally verbose and is for a frozen final replay
only: ``--include-british-gp-final-replay``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.track.api import BASELINE_SCHEMA_VERSION, build_segment_baselines  # noqa: E402

SEGMENTS = ROOT / "data" / "processed" / "segments"
OUT_ROOT = ROOT / "data" / "processed"
CONFIG = ROOT / "config" / "baselines.yaml"


def _load_config(path: Path) -> dict[str, Any]:
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"baseline config {path} is not a mapping")
    return data


def _load_segments(directory: Path, circuit: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    import pandas as pd

    paths = sorted(directory.glob("circuit=*/segments.parquet"))
    if circuit is not None:
        paths = [path for path in paths if path.parent.name == f"circuit={circuit}"]
    if not paths:
        raise ValueError(f"no C1 segments found under {directory}")
    rows: list[dict[str, Any]] = []
    for path in paths:
        frame = pd.read_parquet(path)
        circuit_key = path.parent.name.removeprefix("circuit=")
        frame["circuit"] = circuit_key
        frame = frame.where(frame.notna(), None)
        rows.extend(frame.to_dict(orient="records"))
    return rows, [str(path) for path in paths]


def _write_table(root: Path, level: str, rows: list[dict[str, Any]]) -> str:
    import pandas as pd

    target = root / f"{level}_segment_baselines"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{level}_segment_baselines.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return str(path)


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments-dir", type=Path, default=SEGMENTS)
    parser.add_argument("--output-root", type=Path, default=OUT_ROOT)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--circuit")
    parser.add_argument("--allow-legacy-c7-track-status-bridge", action="store_true")
    parser.add_argument("--include-british-gp-final-replay", action="store_true")
    args = parser.parse_args()

    config = _load_config(args.config)
    source_rows, source_paths = _load_segments(args.segments_dir, args.circuit)
    result = build_segment_baselines(
        source_rows,
        minimum_samples=config.get("minimum_samples"),
        year_groups=config.get("year_groups"),
        legacy_c7_track_status_bridge=config.get("legacy_c7_track_status_bridge"),
        allow_legacy_c7_track_status_bridge=args.allow_legacy_c7_track_status_bridge,
        include_british_gp=args.include_british_gp_final_replay,
    )
    tables = {
        "driver": _write_table(args.output_root, "driver", result.driver),
        "team": _write_table(args.output_root, "team", result.team),
        "field": _write_table(args.output_root, "field", result.field),
    }
    manifest = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "source_datasets": {"c1_segments": source_paths, "c7_source": result.c7_source},
        "source_row_count": len(source_rows),
        "eligible_source_row_count": result.eligible_source_rows,
        "excluded_rows_by_reason": result.excluded_rows_by_reason,
        "year_groups": config.get("year_groups"),
        "thresholds": config.get("minimum_samples"),
        "legacy_c7_track_status_bridge": config.get("legacy_c7_track_status_bridge") if "legacy" in result.c7_source else None,
        "british_gp_included": args.include_british_gp_final_replay,
        "tables": tables,
        "row_counts_by_level": {level: len(getattr(result, level)) for level in ("driver", "team", "field")},
        "baseline_valid_counts_by_level": {
            level: sum(row["baseline_valid"] for row in getattr(result, level))
            for level in ("driver", "team", "field")
        },
        "metric_schema": {
            "segment_time_s": {"unit": "s", "provenance": "DERIVED"},
            "exit_speed_kmh": {"unit": "km/h", "provenance": "DERIVED"},
            "brake_onset_m": {"unit": "m", "provenance": "DERIVED"},
            "full_throttle_fraction": {"unit": "ratio", "provenance": "DERIVED"},
            "max_speed_kmh": {"unit": "km/h", "provenance": "DERIVED"},
        },
        "residual_policy": "C2 does not write residuals; consumers compute current_value - baseline_median at use time.",
    }
    manifest_path = args.output_root / "segment_baselines_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
