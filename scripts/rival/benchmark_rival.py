#!/usr/bin/env python3
"""Run the CP-07 M09/M09b -> C10 rival-state benchmark.

Only versioned CP-05 M08 Parquet and persistent C9 assignments are consumed.
The generated directory lives below ``data/processed`` and is intentionally
ignored by Git.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.rival.api import (  # noqa: E402
    BENCHMARK_SEED,
    REGRESSION_SEED,
    SyntheticConfig,
    benchmark_candidates,
    generate,
    load_m08_sequences,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = ROOT / "data/processed/cp05_full_nonbritish_validation_v3"
    parser.add_argument("--m08-root", type=Path, default=default_root / "m08")
    parser.add_argument("--c9-assignments", type=Path, default=default_root / "c9/split_assignments.jsonl")
    parser.add_argument("--cp05-manifest", type=Path, default=default_root / "run_manifest.json")
    parser.add_argument("--c9-manifest", type=Path, default=default_root / "c9/split_manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/cp07_rival_benchmark_v1")
    return parser.parse_args()


def main() -> int:
    args = _args()
    sequences = load_m08_sequences(args.m08_root, args.c9_assignments, cp05_manifest=args.cp05_manifest)
    c9_manifest = json.loads(args.c9_manifest.read_text(encoding="utf-8"))
    split_version = str(c9_manifest["assignment_version"])
    regression = generate(SyntheticConfig(seed=REGRESSION_SEED, sequences=48, length=32))
    benchmark = generate(SyntheticConfig(seed=BENCHMARK_SEED, sequences=48, length=32))
    result = benchmark_candidates(regression, benchmark, sequences, c9_split_version=split_version)
    result["training_evaluation_contract"] = {
        "synthetic_regression_seed": REGRESSION_SEED,
        "synthetic_benchmark_seed": BENCHMARK_SEED,
        "synthetic_sequences": 48,
        "synthetic_length": 32,
        "real_evaluation_split": "persistent C9 battle_id kfold:5",
        "identical_c9_splits_for_all_candidates": True,
        "british_gp_excluded": True,
    }

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    models = result.pop("_models")
    for name, model in models.items():
        (output / f"{name}.json").write_text(json.dumps(model.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    selected = str(result["selected"])
    selected_path = output / "selected_model.json"
    selected_path.write_text(json.dumps(models[selected].to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path = output / "benchmark_report.json"
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# CP-07 Rival-state benchmark (M09 → C10)", "",
        f"Selected `{selected}` from identical fixed-seed synthetic and persistent C9 evaluations.",
        "Real M08 has no tactical-state labels; its score is next-segment observation predictive likelihood, not tactical-state calibration.", "",
        "| Candidate | Recovery @5 / @10 / @20 | Real mean NLL | Stability Δmax | CPU ms/sequence |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, metrics in result["candidates"].items():
        recovery = metrics["synthetic_recovery"]
        lines.append(f"| `{name}` | {recovery['5']['accuracy']:.3f} / {recovery['10']['accuracy']:.3f} / {recovery['20']['accuracy']:.3f} | {metrics['real_next_segment']['mean_nll']:.3f} | {metrics['stability']['mean_max_abs_delta']:.5f} | {metrics['cpu_latency_ms_per_sequence']:.5f} |")
    lines.extend([
        "", f"State space: `{', '.join(result['state_space'])}`. {result['merge_decision']}.",
        "British Grand Prix was excluded from training/calibration; only non-British 2026 M08 was consumed.",
        "", "## CP-08 handoff", "",
        "Historical M08 materialisation for 2022–2025 is the next blocker. A regulation-era comparison is not possible from 2026-only M08 data; CP-08 remains unchecked.", "",
    ])
    (output / "CP07_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    manifest = {
        "schema_version": "c10_rival_artifacts_manifest_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        "model_version": result["model_version"],
        "m08_schema_version": "m08_rival_state_features_v1",
        "c9_split_version": split_version,
        "c9_manifest": str(args.c9_manifest),
        "cp05_manifest": str(args.cp05_manifest),
        "source": "CP-05 full non-British 2026 M08 only",
        "source_datasets": [str(args.m08_root), str(args.c9_assignments), str(args.cp05_manifest)],
        "years_events": {"years": [2026], "event_scope": "non-British", "holdout": "British Grand Prix"},
        "config": {"candidate_order": ["interpretable_hmm", "dwell_aware_hsmm_equivalent", "rolling_window_gbm"], "real_split": "persistent C9 battle_id kfold:5"},
        "feature_schema": ["schema_version", "feature_fields", "causal_cutoff", "provenance"],
        "model_params": {"selected_candidate": selected, "states": list(models[selected].states), "duration_equivalent": models[selected].duration_equivalent},
        "seed": {"synthetic_regression": REGRESSION_SEED, "synthetic_benchmark": BENCHMARK_SEED, "stability": 911},
        "device_trained_on": "CPU",
        "cpu_inference_verified": True,
        "british_gp_used_for_training_or_calibration": False,
        "real_sequence_count": len(sequences),
        "real_row_count": sum(len(sequence["rows"]) for sequence in sequences),
        "candidate_artifacts": sorted(path.name for path in output.glob("*.json") if path.name not in {"run_manifest.json", "benchmark_report.json", "selected_model.json"}),
        "selected_model": selected_path.name,
        "benchmark_report": report_path.name,
        "human_report": "CP07_REPORT.md",
        "c10": {
            "api": "trackshift.rival.api.rival_state",
            "provenance": "INFERRED",
            "states": list(models[selected].states),
            "merged_states": models[selected].merged_states,
            "probabilities_sum_to_one": True,
            "failure_without_artifact": "ModelUnavailableError",
        },
        "cp08_handoff": "Historical M08 materialisation for 2022-2025 is required next; 2026-only M08 cannot support a regulation-era comparison.",
    }
    (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "selected": selected, "real_sequences": len(sequences), "real_rows": manifest["real_row_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
