#!/usr/bin/env python3
"""Materialise persistent battle-level C9 split assignments (A2).

CP-14 splits on ``battle_id``. Deriving that assignment inside the trainer means
every run re-derives it, and two runs that disagree cannot be compared -- the
numbers would differ for a reason nobody recorded. So the assignment is built
once, written down, hashed, and read back by every consumer.

The assignment is over *battles*, not rows and not events. A battle is the unit
that must not straddle a split: the same two cars over the same few laps scored
on both sides of a boundary is the leak CP-14's split exists to prevent.

Opportunities whose ``battle_id`` is null are **not** assigned. They are counted
and reported, and the trainer refuses to train on them. Inventing a group key
for them would put unrelated rows in one split group, which is the exact failure
the persistent assignment is meant to rule out.

    python scripts/features/build_split_assignments.py --dry-run
    python scripts/features/build_split_assignments.py
    python scripts/features/build_split_assignments.py --design kfold:5

Writes ``data/processed/split_assignments/`` -- generated evidence, not source.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.splits import (  # noqa: E402
    SPLIT_SCHEMA_VERSION,
    build_split_manifest,
    make_split,
)

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
OUT = ROOT / "data" / "processed" / "split_assignments"

#: The unit CP-14 requires. Exposed as a flag only so the refusal path can be
#: tested; changing it is not a supported way to make a blocked run proceed.
DEFAULT_UNIT = "battle_id"


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _display(path: Path) -> str:
    """Repo-relative inside the tree, absolute outside it.

    --opportunities-root may legitimately point outside the repo (a scratch run,
    a shared drive). A bare relative_to raises there, refusing the dataset for a
    reason that has nothing to do with the data.
    """
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
            "  python scripts/features/build_opportunities.py")
    frames = [pd.read_parquet(p) for p in paths]
    return pd.concat(frames, ignore_index=True), [
        Path(_display(p.resolve())).as_posix() for p in paths]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--opportunities-root", type=Path, default=OPPORTUNITIES)
    parser.add_argument("--output-root", type=Path, default=OUT)
    parser.add_argument("--unit", default=DEFAULT_UNIT,
                        help="Split unit. CP-14 requires battle_id; anything else is "
                             "recorded as reduced evidence.")
    parser.add_argument("--design", default="leave_one_event_out",
                        help="C9 design: leave_one_event_out, kfold:<k>, or year_forward")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report coverage and refusals without writing")
    args = parser.parse_args()

    import pandas as pd

    frame, sources = load_opportunities(args.opportunities_root)

    if args.unit not in frame.columns:
        raise SystemExit(
            f"opportunities carry no {args.unit!r} column. CP-14 splits on battle_id; "
            "run the M07 build with the C8 join so it is populated.")

    total = len(frame)
    present = frame[args.unit].notna() & (frame[args.unit].astype(str).str.strip() != "")
    assignable = frame[present]
    unassignable = int((~present).sum())

    status_counts: dict[str, int] = {}
    if "battle_join_status" in frame.columns:
        status_counts = {str(k): int(v) for k, v in
                         frame["battle_join_status"].value_counts(dropna=False).items()}

    coverage = float(present.mean()) if total else 0.0
    report: dict[str, Any] = {
        "schema_version": SPLIT_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "unit": args.unit,
        "design": args.design,
        "seed": args.seed,
        "source_datasets": sources,
        "rows_total": total,
        "rows_assignable": int(present.sum()),
        "rows_without_unit": unassignable,
        "unit_coverage": round(coverage, 6),
        "battle_join_status": status_counts,
        "groups": int(assignable[args.unit].nunique()) if len(assignable) else 0,
        "note": (
            "Rows without a battle_id are deliberately unassigned. They are not "
            "given a synthetic group: that would place unrelated opportunities in "
            "one split group and defeat the leakage guarantee the unit exists for."
        ),
    }

    if not len(assignable):
        report["status"] = "REFUSED_NO_ASSIGNABLE_ROWS"
        print(json.dumps(report, indent=2))
        raise SystemExit(
            f"every row has a null {args.unit}; there is nothing to assign. This is the "
            "A2 battle join gap, not a splitter problem -- populate battle_id first.")

    # dict.fromkeys dedupes while preserving order: --unit event would otherwise
    # select the same column twice and pandas would silently drop one.
    columns = [c for c in dict.fromkeys((args.unit, "event", "year", "session"))
               if c in assignable.columns]
    rows = assignable.loc[:, columns].to_dict("records")
    assignments = make_split(rows, unit=args.unit, design=args.design, seed=args.seed)
    manifest = dict(build_split_manifest(
        assignments, args.unit, args.design, args.seed, len(rows)))
    manifest.update({
        "created_utc": report["created_utc"],
        "git_commit": report["git_commit"],
        "source_datasets": sources,
        "rows_total": total,
        "rows_assignable": report["rows_assignable"],
        "rows_without_unit": unassignable,
        "unit_coverage": report["unit_coverage"],
        "battle_join_status": status_counts,
    })
    report["status"] = "OK"
    report["assignment_version"] = manifest["assignment_version"]
    report["holdout_groups"] = sum(
        1 for a in assignments if a["split_role"] == "HOLDOUT")
    report["evaluation_folds"] = sorted({
        a["fold_id"] for a in assignments if a["split_role"] == "EVALUATION"})

    if args.dry_run:
        report["dry_run"] = True
        print(json.dumps(report, indent=2))
        return 0

    args.output_root.mkdir(parents=True, exist_ok=True)
    frame_out = pd.DataFrame(assignments)
    frame_out.to_parquet(args.output_root / "split_assignments.parquet", index=False)
    (args.output_root / "split_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    report["written"] = [
        Path(_display((args.output_root / name).resolve())).as_posix()
        for name in ("split_assignments.parquet", "split_manifest.json")
    ]
    print(json.dumps(report, indent=2))
    if coverage < 1.0:
        print(f"\nWARNING: {unassignable} of {total} rows have no {args.unit} and are "
              "excluded from every split. CP-14 will refuse to train on them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
