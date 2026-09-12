#!/usr/bin/env python3
"""Build causal M06 rows from C8 battle rows and C1 segments."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.features.api import (  # noqa: E402
    PAIRWISE_FEATURE_SCHEMA, PAIRWISE_FEATURE_SCHEMA_VERSION, build_pairwise_features,
)

DEFAULT_C8 = ROOT / "data" / "processed" / "battle_episodes" / "battle_segment_rows.jsonl"
DEFAULT_C1 = ROOT / "data" / "processed" / "segments"
DEFAULT_C2_DRIVER = ROOT / "data" / "processed" / "driver_segment_baselines" / "driver_segment_baselines.parquet"
DEFAULT_OUT = ROOT / "data" / "processed" / "pairwise_segment_features"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _filter(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    return [row for row in rows if all(getattr(args, key) is None or str(row.get(key)) == str(getattr(args, key)) for key in ("year", "event", "session"))]


def _load_c1(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    import pandas as pd

    paths = sorted(args.segments_dir.glob("circuit=*/segments.parquet"))
    if args.circuit:
        paths = [path for path in paths if path.parent.name == f"circuit={args.circuit}"]
    if not paths:
        raise ValueError(f"no C1 segment files under {args.segments_dir}")
    frames = []
    for path in paths:
        frame = pd.read_parquet(path)
        frame["circuit"] = path.parent.name.removeprefix("circuit=")
        frames.append(frame)
    frame = pd.concat(frames, ignore_index=True)
    for key in ("year", "event", "session"):
        value = getattr(args, key)
        if value is not None:
            frame = frame[frame[key].astype(str) == str(value)]
    return frame.where(frame.notna(), None).to_dict(orient="records"), [str(path) for path in paths]


def _load_driver_baselines(path: Path) -> list[dict[str, Any]]:
    import pandas as pd

    if not path.exists():
        raise ValueError(
            f"C2 driver-baseline artifact is absent: {path}. Run scripts/features/build_baselines.py; do not substitute values."
        )
    return pd.read_parquet(path).where(lambda frame: frame.notna(), None).to_dict(orient="records")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--battle-rows", type=Path, default=DEFAULT_C8)
    parser.add_argument("--segments-dir", type=Path, default=DEFAULT_C1)
    parser.add_argument("--driver-baselines", type=Path, default=DEFAULT_C2_DRIVER)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--circuit")
    parser.add_argument("--year")
    parser.add_argument("--event")
    parser.add_argument("--session")
    args = parser.parse_args()

    c8_rows = _filter(_load_jsonl(args.battle_rows), args)
    if not c8_rows:
        raise ValueError("selected C8 battle rows are empty")
    c1_rows, c1_paths = _load_c1(args)
    c2_driver_rows = _load_driver_baselines(args.driver_baselines)
    result = build_pairwise_features(c8_rows, c1_rows, driver_baseline_rows=c2_driver_rows)

    import pandas as pd
    from trackshift.data.registry import assert_registered, feature

    frame = pd.DataFrame(result.rows)
    assert_registered(frame.columns, "pairwise feature build")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "pairwise_segment_features.parquet"
    frame.to_parquet(output_path, index=False)
    unavailable = Counter()
    for row in result.rows:
        unavailable.update(row["unavailable_quantities"].keys())
    emitted_feature_schema = {
        column: {
            key: feature(column)[key]
            for key in ("definition", "unit", "provenance", "live_safe", "availability_scope", "causal_status")
        }
        for column in frame.columns
    }
    manifest = {
        "schema_version": PAIRWISE_FEATURE_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_inputs": {
            "c8_battle_rows": str(args.battle_rows),
            "c1_segments": c1_paths,
            "c7_eligibility_and_transition_fields": "embedded in C8 model-ready battle rows",
            "c2_driver_baselines": str(args.driver_baselines),
        },
        "source_row_counts": {"c8_battle_rows": len(c8_rows), "c1_segments": len(c1_rows), "c2_driver_baselines": len(c2_driver_rows)},
        "output_row_count": len(result.rows),
        "feature_schema": emitted_feature_schema,
        "core_live_feature_schema": PAIRWISE_FEATURE_SCHEMA,
        "causal_cutoff_policy": "current C8/C1 row plus at most two preceding rows in the same battle; C7 transition rows reset history and are excluded",
        "excluded_rows_by_reason": result.excluded_rows_by_reason,
        "missing_defender_segments": result.missing_defender_segments,
        "defender_alignment_gaps_by_reason": result.defender_alignment_gaps_by_reason,
        "unavailable_quantity_counts": dict(sorted(unavailable.items())),
    }
    (args.output_dir / "pairwise_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
