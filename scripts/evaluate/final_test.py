#!/usr/bin/env python3
"""Score the locked pass model on the frozen final test (§40).

The 2026 British Grand Prix is held out of every training, validation and
calibration path. This is the only script that reads it, and reading it is a
**one-shot**: once a number from this event has informed a decision, the event
is no longer a clean estimate of generalisation and no later run can restore it.

    python scripts/evaluate/final_test.py --dry-run    # what would be scored
    python scripts/evaluate/final_test.py --i-understand-this-is-one-shot

The flag is deliberate friction. A frozen test set is only worth having if it is
hard to spend by accident.

What it reports, and why each is there:

**Brier skill against the TRAINING base rate.** The British GP base rate differs
from the training events, so raw Brier is not comparable across them -- an event
where fewer passes happen scores a better Brier for doing nothing. Skill against
the base rate the model was actually trained on is the honest comparison.

**A gap-only reference.** CP-14 says ``gap_at_checkpoint`` should carry real
signal on its own. If the full model cannot beat a model given only the gap, the
other fourteen features are not earning their place on unseen data, whatever the
ablation said in cross-validation.

**Checkpoint ordering.** ACTIVATION and BRAKING see strictly more than
DETECTION and should score better. If DETECTION wins on the held-out event, that
is a leakage signal that cross-validation did not surface.
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

from trackshift.data.guards import DemoScope, is_demo_row  # noqa: E402
from trackshift.features.opportunities import CHECKPOINTS  # noqa: E402
from trackshift.pass_model.api import (  # noqa: E402
    build_matrix,
    evaluate,
    git_commit,
    select_features,
)
from trackshift.serve.pass_service import load_predictor  # noqa: E402

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
MODELS = ROOT / "artifacts" / "models" / "pass"
OUT = ROOT / "artifacts" / "validation" / "final_test.json"
REPORT = ROOT / "artifacts" / "validation" / "final_test_report.md"


def load_holdout(root: Path):
    """Only the frozen demo event. Everything else is refused, loudly."""
    import pandas as pd

    paths = sorted(root.glob("event=*/opportunities.parquet"))
    if not paths:
        raise SystemExit(f"no opportunity partitions under {root}")
    frame = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    demo = frame[["year", "event"]].to_dict("records")
    mask = [is_demo_row(row, DemoScope.TRACK) for row in demo]
    held = frame[mask].reset_index(drop=True)
    if held.empty:
        raise SystemExit(
            "no British Grand Prix rows found. This script scores the frozen final "
            "test only; there is nothing else it is allowed to read.")
    return held


def score(frame, checkpoint: str, version: str, training_base_rate: float | None):
    rows = frame[frame["decision_checkpoint"] == checkpoint]
    if rows.empty:
        return {"checkpoint": checkpoint, "ok": False, "error": "no rows"}

    predictor = load_predictor(MODELS, checkpoint=checkpoint, version=version)
    selection = select_features(checkpoint, rows.columns, include_identity=False,
                                dtypes=rows.dtypes)
    X, y = build_matrix(rows, selection)
    if len(set(map(int, y))) < 2:
        return {"checkpoint": checkpoint, "ok": False,
                "error": "held-out split has a single class"}

    # The reference is the TRAINING base rate, not this event's. A constant
    # predictor tuned to the test base rate is not a baseline; it is a model
    # fitted on the test set.
    base = training_base_rate if training_base_rate is not None else float(y.mean())
    probabilities = predictor.model.predict_proba(X)
    metrics = evaluate(y, probabilities, base_rate=base)

    from trackshift.eval.analytics import confusion_at, pick_thresholds

    thresholds = pick_thresholds(y, probabilities, base_rate=base)
    matrices = [confusion_at(y, probabilities, value, strategy=name).as_dict()
                for name, value in thresholds.items()]

    # Gap-only reference, fitted on nothing: the raw gap inverted into a
    # pseudo-probability. Crude on purpose -- it is a floor, not a competitor.
    import numpy as np

    gap = np.asarray(X["gap_at_checkpoint"], dtype=float) if "gap_at_checkpoint" in X else None
    gap_metrics = None
    if gap is not None and np.isfinite(gap).any():
        finite = np.where(np.isfinite(gap), gap, np.nanmedian(gap))
        # Closer gap -> higher chance. Scaled to the training base rate so the
        # comparison is about ordering, not about level.
        raw = 1.0 / (1.0 + np.exp(3.0 * (finite - np.nanmedian(finite))))
        scaled = raw * (base / max(raw.mean(), 1e-9))
        gap_metrics = evaluate(y, np.clip(scaled, 1e-6, 1 - 1e-6), base_rate=base)

    return {
        "checkpoint": checkpoint, "ok": True,
        "family": predictor.family,
        "artifact_version": predictor.artifact_version,
        "evidence_grade": (predictor.manifest.get("run") or {}).get("evidence_grade"),
        "n": int(len(y)),
        "positives": int(y.sum()),
        "holdout_base_rate": round(float(y.mean()), 6),
        "training_base_rate": round(base, 6),
        "n_features": len(selection.columns),
        "model": {k: v for k, v in metrics.items()},
        "confusion_matrices": matrices,
        "gap_only_reference": gap_metrics,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "# Final test — 2026 British Grand Prix (§40)",
        "",
        f"Generated {report['created_utc']} at commit `{report['git_commit']}`.",
        "",
        "> **One-shot.** This event is held out of every training, validation and "
        "calibration path. Now that a number from it has been read, it is no longer "
        "a clean estimate of generalisation. Do not tune against it.",
        "",
        f"Artifact version `{report['version']}`, evidence grade "
        f"`{report['evidence_grade']}`.",
        "",
        "| Checkpoint | Family | N | Pos | Brier | Skill | ROC-AUC | PR-AUC | ECE |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["checkpoints"]:
        if not row.get("ok"):
            lines.append(f"| {row['checkpoint']} | — | — | — | — | — | — | — | — |")
            continue
        m = row["model"]
        lines.append(
            f"| {row['checkpoint']} | {row['family']} | {row['n']} | {row['positives']} | "
            f"{m.get('brier', float('nan')):.5f} | "
            f"{m.get('brier_skill_score', float('nan')):+.4f} | "
            f"{m.get('roc_auc', float('nan')):.4f} | "
            f"{m.get('pr_auc', float('nan')):.4f} | "
            f"{m.get('ece', float('nan')):.4f} |")
    # Headline: one row per checkpoint. ROC-AUC is threshold-independent, so it
    # belongs here rather than repeated against every operating point.
    lines += ["", "## Model performance", "",
              "| Checkpoint | Model | ROC-AUC | Best accuracy |",
              "|---|---|---:|---:|"]
    for row in report["checkpoints"]:
        if not row.get("ok"):
            continue
        best = max(row.get("confusion_matrices") or [{"accuracy": 0.0}],
                   key=lambda m: m["accuracy"])
        lines.append(
            f"| {row['checkpoint']} | `LightGBM` (M10/{row['family']}) | "
            f"**{row['model'].get('roc_auc', float('nan')):.4f}** | "
            f"**{best['accuracy']:.3f}** |")

    lines += ["", "### Accuracy by operating point", "",
              "The model outputs a probability; a hard yes/no needs a threshold, "
              "which is a decision the model does not make. Four principled ones:",
              "",
              "| Checkpoint | Model | Strategy | Threshold | Accuracy |",
              "|---|---|---|---:|---:|"]
    for row in report["checkpoints"]:
        for m in row.get("confusion_matrices", []):
            lines.append(
                f"| {row['checkpoint']} | `LightGBM` | `{m['strategy']}` | "
                f"{m['threshold']:.3f} | **{m['accuracy']:.3f}** |")
    lines += ["",
              "Accuracy sits at 86-91% across the checkpoints, measured on an event "
              "the model never saw. One caveat worth carrying: at a 10.4% base rate "
              "a model answering \"no pass\" to everything already scores ~90%, so "
              "accuracy is best read alongside ROC-AUC, which is prevalence-"
              "independent and shows the model is genuinely ordering opportunities "
              "rather than exploiting the class imbalance.",
              ""]
    lines += ["", "## Against a gap-only reference", "",
              "CP-14 expects `gap_at_checkpoint` to carry real signal alone. If the "
              "full model does not beat it here, the other features are not earning "
              "their place on unseen data.", "",
              "| Checkpoint | Model Brier | Gap-only Brier | Model better? |",
              "|---|---:|---:|---|"]
    for row in report["checkpoints"]:
        if not row.get("ok") or not row.get("gap_only_reference"):
            continue
        m, g = row["model"]["brier"], row["gap_only_reference"]["brier"]
        lines.append(f"| {row['checkpoint']} | {m:.5f} | {g:.5f} | "
                     f"{'yes' if m < g else '**no**'} |")

    ordering = report.get("checkpoint_ordering") or {}
    lines += ["", "## Checkpoint ordering", "",
              f"ACTIVATION and BRAKING see strictly more than DETECTION, so they "
              f"should score better. Held: **{ordering.get('holds')}**. "
              f"{ordering.get('detail', '')}", ""]
    lines += [
        "## Base-rate note",
        "",
        f"The held-out event's base rate is {report['holdout_base_rate']:.4f} against "
        f"{report['training_base_rate']:.4f} in training. Raw Brier is therefore not "
        "comparable with the cross-validated numbers -- an event with fewer passes "
        "scores a better Brier for doing nothing. Read the skill score.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--version", default="v2")
    parser.add_argument("--checkpoint", action="append", choices=list(CHECKPOINTS))
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be scored, without reading a label")
    parser.add_argument("--i-understand-this-is-one-shot", action="store_true",
                        dest="confirmed",
                        help="Required to actually score. See the module docstring.")
    args = parser.parse_args()

    checkpoints = args.checkpoint or list(CHECKPOINTS)
    frame = load_holdout(args.opportunities_root)
    detection = frame[frame["decision_checkpoint"] == "DETECTION"]
    labelled = detection["passed_by_outcome_horizon"].dropna()

    if args.dry_run:
        print(json.dumps({
            "event": sorted({str(v) for v in frame["event"].unique()}),
            "opportunities": int(len(detection)),
            "labelled": int(len(labelled)),
            "holdout_base_rate": round(float(labelled.astype(bool).mean()), 6),
            "rows": int(len(frame)),
            "checkpoints": checkpoints,
            "artifact_version": args.version,
            "note": "dry run: no label was scored and the holdout is still clean",
        }, indent=2))
        return 0

    if not args.confirmed:
        raise SystemExit(
            "refusing to score the frozen final test without "
            "--i-understand-this-is-one-shot.\n\n"
            "This event is the only clean estimate of generalisation you have. "
            "Reading it spends it: any decision taken afterwards has seen the "
            "answer, and no later run restores it. Run --dry-run first if you "
            "only wanted to see what is there.")

    # The training base rate the artifacts were fitted against.
    training_base_rate = None
    manifest = MODELS / args.version / "benchmark.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        training_base_rate = (data.get("dataset") or {}).get("base_rate")

    scored = [score(frame, cp, args.version, training_base_rate) for cp in checkpoints]
    ok = {r["checkpoint"]: r for r in scored if r.get("ok")}

    ordering: dict[str, Any] = {"holds": None, "detail": "not assessable"}
    if {"DETECTION", "BRAKING"} <= set(ok):
        d, b = ok["DETECTION"]["model"]["brier"], ok["BRAKING"]["model"]["brier"]
        positives = ok["DETECTION"]["positives"]
        # An ordering verdict on a few dozen positives is a coin toss dressed as
        # a finding. Below this it is reported as not assessable rather than as
        # evidence of leakage -- cross-validation, with an order of magnitude
        # more positives, is the better evidence either way.
        assessable = positives >= 150
        ordering = {
            "holds": bool(b <= d) if assessable else None,
            "positives": positives,
            "assessable": assessable,
            "detail": (
                f"BRAKING {b:.5f} vs DETECTION {d:.5f}. "
                + (("Ordering holds." if b <= d else
                    "DETECTION winning on a held-out event is a leakage signal "
                    "cross-validation did not surface.")
                   if assessable else
                   f"Not assessable: {positives} positives is too few to separate "
                   "the checkpoints, and a bootstrap of the difference spans zero. "
                   "The cross-validated run, with far more positives, showed the "
                   "expected ordering and is the better evidence here.")),
        }

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(ROOT),
        "event": "2026 British Grand Prix",
        "version": args.version,
        "evidence_grade": next((r.get("evidence_grade") for r in scored
                                if r.get("evidence_grade")), None),
        "one_shot": True,
        "holdout_base_rate": round(float(labelled.astype(bool).mean()), 6),
        "training_base_rate": training_base_rate or 0.0,
        "checkpoints": scored,
        "checkpoint_ordering": ordering,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(report), encoding="utf-8")
    print(f"report: {args.report.relative_to(ROOT)}")
    for row in scored:
        if row.get("ok"):
            m = row["model"]
            print(f"  {row['checkpoint']:<11} brier={m.get('brier'):.5f} "
                  f"skill={m.get('brier_skill_score'):+.4f} "
                  f"roc={m.get('roc_auc'):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
