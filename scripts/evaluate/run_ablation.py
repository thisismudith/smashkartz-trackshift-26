#!/usr/bin/env python3
"""Run the CP-23 feature-group ablation (M28).

Leave-one-group-out and add-one-group-in against a minimal baseline, over
repeated seeds, on the same splits CP-14 uses. Section 35 keeps a feature group
only if it earns its place; this measures whether it does.

    python scripts/evaluate/run_ablation.py --dry-run
    python scripts/evaluate/run_ablation.py --checkpoint DETECTION --family lightgbm
    python scripts/evaluate/run_ablation.py --jobs 10

``--dry-run`` reports the groups discovered from the registry, the configurations
that would be fitted and the total cell count, without fitting anything. Run it
first: the cell count is the product of checkpoints, families, folds, seeds and
configurations, and it grows faster than people expect.

The split contract is CP-14's: ``battle_id`` against the written C9 assignment,
opportunities with no C8 episode excluded rather than relabelled. An ablation
measured on a different split from the benchmark it informs would not be
comparable with it.
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
from trackshift.eval.ablation import (  # noqa: E402
    ABLATION_SCHEMA_VERSION,
    DEFAULT_SEEDS,
    PRIMARY_METRIC,
    AblationResult,
    build_plans,
    noise_floor,
    summarise_group,
)
from trackshift.features.opportunities import CHECKPOINTS  # noqa: E402
from trackshift.pass_model.api import (  # noqa: E402
    FAMILIES,
    assert_disjoint,
    available_families,
    fit_cell,
    git_commit,
    grade_evidence,
    load_assignments,
    plan_splits,
    select_features,
)
from trackshift.pass_model.features import feature_groups  # noqa: E402

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
SPLIT_ASSIGNMENTS = ROOT / "data" / "processed" / "split_assignments"
REPORT = ROOT / "artifacts" / "validation" / "ablation_report.md"
OUT = ROOT / "artifacts" / "validation" / "ablation.json"


def load_opportunities(root: Path):
    import pandas as pd

    root = root.expanduser().resolve()
    paths = sorted(root.glob("event=*/opportunities.parquet"))
    if not paths:
        raise SystemExit(
            f"no opportunity partitions under {root}. Build them first:\n"
            "  python scripts/features/build_opportunities.py")
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def run_one(frame, checkpoint, family, fold, seed, plan, deterministic):
    """One ablation cell. Module-level shape so it pickles into a pool."""
    result, _ = fit_cell(
        frame, checkpoint, family, fold, seed=seed, threads=1,
        deterministic=deterministic, include_identity=True,
        drop_features=tuple(plan["drop"]), return_model=False,
    ), None
    metrics = (result.metrics or {}).get("test") or {}
    schema = (result.metrics or {}).get("feature_schema") or {}
    return AblationResult(
        checkpoint=checkpoint, family=family, fold=fold.name, seed=seed,
        mode=plan["mode"], group=plan["group"],
        metric=metrics.get(PRIMARY_METRIC),
        n_test=metrics.get("n"),
        n_features=schema.get("n_features"),
        ok=result.ok and metrics.get(PRIMARY_METRIC) is not None,
        error=result.error,
    )


def render(report: dict[str, Any]) -> str:
    lines = [
        "# Feature-group ablation (M28, CP-23)",
        "",
        f"Generated {report['created_utc']} at commit `{report['git_commit']}`.",
        "",
    ]
    evidence = report.get("evidence") or {}
    if evidence and not evidence.get("is_cp14_acceptance_run"):
        lines += [
            f"> **Evidence grade: {evidence.get('grade')}.** The split is leakage-safe "
            "and these deltas are real, but the run does not meet CP-14's acceptance "
            "conditions, so the decisions below are provisional:",
            ">",
        ]
        lines += [f"> - {r}" for r in evidence.get("reasons", [])]
        lines += [""]

    lines += [
        "| Setting | Value |",
        "|---|---|",
        f"| Primary metric | `{PRIMARY_METRIC}` (lower is better) |",
        f"| Seeds | {', '.join(map(str, report['seeds']))} |",
        f"| Families | {', '.join(report['families'])} |",
        f"| Folds | {report['n_folds']} |",
        f"| Cells fitted | {report['cells_ok']} of {report['cells_total']} |",
        "",
        "A **positive** leave-one-out delta means removing the group made the model "
        "worse, so the group was earning its place. Deltas inside the seed-to-seed "
        "noise floor are not distinguishable from rerunning the same configuration.",
        "",
    ]

    for checkpoint, verdicts in report["verdicts"].items():
        floor = report["noise_floor"].get(checkpoint)
        lines += [
            f"## {checkpoint}",
            "",
            f"Seed-to-seed noise floor: "
            + (f"`{floor:.5f}` {PRIMARY_METRIC}." if floor is not None
               else "not measurable (one seed)."),
            "",
            "| Group | n feat | Leave-one-out Δ | 95% interval | Add-one-in Δ | Verdict |",
            "|---|---:|---:|---|---:|---|",
        ]
        for v in verdicts:
            interval = (f"[{v['leave_one_out_interval'][0]:+.5f}, "
                        f"{v['leave_one_out_interval'][1]:+.5f}]"
                        if v.get("leave_one_out_interval") else "—")
            loo = f"{v['leave_one_out_delta']:+.5f}" if v.get("leave_one_out_delta") is not None else "—"
            aoi = f"{v['add_one_in_delta']:+.5f}" if v.get("add_one_in_delta") is not None else "—"
            flag = " ⚠️" if v.get("is_identity") else ""
            lines.append(
                f"| `{v['group']}`{flag} | {v['n_features']} | {loo} | {interval} | "
                f"{aoi} | **{v['verdict']}** |")
        lines += [""]
        for v in verdicts:
            lines.append(f"- `{v['group']}` — {v['rationale']}")
        lines += [""]

    lines += [
        "## Reading the verdicts",
        "",
        "- **KEEP** — removing it costs more than the noise floor, interval clear of zero.",
        "- **DROP** — removing it *improves* the metric; the group is costing accuracy.",
        "- **REDUNDANT** — no leave-one-out effect, but it beats the minimal baseline "
        "alone. The signal is real and also carried elsewhere, so it is safe to drop "
        "only while that other group stays.",
        "- **UNINFORMATIVE** — no effect either way; the signal was never there.",
        "",
        "⚠️ marks identity groups. Section 17: a large gain there is evidence of "
        "memorising drivers and teams rather than learning racecraft, and should be "
        "read against the racecraft groups before being kept.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--split-assignments", type=Path, default=SPLIT_ASSIGNMENTS)
    parser.add_argument("--checkpoint", action="append", choices=list(CHECKPOINTS))
    parser.add_argument("--family", action="append", choices=list(FAMILIES),
                        default=None)
    parser.add_argument("--seed", action="append", type=int, default=None,
                        help=f"Repeat for several; default {list(DEFAULT_SEEDS)}")
    parser.add_argument("--group", action="append",
                        help="Ablate only these groups; default is every discovered group")
    parser.add_argument("--demo-scope", choices=[s.value for s in DemoScope],
                        default=DemoScope.TRACK.value)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--fast", action="store_true",
                        help="Drop determinism constraints; recorded in the report")
    parser.add_argument("--allow-event-split", action="store_true",
                        help="Coarser split unit; recorded as REDUCED evidence")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    checkpoints = args.checkpoint or list(CHECKPOINTS)
    seeds = tuple(args.seed) if args.seed else DEFAULT_SEEDS
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

    plan_obj = plan_splits(frame, seed=seeds[0], demo_scope=DemoScope(args.demo_scope),
                           require_unit=require_unit, assignments=assignments)
    assert_disjoint(plan_obj)
    evidence = grade_evidence(frame, plan_obj)
    if not evidence["is_cp14_acceptance_run"]:
        print(f"EVIDENCE GRADE {evidence['grade']}: decisions below are provisional",
              flush=True)

    # Groups and configurations, per checkpoint: a group present at BRAKING may
    # be absent at DETECTION, and averaging across checkpoints would hide that.
    per_checkpoint: dict[str, dict[str, Any]] = {}
    for checkpoint in checkpoints:
        rows = frame[frame["decision_checkpoint"] == checkpoint]
        selection = select_features(checkpoint, rows.columns, include_identity=True,
                                    dtypes=rows.dtypes)
        groups = feature_groups(selection)
        if args.group:
            groups = {k: v for k, v in groups.items() if k in set(args.group)}
        per_checkpoint[checkpoint] = {
            "selection": selection,
            "groups": groups,
            "plans": build_plans(groups, all_columns=selection.columns),
        }

    cells = [(cp, fam, fold, seed, plan)
             for cp, info in per_checkpoint.items()
             for fam in families
             for fold in plan_obj.folds
             for seed in seeds
             for plan in info["plans"]]

    if args.dry_run:
        print(json.dumps({
            "evidence": evidence,
            "seeds": list(seeds),
            "families": families,
            "n_folds": len(plan_obj.folds),
            "cells_total": len(cells),
            "groups": {cp: {g: list(c) for g, c in info["groups"].items()}
                       for cp, info in per_checkpoint.items()},
            "configurations_per_checkpoint": {
                cp: [f"{p['mode']}:{p['group'] or '-'}" for p in info["plans"]]
                for cp, info in per_checkpoint.items()},
        }, indent=2, default=str))
        return 0

    print(f"fitting {len(cells)} ablation cell(s)", flush=True)
    results: list[AblationResult] = []
    deterministic = not args.fast

    if args.jobs and args.jobs > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(run_one, frame, cp, fam, fold, seed, plan, deterministic): i
                for i, (cp, fam, fold, seed, plan) in enumerate(cells)
            }
            for done, future in enumerate(as_completed(futures), start=1):
                results.append(future.result())
                if done % 25 == 0 or done == len(cells):
                    print(f"  [{done}/{len(cells)}]", flush=True)
    else:
        for done, (cp, fam, fold, seed, plan) in enumerate(cells, start=1):
            results.append(run_one(frame, cp, fam, fold, seed, plan, deterministic))
            if done % 25 == 0 or done == len(cells):
                print(f"  [{done}/{len(cells)}]", flush=True)

    verdicts: dict[str, list[dict[str, Any]]] = {}
    floors: dict[str, Any] = {}
    for checkpoint, info in per_checkpoint.items():
        floors[checkpoint] = noise_floor(results, checkpoint)
        verdicts[checkpoint] = [
            summarise_group(results, checkpoint=checkpoint, group=group,
                            n_features=len(members)).as_dict()
            for group, members in info["groups"].items()
        ]

    report = {
        "schema_version": ABLATION_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(ROOT),
        "primary_metric": PRIMARY_METRIC,
        "seeds": list(seeds),
        "families": families,
        "n_folds": len(plan_obj.folds),
        "deterministic": deterministic,
        "evidence": evidence,
        "split": plan_obj.as_dict(),
        "cells_total": len(cells),
        "cells_ok": sum(1 for r in results if r.ok),
        "noise_floor": floors,
        "verdicts": verdicts,
        "results": [r.as_dict() for r in results],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(report), encoding="utf-8")
    print(f"report: {args.report.relative_to(ROOT)}")
    print(f"data  : {args.out.relative_to(ROOT)}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"{len(failed)} cell(s) failed; first: {failed[0].error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
