#!/usr/bin/env python3
"""Summarise telemetry quality from the schema audit, broken down by session type.

`audit_raw_data.py` writes one row per lap file. The single number that matters
for downstream work is the share of laps whose distance channel is monotonic,
because `validation.py` rejects the rest and they never reach the 20 m lake.

A single global percentage is misleading, though, because the failure rate is
structural and differs by session type:

* **Race / Sprint** - lap 1 starts from a grid slot rather than the timing line,
  and pit in/out laps traverse a different path length. Measured on the 2026
  British Grand Prix Race: 100% of lap-1 files and 94% of the remaining
  rejections were pit laps.
* **Qualifying** - out-laps and in-laps surround every flying lap by design, so
  the non-monotonic share is far higher and is *expected*, not a defect.

So this reports per session type and flags only what is anomalous for that type.
Do not repair raw data to make these numbers look better (AGENTS.md section 7):
the validator rejecting these laps is the system working.

Usage:
    python scripts/data/quality_report.py
    python scripts/data/quality_report.py --audit artifacts/schema_audit --json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Session types grouped by the failure mode that dominates them.
RACE_LIKE = {"Race", "Sprint"}
QUALI_LIKE = {"Qualifying", "Sprint Qualifying", "Sprint Shootout"}
PRACTICE_LIKE = {"Practice 1", "Practice 2", "Practice 3"}

#: Provisional floors. These catch catastrophic corruption, not the structural
#: out/in-lap losses above. They are deliberately loose and should be tightened
#: once a full-mirror audit has established the real distribution.
PROVISIONAL_FLOORS = {
    "race_like": 85.0,
    "quali_like": 70.0,
    "practice_like": 60.0,
    "overall": 70.0,
}


def _session_group(session: str) -> str:
    if session in RACE_LIKE:
        return "race_like"
    if session in QUALI_LIKE:
        return "quali_like"
    if session in PRACTICE_LIKE:
        return "practice_like"
    return "other"


def _is_true(value: object) -> bool:
    return str(value).strip().lower() == "true"


def load_rows(audit_dir: Path) -> list[dict]:
    path = audit_dir / "data_quality_summary.csv"
    if not path.exists():
        raise SystemExit(
            f"no audit found at {path}\n"
            "Run: python scripts/audit_raw_data.py --raw-root data/raw/tracinginsights "
            "--output artifacts/schema_audit"
        )
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def summarise(rows: list[dict]) -> dict:
    def blank() -> dict:
        return {"laps": 0, "monotonic": 0}

    by_group: dict[str, dict] = defaultdict(blank)
    by_session: dict[str, dict] = defaultdict(blank)
    by_year: dict[str, dict] = defaultdict(blank)

    for row in rows:
        session = row.get("session", "")
        ok = _is_true(row.get("distance_monotonic"))
        for bucket, key in (
            (by_group, _session_group(session)),
            (by_session, session),
            (by_year, row.get("year", "")),
        ):
            bucket[key]["laps"] += 1
            bucket[key]["monotonic"] += int(ok)

    def pct(d: dict) -> dict:
        return {
            **d,
            "rejected": d["laps"] - d["monotonic"],
            "monotonic_pct": round(100.0 * d["monotonic"] / d["laps"], 2) if d["laps"] else 0.0,
        }

    total = {"laps": len(rows), "monotonic": sum(1 for r in rows if _is_true(r.get("distance_monotonic")))}

    flags = []
    groups = {k: pct(v) for k, v in by_group.items()}
    for group, stats in groups.items():
        floor = PROVISIONAL_FLOORS.get(group)
        if floor is not None and stats["laps"] and stats["monotonic_pct"] < floor:
            flags.append(
                f"{group}: {stats['monotonic_pct']}% monotonic is below the provisional "
                f"floor of {floor}% across {stats['laps']} laps"
            )

    return {
        "overall": pct(total),
        "by_session_group": groups,
        "by_session": {k: pct(v) for k, v in sorted(by_session.items())},
        "by_year": {k: pct(v) for k, v in sorted(by_year.items())},
        "provisional_floors": PROVISIONAL_FLOORS,
        "flags": flags,
        "note": (
            "Non-monotonic distance is structural, not corruption: grid starts, pit "
            "in/out laps and qualifying out/in laps all traverse a different path. "
            "The validator rejects them by design (AGENTS.md section 7)."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audit", type=Path, default=ROOT / "artifacts" / "schema_audit")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    parser.add_argument("--fail-under", type=float, default=None,
                        help="Exit non-zero if overall monotonic %% falls below this")
    args = parser.parse_args()

    report = summarise(load_rows(args.audit))
    (args.audit / "quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        o = report["overall"]
        print(f"distance_monotonic: {o['monotonic_pct']}%  ({o['monotonic']}/{o['laps']} laps, "
              f"{o['rejected']} would be rejected)\n")
        print(f"{'session type':<22}{'laps':>8}{'monotonic':>12}{'rejected':>10}{'floor':>8}")
        for group, s in sorted(report["by_session_group"].items()):
            floor = PROVISIONAL_FLOORS.get(group)
            mark = "" if floor is None or s["monotonic_pct"] >= floor else "  <-- below floor"
            print(f"{group:<22}{s['laps']:>8}{s['monotonic_pct']:>11}%{s['rejected']:>10}"
                  f"{(str(floor) + '%') if floor else '-':>8}{mark}")
        print(f"\n{'session':<22}{'laps':>8}{'monotonic':>12}")
        for session, s in report["by_session"].items():
            print(f"{session:<22}{s['laps']:>8}{s['monotonic_pct']:>11}%")
        if report["flags"]:
            print("\nFLAGS:")
            for flag in report["flags"]:
                print(f"  - {flag}")
        print(f"\n{report['note']}")

    if args.fail_under is not None and report["overall"]["monotonic_pct"] < args.fail_under:
        print(f"\nFAIL: overall {report['overall']['monotonic_pct']}% < {args.fail_under}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
