#!/usr/bin/env python3
"""Build the M12 ensemble and measure its spread (Tanveer CP-16, section 42).

``p_pass`` is the mean of the member probabilities and ``ensemble_spread`` their
standard deviation, per decision checkpoint, model family and fold.

    python scripts/train/build_ensemble.py --dry-run
    python scripts/train/build_ensemble.py --checkpoint DETECTION --family lightgbm
    python scripts/train/build_ensemble.py --members 21 --jackknife

Three departures from CP-16's written steps, each forced by measurement and each
recorded in the report rather than applied quietly:

- Members are built on a **bootstrap of the training rows** as well as a seed.
  ``LogisticRegression(solver="lbfgs")`` ignores ``random_state`` entirely, so the
  literal recipe yields a spread of exactly 0.0 for the family CP-14 selected at
  DETECTION.
- **21 members by default, not 5.** The standard deviation of five values carries a
  35.4% relative standard error; 21 brings that to 15.8% for a few seconds of CPU.
- ``--jackknife`` additionally refits with one training event removed at a time.
  Member disagreement cannot see error common to every member, and that error
  dominates here, so the jackknife figure is reported beside it as the honest one.

Parallelism is sized from the machine: fits are small, so the cores go to running
many cells at once rather than many threads inside one. See ``plan_hardware``.
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
    DESIGNS,
    FAMILIES,
    assert_disjoint,
    available_families,
    configure_threads,
    git_commit,
    plan_hardware,
    plan_splits,
    summarise,
)
from trackshift.pass_model.ensemble import (  # noqa: E402
    BAGGED_FAMILIES,
    DEFAULT_MEMBERS,
    EnsembleError,
    check_ensemble_gates,
    combine,
    event_jackknife_sd,
    fit_members,
    out_of_support_flags,
    relative_standard_error,
)

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
VALIDATION = ROOT / "artifacts" / "validation"
REPORT = VALIDATION / "ensemble_report.md"
MODELS_ROOT = ROOT / "artifacts" / "models" / "pass"

#: Set once per worker process by the pool initialiser. The opportunity table is
#: ~15k rows; passing it per task would pickle it once for every cell.
_FRAME: Any = None


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _init_worker(threads: int, frame: Any) -> None:
    """Pin thread pools before the numeric stack loads, then share the frame."""
    configure_threads(threads)
    global _FRAME
    _FRAME = frame


def build_cell(
    checkpoint: str,
    family: str,
    fold,
    *,
    n_members: int,
    seed: int,
    threads: int,
    deterministic: bool,
    include_identity: bool,
    jackknife: bool,
    frame: Any = None,
) -> dict[str, Any]:
    """Fit one (checkpoint, family, fold) ensemble. Module-level so it pickles."""
    import time

    started = time.perf_counter()
    table = frame if frame is not None else _FRAME
    if table is None:  # pragma: no cover - defensive
        raise EnsembleError("worker was not initialised with the opportunity table")
    try:
        P, y, members = fit_members(
            table, checkpoint, family, fold,
            n_members=n_members, seed=seed, threads=threads,
            deterministic=deterministic, include_identity=include_identity,
        )
        jack = None
        jack_refits = 0
        notes: list[str] = []
        if jackknife:
            try:
                jack, jack_refits = event_jackknife_sd(
                    table, checkpoint, family, fold, seed=seed, threads=threads,
                    deterministic=deterministic, include_identity=include_identity,
                )
            except EnsembleError as exc:
                notes.append(f"event jackknife unavailable: {exc}")

        result = combine(
            P, y, checkpoint=checkpoint, family=family, fold=fold.name,
            members=members, event_jackknife=jack, notes=notes,
        )
        flags = out_of_support_flags(table, checkpoint, fold,
                                     include_identity=include_identity)
        payload = result.summary()
        payload.update({
            "ok": True,
            "bagged": family in BAGGED_FAMILIES,
            "jackknife_refits": jack_refits,
            "out_of_support_rows": int(flags.sum()),
            "out_of_support_fraction": float(flags.mean()) if flags.size else 0.0,
            "gates": check_ensemble_gates(result),
            "seconds": round(time.perf_counter() - started, 3),
        })
        return payload
    except Exception as exc:
        return {
            "ok": False, "checkpoint": checkpoint, "family": family, "fold": fold.name,
            "error": f"{type(exc).__name__}: {exc}",
            "seconds": round(time.perf_counter() - started, 3),
        }


def _fmt(value: Any, places: int = 4) -> str:
    if value is None:
        return "--"
    return f"{value:.{places}f}" if isinstance(value, float) else str(value)


def render(run: dict[str, Any], split: dict[str, Any], cells: list[dict[str, Any]],
           hardware: dict[str, Any]) -> str:
    lines = [
        "# Pass-model ensemble spread (M12, CP-16)",
        "",
        f"Generated {run['created_utc']} at commit `{run['git_commit']}`.",
        "",
        "| Run | Value |",
        "|---|---|",
        f"| Members per ensemble | {run['members']} |",
        f"| Base seed | {run['seed']} |",
        f"| Bagged families | {', '.join(sorted(BAGGED_FAMILIES))} |",
        f"| Event jackknife | {run['jackknife']} |",
        f"| Split design | {split['design']} ({split['n_folds']} folds) |",
        f"| Spread relative standard error | {_fmt(run['spread_rse'], 3)} |",
        "",
        "## What this number is, and is not",
        "",
        "`ensemble_spread` is the standard deviation across members, which is CP-16's",
        "definition and what the API contract expects (MODELS.md C4). It measures",
        "**disagreement among members that share a training distribution**, so it cannot",
        "see error common to all of them. It goes to the UI beside `p_pass`; section 33",
        "does not list it among the planner's uncertainty inputs.",
        "",
        "The scale check is `gap/band`: the RMS calibration gap against the mean spread.",
        "It is deliberately NOT measured against raw RMSE -- a probability scored on a 0/1",
        "label carries irreducible Bernoulli error of sqrt(b(1-b)) (the `Irreducible`",
        "column), which no band should be asked to cover and which no model removes.",
        "",
        "Members are bootstrapped as well as seeded. `LogisticRegression(solver=\"lbfgs\")`",
        "ignores `random_state`, so seeds alone give a spread of exactly 0.0 for the",
        "family CP-14 selected at DETECTION -- measured on all 21 checkpoint-by-fold cells.",
        "MLP is deliberately not bagged: its seed already drives weight initialisation.",
        "",
        "## Results",
        "",
        "| Checkpoint | Family | Fold | Mean spread | p95 | Zero-spread | Sharpness kept | RMS calib gap | gap/band | Irreducible | Jackknife x | Out-of-support |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cell in sorted([c for c in cells if c.get("ok")],
                       key=lambda c: (c["checkpoint"], c["family"], c["fold"])):
        lines.append(
            f"| {cell['checkpoint']} | {cell['family']} | {cell['fold']} | "
            f"{_fmt(cell['mean_spread'])} | {_fmt(cell['p95_spread'])} | "
            f"{cell['zero_spread_fraction']:.1%} | "
            f"{_fmt(cell['sharpness_retention'], 3)} | "
            f"{_fmt(cell.get('rms_calibration_gap'))} | "
            + (f"{cell['rms_calibration_gap'] / cell['mean_spread']:.2f}x | "
               if cell.get('rms_calibration_gap') and cell.get('mean_spread') else "-- | ")
            + f"{_fmt(cell.get('irreducible_rmse'), 3)} | "
            f"{_fmt(cell.get('jackknife_to_member_ratio'), 2)} | "
            f"{cell['out_of_support_fraction']:.1%} |"
        )

    failed_gates: dict[str, list[str]] = {}
    for cell in cells:
        for gate in cell.get("gates", []):
            if gate["passed"] is False:
                failed_gates.setdefault(gate["gate"], []).append(
                    f"{cell['checkpoint']}/{cell['family']}/{cell['fold']}"
                )
    lines += ["", "## CP-16 acceptance gates", "",
              "CP-16's own thresholds are restated here so they can fire. \"Spread not ~0\"",
              "and \"spread not > 0.25\" are not on a meaningful scale at a 15% base rate, and",
              "\"the ensemble mean is at least as well calibrated as any member\" reduces to a",
              "Jensen identity when tested against the member mean.", "",
              "| Gate | Failing cells |", "|---|---|"]
    all_gates = {g["gate"] for c in cells for g in c.get("gates", [])}
    for name in sorted(all_gates):
        hits = failed_gates.get(name, [])
        lines.append(f"| {name} | {'none' if not hits else f'{len(hits)}: ' + ', '.join(hits[:4])} |")

    broken = [c for c in cells if not c.get("ok")]
    if broken:
        lines += ["", "## Cells that did not complete", "",
                  "| Checkpoint | Family | Fold | Error |", "|---|---|---|---|"]
        for cell in broken:
            lines.append(f"| {cell['checkpoint']} | {cell['family']} | {cell['fold']} | "
                         f"{cell['error']} |")

    lines += [
        "", "## Known limitation: spread shrinks outside the training support", "",
        "CP-16 asks that spread widen where data is thin. For the tree families it does",
        "the opposite: beyond the training range every member returns its boundary leaf,",
        "so they agree and the spread falls. Extrapolation is therefore reported as its",
        "own `out_of_support` fraction rather than inferred from spread, which would read",
        "as confidence exactly where the model is least supported.",
        "",
        "## Hardware", "",
        f"{hardware['workers']} concurrent cell(s), {hardware['threads_per_fit']} thread(s) "
        f"each; process pool: {hardware['use_process_pool']}.", "",
    ]
    for note in hardware.get("rationale", []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--checkpoint", action="append", choices=list(CHECKPOINTS))
    parser.add_argument("--family", action="append", choices=list(FAMILIES))
    parser.add_argument("--design", choices=list(DESIGNS), default="auto")
    parser.add_argument("--demo-scope", choices=[s.value for s in DemoScope],
                        default=DemoScope.TRACK.value)
    parser.add_argument("--members", type=int, default=DEFAULT_MEMBERS,
                        help=f"Members per ensemble (default {DEFAULT_MEMBERS}; "
                             "CP-16 says 5, which is too few to report a per-row std)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--jackknife", action="store_true",
                        help="Also refit leaving out one training event at a time")
    parser.add_argument("--jobs", type=int, default=None,
                        help="Concurrent cells; default sized from cores and free memory")
    parser.add_argument("--threads-per-fit", type=int, default=None)
    parser.add_argument("--identity", action="store_true")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--out-root", type=Path, default=MODELS_ROOT)
    parser.add_argument("--version", default="v1")
    parser.add_argument("--no-artifacts", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import pandas as pd

    checkpoints = args.checkpoint or list(CHECKPOINTS)
    requested = args.family or list(FAMILIES)
    availability = available_families(requested)
    families = [f for f in requested if availability[f] is None]
    if not families:
        raise SystemExit(f"no requested family could be imported: {availability}")

    paths = sorted(args.opportunities_root.glob("event=*/opportunities.parquet"))
    if not paths:
        raise SystemExit(
            f"no opportunity partitions under {args.opportunities_root}. Build them first:\n"
            "  python scripts/features/build_opportunities.py"
        )
    frame = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    sources = [p.relative_to(ROOT).as_posix() for p in paths]

    plan = plan_splits(frame, design=args.design, seed=args.seed,
                       demo_scope=DemoScope(args.demo_scope))
    assert_disjoint(plan)
    split_summary = summarise(frame, plan)
    train_rows = max((r["train_labelled"] for r in split_summary), default=0)

    cells = [(c, f, fold) for c in checkpoints for f in families for fold in plan.folds]
    # Each cell fits `members` models (plus jackknife refits), so the per-cell cost
    # is members x a single fit. Size the pool on that, not on one fit.
    hardware = plan_hardware(len(cells), train_rows, workers=args.jobs,
                             threads_per_fit=args.threads_per_fit)

    if args.dry_run:
        print(json.dumps({
            "split": plan.as_dict(),
            "checkpoints": checkpoints, "families": families,
            "members": args.members,
            "bagged_families": sorted(BAGGED_FAMILIES),
            "cells": len(cells),
            "model_fits": len(cells) * args.members
            + (len(cells) * max(0, len(plan.folds) - 1) if args.jackknife else 0),
            "spread_relative_standard_error": relative_standard_error(args.members),
            "hardware": hardware.as_dict(),
        }, indent=2, default=str))
        return 0

    common = dict(
        n_members=args.members, seed=args.seed, threads=hardware.threads_per_fit,
        deterministic=not args.fast, include_identity=args.identity,
        jackknife=args.jackknife,
    )
    results: list[dict[str, Any]] = []
    print(f"building {len(cells)} ensemble(s) of {args.members} members", flush=True)

    if hardware.use_process_pool:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=hardware.workers,
                                 initializer=_init_worker,
                                 initargs=(hardware.threads_per_fit, frame)) as pool:
            futures = [pool.submit(build_cell, c, f, fold, **common) for c, f, fold in cells]
            for done, future in enumerate(as_completed(futures), start=1):
                cell = future.result()
                results.append(cell)
                flag = "ok " if cell.get("ok") else "ERR"
                print(f"  [{done:>3}/{len(cells)}] {flag} {cell['checkpoint']:<10} "
                      f"{cell['family']:<9} {cell['fold']:<24} {cell.get('seconds')}s"
                      + ("" if cell.get("ok") else f"  {cell['error']}"), flush=True)
    else:
        configure_threads(hardware.threads_per_fit)
        for done, (checkpoint, family, fold) in enumerate(cells, start=1):
            cell = build_cell(checkpoint, family, fold, frame=frame, **common)
            results.append(cell)
            flag = "ok " if cell.get("ok") else "ERR"
            print(f"  [{done:>3}/{len(cells)}] {flag} {cell['checkpoint']:<10} "
                  f"{cell['family']:<9} {cell['fold']:<24} {cell.get('seconds')}s", flush=True)

    # Finish order varies run to run; sort so the report is comparable between runs.
    results.sort(key=lambda c: (c["checkpoint"], c["family"], c["fold"]))

    run = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(ROOT),
        "seed": args.seed,
        "members": args.members,
        "jackknife": args.jackknife,
        "deterministic": not args.fast,
        "spread_rse": relative_standard_error(args.members),
        "checkpoints": checkpoints,
        "families": families,
        "source_datasets": sources,
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(run, plan.as_dict(), results, hardware.as_dict()),
                           encoding="utf-8")
    if not args.no_artifacts:
        target = args.out_root / args.version
        target.mkdir(parents=True, exist_ok=True)
        (target / "ensemble.json").write_text(
            json.dumps({"run": run, "split": plan.as_dict(), "cells": results},
                       indent=2, default=str), encoding="utf-8")
        print(f"artifact: {_display(target / 'ensemble.json')}")
    print(f"report: {_display(args.report)}")

    failed = [g["gate"] for c in results for g in c.get("gates", []) if g["passed"] is False]
    for name in sorted(set(failed)):
        print(f"GATE FAILED: {name} ({failed.count(name)} cell(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
