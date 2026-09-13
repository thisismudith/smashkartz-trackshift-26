#!/usr/bin/env python3
"""Fine-tune the selected pass model on the 2026 dataset (M13, CP-17).

CP-14 selects a family; CP-15 and CP-16 say which calibrated or ensembled form
of it to use. This searches that family's hyperparameters on the same C9
battle_id splits and reports what the tuning bought.

    python scripts/train/tune_pass_model.py --dry-run
    python scripts/train/tune_pass_model.py --objective roc_auc --jobs 10
    python scripts/train/tune_pass_model.py --family lightgbm --checkpoint BRAKING

The family defaults to whichever CP-14 ranked first for the checkpoint, read
from ``artifacts/models/pass/<version>/benchmark.json``. Pass ``--family`` to
override it.

**Two things this refuses to do.** It never scores a configuration on the test
split -- selection happens on each fold's validation split, and the winner is
scored on the test split exactly once, afterwards. And it never reports a
ROC-AUC win without the calibration cost beside it: section 26 selects on
calibration because the DP consumes probabilities, not rankings.
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
from trackshift.pass_model.tuning import (  # noqa: E402
    OBJECTIVES,
    SEARCH_SPACES,
    TUNING_SCHEMA_VERSION,
    TrialResult,
    expand_space,
    rank_trials,
    select_winner,
)

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
SPLIT_ASSIGNMENTS = ROOT / "data" / "processed" / "split_assignments"
BENCHMARK = ROOT / "artifacts" / "models" / "pass"
REPORT = ROOT / "artifacts" / "validation" / "tuning_report.md"
OUT = ROOT / "artifacts" / "validation" / "tuning.json"


def load_opportunities(root: Path):
    import pandas as pd

    root = root.expanduser().resolve()
    paths = sorted(root.glob("event=*/opportunities.parquet"))
    if not paths:
        raise SystemExit(
            f"no opportunity partitions under {root}. Build them first:\n"
            "  python scripts/features/build_opportunities.py")
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def best_family_from_benchmark(path: Path, checkpoint: str) -> tuple[str | None, str]:
    """The family CP-14 ranked first at ``checkpoint``, and how it was decided."""
    manifest = path / "benchmark.json"
    if not manifest.exists():
        return None, f"no CP-14 benchmark at {manifest}; --family must be given"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{manifest} is unreadable ({exc}); --family must be given"

    primary = (data.get("run") or {}).get("primary_pass") or "without_identity"
    rows = [r for r in (data.get("aggregated") or {}).get(primary, [])
            if r.get("checkpoint") == checkpoint and r.get("brier") is not None]
    if not rows:
        return None, f"{manifest} has no scored rows for {checkpoint}"
    # Section 26: calibration first, so Brier orders the choice.
    rows.sort(key=lambda r: float(r["brier"]))
    return str(rows[0]["family"]), (
        f"CP-14 ranked {rows[0]['family']} first at {checkpoint} on Brier "
        f"({float(rows[0]['brier']):.5f}) in {manifest.name}")


def run_trial(frame, trial: int, checkpoint: str, family: str, fold,
              params: dict[str, Any], seed: int, role: str = "validation") -> TrialResult:
    """Fit one configuration on one fold and score it on ``role``.

    ``role`` is "validation" during the search. The test split is scored once,
    for the winner only, after selection is finished -- scoring every trial on
    it would make the reported test number a selection statistic.
    """
    out = TrialResult(trial=trial, checkpoint=checkpoint, family=family,
                      fold=fold.name, params=params, ok=False)
    try:
        rows = frame[frame["decision_checkpoint"] == checkpoint]
        selection = select_features(checkpoint, rows.columns, include_identity=False,
                                    dtypes=rows.dtypes)
        train = rows.loc[rows.index.intersection(fold.train)]
        target = rows.loc[rows.index.intersection(
            fold.validation if role == "validation" else fold.test)]
        if train.empty or target.empty:
            raise ValueError(f"empty train or {role} split")

        X_train, y_train = build_matrix(train, selection)
        X_eval, y_eval = build_matrix(target, selection)
        if len(set(map(int, y_train))) < 2 or len(set(map(int, y_eval))) < 2:
            raise ValueError("a split has a single class; nothing to score")

        model = PassModel(family, numeric=selection.numeric,
                          categorical=selection.categorical, seed=seed, threads=1,
                          overrides=params)
        # No eval set during the search: early stopping on the same split the
        # trial is scored against would select the stopping point on the score.
        model.fit(X_train, y_train)
        metrics = evaluate(y_eval, model.predict_proba(X_eval),
                           base_rate=float(y_train.mean()))
        return TrialResult(trial=trial, checkpoint=checkpoint, family=family,
                           fold=fold.name, params=params, metrics=metrics,
                           n_validation=int(len(y_eval)), ok=True)
    except Exception as exc:
        return TrialResult(trial=trial, checkpoint=checkpoint, family=family,
                           fold=fold.name, params=params,
                           ok=False, error=f"{type(exc).__name__}: {exc}")
    return out


def render(report: dict[str, Any]) -> str:
    lines = [
        "# Pass-model fine-tuning (M13, CP-17)",
        "",
        f"Generated {report['created_utc']} at commit `{report['git_commit']}`.",
        "",
    ]
    evidence = report.get("evidence") or {}
    if evidence and not evidence.get("is_cp14_acceptance_run"):
        lines += [
            f"> **Evidence grade: {evidence.get('grade')}.** The split is leakage-safe "
            "and these numbers are real, but the run does not meet CP-14's acceptance "
            "conditions, so any adopted configuration is provisional:",
            ">",
        ]
        lines += [f"> - {r}" for r in evidence.get("reasons", [])]
        lines += [""]

    lines += [
        "| Setting | Value |",
        "|---|---|",
        f"| Objective | `{report['objective']}` |",
        f"| Family | `{report['family']}` ({report['family_reason']}) |",
        f"| Trials | {report['n_trials']} (trial 0 is CP-14's untuned block) |",
        f"| Folds | {report['n_folds']} |",
        f"| Selection split | validation (the test split is never tuned on) |",
        "",
    ]

    for checkpoint, decision in report["decisions"].items():
        lines += [f"## {checkpoint}", ""]
        if decision.get("status") != "OK":
            lines += [f"{decision.get('detail', decision.get('status'))}", ""]
            continue
        winner, baseline = decision["winner"], decision["baseline"]
        lines += [
            f"**{decision['verdict']}** — {decision['detail']}",
            "",
            "| | Trial | Params | "
            + " | ".join(f"`{m}`" for m in OBJECTIVES) + " |",
            "|---|---:|---|" + "---:|" * len(OBJECTIVES),
        ]
        for label, row in (("baseline", baseline), ("best", winner)):
            params = ", ".join(f"{k}={v}" for k, v in sorted(row["params"].items())) or "—"
            values = " | ".join(
                (f"{row[m]:.5f}" if row.get(m) is not None else "—") for m in OBJECTIVES)
            lines.append(f"| {label} | {row['trial']} | {params} | {values} |")
        lines += [""]
        if decision.get("calibration_regressed"):
            lines += [
                f"⚠️ Calibration regressed: `{decision['calibration_metric']}` moved "
                f"{decision['calibration_delta']:+.5f}. Section 26 selects on "
                "calibration because the DP consumes probabilities, not rankings.",
                "",
            ]
        held = report["holdout"].get(checkpoint)
        if held:
            lines += [
                "Scored once on the held-out test split, after selection:",
                "",
                "| Config | " + " | ".join(f"`{m}`" for m in OBJECTIVES) + " |",
                "|---|" + "---:|" * len(OBJECTIVES),
            ]
            for label, row in held.items():
                values = " | ".join(
                    (f"{row[m]:.5f}" if row.get(m) is not None else "—")
                    for m in OBJECTIVES)
                lines.append(f"| {label} | {values} |")
            lines += [""]

    lines += [
        "## How to read this",
        "",
        "- Configurations are selected on each fold's **validation** split. The test "
        "split is scored once, afterwards, for the baseline and the winner only.",
        "- `KEEP_BASELINE` means the search found no gain distinguishable from the "
        "baseline's own fold-to-fold spread. A search always produces a leader; that "
        "is not the same as finding an improvement.",
        "- A ROC-AUC gain paid for in Brier is flagged, not silently adopted.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--split-assignments", type=Path, default=SPLIT_ASSIGNMENTS)
    parser.add_argument("--benchmark-root", type=Path, default=BENCHMARK)
    parser.add_argument("--benchmark-version", default="v1")
    parser.add_argument("--checkpoint", action="append", choices=list(CHECKPOINTS))
    parser.add_argument("--family", choices=list(FAMILIES),
                        help="Default: whichever CP-14 ranked first on Brier")
    parser.add_argument("--objective", choices=sorted(OBJECTIVES), default="roc_auc",
                        help="Metric the search maximises or minimises. Section 26 "
                             "selects on calibration, so 'brier' is the safer choice; "
                             "whichever is chosen, every metric is reported.")
    parser.add_argument("--max-trials", type=int, default=24,
                        help="Cap on configurations; the grid is sampled if larger")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--demo-scope", choices=[s.value for s in DemoScope],
                        default=DemoScope.TRACK.value)
    parser.add_argument("--allow-event-split", action="store_true")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    checkpoints = args.checkpoint or list(CHECKPOINTS)
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

    plan = plan_splits(frame, seed=args.seed, demo_scope=DemoScope(args.demo_scope),
                       require_unit=require_unit, assignments=assignments)
    assert_disjoint(plan)
    evidence = grade_evidence(frame, plan)
    if not evidence["is_cp14_acceptance_run"]:
        print(f"EVIDENCE GRADE {evidence['grade']}: any adopted configuration is "
              "provisional", flush=True)

    # Which family to tune, per checkpoint.
    family_for: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for checkpoint in checkpoints:
        if args.family:
            family_for[checkpoint] = args.family
            reasons[checkpoint] = "given on the command line"
            continue
        chosen, reason = best_family_from_benchmark(
            args.benchmark_root / args.benchmark_version, checkpoint)
        if chosen is None:
            raise SystemExit(f"{reason}")
        family_for[checkpoint] = chosen
        reasons[checkpoint] = reason

    availability = available_families(sorted(set(family_for.values())))
    missing = {k: v for k, v in availability.items() if v}
    if missing:
        raise SystemExit(f"family unavailable: {missing}")

    # Trial 0 is always CP-14's untuned block, so the comparison has a floor.
    trials_for: dict[str, list[dict[str, Any]]] = {}
    for checkpoint in checkpoints:
        space = SEARCH_SPACES.get(family_for[checkpoint], {})
        trials_for[checkpoint] = [{}] + expand_space(
            space, limit=max(0, args.max_trials - 1), seed=args.seed)

    cells = [(cp, i, params, fold)
             for cp in checkpoints
             for i, params in enumerate(trials_for[cp])
             for fold in plan.folds]

    if args.dry_run:
        print(json.dumps({
            "evidence": evidence,
            "objective": args.objective,
            "family_per_checkpoint": family_for,
            "family_reason": reasons,
            "trials_per_checkpoint": {c: len(t) for c, t in trials_for.items()},
            "n_folds": len(plan.folds),
            "cells_total": len(cells),
            "search_space": {c: SEARCH_SPACES.get(family_for[c], {}) for c in checkpoints},
        }, indent=2, default=str))
        return 0

    print(f"running {len(cells)} trial fit(s)", flush=True)
    results: list[TrialResult] = []
    if args.jobs and args.jobs > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(run_trial, frame, i, cp, family_for[cp], fold,
                                   params, args.seed)
                       for cp, i, params, fold in cells]
            for done, future in enumerate(as_completed(futures), start=1):
                results.append(future.result())
                if done % 25 == 0 or done == len(cells):
                    print(f"  [{done}/{len(cells)}]", flush=True)
    else:
        for done, (cp, i, params, fold) in enumerate(cells, start=1):
            results.append(run_trial(frame, i, cp, family_for[cp], fold, params, args.seed))
            if done % 25 == 0 or done == len(cells):
                print(f"  [{done}/{len(cells)}]", flush=True)

    decisions: dict[str, Any] = {}
    holdout: dict[str, Any] = {}
    for checkpoint in checkpoints:
        ranked = rank_trials(results, objective=args.objective, checkpoint=checkpoint)
        decision = select_winner(ranked, objective=args.objective)
        decisions[checkpoint] = decision
        if decision.get("status") != "OK":
            continue
        # The test split, scored once, for the baseline and the winner only.
        held: dict[str, Any] = {}
        for label, row in (("baseline", decision["baseline"]),
                           ("best", decision["winner"])):
            scored = [run_trial(frame, row["trial"], checkpoint, family_for[checkpoint],
                                fold, row["params"], args.seed, role="test")
                      for fold in plan.folds]
            ok = [s for s in scored if s.ok]
            if ok:
                held[label] = {
                    m: round(sum(float(s.metrics[m]) for s in ok if s.metrics.get(m)
                                 is not None) / max(1, sum(1 for s in ok
                                                           if s.metrics.get(m) is not None)), 6)
                    for m in OBJECTIVES
                }
        holdout[checkpoint] = held

    report = {
        "schema_version": TUNING_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(ROOT),
        "objective": args.objective,
        "family": ", ".join(sorted(set(family_for.values()))),
        "family_reason": "; ".join(sorted(set(reasons.values()))),
        "family_per_checkpoint": family_for,
        "seed": args.seed,
        "n_trials": max(len(t) for t in trials_for.values()),
        "n_folds": len(plan.folds),
        "evidence": evidence,
        "split": plan.as_dict(),
        "decisions": decisions,
        "holdout": holdout,
        "results": [r.as_dict() for r in results],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(report), encoding="utf-8")
    print(f"report: {args.report.relative_to(ROOT)}")
    for checkpoint, decision in decisions.items():
        print(f"  {checkpoint}: {decision.get('verdict', decision.get('status'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
