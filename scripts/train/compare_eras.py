#!/usr/bin/env python3
"""Compare regulation-era strategies for the pass model (AGENTS.md section 41).

Deferred, not a checkpoint: CP-17 is now pass-model fine-tuning
(``scripts/train/tune_pass_model.py``). This is kept ready for the moment
historical opportunities land.

Section 41's five strategies plus the 2026-only baseline, all on the same splits
and metrics, all reported. Section 34 forbids picking a strategy on
sophistication, so every strategy that can run is run and every strategy that
cannot is reported as blocked with its reason.

    python scripts/train/compare_eras.py --dry-run
    python scripts/train/compare_eras.py --family lightgbm
    python scripts/train/compare_eras.py --jobs 10

**The baseline matters most.** If ``modern_only`` wins, historical DRS-era data
is not helping the 2026 model, which is exactly what section 41 warns is likely
because DRS and Overtake are different mechanisms. That is a finding to record,
not a problem to engineer around.

This is blocked until 2022-2025 opportunities exist. ``--dry-run`` reports
per-strategy feasibility against whatever the table currently holds, so the
block is visible without fitting anything.
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
from trackshift.features.opportunities import CHECKPOINTS  # noqa: E402
from trackshift.pass_model.api import (  # noqa: E402
    DEFAULT_SEED,
    FAMILIES,
    assert_disjoint,
    available_families,
    build_matrix,
    evaluate,
    git_commit,
    grade_evidence,
    load_assignments,
    plan_splits,
    select_features,
)
from trackshift.pass_model.candidates import PassModel  # noqa: E402
from trackshift.pass_model.era import (  # noqa: E402
    DEFAULT_MODERN_WEIGHT,
    ERA_SCHEMA_VERSION,
    STRATEGIES,
    assess_feasibility,
    domain_weights,
    era_of,
    rank_strategies,
    split_by_era,
)

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
SPLIT_ASSIGNMENTS = ROOT / "data" / "processed" / "split_assignments"
REPORT = ROOT / "artifacts" / "validation" / "era_report.md"
OUT = ROOT / "artifacts" / "validation" / "era_comparison.json"


def load_opportunities(root: Path):
    import pandas as pd

    root = root.expanduser().resolve()
    paths = sorted(root.glob("event=*/opportunities.parquet"))
    if not paths:
        raise SystemExit(
            f"no opportunity partitions under {root}. Build them first:\n"
            "  python scripts/features/build_opportunities.py")
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def fit_strategy(frame, strategy: str, checkpoint: str, family: str, fold,
                 *, seed: int, modern_weight: float) -> dict[str, Any]:
    """Fit one strategy for one cell and score it on the 2026 test split.

    Every strategy is scored on the *same* 2026 rows, whatever it trained on.
    Scoring each on its own era's test set would compare answers to different
    questions and call the comparison a ranking.
    """
    import numpy as np

    out: dict[str, Any] = {"strategy": strategy, "checkpoint": checkpoint,
                           "family": family, "fold": fold.name, "seed": seed, "ok": False}
    try:
        rows = frame[frame["decision_checkpoint"] == checkpoint]
        selection = select_features(checkpoint, rows.columns, include_identity=False,
                                    dtypes=rows.dtypes)
        train_rows = rows.loc[rows.index.intersection(fold.train)]
        val_rows = rows.loc[rows.index.intersection(fold.validation)]
        # The test split is 2026 only, always.
        test_rows = rows.loc[rows.index.intersection(fold.test)]
        test_rows = test_rows[test_rows["year"].map(era_of) == "modern"]
        if test_rows.empty:
            raise ValueError("no 2026 rows in the test split")

        eras = train_rows["year"].map(era_of)
        if strategy == "modern_only":
            train_rows = train_rows[eras == "modern"]
        elif strategy == "separate_models":
            # The historical arm, scored on 2026: "what does a purely historical
            # model know about the new era?"
            train_rows = train_rows[eras == "historical"]

        if train_rows.empty:
            raise ValueError(f"strategy {strategy} has no training rows")

        X_train, y_train = build_matrix(train_rows, selection)
        if len(set(map(int, y_train))) < 2:
            raise ValueError("training split has a single class")
        X_val, y_val = (build_matrix(val_rows, selection)
                        if len(val_rows) else (None, None))
        X_test, y_test = build_matrix(test_rows, selection)

        weights = None
        if strategy == "domain_weighting":
            weights = domain_weights(train_rows, modern_weight)

        model = PassModel(family, numeric=selection.numeric,
                          categorical=selection.categorical, seed=seed, threads=1)
        model.fit(X_train, y_train, X_val, y_val, sample_weight=weights)

        if strategy == "historical_pretraining":
            # Continue on 2026 after the historical fit. Expressed as a second
            # fit on the modern rows seeded from the first, which is what the
            # tree families support; a true warm start would need per-family
            # handling and is recorded as a limitation rather than faked.
            modern = train_rows[train_rows["year"].map(era_of) == "modern"]
            if modern.empty:
                raise ValueError("no 2026 rows to continue training on")
            X_modern, y_modern = build_matrix(modern, selection)
            if len(set(map(int, y_modern))) > 1:
                model.fit(X_modern, y_modern, X_val, y_val)
                out["note"] = ("continued fit on 2026 rows; not a true warm start, "
                               "see the report limitation")

        metrics = evaluate(y_test, model.predict_proba(X_test),
                           base_rate=float(y_train.mean()))
        out.update({k: v for k, v in metrics.items()})
        out.update({"ok": True, "n_train": int(len(y_train)),
                    "n_train_modern": int((eras == "modern").sum()),
                    "n_train_historical": int((eras == "historical").sum()),
                    "n_test": int(len(y_test))})
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def render(report: dict[str, Any]) -> str:
    lines = [
        "# Regulation-era comparison (M13, AGENTS.md section 41)",
        "",
        f"Generated {report['created_utc']} at commit `{report['git_commit']}`.",
        "",
    ]
    blocked = [f for f in report["feasibility"] if not f["runnable"]]
    if blocked:
        lines += [
            f"> **{len(blocked)} of {len(report['feasibility'])} strategies could not "
            "be run.** Section 41's comparison needs two eras; the reasons are below "
            "and no substitute was fitted in their place.",
            "",
        ]
    lines += [
        "| Strategy | Runnable | Blockers |",
        "|---|---|---|",
    ]
    for f in report["feasibility"]:
        lines.append(f"| `{f['strategy']}` | {'yes' if f['runnable'] else '**no**'} | "
                     f"{'; '.join(f['blockers']) or '—'} |")
    lines += [""]

    if report.get("ranked"):
        lines += [
            "## Results",
            "",
            "Scored on the same 2026 test rows whatever each strategy trained on. "
            "Brier is primary and lower wins (section 26). **N is not comparable "
            "across rows** -- a strategy trained on four extra seasons is not "
            "competing on equal footing, and the training counts say so.",
            "",
            "| Rank | Strategy | Brier | Brier sd | Log loss | ROC-AUC | "
            "Folds | N train (2026 / hist) | Δ vs 2026-only |",
            "|---:|---|---:|---:|---:|---:|---:|---|---:|",
        ]
        for r in report["ranked"]:
            delta = r.get("delta_vs_modern_only")
            sd = r.get("brier_std")
            lines.append(
                f"| {r['rank']} | `{r['strategy']}` | {r.get('brier', float('nan')):.5f} | "
                + (f"{sd:.5f}" if sd is not None else "—") + " | "
                f"{r.get('log_loss', float('nan')):.5f} | "
                f"{r.get('roc_auc', float('nan')):.4f} | {r.get('folds', 0)} | "
                f"{r.get('n_train_modern', 0)} / {r.get('n_train_historical', 0)} | "
                + (f"{delta:+.5f}" if delta is not None else "—") + " |")
        lines += [""]
        winner = report["ranked"][0]
        if len(report["ranked"]) < 2:
            lines += [
                f"Only one strategy (`{winner['strategy']}`) could be run, so this is a "
                "single measurement rather than a comparison. Section 41's question -- "
                "whether historical data helps -- is not answered here and must not be "
                "reported as answered.",
                "",
            ]
        elif winner["strategy"] == "modern_only":
            lines += [
                "**The 2026-only baseline wins.** Section 41 anticipated this: DRS and "
                "Overtake are different mechanisms, so historical data need not "
                "transfer. Record it and use 2026 only; do not force historical rows "
                "in because they are available.",
                "",
            ]
    else:
        lines += [
            "## Results",
            "",
            "No strategy produced a result. With one era present there is nothing to "
            "compare, and an era comparison over a single era is not an inconclusive "
            "result -- it is an unrunnable one.",
            "",
        ]

    lines += [
        "## Limitations",
        "",
        "- `historical_pretraining` is a continued fit on the 2026 rows rather than a "
        "true warm start; the tree families do not all expose one. Read it as an "
        "upper bound on what sequential training would give, not as the strategy "
        "itself.",
        "- `domain_weighting` is unavailable for the `mlp` family, which takes no "
        "sample weights. It is omitted there rather than silently run unweighted.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--split-assignments", type=Path, default=SPLIT_ASSIGNMENTS)
    parser.add_argument("--checkpoint", action="append", choices=list(CHECKPOINTS))
    parser.add_argument("--family", action="append", choices=list(FAMILIES))
    parser.add_argument("--strategy", action="append",
                        choices=[s.name for s in STRATEGIES])
    parser.add_argument("--modern-weight", type=float, default=DEFAULT_MODERN_WEIGHT,
                        help="Weight on 2026 rows for domain_weighting (section 41: 3-5x)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--demo-scope", choices=[s.value for s in DemoScope],
                        default=DemoScope.TRACK.value)
    parser.add_argument("--allow-event-split", action="store_true")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    checkpoints = args.checkpoint or list(CHECKPOINTS)
    requested = args.family or ["lightgbm"]
    availability = available_families(requested)
    families = [n for n in requested if availability[n] is None]
    if not families:
        raise SystemExit(f"no requested family could be imported: {availability}")

    frame = load_opportunities(args.opportunities_root)

    require_unit = None if args.allow_event_split else "battle_id"
    assignments = None
    if require_unit:
        assignments = load_assignments(args.split_assignments)
        usable = frame[require_unit].notna() & (
            frame[require_unit].astype(str).str.strip() != "")
        dropped = int((~usable).sum())
        if dropped:
            print(f"excluding {dropped} of {len(frame)} rows with no battle_id "
                  f"({dropped / len(frame):.1%})", flush=True)
            frame = frame[usable].reset_index(drop=True)

    strategies = [s for s in STRATEGIES
                  if not args.strategy or s.name in set(args.strategy)]
    feasibility = [f.as_dict() for f in assess_feasibility(frame, families, strategies)]
    parts = split_by_era(frame)
    era_counts = {name: int(len(rows)) for name, rows in parts.items()}

    plan = plan_splits(frame, seed=args.seed, demo_scope=DemoScope(args.demo_scope),
                       require_unit=require_unit, assignments=assignments)
    assert_disjoint(plan)
    evidence = grade_evidence(frame, plan)

    runnable = [f for f in feasibility if f["runnable"]]
    if args.dry_run:
        print(json.dumps({
            "era_counts": era_counts,
            "evidence": evidence,
            "feasibility": feasibility,
            "runnable_strategies": [f["strategy"] for f in runnable],
            "cells": len(runnable) * len(checkpoints) * len(families) * len(plan.folds),
        }, indent=2, default=str))
        return 0

    results: list[dict[str, Any]] = []
    for f in runnable:
        for checkpoint in checkpoints:
            for family in f["families"]:
                for fold in plan.folds:
                    results.append(fit_strategy(
                        frame, f["strategy"], checkpoint, family, fold,
                        seed=args.seed, modern_weight=args.modern_weight))
        print(f"  {f['strategy']}: done", flush=True)

    report = {
        "schema_version": ERA_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(ROOT),
        "seed": args.seed,
        "modern_weight": args.modern_weight,
        "families": families,
        "checkpoints": checkpoints,
        "era_counts": era_counts,
        "evidence": evidence,
        "split": plan.as_dict(),
        "feasibility": feasibility,
        "results": results,
        "ranked": rank_strategies(results),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(report), encoding="utf-8")
    print(f"report: {args.report.relative_to(ROOT)}")

    if not runnable or len(runnable) < len(feasibility):
        blocked = [f["strategy"] for f in feasibility if not f["runnable"]]
        print(f"BLOCKED strategies (not fitted, not substituted): {blocked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
