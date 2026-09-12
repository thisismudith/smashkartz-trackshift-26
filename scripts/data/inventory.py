#!/usr/bin/env python3
"""Session-level inventory of the raw mirror (CP-01, AGENTS.md sections 51, 63).

`audit_raw_data.py` answers "what do the telemetry *fields* look like" by parsing
every lap. This answers the cheaper, different question needed for scoping work:
"what sessions exist, how complete is each, and which are usable?"

It deliberately does not open `*_tel.json`. Counting and stat-ing files is roughly
three orders of magnitude faster than parsing 177k JSON documents, so this runs in
seconds over the whole mirror while the schema audit takes ~40 minutes. Use this
to decide scope; use the schema audit to check field quality.

Writes `inventory.csv` (one row per year/event/session) plus `inventory.json`
with a summary and the per-year rollup.

Usage:
    python scripts/data/inventory.py --raw-root data/raw/tracinginsights
    python scripts/data/inventory.py --years 2026 --output artifacts/schema_audit
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

YEARS = ("2022", "2023", "2024", "2025", "2026")

#: Session files the pipeline expects alongside the per-driver directories.
SESSION_FILES = (
    "corners.json",
    "drivers.json",
    "rcm.json",
    "weather.json",
    "session_laptimes.json",
)

#: Sessions in the project core scope (AGENTS.md section 8). Practice 1 is 2026-only.
CORE_SESSIONS_HISTORIC = ("Qualifying", "Sprint Qualifying", "Sprint Shootout", "Sprint", "Race")
CORE_SESSIONS_2026 = ("Practice 1",) + CORE_SESSIONS_HISTORIC

_TEL_RE = re.compile(r"^(\d+)_tel\.json$")


def _records(payload, key: str) -> list[dict]:
    """Normalise TracingInsights JSON, which is columnar in some files and a list
    of records in others, into a list of records."""
    if isinstance(payload, dict) and key in payload:
        payload = payload[key]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        columns = {k: v for k, v in payload.items() if isinstance(v, list)}
        if columns:
            n = max(len(v) for v in columns.values())
            return [{k: (v[i] if i < len(v) else None) for k, v in columns.items()} for i in range(n)]
    return []


def _load(path: Path):
    """Read JSON, tolerating a UTF-8 BOM. Returns None on any failure.

    Never raises: a malformed session file is recorded as a finding, not a crash,
    because the point of an inventory is to report what is broken.
    """
    try:
        with path.open(encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _overtake_message_counts(session_dir: Path) -> tuple[int, int, int]:
    """Count OVERTAKE ENABLED / DISABLED / DRS messages in race control.

    The 2026 regulations replace DRS with the Overtake mechanism, so which of
    these a session carries is a direct signal of the regulation era present in
    the data, independent of the folder's year. Returns (enabled, disabled, drs).
    """
    payload = _load(session_dir / "rcm.json")
    if payload is None:
        return 0, 0, 0
    enabled = disabled = drs = 0
    for message in _records(payload, "rcm"):
        text = str(message.get("msg", "")).upper()
        if "OVERTAKE ENABLED" in text:
            enabled += 1
        elif "OVERTAKE DISABLED" in text:
            disabled += 1
        if "DRS" in text:
            drs += 1
    return enabled, disabled, drs


def _scan_session(year: str, event: str, session_dir: Path) -> dict:
    driver_dirs = sorted(p for p in session_dir.iterdir() if p.is_dir())
    present_files = {p.name for p in session_dir.iterdir() if p.is_file()}

    lap_files = 0
    total_bytes = 0
    laps_per_driver: dict[str, int] = {}
    max_lap = 0
    for driver in driver_dirs:
        count = 0
        for entry in driver.iterdir():
            if not entry.is_file():
                continue
            try:
                total_bytes += entry.stat().st_size
            except OSError:
                pass
            match = _TEL_RE.match(entry.name)
            if match:
                count += 1
                max_lap = max(max_lap, int(match.group(1)))
        laps_per_driver[driver.name] = count
        lap_files += count
    for name in present_files:
        try:
            total_bytes += (session_dir / name).stat().st_size
        except OSError:
            pass

    enabled, disabled, drs = _overtake_message_counts(session_dir)
    has_laptimes = sum(1 for d in driver_dirs if (d / "laptimes.json").exists())
    counts = sorted(laps_per_driver.values())

    return {
        "year": year,
        "event": event,
        "session": session_dir.name,
        "drivers": len(driver_dirs),
        "drivers_with_laptimes": has_laptimes,
        "lap_files": lap_files,
        "max_lap_number": max_lap,
        "min_laps_per_driver": counts[0] if counts else 0,
        "median_laps_per_driver": counts[len(counts) // 2] if counts else 0,
        "max_laps_per_driver": counts[-1] if counts else 0,
        "drivers_with_zero_laps": sum(1 for v in laps_per_driver.values() if v == 0),
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / 1_048_576, 1),
        **{f"has_{name.replace('.json', '')}": int(name in present_files) for name in SESSION_FILES},
        "missing_session_files": ";".join(sorted(set(SESSION_FILES) - present_files)),
        "overtake_enabled_msgs": enabled,
        "overtake_disabled_msgs": disabled,
        "drs_msgs": drs,
    }


def _is_complete(row: dict) -> bool:
    """A session is complete enough to build from: it has drivers, telemetry for
    all of them, and every expected session file."""
    return (
        row["drivers"] > 0
        and row["lap_files"] > 0
        and row["drivers_with_zero_laps"] == 0
        and row["missing_session_files"] == ""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "data" / "raw" / "tracinginsights",
                        help="Mirror root (default: data/raw/tracinginsights, the AGENTS.md section 7 layout)")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "schema_audit",
                        help="Directory for inventory.csv and inventory.json")
    parser.add_argument("--years", default=None, help="CSV subset, e.g. 2026 or 2025,2026")
    args = parser.parse_args()

    selected = YEARS
    if args.years:
        selected = tuple(y.strip() for y in args.years.split(",") if y.strip())
        unknown = [y for y in selected if y not in YEARS]
        if unknown:
            parser.error(f"unsupported year(s): {','.join(unknown)}; supported: {','.join(YEARS)}")

    if not args.raw_root.is_dir():
        parser.error(f"raw root does not exist: {args.raw_root}")

    rows: list[dict] = []
    missing_years: list[str] = []
    for year in selected:
        year_dir = args.raw_root / year
        if not year_dir.is_dir():
            missing_years.append(year)
            continue
        for event_dir in sorted(p for p in year_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for session_dir in sorted(p for p in event_dir.iterdir() if p.is_dir()):
                rows.append(_scan_session(year, event_dir.name, session_dir))

    for row in rows:
        row["complete"] = int(_is_complete(row))

    args.output.mkdir(parents=True, exist_ok=True)
    csv_path = args.output / "inventory.csv"
    if rows:
        fieldnames = list(rows[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    # Per-year rollup, and which core sessions each event is missing.
    by_year: dict[str, dict] = {}
    for year in selected:
        year_rows = [r for r in rows if r["year"] == year]
        core = CORE_SESSIONS_2026 if year == "2026" else CORE_SESSIONS_HISTORIC
        events = sorted({r["event"] for r in year_rows})
        complete_events = []
        incomplete_events = {}
        for event in events:
            sessions = {r["session"] for r in year_rows if r["event"] == event and r["complete"]}
            # Sprint Qualifying and Sprint Shootout are the same slot in different
            # seasons, and only sprint weekends have either, so neither is required.
            required = {s for s in core if s not in {"Sprint Qualifying", "Sprint Shootout", "Sprint"}}
            missing = sorted(required - sessions)
            if missing:
                incomplete_events[event] = missing
            else:
                complete_events.append(event)
        by_year[year] = {
            "events": len(events),
            "sessions": len(year_rows),
            "complete_sessions": sum(r["complete"] for r in year_rows),
            "lap_files": sum(r["lap_files"] for r in year_rows),
            "total_gb": round(sum(r["total_bytes"] for r in year_rows) / 1_073_741_824, 2),
            "events_with_all_required_core_sessions": len(complete_events),
            "complete_events": complete_events,
            "events_missing_core_sessions": incomplete_events,
        }

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "raw_root": str(args.raw_root),
        "years_requested": list(selected),
        "years_missing_from_mirror": missing_years,
        "partial_inventory": list(selected) != list(YEARS),
        "totals": {
            "sessions": len(rows),
            "complete_sessions": sum(r["complete"] for r in rows),
            "lap_files": sum(r["lap_files"] for r in rows),
            "total_gb": round(sum(r["total_bytes"] for r in rows) / 1_073_741_824, 2),
        },
        "by_year": by_year,
        "sessions_missing_files": [
            {"year": r["year"], "event": r["event"], "session": r["session"], "missing": r["missing_session_files"]}
            for r in rows if r["missing_session_files"]
        ],
        "sessions_with_driverless_gaps": [
            {"year": r["year"], "event": r["event"], "session": r["session"], "drivers_with_zero_laps": r["drivers_with_zero_laps"]}
            for r in rows if r["drivers_with_zero_laps"]
        ],
        "session_name_vocabulary": dict(Counter(r["session"] for r in rows).most_common()),
    }
    (args.output / "inventory.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps({
        "sessions": summary["totals"]["sessions"],
        "complete_sessions": summary["totals"]["complete_sessions"],
        "lap_files": summary["totals"]["lap_files"],
        "total_gb": summary["totals"]["total_gb"],
        "years_missing_from_mirror": missing_years,
        "csv": str(csv_path),
        "json": str(args.output / "inventory.json"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
