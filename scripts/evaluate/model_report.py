#!/usr/bin/env python3
"""Full evaluation analytics for the pass model (CP-14).

Confusion matrices at principled thresholds, ROC and precision-recall curves,
reliability diagrams, threshold sweeps, separation histograms and feature
importance -- written as PNGs plus one JSON and one markdown report.

    python scripts/evaluate/model_report.py --dry-run
    python scripts/evaluate/model_report.py --jobs 8
    python scripts/evaluate/model_report.py --holdout --i-understand-this-is-one-shot

**Predictions are out-of-fold by construction.** The script refits the family
per fold and scores each fold's own held-out rows, so no prediction comes from a
model that saw that row. Scoring the saved artifact on its own training data
would produce a beautiful and completely meaningless set of figures -- and it is
the easy mistake to make here, because the artifact is right there on disk.

``--holdout`` instead scores the saved artifact on the frozen 2026 British Grand
Prix, which is legitimate precisely because that event was never trained on. It
is one-shot and gated behind the same flag as ``final_test.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.guards import DemoScope, is_demo_row  # noqa: E402
from trackshift.eval.analytics import (  # noqa: E402
    ANALYTICS_SCHEMA_VERSION,
    THRESHOLD_STRATEGIES,
    confusion_at,
    curve_points,
    pick_thresholds,
    threshold_sweep,
)
from trackshift.eval.plots import (  # noqa: E402
    plot_checkpoint_comparison,
    plot_confusion_grid,
    plot_feature_importance,
    plot_precision_recall,
    plot_probability_distribution,
    plot_roc,
    plot_threshold_sweep,
)
from trackshift.features.opportunities import CHECKPOINTS  # noqa: E402
from trackshift.pass_model.api import (  # noqa: E402
    DEFAULT_SEED,
    FAMILIES,
    assert_disjoint,
    build_matrix,
    evaluate,
    git_commit,
    grade_evidence,
    load_assignments,
    plan_splits,
    select_features,
)
from trackshift.pass_model.calibrate import bin_diagnostics  # noqa: E402
from trackshift.pass_model.candidates import PassModel  # noqa: E402
from trackshift.pass_model.reliability import plot_reliability  # noqa: E402

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
SPLIT_ASSIGNMENTS = ROOT / "data" / "processed" / "split_assignments"
MODELS = ROOT / "artifacts" / "models" / "pass"
FIGURES = ROOT / "artifacts" / "validation" / "figures"
OUT = ROOT / "artifacts" / "validation" / "model_report.json"
REPORT = ROOT / "artifacts" / "validation" / "model_report.md"


def load_opportunities(root: Path):
    import pandas as pd

    paths = sorted(root.glob("event=*/opportunities.parquet"))
    if not paths:
        raise SystemExit(f"no opportunity partitions under {root}")
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def fold_predictions(frame, checkpoint: str, family: str, fold, seed: int):
    """Fit on this fold's train split, predict its test split. Out-of-fold."""
    rows = frame[frame["decision_checkpoint"] == checkpoint]
    selection = select_features(checkpoint, rows.columns, include_identity=False,
                               dtypes=rows.dtypes)
    train = rows.loc[rows.index.intersection(fold.train)]
    validation = rows.loc[rows.index.intersection(fold.validation)]
    test = rows.loc[rows.index.intersection(fold.test)]
    if train.empty or test.empty:
        return None
    X_train, y_train = build_matrix(train, selection)
    X_test, y_test = build_matrix(test, selection)
    # Early stopping on the fold's validation split, exactly as the CP-14
    # benchmark does. Without it the tree families run all 2000 estimators and
    # overfit, and the analytics then understate the model rather than measure
    # it -- the figures would be a picture of this script, not of CP-14.
    X_val, y_val = (build_matrix(validation, selection)
                    if len(validation) else (None, None))
    if len(set(map(int, y_train))) < 2 or len(y_test) == 0:
        return None
    model = PassModel(family, numeric=selection.numeric,
                      categorical=selection.categorical, seed=seed, threads=1)
    model.fit(X_train, y_train, X_val, y_val)
    return {
        "fold": fold.name,
        "y": [int(v) for v in y_test],
        "p": [float(v) for v in model.predict_proba(X_test)],
        "features": list(selection.columns),
        "importance": _importance(model, selection),
    }


