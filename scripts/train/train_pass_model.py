#!/usr/bin/env python3
"""Train and score the M10 pass-model benchmark (Tanveer CP-14).

Three decision checkpoints times five model families, selected on calibration
rather than accuracy (AGENTS.md section 26), with the 2026 British Grand Prix
frozen out of every training and validation split (section 40).

    python scripts/train/train_pass_model.py --dry-run
    python scripts/train/train_pass_model.py
    python scripts/train/train_pass_model.py --identity-ablation

``--dry-run`` reports the dataset, the split plan, the per-checkpoint feature
selection and the hardware plan without fitting anything. Run it first: it is
where a missing column, an unbuildable split or a surprising feature exclusion
shows up, and it takes a second.

Two runs write two artifacts, never one silently overwritten: ``--version``
names the directory under ``artifacts/models/pass/``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.pass_model.api import (  # noqa: E402
    DEFAULT_SEED,
    DESIGNS,
    FAMILIES,
    Fold,
    aggregate,
    audit_feature_matrix,
    assert_disjoint,
    available_families,
    build_matrix,
    check_gates,
    fit_cell,
    git_commit,
    identity_comparison,
    plan_hardware,
    plan_splits,
    rank_results,
    render_report,
    run_benchmark,
    select_features,
    summarise,
    write_fit_artifact,
)
from trackshift.data.guards import DemoScope  # noqa: E402
from trackshift.features.opportunities import CHECKPOINTS, LABEL_DEFINITION, OPPORTUNITY_SCHEMA_VERSION  # noqa: E402

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
MODELS_ROOT = ROOT / "artifacts" / "models" / "pass"
REPORT = ROOT / "artifacts" / "validation" / "pass_model_report.md"
CONFIG_FILES = ("config/feature_registry.yaml", "config/data_registry.yaml")


def _display(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute when it is not.

    ``--report`` may legitimately point outside the tree (a scratch run, a shared
    drive), and a bare ``relative_to`` raises there -- which would throw away a
    completed benchmark at the final print.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_opportunities(root: Path) -> tuple[Any, list[str]]:
    """Read every event partition into one frame, newest schema wins nothing.

    Partitions are concatenated rather than read through a dataset API so a
    schema drift between events fails loudly here, at load, instead of appearing
    as a silently all-null column in a feature matrix.
    """
    import pandas as pd

    # ``argparse`` preserves a relative --opportunities-root as relative.  The
    # manifest provenance below is repository-relative, so resolve both the
    # root and each discovered partition before calling ``relative_to``.
    root = root.expanduser().resolve()
    paths = sorted(root.glob("event=*/opportunities.parquet"))
    if not paths:
        raise SystemExit(
            f"no opportunity partitions under {root}. Build them first:\n"
            "  python scripts/features/build_opportunities.py"
        )
    frames, sources, schemas = [], [], set()
    for path in paths:
        frame = pd.read_parquet(path)
        if "schema_version" in frame.columns:
            versions = {str(value) for value in frame["schema_version"].dropna().unique()}
            if versions and versions != {OPPORTUNITY_SCHEMA_VERSION}:
                raise SystemExit(
                    f"opportunity schema mismatch in {path}: found {sorted(versions)}, "
                    f"expected {OPPORTUNITY_SCHEMA_VERSION}; rebuild M07 artifacts"
                )
        schemas.add(tuple(sorted(frame.columns)))
        frames.append(frame)
        # posix separators so a manifest written on Windows compares byte-for-byte
        # against one written on Linux (MODELS.md section 6.3).
        sources.append(path.resolve().relative_to(ROOT.resolve()).as_posix())
    if len(schemas) > 1:
        raise SystemExit(
            f"opportunity partitions under {root} disagree on their columns; "
            "rebuild the whole table rather than mixing schema versions"
        )
    return pd.concat(frames, ignore_index=True), sources


def describe_dataset(frame) -> dict[str, Any]:
    detection = frame[frame["decision_checkpoint"] == "DETECTION"]
    labelled = detection["passed_by_outcome_horizon"].dropna()
    return {
        "rows": int(len(frame)),
        "opportunities": int(frame["opportunity_id"].nunique()),
        "events": int(frame["event"].nunique()),
        "years": sorted({str(v) for v in frame["year"].unique()}),
        "sessions": sorted({str(v) for v in frame["session"].unique()}),
        "labelled_opportunities": int(len(labelled)),
        "unlabelled_opportunities": int(len(detection) - len(labelled)),
        "base_rate": float(labelled.astype(bool).mean()) if len(labelled) else None,
        "label_definition": LABEL_DEFINITION,
        "checkpoint_counts": {
            str(k): int(v) for k, v in frame["decision_checkpoint"].value_counts().items()
        },
    }


def final_fold(frame, plan) -> Fold:
    """The fold the saved artifact is fitted on: everything except the holdout.

    The benchmark's numbers come from cross-validation; this fit exists so the
    artifact is trained on all the data available to it rather than on whichever
    fold happened to be first. Its validation split is reused for early stopping,
    so its own training metrics are optimistic and the artifact records the
    cross-validated metrics as the honest ones.
    """
    if len(plan.folds) == 1:
        return plan.folds[0]
    last = plan.folds[-1]
    train = frame.index.difference(plan.holdout).difference(last.validation)
    return Fold(
        name="final",
        train=train,
        validation=last.validation,
        test=frame.index[:0],
        notes=(
            "fitted on every non-holdout row; early stopping on the validation split "
            f"of fold {last.name}"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--checkpoint", action="append", choices=list(CHECKPOINTS),
                        help="Repeat to select several; default is all three")
    parser.add_argument("--family", action="append", choices=list(FAMILIES),
                        help="Repeat to select several; default is all five")
    parser.add_argument("--design", choices=list(DESIGNS), default="auto")
    parser.add_argument(
        "--demo-scope", choices=[s.value for s in DemoScope], default=DemoScope.TRACK.value,
        help=(
            "How much of the British Grand Prix to freeze. 'track' (default) holds out "
            "every season and is the only setting where this and C9 agree; 'event_year' "
            "holds out 2026 only, leaving historical Silverstone trainable as CP-14's "
            "split table describes."
        ),
    )
    parser.add_argument("--folds", type=int, default=5, help="k for the kfold design")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--jobs", type=int, default=None,
                        help="Concurrent fits; default is sized from cores and free memory")
    parser.add_argument("--threads-per-fit", type=int, default=None,
                        help="Threads inside each fit; default is sized from the row count")
    parser.add_argument("--identity", action="store_true",
                        help="Include driver identity in the feature matrix (section 17)")
    parser.add_argument("--identity-ablation", action="store_true",
                        help="Run the benchmark twice, with and without driver identity")
    parser.add_argument("--fast", action="store_true",
                        help="Drop the determinism constraints; recorded in the manifest")
    parser.add_argument("--out-root", type=Path, default=MODELS_ROOT)
    parser.add_argument("--version", default="v1", help="Artifact directory under --out-root")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--no-artifacts", action="store_true",
                        help="Score only; write the report but no model files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    checkpoints = args.checkpoint or list(CHECKPOINTS)
    requested = args.family or list(FAMILIES)
    availability = available_families(requested)
    families = [name for name in requested if availability[name] is None]
    unavailable = {name: availability[name] for name in requested if availability[name]}
    if not families:
        raise SystemExit(f"no requested model family could be imported: {unavailable}")

    frame, sources = load_opportunities(args.opportunities_root)
    dataset = describe_dataset(frame)

    plan = plan_splits(frame, design=args.design, seed=args.seed, folds=args.folds,
                       demo_scope=DemoScope(args.demo_scope))
    assert_disjoint(plan)
    split_summary = summarise(frame, plan)

    train_rows = max((row["train_labelled"] for row in split_summary), default=0)
    n_cells = len(checkpoints) * len(families) * len(plan.folds)
    hardware = plan_hardware(
        n_cells, train_rows, workers=args.jobs, threads_per_fit=args.threads_per_fit
    )

    if args.dry_run:
        preview = {}
        for checkpoint in checkpoints:
            rows = frame[frame["decision_checkpoint"] == checkpoint]
            selection = select_features(
                checkpoint, rows.columns, include_identity=args.identity, dtypes=rows.dtypes
            )
            audit_feature_matrix(rows, selection)
            preview[checkpoint] = selection.as_schema()
        print(json.dumps({
            "dataset": dataset,
            "split": plan.as_dict(),
            "split_summary": split_summary,
            "hardware": hardware.as_dict(),
            "cells": n_cells,
            "families": families,
            "families_unavailable": unavailable,
            "feature_selection": preview,
        }, indent=2, default=str))
        return 0

    passes = [("without_identity", False), ("with_identity", True)] if args.identity_ablation \
        else [("with_identity" if args.identity else "without_identity", args.identity)]

    all_results: dict[str, list] = {}
    for label, include_identity in passes:
        done = {"n": 0}

        def tick(result, total=n_cells, state=done, tag=label):
            state["n"] += 1
            status = "ok " if result.ok else "ERR"
            print(f"  [{state['n']:>3}/{total}] {tag} {status} {result.checkpoint:<10} "
                  f"{result.family:<9} {result.fold:<24} {result.seconds}s"
                  + ("" if result.ok else f"  {result.error}"), flush=True)

        print(f"fitting {n_cells} cell(s), pass '{label}'", flush=True)
        all_results[label] = run_benchmark(
            frame, plan,
            checkpoints=checkpoints, families=families, hardware=hardware,
            seed=args.seed, deterministic=not args.fast,
            include_identity=include_identity, progress=tick,
        )

    # The ablation runs both passes; the reported one is whichever the flags asked
    # for, so --identity-ablation adds a comparison without changing the headline.
    primary = "with_identity" if args.identity else "without_identity"
    results = all_results[primary]
    aggregated = aggregate(results)
    gates = check_gates(aggregated, expected_cells=len(checkpoints) * len(families),
                        checkpoints=checkpoints)
    failures = [r.as_dict() for r in results if not r.ok]

    run = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(ROOT),
        "seed": args.seed,
        "checkpoints": checkpoints,
        "families": families,
        "families_unavailable": ", ".join(f"{k} ({v})" for k, v in unavailable.items()) or None,
        "include_identity": args.identity,
        "identity_ablation": args.identity_ablation,
        "deterministic": not args.fast,
        "demo_scope": args.demo_scope,
        "primary_pass": primary,
    }

    if not args.no_artifacts:
        fold = final_fold(frame, plan)
        version_dir = args.out_root / args.version
        for checkpoint in checkpoints:
            ranked = rank_results([r for r in aggregated if r["checkpoint"] == checkpoint])
            for entry in ranked:
                family = entry["family"]
                result, model = fit_cell(
                    frame, checkpoint, family, fold,
                    seed=args.seed, threads=hardware.threads_per_fit,
                    deterministic=not args.fast, include_identity=args.identity,
                    return_model=True,
                )
                if model is None:
                    print(f"  artifact skipped: {checkpoint}/{family}: {result.error}", flush=True)
                    continue
                rows = frame[frame["decision_checkpoint"] == checkpoint]
                selection = select_features(
                    checkpoint, rows.columns, include_identity=args.identity, dtypes=rows.dtypes
                )
                # One row in the model's own schema, so write_fit_artifact can score
                # it before and after a pickle round-trip and record whether the
                # reloaded artifact agrees (MODELS.md section 1.1).
                sample_X, _ = build_matrix(rows.head(1), selection, require_label=False)
                directory = version_dir / checkpoint.lower() / family
                write_fit_artifact(
                    directory,
                    model=model, selection=selection,
                    metrics={
                        "cross_validated": entry,
                        "final_fit": result.as_dict(),
                        "selection_rank": ranked.index(entry) + 1,
                    },
                    split_plan=plan.as_dict(),
                    sample_row=sample_X,
                    source_datasets=sources,
                    config_files=list(CONFIG_FILES),
                    extra={"run": run, "hardware": hardware.as_dict()},
                    root=ROOT,
                )
                print(f"  wrote {_display(directory)}", flush=True)

        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "benchmark.json").write_text(json.dumps({
            "run": run, "dataset": dataset, "split": plan.as_dict(),
            "split_summary": split_summary, "hardware": hardware.as_dict(),
            "aggregated": {k: aggregate(v) for k, v in all_results.items()},
            "results": {k: [r.as_dict() for r in v] for k, v in all_results.items()},
            "gates": list(gates),
        }, indent=2, default=str), encoding="utf-8")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    identity_rows = []
    if args.identity_ablation:
        identity_rows = identity_comparison(
            aggregate(all_results["without_identity"]),
            aggregate(all_results["with_identity"]),
        )
    args.report.write_text(render_report(
        run=run, dataset=dataset, split=plan.as_dict(), split_summary=split_summary,
        aggregated=aggregated, gates=gates, hardware=hardware.as_dict(), failures=failures,
        identity=identity_rows,
    ), encoding="utf-8")
    print(f"report: {_display(args.report)}")

    failed_gates = [g for g in gates if g["passed"] is False]
    for gate in failed_gates:
        print(f"GATE FAILED: {gate['gate']} -- {gate['detail']}")
    return 1 if failed_gates else 0


if __name__ == "__main__":
    raise SystemExit(main())
