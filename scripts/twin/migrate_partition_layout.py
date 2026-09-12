#!/usr/bin/env python3
"""Migrate twin outputs from circuit-only to circuit+year partitioning.

The energy twin and override state used to be written to
``circuit=<c>/<name>.parquet``. Keyed on circuit alone, a second season
overwrote the first: a ``--year 2024`` control run destroyed the 2026 twin for
every circuit that had 2024 data, and did it silently, because every reader
joins on ``year`` and so merely saw no rows where a season used to be.

They are now written to ``circuit=<c>/year=<y>/<name>.parquet``. This moves what
is already on disk into that layout, splitting a mixed file by its ``year``
column. Nothing is deleted until its rows have been written out and counted, so
the 2024 control season survives the move.

Idempotent: run it twice and the second run finds nothing to do.

Usage:
    python scripts/twin/migrate_partition_layout.py --dry-run
    python scripts/twin/migrate_partition_layout.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TARGETS = (
    (ROOT / "data" / "processed" / "energy_twin", "energy_twin.parquet"),
    (ROOT / "data" / "processed" / "override_state", "override_state.parquet"),
)


def migrate(root: Path, filename: str, dry_run: bool) -> list[str]:
    import pandas as pd

    notes: list[str] = []
    if not root.exists():
        return [f"{root.name}: nothing on disk"]

    for legacy in sorted(root.glob(f"circuit=*/{filename}")):
        circuit = legacy.parent.name.removeprefix("circuit=")
        frame = pd.read_parquet(legacy)
        if "year" not in frame.columns:
            notes.append(f"  {circuit}: no year column, left alone")
            continue

        written = 0
        plan: list[tuple[Path, int]] = []
        for year, block in frame.groupby(frame["year"].astype(str), sort=True):
            destination = legacy.parent / f"year={year}" / filename
            plan.append((destination, len(block)))
            if dry_run:
                continue
            if destination.exists():
                existing = pd.read_parquet(destination)
                if len(existing) >= len(block):
                    # Already migrated, or the newer build is more complete.
                    written += len(block)
                    continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            block.to_parquet(destination, index=False)
            written += len(block)

        detail = ", ".join(f"{p.parent.name}={n}" for p, n in plan)
        if dry_run:
            notes.append(f"  {circuit}: would split {len(frame)} rows -> {detail}")
            continue

        # Only now is the legacy file redundant.
        if written == len(frame):
            legacy.unlink()
            notes.append(f"  {circuit}: split {len(frame)} rows -> {detail}, legacy removed")
        else:
            notes.append(
                f"  {circuit}: WROTE {written} of {len(frame)} rows; legacy KEPT at "
                f"{legacy.relative_to(ROOT)} -- inspect before removing it")
    return notes or [f"  {root.name}: already migrated"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would move without touching anything")
    args = parser.parse_args()

    for root, filename in TARGETS:
        print(f"{root.relative_to(ROOT)}:")
        for line in migrate(root, filename, args.dry_run):
            print(line)
    if args.dry_run:
        print("\nDry run. Re-run without --dry-run to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