def _importance(model, selection) -> dict[str, float]:
    """Native importance where the family exposes it, else empty."""
    inner = getattr(model, "_model", None)
    raw = getattr(inner, "feature_importances_", None)
    if raw is None:
        return {}
    names = list(selection.columns)
    return {n: float(v) for n, v in zip(names, list(raw))}


def analyse(checkpoint: str, y, p, base_rate: float, figures: Path,
            importance: dict[str, float], label: str) -> dict[str, Any]:
    """Every metric, matrix, curve and figure for one checkpoint."""
    metrics = evaluate(y, p, base_rate=base_rate)
    thresholds = pick_thresholds(y, p, base_rate=base_rate)
    matrices = [confusion_at(y, p, value, strategy=name).as_dict()
                for name, value in thresholds.items()]
    sweep = threshold_sweep(y, p)
    curves = curve_points(y, p)
    slug = f"{label}_{checkpoint.lower()}"

    written = {
        "confusion": plot_confusion_grid(
            matrices, figures / f"{slug}_confusion.png",
            title=f"{checkpoint} — confusion at four thresholds (n={len(y)})"),
        "roc": plot_roc(curves["roc"], figures / f"{slug}_roc.png",
                        title=f"{checkpoint} — ROC"),
        "precision_recall": plot_precision_recall(
            curves["pr"], figures / f"{slug}_pr.png",
            title=f"{checkpoint} — precision-recall"),
        "threshold_sweep": plot_threshold_sweep(
            sweep, thresholds, figures / f"{slug}_thresholds.png",
            title=f"{checkpoint} — operating points"),
        "separation": plot_probability_distribution(
            y, p, figures / f"{slug}_separation.png",
            title=f"{checkpoint} — predicted probability by outcome"),
    }

    # bin_diagnostics, not reliability_table: the plotter wants the Wilson
    # bands and the *realised* bin count, which the raw table does not carry.
    diagnostics = bin_diagnostics(y, p)
    try:
        written["reliability"] = plot_reliability(
            diagnostics, figures / f"{slug}_reliability.png",
            title=f"{checkpoint} — reliability")
    except Exception as exc:            # plotting is optional, metrics are not
        written["reliability"] = f"unavailable: {type(exc).__name__}: {exc}"
        print(f"  reliability plot failed for {checkpoint}: {exc}", flush=True)

    if importance:
        written["feature_importance"] = plot_feature_importance(
            list(importance), list(importance.values()),
            figures / f"{slug}_importance.png",
            title=f"{checkpoint} — feature importance")

    return {
        "checkpoint": checkpoint,
        "n": int(len(y)), "positives": int(sum(y)),
        "base_rate_observed": round(float(sum(y) / len(y)), 6),
        "base_rate_reference": round(base_rate, 6),
        "metrics": metrics,
        "thresholds": {k: round(v, 6) for k, v in thresholds.items()},
        "confusion_matrices": matrices,
        "roc_auc": curves["roc"]["auc"],
        "pr_auc": curves["pr"]["auc"],
        "pr_baseline": curves["pr"]["baseline"],
        "feature_importance": dict(sorted(importance.items(),
                                          key=lambda kv: kv[1], reverse=True)),
        "figures": {k: (str(Path(v).relative_to(ROOT)) if Path(str(v)).exists() else v)
                    for k, v in written.items()},
    }


