#!/usr/bin/env python3
"""Audit and evaluate the CP-08 rival-era comparison without writing data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.rival.api import (  # noqa: E402
    c10_prediction_evidence,
    evaluate_era_strategies,
    load_m08_sequences,
    load_model,
    materialise_historical_m08,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = ROOT / "data/processed/cp05_full_nonbritish_validation_v3"
    parser.add_argument("--processed-root", type=Path, default=root)
    parser.add_argument("--historical-processed-root", type=Path, default=None,
                        help="Versioned historical C7/C8/M06/C9/M08 root; no materialisation is performed here")
    parser.add_argument("--model-artifact", type=Path, default=ROOT / "data/processed/cp07_rival_benchmark_v1/selected_model.json")
    args = parser.parse_args()
    audit = materialise_historical_m08(processed_root=args.historical_processed_root or args.processed_root)
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
    historical_rows: list[dict] = []
    if args.historical_processed_root:
        historical_root = args.historical_processed_root
        historical_m08 = historical_root / "m08"
        historical_c9 = historical_root / "c9/split_assignments.jsonl"
        if historical_m08.exists() and historical_c9.exists():
            historical_sequences = load_m08_sequences(historical_m08, historical_c9)
            historical_rows = [row for sequence in historical_sequences for row in sequence["rows"]]
    evidence: list[dict] = []
    if args.model_artifact.exists() and current_rows:
        model = load_model(args.model_artifact, expected_split_version=split_version)
        evidence = c10_prediction_evidence(model, sequences)
    report = evaluate_era_strategies(current_rows, split_version=split_version,
                                     rule_configuration_version="2026-config-unavailable",
                                     historical_rows=historical_rows, materialisation=audit,
                                     prediction_evidence={"2026_only": evidence})
    print(json.dumps({"materialisation": audit, "comparison": report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
