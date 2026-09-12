#!/usr/bin/env python3
"""Build portable C9 split assignments from CSV or JSONL metadata."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.splits import build_split_manifest, make_split


def load_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    raise ValueError("input must be a .csv or .jsonl file")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--design", required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    rows = load_rows(args.input)
    assignments = make_split(rows, args.unit, args.design, args.seed)
    manifest = build_split_manifest(assignments, args.unit, args.design, args.seed, len(rows))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    assignment_path = args.output_dir / "split_assignments.jsonl"
    with assignment_path.open("w", encoding="utf-8") as handle:
        for row in assignments:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (args.output_dir / "split_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"assignments": len(assignments), "manifest": manifest["assignment_version"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