def render(report: dict[str, Any]) -> str:
    rel = lambda p: str(p).replace("\\", "/")  # noqa: E731
    lines = [
        "# Pass-model evaluation analytics (M10, CP-14)",
        "",
        f"Generated {report['created_utc']} at commit `{report['git_commit']}`.",
        "",
        f"**Source:** {report['prediction_source']}. "
        f"**Family:** `{report['family']}`. "
        f"**Evidence grade:** `{report.get('evidence_grade')}`.",
        "",
    ]
    if report["prediction_source"].startswith("out-of-fold"):
        lines += [
            "> Predictions are **out-of-fold**: the family is refitted per fold and "
            "each fold scores only its own held-out rows. No prediction comes from a "
            "model that saw that row.",
            "",
        ]
    else:
        lines += [
            "> **One-shot.** These numbers come from the frozen 2026 British Grand "
            "Prix. Having been read, that event is no longer a clean estimate of "
            "generalisation. Do not tune against it.",
            "",
        ]

    lines += ["## Headline", "",
              "| Checkpoint | N | Pos | Brier | Skill | ROC-AUC | PR-AUC | ECE |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["checkpoints"]:
        m = row["metrics"]
        lines.append(
            f"| {row['checkpoint']} | {row['n']} | {row['positives']} | "
            f"{m.get('brier', float('nan')):.5f} | "
            f"{m.get('brier_skill_score', float('nan')):+.4f} | "
            f"{row['roc_auc']:.4f} | {row['pr_auc']:.4f} | "
            f"{m.get('ece', float('nan')):.4f} |")
    lines += ["",
              f"![checkpoints]({rel(report['comparison_figure'])})", ""]

    lines += [
        "## How to read the confusion matrices",
        "",
        "This model outputs a probability and is selected on calibration (§26). A "
        "confusion matrix needs a **threshold**, which is a decision the model does "
        "not make, so four principled ones are reported rather than a single "
        "arbitrary cut:",
        "",
    ]
    for name, why in THRESHOLD_STRATEGIES.items():
        lines.append(f"- **`{name}`** — {why}")
    lines += [
        "",
        "Accuracy is shown in the JSON only so its uselessness is visible: at this "
        "base rate, predicting \"no pass\" for everything scores about 85%. Read "
        "precision and recall.",
        "",
    ]

    for row in report["checkpoints"]:
        lines += [f"## {row['checkpoint']}", ""]
        lines += ["| Strategy | Thr | TP | FP | FN | TN | Precision | Recall | F1 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for m in row["confusion_matrices"]:
            lines.append(
                f"| `{m['strategy']}` | {m['threshold']:.3f} | {m['tp']} | {m['fp']} | "
                f"{m['fn']} | {m['tn']} | {m['precision']:.3f} | {m['recall']:.3f} | "
                f"{m['f1']:.3f} |")
        lines += [""]
        for key, caption in (
            ("confusion", "Confusion at each threshold"),
            ("reliability", "Reliability — the §26 headline"),
            ("precision_recall", "Precision-recall against the base-rate line"),
            ("roc", "ROC"),
            ("threshold_sweep", "Operating points"),
            ("separation", "Separation by outcome"),
            ("feature_importance", "Feature importance"),
        ):
            figure = row["figures"].get(key)
            if figure and not str(figure).startswith("unavailable"):
                lines.append(f"**{caption}**")
                lines.append("")
                lines.append(f"![{key}]({rel(figure)})")
                lines.append("")

    lines += [
        "## Caveats worth carrying into any slide",
        "",
        "- PR-AUC is the number to quote, not ROC-AUC. At a ~15% base rate ROC-AUC "
        "is flattered by the large negative class; the PR curve against the "
        "prevalence line shows whether the model actually finds passes.",
        "- The skill score, not raw Brier, is comparable across events. An event "
        "with fewer passes scores a better Brier for doing nothing.",
        "- Read feature importance against the CP-23 ablation. A feature that "
        "dominates importance but shows no held-out delta is being leaned on "
        "without earning anything, usually because it correlates with something "
        "that does.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--split-assignments", type=Path, default=SPLIT_ASSIGNMENTS)
    parser.add_argument("--checkpoint", action="append", choices=list(CHECKPOINTS))
    parser.add_argument("--family", choices=list(FAMILIES), default="lightgbm")
    parser.add_argument("--version", default="v2")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--figures", type=Path, default=FIGURES)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--holdout", action="store_true",
                        help="Score the frozen British GP instead of cross-validating")
    parser.add_argument("--i-understand-this-is-one-shot", action="store_true",
                        dest="confirmed")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    checkpoints = args.checkpoint or list(CHECKPOINTS)
    frame = load_opportunities(args.opportunities_root)

    usable = frame["battle_id"].notna() & (frame["battle_id"].astype(str).str.strip() != "")
    dropped = int((~usable).sum())
    if dropped:
        print(f"excluding {dropped} of {len(frame)} rows with no battle_id "
              f"({dropped / len(frame):.1%})", flush=True)
        frame = frame[usable].reset_index(drop=True)

    assignments = load_assignments(args.split_assignments)
    plan = plan_splits(frame, seed=args.seed, require_unit="battle_id",
                       assignments=assignments)
    assert_disjoint(plan)
    evidence = grade_evidence(frame, plan)

    detection = frame[frame["decision_checkpoint"] == "DETECTION"]
    base_rate = float(detection["passed_by_outcome_horizon"].dropna().astype(bool).mean())

    if args.dry_run:
        print(json.dumps({
            "evidence": evidence,
            "family": args.family,
            "checkpoints": checkpoints,
            "folds": len(plan.folds),
            "fits": len(checkpoints) * len(plan.folds),
            "base_rate": round(base_rate, 6),
            "mode": "holdout" if args.holdout else "out-of-fold",
            "figures_per_checkpoint": 7,
        }, indent=2, default=str))
        return 0

    if args.holdout and not args.confirmed:
        raise SystemExit(
            "--holdout scores the frozen 2026 British Grand Prix and is one-shot. "
            "Pass --i-understand-this-is-one-shot, or drop --holdout to run the "
            "repeatable out-of-fold analysis instead.")

    results: dict[str, dict[str, Any]] = {}
    if args.holdout:
        from trackshift.serve.pass_service import load_predictor

        mask = [is_demo_row(r, DemoScope.TRACK)
                for r in frame[["year", "event"]].to_dict("records")]
        held = frame[mask].reset_index(drop=True)
        for checkpoint in checkpoints:
            rows = held[held["decision_checkpoint"] == checkpoint]
            predictor = load_predictor(MODELS, checkpoint=checkpoint, version=args.version)
            selection = select_features(checkpoint, rows.columns,
                                        include_identity=False, dtypes=rows.dtypes)
            X, y = build_matrix(rows, selection)
            results[checkpoint] = {
                "y": [int(v) for v in y],
                "p": [float(v) for v in predictor.model.predict_proba(X)],
                "importance": _importance(predictor.model, selection),
            }
        source = "the frozen 2026 British Grand Prix (one-shot)"
    else:
        cells = [(cp, fold) for cp in checkpoints for fold in plan.folds]
        print(f"fitting {len(cells)} out-of-fold cell(s)", flush=True)
        collected: dict[str, list] = {cp: [] for cp in checkpoints}
        if args.jobs > 1:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                futures = {pool.submit(fold_predictions, frame, cp, args.family,
                                       fold, args.seed): cp for cp, fold in cells}
                for done, future in enumerate(as_completed(futures), start=1):
                    out = future.result()
                    if out:
                        collected[futures[future]].append(out)
                    print(f"  [{done}/{len(cells)}]", flush=True)
        else:
            for done, (cp, fold) in enumerate(cells, start=1):
                out = fold_predictions(frame, cp, args.family, fold, args.seed)
                if out:
                    collected[cp].append(out)
                print(f"  [{done}/{len(cells)}]", flush=True)

        for cp, parts in collected.items():
            if not parts:
                continue
            importance: dict[str, float] = {}
            for part in parts:
                for name, value in (part["importance"] or {}).items():
                    importance[name] = importance.get(name, 0.0) + value
            results[cp] = {
                "y": [v for part in parts for v in part["y"]],
                "p": [v for part in parts for v in part["p"]],
                "importance": {k: v / len(parts) for k, v in importance.items()},
            }
        source = f"out-of-fold across {len(plan.folds)} folds"

    label = "holdout" if args.holdout else "oof"
    analysed = [analyse(cp, r["y"], r["p"], base_rate, args.figures,
                        r["importance"], label)
                for cp, r in results.items()]
    comparison = plot_checkpoint_comparison(
        [{"checkpoint": a["checkpoint"], **a["metrics"], "roc_auc": a["roc_auc"]}
         for a in analysed],
        args.figures / f"{label}_checkpoints.png")

    report = {
        "schema_version": ANALYTICS_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(ROOT),
        "family": args.family,
        "prediction_source": source,
        "evidence_grade": evidence["grade"],
        "base_rate": round(base_rate, 6),
        "split": plan.as_dict(),
        "checkpoints": analysed,
        "comparison_figure": str(Path(comparison).relative_to(ROOT)),
        "threshold_strategies": THRESHOLD_STRATEGIES,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(report), encoding="utf-8")

    figures = sum(len([f for f in a["figures"].values()
                       if not str(f).startswith("unavailable")]) for a in analysed) + 1
    print(f"report : {args.report.relative_to(ROOT)}")
    print(f"figures: {figures} PNG(s) under {args.figures.relative_to(ROOT)}")
    for a in analysed:
        m = a["metrics"]
        print(f"  {a['checkpoint']:<11} brier={m.get('brier'):.5f} "
              f"skill={m.get('brier_skill_score'):+.4f} pr_auc={a['pr_auc']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
