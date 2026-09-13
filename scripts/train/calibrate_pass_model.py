#!/usr/bin/env python3
"""Calibrate the M10 pass model and compare the three variants (Tanveer CP-15).

Uncalibrated against Platt/sigmoid against isotonic, per decision checkpoint and
model family, scored on a held-out event the calibrator never saw.

    python scripts/train/calibrate_pass_model.py --dry-run
    python scripts/train/calibrate_pass_model.py --checkpoint DETECTION
    python scripts/train/calibrate_pass_model.py

Calibration data is cross-fitted over the training events rather than taken from
the validation split, because the validation split is already spent on early
stopping for the three tree families -- see ``calibrate.py`` for the measurements
that forced that choice.

``--dry-run`` reports the split plan, the calibration-set sizes per fold and
which method the sample-size rule licenses, without fitting anything.
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

from trackshift.data.guards import DemoScope  # noqa: E402
from trackshift.features.opportunities import CHECKPOINTS, OPPORTUNITY_SCHEMA_VERSION  # noqa: E402
from trackshift.pass_model.api import (  # noqa: E402
    DEFAULT_SEED,
    DESIGNS,
    FAMILIES,
    METHODS,
    aggregate_variants,
    assert_disjoint,
    available_families,
    crossfit_calibration_set,
    evaluate_variants,
    git_commit,
    plan_hardware,
    grade_evidence,
    load_assignments,
    plan_splits,
    recommended_method,
    select_variant,
    summarise,
)
from trackshift.pass_model.features import assert_model_feature_boundary  # noqa: E402
from trackshift.pass_model.reliability import plot_all, write_bin_table  # noqa: E402

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
SPLIT_ASSIGNMENTS = ROOT / "data" / "processed" / "split_assignments"
VALIDATION = ROOT / "artifacts" / "validation"
REPORT = VALIDATION / "calibration_report.md"
PLOTS = VALIDATION / "reliability"


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_opportunities(root: Path):
    import pandas as pd

    root = root.expanduser().resolve()
    paths = sorted(root.glob("event=*/opportunities.parquet"))
    if not paths:
        raise SystemExit(
            f"no opportunity partitions under {root}. Build them first:\n"
            "  python scripts/features/build_opportunities.py"
        )
    frames, sources = [], []
    for path in paths:
        frame = pd.read_parquet(path)
        if "schema_version" in frame.columns:
            versions = {str(value) for value in frame["schema_version"].dropna().unique()}
            if versions and versions != {OPPORTUNITY_SCHEMA_VERSION}:
                raise SystemExit(
                    f"opportunity schema mismatch in {path}: found {sorted(versions)}, "
                    f"expected {OPPORTUNITY_SCHEMA_VERSION}; rebuild M07 artifacts"
                )
        frames.append(frame)
        sources.append(path.resolve().relative_to(ROOT.resolve()).as_posix())
    return pd.concat(frames, ignore_index=True), sources


def _fmt(value: Any, places: int = 4) -> str:
    if value is None:
        return "--"
    return f"{value:.{places}f}" if isinstance(value, float) else str(value)


def render(run: dict[str, Any], split: dict[str, Any], aggregated, per_fold,
           selections: dict[str, Any], plots: list[str]) -> str:
    lines = [
        "# Pass-model calibration (M11, CP-15)",
        "",
        f"Generated {run['created_utc']} at commit `{run['git_commit']}`.",
        "",
        "| Run | Value |",
        "|---|---|",
        f"| Seed | {run['seed']} |",
        f"| Checkpoints | {', '.join(run['checkpoints'])} |",
        f"| Families | {', '.join(run['families'])} |",
        f"| Methods | {', '.join(run['methods'])} |",
        f"| Calibration data | cross-fitted over the training events |",
        f"| Split design | {split['design']} ({split['n_folds']} folds) |",
        "",
        "## Why calibration data is cross-fitted",
        "",
        "CP-15 specifies fitting the calibrator on the validation split. In CP-14 that",
        "split is already consumed by early stopping for LightGBM, XGBoost and CatBoost,",
        "so a calibrator fitted there measures calibration on rows the model was tuned",
        "against. Spending a whole event instead was measured and is unusable at this",
        "table size: the seven candidate blocks hold 61-1,228 rows at base rates from",
        "1.6% to 27.1%, and one fold would calibrate on 61 rows containing one positive.",
        "Cross-fitting over the training events keeps every calibration row out-of-sample",
        "for the model that scored it, spends no training data, and leaves CP-14's",
        "train/validation/test indices byte-identical.",
        "",
        "## Calibration sets",
        "",
        "| Fold | n | +ve | Base rate | Inner fits | Licensed method |",
        "|---|---|---|---|---|---|",
    ]
    for row in run.get("calibration_sets", []):
        lines.append(
            f"| {row['fold']} | {row['n']} | {row['n_positive']} | "
            f"{_fmt(row['base_rate'])} | {row['inner_fits']} | {row['licensed']} |"
        )

    lines += ["", "## Variant comparison", "",
              "Weighted by n across folds. ECE is a count-weighted mean within a fold, so",
              "an unweighted mean across folds of unequal size is a mean of means -- the",
              "same ratio trap CP-14 hit with Brier skill.", ""]
    for checkpoint in run["checkpoints"]:
        rows = [r for r in aggregated if r["checkpoint"] == checkpoint]
        if not rows:
            continue
        lines += [
            f"### {checkpoint}", "",
            "| Family | Method | N | Brier | ECE | Log loss | ROC-AUC | PR-AUC | Sharpness | Min bins | Rejected folds |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in sorted(rows, key=lambda r: (r["family"], r["method"])):
            lines.append(
                f"| {row['family']} | {row['method']} | {row['n']} | {_fmt(row.get('brier'))} | "
                f"{_fmt(row.get('ece'))} | {_fmt(row.get('log_loss'))} | "
                f"{_fmt(row.get('roc_auc'))} | {_fmt(row.get('pr_auc'))} | "
                f"{_fmt(row.get('sharpness'), 5)} | {row.get('realised_bins_min')} | "
                f"{row.get('rejected_folds')} |"
            )
        lines.append("")
        chosen = selections.get(checkpoint)
        if chosen:
            lines += [
                f"**Selected: {chosen['family']} / {chosen['method']}** "
                f"(Brier {_fmt(chosen.get('brier'))}, ECE {_fmt(chosen.get('ece'))}, "
                f"sharpness {_fmt(chosen.get('sharpness'), 5)}).", "",
            ]

    rejected = [r for r in per_fold if r.get("rejected_reason")]
    if rejected:
        lines += ["## Rejected calibrators", "",
                  "A calibrator that collapses toward a constant scores well on ECE and Brier",
                  "while discarding the discrimination the planner needs, and a Platt fit whose",
                  "slope takes the wrong sign inverts the model's ranking while improving ECE.",
                  "Both are refused rather than selected.", "",
                  "| Checkpoint | Family | Fold | Method | Reason |", "|---|---|---|---|---|"]
        for row in rejected:
            lines.append(
                f"| {row['checkpoint']} | {row['family']} | {row['fold']} | {row['method']} | "
                f"{row['rejected_reason']} |"
            )
        lines.append("")

    failures = [r for r in per_fold if not r.get("ok")]
    if failures:
        lines += ["## Variants that did not complete", "",
                  "| Checkpoint | Family | Fold | Method | Error |", "|---|---|---|---|---|"]
        for row in failures:
            lines.append(
                f"| {row['checkpoint']} | {row['family']} | {row['fold']} | "
                f"{row['method']} | {row['error']} |"
            )
        lines.append("")

    sample = next((r for r in per_fold
                   if r.get("ok") and (r.get("metrics") or {}).get("bin_diagnostics")), None)
    if sample:
        lines += ["## Example reliability bins", "",
                  f"{sample['checkpoint']} / {sample['family']} / {sample['method']}, "
                  f"fold {sample['fold']}.", ""]
        lines += write_bin_table(sample["metrics"]["bin_diagnostics"])
        lines.append("")

    if plots:
        lines += ["## Reliability diagrams", "",
                  f"{len(plots)} figure(s) written to `{_display(PLOTS)}`, each beside a",
                  "`.json` sidecar carrying the bin numbers it was drawn from.", ""]

    lines += [
        "## Reading this report",
        "",
        "- A constant predictor is perfectly calibrated. ECE is not a proper scoring rule",
        "  and is minimised at the base rate, so sharpness is reported beside it and a",
        "  collapsed calibrator is refused rather than selected.",
        "- In-sample isotonic ECE is 0.0 by algebraic identity, so every number here is",
        "  measured on a held-out event the calibrator never saw.",
        f"- ECE below n={run['min_ece_n']} is dominated by its own positive bias. Those folds",
        "  are marked not measurable rather than reported as small numbers.",
        "- Platt preserves ranking exactly when its slope is negative; isotonic may lose a",
        "  little ROC-AUC to tie-flattening, which is expected and not a bug.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--checkpoint", action="append", choices=list(CHECKPOINTS))
    parser.add_argument("--family", action="append", choices=list(FAMILIES))
    parser.add_argument("--method", action="append", choices=list(METHODS))
    parser.add_argument("--design", choices=list(DESIGNS), default="auto")
    parser.add_argument("--demo-scope", choices=[s.value for s in DemoScope],
                        default=DemoScope.TRACK.value)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--threads-per-fit", type=int, default=None)
    parser.add_argument("--identity", action="store_true")
    parser.add_argument("--fast", action="store_true",
                        help="Drop determinism constraints; recorded in the report")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--plots", type=Path, default=PLOTS)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument(
        "--split-assignments", type=Path, default=SPLIT_ASSIGNMENTS,
        help="Persistent C9 assignment directory, read rather than re-derived")
    parser.add_argument(
        "--allow-event-split", action="store_true",
        help=(
            "Permit the coarser event-level split when battle_id is unavailable. "
            "Recorded as REDUCED evidence; does not satisfy the checkpoint's gate."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--final-mode", action="store_true",
        help="Require a proxy-free 2026 non-British calibration input; unresolved final gates fail closed",
    )
    args = parser.parse_args()

    from trackshift.pass_model.calibration import MIN_ECE_N

    checkpoints = args.checkpoint or list(CHECKPOINTS)
    requested = args.family or list(FAMILIES)
    availability = available_families(requested)
    families = [f for f in requested if availability[f] is None]
    if not families:
        raise SystemExit(f"no requested family could be imported: {availability}")
    methods = args.method or list(METHODS)

    frame, sources = load_opportunities(args.opportunities_root)
    if args.final_mode:
        years = set(frame["year"].dropna().astype(int).unique()) if "year" in frame else set()
        if years != {2026}:
            raise SystemExit(f"final mode requires only 2026 opportunity rows; found years {sorted(years)}")
        if "event" in frame:
            british = frame["event"].astype(str).str.contains("british", case=False, na=False)
            if bool(british.any()):
                raise SystemExit("final mode rejects British Grand Prix rows: held-out replay/demo only")
        try:
            assert_model_feature_boundary(
                frame, year=2026, consumer="C4 calibration", mode="final",
            )
        except Exception as exc:
            raise SystemExit(f"final-mode C4 feature boundary rejected: {exc}") from exc
    # Same split contract as CP-14: battle_id against a written C9 assignment,
    # and rows C8 has no episode for are excluded rather than relabelled.
    # Re-deriving the split here would let this stage disagree with the
    # benchmark it is calibrating, with nothing in either artifact to say so.
    require_unit = None if args.allow_event_split else "battle_id"
    assignments = None
    excluded_rows = {}
    if require_unit:
        assignments = load_assignments(args.split_assignments)
        usable = frame[require_unit].notna() & (
            frame[require_unit].astype(str).str.strip() != "")
        dropped = int((~usable).sum())
        if dropped:
            excluded_rows = {
                "rows_dropped_without_battle_id": dropped,
                "rows_kept": int(usable.sum()),
                "share_dropped": round(dropped / len(frame), 6),
            }
            print(f"excluding {dropped} of {len(frame)} rows with no battle_id "
                  f"({dropped / len(frame):.1%})", flush=True)
            frame = frame[usable].reset_index(drop=True)
        if frame.empty:
            raise SystemExit(
                "every row was dropped for want of a battle_id; the A2 C8 join has "
                "not been applied to this M07 build.")

    plan = plan_splits(frame, design=args.design, seed=args.seed,
                       demo_scope=DemoScope(args.demo_scope),
                       require_unit=require_unit, assignments=assignments)
    assert_disjoint(plan)
    evidence = grade_evidence(frame, plan)
    if not evidence["is_cp14_acceptance_run"]:
        print(f"EVIDENCE GRADE {evidence['grade']}: not a full-gate run", flush=True)
        for reason in evidence["reasons"]:
            print(f"  - {reason}", flush=True)
    split_summary = summarise(frame, plan)
    train_rows = max((r["train_labelled"] for r in split_summary), default=0)
    hardware = plan_hardware(len(checkpoints) * len(families) * len(plan.folds),
                             train_rows, workers=1, threads_per_fit=args.threads_per_fit)

    if args.dry_run:
        preview = []
        for fold in plan.folds:
            try:
                cal = crossfit_calibration_set(
                    frame, checkpoints[0], families[0], fold,
                    seed=args.seed, threads=hardware.threads_per_fit,
                    deterministic=not args.fast, include_identity=args.identity,
                    final_mode=args.final_mode,
                )
                licensed, why = recommended_method(cal.n)
                preview.append({"fold": fold.name, **cal.as_dict(),
                                "licensed": licensed, "reason": why})
            except Exception as exc:
                preview.append({"fold": fold.name, "error": f"{type(exc).__name__}: {exc}"})
        print(json.dumps({
            "split": plan.as_dict(), "checkpoints": checkpoints, "families": families,
            "methods": methods, "calibration_sets": preview,
        }, indent=2, default=str))
        return 0

    per_fold: list[dict[str, Any]] = []
    calibration_sets: list[dict[str, Any]] = []
    total = len(checkpoints) * len(families) * len(plan.folds)
    done = 0
    for checkpoint in checkpoints:
        for family in families:
            for fold in plan.folds:
                done += 1
                results, cal = evaluate_variants(
                    frame, checkpoint, family, fold,
                    seed=args.seed, threads=hardware.threads_per_fit,
                    deterministic=not args.fast, include_identity=args.identity,
                    methods=methods, bins=args.bins, final_mode=args.final_mode,
                )
                per_fold.extend(r.as_dict() for r in results)
                if cal is not None and checkpoint == checkpoints[0] and family == families[0]:
                    licensed, _ = recommended_method(cal.n)
                    calibration_sets.append({"fold": fold.name, **cal.as_dict(),
                                             "licensed": licensed})
                ok = sum(1 for r in results if r.ok)
                print(f"  [{done:>3}/{total}] {checkpoint:<10} {family:<9} {fold.name:<24} "
                      f"{ok}/{len(results)} variants", flush=True)

    aggregated = aggregate_variants(per_fold)
    selections: dict[str, Any] = {}
    for checkpoint in checkpoints:
        rows = [r for r in aggregated if r["checkpoint"] == checkpoint]
        eligible = [
            {**r, "ok": True, "ece_measurable": True, "metrics": r}
            for r in rows if r.get("brier") is not None
        ]
        chosen = select_variant(eligible)
        if chosen:
            selections[checkpoint] = chosen

    run = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(ROOT),
        "seed": args.seed,
        "checkpoints": checkpoints,
        "families": families,
        "methods": methods,
        "deterministic": not args.fast,
        "min_ece_n": MIN_ECE_N,
        "calibration_sets": calibration_sets,
        "source_datasets": sources,
        "final_mode": args.final_mode,
    }

    plots: list[str] = []
    if not args.no_plots:
        best_per_cell: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in per_fold:
            if not row.get("ok"):
                continue
            key = (row["checkpoint"], row["family"], row["method"])
            current = best_per_cell.get(key)
            if current is None or (row["metrics"].get("n") or 0) > (
                current["metrics"].get("n") or 0
            ):
                best_per_cell[key] = row
        plots = plot_all(list(best_per_cell.values()), args.plots,
                         run_label=f"seed {args.seed}")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render(run, plan.as_dict(), aggregated, per_fold, selections, plots),
        encoding="utf-8",
    )
    (args.report.parent / "calibration.json").write_text(
        json.dumps({"run": run, "split": plan.as_dict(), "aggregated": aggregated,
                    "per_fold": per_fold}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"report: {_display(args.report)}")
    if plots:
        print(f"plots : {len(plots)} written to {_display(args.plots)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
