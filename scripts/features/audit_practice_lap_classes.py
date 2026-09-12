#!/usr/bin/env python3
"""Audit the M01 Practice-lap overlay against its CP-07 acceptance gates.

The manifest written by ``build_practice_lap_classes.py`` reports class shares
and whether the gates pass. That is not enough to judge whether the gates pass
*honestly*, which is the question CP-07 actually asks. This script answers the
harder one, from the same inputs:

**Why is a lap UNKNOWN?** ``no_rule_matched`` is one reason string covering
several distinct causes -- no running best yet, a lap time in the 108-115% dead
band, a missing tyre life. Counting them separately says whether UNKNOWN is a
residue of genuine ambiguity or a bucket hiding a fixable gap.

**Why did a candidate run fail?** A run can die from length, from lap-time
spread, from non-increasing tyre life, or from a structural reset at a pit,
invalid or non-green lap. Those have different implications: the first is the
causal ceiling, the last is the boundary rule working as intended.

**Is RACE_PACE part of sustained running, or beside it?** This is the decisive
one. The CP-07 gate was revised to apply the 20-50% band to LONG_RUN plus
RACE_PACE on the argument that causal labelling cannot label the first three
laps of a run. That argument holds only if RACE_PACE laps are predominantly
*inside* runs that reached four laps. If they are isolated laps, the union
inflates the numerator and the revision moved the goalposts. The audit
reconstructs each candidate run and reports the split, so the claim is
measured rather than asserted.

Generated output is written under ``artifacts/validation/`` and is gitignored.

Usage:
    python scripts/features/audit_practice_lap_classes.py
    python scripts/features/audit_practice_lap_classes.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.track.lap_classifier import (  # noqa: E402
    KEY_COLUMNS,
    LAP_CLASSES,
    load_lap_classification_config,
)

LAKE = ROOT / "data" / "processed" / "telemetry_20m"
OVERLAY = ROOT / "data" / "processed" / "practice_lap_classes"
OUT = ROOT / "artifacts" / "validation"

METADATA_COLUMNS = [
    *KEY_COLUMNS, "lap_time_s", "tyre_life_laps", "pit_in_session_s",
    "pit_out_session_s", "is_accurate", "lap_deleted", "track_status",
]


def _safe(value: str) -> str:
    return value.replace(" ", "_")


def _truthy(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _positive(value) -> bool:
    try:
        return value is not None and not pd.isna(value) and float(value) > 0
    except (TypeError, ValueError):
        return False


def load_joined(year: int, session: str) -> pd.DataFrame:
    """Lap metadata joined to its emitted class, one row per lap."""
    frames = []
    pattern = f"year={year}/event=*/session={_safe(session)}/telemetry_20m.parquet"
    for source in sorted(LAKE.glob(pattern)):
        meta = pd.read_parquet(source, columns=METADATA_COLUMNS)
        meta = meta.drop_duplicates(list(KEY_COLUMNS), keep="first")
        event = str(meta["event"].iloc[0])
        overlay_path = (OVERLAY / f"year={year}" / f"event={_safe(event)}"
                        / f"session={_safe(session)}" / "practice_lap_classes.parquet")
        if not overlay_path.exists():
            raise SystemExit(f"no overlay for {event}; run build_practice_lap_classes.py first")
        overlay = pd.read_parquet(overlay_path)
        frames.append(meta.merge(overlay, on=list(KEY_COLUMNS), how="left"))
    if not frames:
        raise SystemExit(f"no {year} {session} partitions under {LAKE}")
    return pd.concat(frames, ignore_index=True)


def unknown_reasons(frame: pd.DataFrame, cfg) -> Counter:
    """Split ``no_rule_matched`` into the distinct causes behind it."""
    reasons: Counter = Counter()
    unknown = frame[frame["practice_lap_class"] == "UNKNOWN"]
    for row in unknown.itertuples():
        best = getattr(row, "driver_session_best_lap_time_s", None)
        lap_time = getattr(row, "lap_time_s", None)
        life = getattr(row, "tyre_life_laps", None)
        if not _positive(lap_time):
            reasons["NO_LAP_TIME"] += 1
        elif best is None or pd.isna(best):
            reasons["NO_RUNNING_BEST_YET"] += 1
        elif not _positive(life):
            reasons["NO_TYRE_LIFE"] += 1
        else:
            ratio = float(lap_time) / float(best)
            if ratio <= cfg.push_session_best_ratio_max:
                reasons["IN_BAND_BUT_UNLABELLED"] += 1
            elif ratio <= cfg.cooldown_session_best_ratio_min_exclusive:
                reasons["DEAD_BAND_108_TO_115_PCT"] += 1
            else:
                reasons["SLOW_BUT_NOT_AFTER_PUSH"] += 1
    return reasons


def run_analysis(frame: pd.DataFrame, cfg) -> dict:
    """Reconstruct candidate runs and locate every RACE_PACE lap inside them.

    A candidate run is the same sequence the classifier builds: contiguous laps
    of one driver in one session, each preliminarily UNKNOWN, with strictly
    increasing tyre life and a lap-time spread under the configured maximum.
    A run that reaches ``long_run_min_consecutive_laps`` produces LONG_RUN from
    that lap onward; the laps before it are the ones causal labelling cannot
    reach.
    """
    structural = {"IN_LAP", "OUT_LAP", "INVALID", "INTERRUPTED"}
    rejections: Counter = Counter()
    race_pace_in_qualifying_run = 0
    race_pace_in_short_run = 0
    race_pace_isolated = 0
    run_lengths: Counter = Counter()
    head_laps_unlabellable = 0

    ordered = frame.sort_values([*KEY_COLUMNS])
    for _, block in ordered.groupby(["year", "event", "session", "driver"], sort=False):
        run: list[tuple[int, float, float, str]] = []

        def close(run_items):
            nonlocal race_pace_in_qualifying_run, race_pace_in_short_run
            nonlocal race_pace_isolated, head_laps_unlabellable
            if not run_items:
                return
            run_lengths[len(run_items)] += 1
            reached = len(run_items) >= cfg.long_run_min_consecutive_laps
            if reached:
                head_laps_unlabellable += min(len(run_items),
                                              cfg.long_run_min_consecutive_laps - 1)
            for _, _, _, label in run_items:
                if label != "RACE_PACE":
                    continue
                if reached:
                    race_pace_in_qualifying_run += 1
                elif len(run_items) > 1:
                    race_pace_in_short_run += 1
                else:
                    race_pace_isolated += 1

        for row in block.itertuples():
            label = getattr(row, "practice_lap_class", None)
            lap = int(getattr(row, "lap"))
            lap_time = getattr(row, "lap_time_s", None)
            life = getattr(row, "tyre_life_laps", None)

            if label in structural:
                if run:
                    rejections["STRUCTURAL_BOUNDARY"] += 1
                    close(run)
                run = []
                continue
            if not (_positive(lap_time) and _positive(life)):
                if run:
                    rejections["MISSING_TIME_OR_LIFE"] += 1
                    close(run)
                run = []
                continue

            item = (lap, float(lap_time), float(life), str(label))
            if not run:
                run = [item]
                continue
            contiguous = lap == run[-1][0] + 1
            increasing = item[2] > run[-1][2]
            if not contiguous:
                rejections["NON_CONTIGUOUS_LAP"] += 1
                close(run)
                run = [item]
                continue
            if not increasing:
                rejections["TYRE_LIFE_NOT_INCREASING"] += 1
                close(run)
                run = [item]
                continue
            proposal = [*run, item]
            times = [entry[1] for entry in proposal]
            spread = (max(times) - min(times)) / min(times)
            if spread < cfg.long_run_lap_time_spread_ratio_max_exclusive:
                run = proposal
            else:
                rejections["LAP_TIME_SPREAD_EXCEEDED"] += 1
                close(run)
                run = [item]
        close(run)
        if run and len(run) < cfg.long_run_min_consecutive_laps:
            rejections["RUN_TOO_SHORT_AT_SESSION_END"] += 1

    total_race_pace = (race_pace_in_qualifying_run + race_pace_in_short_run
                       + race_pace_isolated)
    return {
        "candidate_run_rejections": dict(rejections.most_common()),
        "run_length_histogram": {str(k): v for k, v in sorted(run_lengths.items())},
        "race_pace_placement": {
            "inside_a_run_that_reached_long_run": race_pace_in_qualifying_run,
            "inside_a_run_of_2_or_3_laps": race_pace_in_short_run,
            "isolated_single_lap": race_pace_isolated,
            "total": total_race_pace,
            "share_inside_a_qualifying_run": (
                race_pace_in_qualifying_run / total_race_pace if total_race_pace else None),
            "share_in_any_multi_lap_run": (
                (race_pace_in_qualifying_run + race_pace_in_short_run) / total_race_pace
                if total_race_pace else None),
        },
        "head_laps_unlabellable_by_causality": head_laps_unlabellable,
    }


def field_quality(frame: pd.DataFrame) -> dict:
    total = len(frame)

    def share(mask) -> float:
        return float(mask.sum()) / total if total else 0.0

    status = frame["track_status"].astype(str).str.strip()
    return {
        "laps": total,
        "is_accurate_false": share(~frame["is_accurate"].map(_truthy)),
        "lap_deleted_true": share(frame["lap_deleted"].map(_truthy)),
        "pit_in_present": share(frame["pit_in_session_s"].notna()),
        "pit_out_present": share(frame["pit_out_session_s"].notna()),
        "track_status_green": share(status == "1"),
        "track_status_non_green": share((status != "1") & status.ne("nan")),
        "track_status_missing": share(status.eq("nan")),
        "lap_time_missing": share(~frame["lap_time_s"].map(_positive)),
        "tyre_life_missing": share(~frame["tyre_life_laps"].map(_positive)),
        "any_required_field_missing": share(
            ~frame["lap_time_s"].map(_positive) | ~frame["tyre_life_laps"].map(_positive)),
    }


def session_table(frame: pd.DataFrame, cfg) -> list[dict]:
    rows = []
    for event, block in frame.groupby("event", sort=True):
        counts = block["practice_lap_class"].value_counts()
        total = len(block)
        status = block["track_status"].astype(str).str.strip()
        shares = {label: int(counts.get(label, 0)) / total for label in LAP_CLASSES}
        rows.append({
            "event": event,
            "laps": total,
            "drivers": int(block["driver"].nunique()),
            "shares": shares,
            "sustained_share": shares["LONG_RUN"] + shares["RACE_PACE"],
            "unknown_share": shares["UNKNOWN"],
            "push_share": shares["PUSH"],
            "non_green_share": float((status != "1").sum()) / total if total else 0.0,
            "missing_field_share": field_quality(block)["any_required_field_missing"],
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--session", default="Practice 1")
    parser.add_argument("--json", action="store_true", help="Machine-readable only")
    parser.add_argument("--output", type=Path, default=OUT / "cp07_audit.json")
    args = parser.parse_args()

    cfg = load_lap_classification_config()
    frame = load_joined(args.year, args.session)
    total = len(frame)
    counts = frame["practice_lap_class"].value_counts()
    shares = {label: int(counts.get(label, 0)) / total for label in LAP_CLASSES}
    sustained = shares["LONG_RUN"] + shares["RACE_PACE"]

    report = {
        "scope": {"year": args.year, "session": args.session,
                  "sessions": int(frame["event"].nunique()), "laps": total},
        "classifier_version": cfg.classifier_version,
        "season_shares": shares,
        "season_counts": {label: int(counts.get(label, 0)) for label in LAP_CLASSES},
        "gates": {
            "unknown_lt_15pct": shares["UNKNOWN"] < 0.15,
            "push_between_5pct_and_20pct": 0.05 <= shares["PUSH"] <= 0.20,
            "sustained_running_between_20pct_and_50pct": 0.20 <= sustained <= 0.50,
            "standalone_long_run_between_20pct_and_50pct": 0.20 <= shares["LONG_RUN"] <= 0.50,
        },
        "sustained_running_share": sustained,
        "unknown_reasons": dict(unknown_reasons(frame, cfg).most_common()),
        "field_quality": field_quality(frame),
        "runs": run_analysis(frame, cfg),
        "by_session": session_table(frame, cfg),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"CP-07 audit -- {args.year} {args.session}, "
          f"{report['scope']['sessions']} sessions, {total:,} laps\n")
    print(f"{'label':<14}{'count':>8}{'share':>9}")
    for label in sorted(LAP_CLASSES, key=lambda l: -shares[l]):
        print(f"{label:<14}{int(counts.get(label, 0)):>8}{shares[label]:>8.2%}")

    print("\nGates")
    for name, passed in report["gates"].items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")

    print("\nWhy a lap is UNKNOWN")
    for reason, n in report["unknown_reasons"].items():
        print(f"  {reason:<32}{n:>6}")

    print("\nWhy a candidate run ended")
    for reason, n in report["runs"]["candidate_run_rejections"].items():
        print(f"  {reason:<32}{n:>6}")

    placement = report["runs"]["race_pace_placement"]
    print("\nWhere RACE_PACE laps sit (the gate-revision test)")
    for key in ("inside_a_run_that_reached_long_run", "inside_a_run_of_2_or_3_laps",
                "isolated_single_lap", "total"):
        print(f"  {key:<40}{placement[key]:>6}")
    if placement["share_inside_a_qualifying_run"] is not None:
        print(f"  {'share inside a qualifying run':<40}"
              f"{placement['share_inside_a_qualifying_run']:>6.1%}")
        print(f"  {'share in any multi-lap run':<40}"
              f"{placement['share_in_any_multi_lap_run']:>6.1%}")
    print(f"  {'head laps causality cannot label':<40}"
          f"{report['runs']['head_laps_unlabellable_by_causality']:>6}")

    print("\nField quality (share of laps)")
    for key, value in report["field_quality"].items():
        if key == "laps":
            continue
        print(f"  {key:<32}{value:>8.2%}")

    print(f"\nwritten: {args.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
