#!/usr/bin/env python3
"""Verify a built 20 m lake against the CP-04 acceptance gates.

Two of the gates as originally written measure the *scope you happened to build*
rather than the quality of the data, and fail on a correct lake:

**"Acceptance rate >95%"** - rejection is structural. Lap 1 starts from a grid
slot and pit in/out laps traverse a different path, so their distance channel is
legitimately non-monotonic and the validator rejects them by design. Measured on
2026 British GP Race with HAM and ANT: 104 laps, 97 accepted (93.27%), and all 7
rejections were lap 1 or a pit lap. The number to watch is not the rate but
*which codes* appear: NON_MONOTONIC_DISTANCE is expected, MISSING_TEL_OBJECT
would mean broken files.

**"gap_ahead_m non-null >95% in Race"** - the race leader has no car ahead, so
those rows are correctly null. On the same build the rate was 90.7%, which looks
like a failure until you notice every null belonged to ANT while running P1, and
that non-leader rows were **100.00%** non-null. With two drivers one of them
leads a large share of the race; with a full 22-car field only about 1/22 of rows
are leader rows and the same data would score ~95.5%. The gate was measuring
field size.

So this checks the exact property instead: gap is present whenever a car is
ahead, and absent only when the driver leads.

Usage:
    python scripts/data/verify_lake.py
    python scripts/data/verify_lake.py --lake data/processed/telemetry_20m --json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

#: Codes that mean the source files are broken rather than the lap being
#: structurally unusable. Any of these appearing is a real problem.
BROKEN_FILE_CODES = {"MISSING_TEL_OBJECT", "MALFORMED_JSON", "MISSING_REQUIRED_FIELD", "ARRAY_LENGTH_MISMATCH"}

#: Rejections that are expected and correct (AGENTS.md section 7).
STRUCTURAL_CODES = {"NON_MONOTONIC_DISTANCE", "INSUFFICIENT_VALID_SAMPLES", "NONFINITE_DISTANCE", "NONFINITE_TIME"}

RACE_LIKE = {"Race", "Sprint"}


def _gate(results: list[dict], name: str, ok: bool, detail: str) -> None:
    results.append({"gate": name, "ok": bool(ok), "detail": detail})


def verify(lake: Path) -> dict:
    import pandas as pd

    results: list[dict] = []
    parquets = sorted(lake.rglob("*.parquet"))
    if not parquets:
        raise SystemExit(f"no Parquet found under {lake}. Build it first: scripts/data/build_lake.py")

    manifest_path = lake / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    # --- manifest present and versioned
    _gate(results, "run_manifest.json present with schema_version",
          manifest.get("schema_version") == "phase2_20m_v1",
          f"schema_version={manifest.get('schema_version')}, sessions={manifest.get('sessions_requested')}")

    _gate(results, "no session failed to build",
          manifest.get("sessions_failed", 0) == 0,
          f"{manifest.get('sessions_failed', 0)} failed of {manifest.get('sessions_requested', 0)}")

    # --- rejection codes: the meaningful check, not the raw rate
    rejected_path = lake / "rejected_laps.csv"
    codes = Counter()
    if rejected_path.exists():
        with rejected_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                codes[row.get("rejection_code") or "UNKNOWN"] += 1
    broken = {c: n for c, n in codes.items() if c in BROKEN_FILE_CODES}
    _gate(results, "no broken-file rejection codes", not broken,
          f"{broken}" if broken else f"only structural codes: {dict(codes) or 'none'}")

    unknown_codes = {c: n for c, n in codes.items() if c not in BROKEN_FILE_CODES | STRUCTURAL_CODES}
    _gate(results, "all rejection codes are recognised", not unknown_codes,
          f"{unknown_codes}" if unknown_codes else "no unexpected codes")

    accepted = manifest.get("accepted_laps", 0)
    discovered = manifest.get("discovered_laps", 0)
    rate = round(100.0 * accepted / discovered, 2) if discovered else 0.0

    # --- per-file checks
    frames = []
    for path in parquets:
        frames.append(pd.read_parquet(path))
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    # rows per lap should match lap length / spacing
    spacing = float(df["resample_spacing_m"].iloc[0]) if "resample_spacing_m" in df else 20.0
    per_lap = df.groupby(["year", "event", "session", "driver", "lap"]).size()
    lap_span = df.groupby(["year", "event", "session", "driver", "lap"])["distance_m"].max()
    expected = (lap_span / spacing).round()
    delta = (per_lap - expected).abs()
    within = float((delta <= 2).mean() * 100)
    _gate(results, "rows per lap match lap length / spacing (+/- 2)", within >= 99.0,
          f"{within:.2f}% of laps within tolerance; median {int(per_lap.median())} rows/lap at {spacing:g} m")

    # gap_ahead_m: the exact property, not a field-size-dependent rate
    race = df[df["session"].isin(RACE_LIKE)]
    if len(race) and "race_position" in race.columns and race["race_position"].notna().any():
        non_leader = race[race["race_position"] != 1]
        leader = race[race["race_position"] == 1]
        missing = int(non_leader["gap_ahead_m"].isna().sum())
        _gate(results, "gap_ahead_m present for every non-leader row", missing == 0,
              f"{missing} missing of {len(non_leader)} non-leader rows; "
              f"{int(leader['gap_ahead_m'].isna().sum())} of {len(leader)} leader rows correctly null")
    else:
        _gate(results, "gap_ahead_m present for every non-leader row", True,
              "no race-like rows with position data in this scope")

    # metadata must not be silently null -- the regression fixed in CP-02
    metadata_cols = ["tyre_compound", "tyre_life_laps", "stint", "race_position", "track_status", "team", "lap_time_s"]
    present = [c for c in metadata_cols if c in df.columns]
    all_null = [c for c in present if df[c].isna().all()]
    _gate(results, "lap metadata is joined, not silently null", not all_null,
          f"all-null: {all_null}" if all_null else f"{len(present)} metadata columns populated")

    # every written column is registered
    try:
        from trackshift.data.registry import feature_names
        unregistered = sorted(set(map(str, df.columns)) - feature_names())
        _gate(results, "every column is in the feature registry", not unregistered,
              f"unregistered: {unregistered}" if unregistered else f"{len(df.columns)} columns all registered")
    except Exception as exc:  # noqa: BLE001
        _gate(results, "every column is in the feature registry", False, f"registry check failed: {exc}")

    # distance grid must sit on exact multiples of the spacing
    residual = (df["distance_m"] % spacing).abs()
    on_grid = float(((residual < 1e-6) | ((spacing - residual) < 1e-6)).mean() * 100)
    _gate(results, "distance_m lies on the resample grid", on_grid > 99.99,
          f"{on_grid:.4f}% of rows on exact multiples of {spacing:g} m")

    passed = sum(1 for r in results if r["ok"])
    return {
        "lake": str(lake),
        "parquet_files": len(parquets),
        "rows": int(len(df)),
        "sessions": manifest.get("sessions_requested"),
        "discovered_laps": discovered,
        "accepted_laps": accepted,
        "acceptance_rate_pct": rate,
        "acceptance_note": (
            "Structural rejections (grid-start lap 1, pit in/out laps, qualifying "
            "out/in laps) mean a correct lake does not reach 95%. Judge the codes, "
            "not the rate."
        ),
        "rejection_codes": dict(codes),
        "gates": results,
        "gates_passed": passed,
        "gates_total": len(results),
        "all_passed": passed == len(results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lake", type=Path, default=ROOT / "data" / "processed" / "telemetry_20m")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = verify(args.lake)
    (args.lake / "verify_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"lake      : {report['lake']}")
        print(f"parquet   : {report['parquet_files']} file(s), {report['rows']:,} rows")
        print(f"laps      : {report['accepted_laps']:,} accepted of {report['discovered_laps']:,} "
              f"({report['acceptance_rate_pct']}%)")
        if report["rejection_codes"]:
            print(f"rejections: {report['rejection_codes']}")
        print()
        for gate in report["gates"]:
            print(f"  [{'PASS' if gate['ok'] else 'FAIL'}] {gate['gate']}")
            print(f"         {gate['detail']}")
        print()
        print(f"{report['gates_passed']}/{report['gates_total']} gates passed")
        if not report["all_passed"]:
            print("\nSee CHECKPOINTS_TANVEER.md CP-04 'If output is bad'.")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
