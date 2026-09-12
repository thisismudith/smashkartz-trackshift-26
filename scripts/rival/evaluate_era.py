#!/usr/bin/env python3
"""Audit and evaluate the CP-08 rival-era comparison without writing data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.rival.api import evaluate_era_strategies, load_m08_sequences, materialise_historical_m08  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = ROOT / "data/processed/cp05_full_nonbritish_validation_v3"
    parser.add_argument("--processed-root", type=Path, default=root)
    args = parser.parse_args()
    audit = materialise_historical_m08(processed_root=args.processed_root)
    current_rows: list[dict] = []
    m08 = args.processed_root / "m08"
    c9 = args.processed_root / "c9/split_assignments.jsonl"
    manifest = args.processed_root / "run_manifest.json"
    split_manifest = args.processed_root / "c9/split_manifest.json"
    if m08.exists() and c9.exists() and manifest.exists():
        sequences = load_m08_sequences(m08, c9, cp05_manifest=manifest)
        current_rows = [row for sequence in sequences for row in sequence["rows"]]
    split_version = "unavailable"
    if split_manifest.exists():
        split_version = str(json.loads(split_manifest.read_text(encoding="utf-8")).get("assignment_version", split_version))
    report = evaluate_era_strategies(current_rows, split_version=split_version, rule_configuration_version="2026-config-unavailable", materialisation=audit)
    print(json.dumps({"materialisation": audit, "comparison": report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
