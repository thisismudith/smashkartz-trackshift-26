#!/usr/bin/env python3
"""Extract race-control windows from rcm.json (CP-03 step 3, Tier B).

This is the one part of the 2026 rule configuration that needs no FIA document.
Race control announces Overtake enablement, Safety Cars, VSCs and flags over the
message feed, and those messages are in the raw mirror. So the resulting windows
are genuinely `OBSERVED_RCM` (Tier B) rather than `UNVERIFIED` guesses.

What it cannot tell you: where the Detection and Activation Lines are. Race
control says *whether* Overtake is available, never *where*. Line positions stay
Tier A (an FIA event note) or Tier C (the historical DRS proxy in
derive_drs_zones.py). Do not conflate the two.

Produces `artifacts/race_control/<year>_<event>.json` with, per session:

  overtake_windows   enabled/disabled intervals in session time
  safety_car         SC deployment and ending intervals
  virtual_safety_car VSC intervals
  flags              yellow, double yellow, red and clear, with sector scope
  messages           every parsed message, for audit

Usage:
    python scripts/data/extract_race_control.py --years 2026
    python scripts/data/extract_race_control.py --years 2026 --events "British Grand Prix"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.progress import Progress  # noqa: E402

YEARS = ("2022", "2023", "2024", "2025", "2026")

#: Message text that toggles Overtake. 2026 wording; the DRS equivalents are
#: kept so the same extractor works on historical seasons for comparison.
OVERTAKE_ENABLE = ("OVERTAKE ENABLED",)
OVERTAKE_DISABLE = ("OVERTAKE DISABLED",)
DRS_ENABLE = ("DRS ENABLED",)
DRS_DISABLE = ("DRS DISABLED",)

SC_DEPLOYED = "SAFETY CAR DEPLOYED"
SC_ENDING = ("SAFETY CAR IN THIS LAP", "SAFETY CAR ENDING")
# Race control writes "VSC DEPLOYED", not the expanded form. Matching only the
# long spelling missed all 31 deployments in the 2026 season while still
# matching the 26 endings, which would have left VSC periods invisible to the
# normal_race_model_eligible gate (section 12).
VSC_DEPLOYED = ("VSC DEPLOYED", "VIRTUAL SAFETY CAR DEPLOYED")
VSC_ENDING = ("VSC ENDING", "VIRTUAL SAFETY CAR ENDING")
#: Messages that mention VSC or the Safety Car without being a state change,
#: e.g. "INCIDENT ... - VSC INFRINGEMENT". Exact-phrase matching already avoids
#: these, but they are listed so the distinction stays deliberate.
NOT_A_STATE_CHANGE = ("INFRINGEMENT", "INVESTIGAT", "PENALTY", "NO FURTHER", "LAPPED CARS")


def slug(value: str) -> str:
    """Lowercase, underscore-joined event key, matching the rule-file naming."""
    return "_".join(str(value).strip().lower().split())


def records(payload, key: str) -> list[dict]:
    """Normalise the source's list / keyed / columnar shapes to records.

    rcm.json is columnar in this mirror, so a naive list check finds nothing --
    the same trap that made every lap-metadata column null.
    """
    if isinstance(payload, dict) and key in payload:
        payload = payload[key]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        columns = {k: v for k, v in payload.items() if isinstance(v, list)}
        if not columns:
            return []
        scalars = {k: v for k, v in payload.items() if not isinstance(v, list)}
        n = max(len(v) for v in columns.values())
        return [{**scalars, **{k: (v[i] if i < len(v) else None) for k, v in columns.items()}} for i in range(n)]
    return []


def _clean(value):
    return None if value in (None, "None", "", "nan") else value


def _time(row: dict):
    """Session time if numeric, else the raw timestamp string.

    2026 rcm rows carry an ISO-ish timestamp rather than a session offset, so
    both are preserved and the caller decides.
    """
    raw = _clean(row.get("time"))
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return raw


def _windows(events: list[tuple], opened_label: str, closed_label: str) -> list[dict]:
    """Pair open/close events into intervals.

    An unmatched open stays open with ``end: null`` rather than being dropped --
    a session that ends under a Safety Car is a real case, and silently
    discarding it would understate how much of the race was neutralised.
    """
    out: list[dict] = []
    current = None
    for when, kind, text in events:
        if kind == opened_label and current is None:
            current = {"start": when, "start_message": text, "end": None, "end_message": None}
        elif kind == closed_label and current is not None:
            current["end"] = when
            current["end_message"] = text
            out.append(current)
            current = None
    if current is not None:
        current["note"] = "still open at the end of the message feed"
        out.append(current)
    return out


def parse_session(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "messages": []}

    rows = records(payload, "rcm")
    overtake_events: list[tuple] = []
    sc_events: list[tuple] = []
    vsc_events: list[tuple] = []
    flags: list[dict] = []
    parsed: list[dict] = []
    counts: Counter = Counter()

    for row in rows:
        text = str(_clean(row.get("msg")) or "").strip()
        upper = text.upper()
        when = _time(row)
        entry = {
            "time": when,
            "category": _clean(row.get("cat")),
            "flag": _clean(row.get("flag")),
            "scope": _clean(row.get("scope")),
            "sector": _clean(row.get("sector")),
            "status": _clean(row.get("status")),
            "lap": _clean(row.get("lap")),
            "message": text,
        }
        parsed.append(entry)

        if any(k in upper for k in OVERTAKE_ENABLE):
            overtake_events.append((when, "enabled", text)); counts["overtake_enabled"] += 1
        elif any(k in upper for k in OVERTAKE_DISABLE):
            overtake_events.append((when, "disabled", text)); counts["overtake_disabled"] += 1
        if any(k in upper for k in DRS_ENABLE):
            counts["drs_enabled"] += 1
        elif any(k in upper for k in DRS_DISABLE):
            counts["drs_disabled"] += 1

        # VSC first: "VIRTUAL SAFETY CAR" contains "SAFETY CAR".
        if any(k in upper for k in VSC_DEPLOYED):
            vsc_events.append((when, "deployed", text)); counts["vsc_deployed"] += 1
        elif any(k in upper for k in VSC_ENDING):
            vsc_events.append((when, "ending", text)); counts["vsc_ending"] += 1
        elif SC_DEPLOYED in upper:
            sc_events.append((when, "deployed", text)); counts["sc_deployed"] += 1
        elif any(k in upper for k in SC_ENDING):
            sc_events.append((when, "ending", text)); counts["sc_ending"] += 1

        if entry["flag"]:
            flags.append({"time": when, "flag": entry["flag"], "scope": entry["scope"],
                          "sector": entry["sector"], "message": text})
            counts[f"flag_{str(entry['flag']).lower().replace(' ', '_')}"] += 1

    return {
        "messages_total": len(rows),
        "counts": dict(sorted(counts.items())),
        "overtake_windows": _windows(overtake_events, "enabled", "disabled"),
        "safety_car": _windows(sc_events, "deployed", "ending"),
        "virtual_safety_car": _windows(vsc_events, "deployed", "ending"),
        "flags": flags,
        "messages": parsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "data" / "raw" / "tracinginsights")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "race_control")
    parser.add_argument("--years", default="2026", help="CSV of seasons")
    parser.add_argument("--events", default=None, help="CSV of exact event names")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    years = tuple(y.strip() for y in args.years.split(",") if y.strip())
    unknown = [y for y in years if y not in YEARS]
    if unknown:
        parser.error(f"unsupported year(s): {','.join(unknown)}")
    wanted = {e.strip().lower() for e in args.events.split(",")} if args.events else None

    targets: list[tuple[str, str, Path]] = []
    for year in years:
        year_dir = args.raw_root / year
        if not year_dir.is_dir():
            continue
        for event_dir in sorted(p for p in year_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if wanted is not None and event_dir.name.lower() not in wanted:
                continue
            targets.append((year, event_dir.name, event_dir))

    if not targets:
        print(json.dumps({"events": 0, "note": "nothing matched"}, indent=2))
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    prog = Progress(len(targets), enabled=not args.no_progress)
    totals: Counter = Counter()
    written: list[str] = []

    for year, event, event_dir in targets:
        prog.set_label(f"{year} {event}")
        sessions: dict[str, dict] = {}
        for session_dir in sorted(p for p in event_dir.iterdir() if p.is_dir()):
            rcm = session_dir / "rcm.json"
            if rcm.exists():
                sessions[session_dir.name] = parse_session(rcm)
        for data in sessions.values():
            totals.update(data.get("counts", {}))

        doc = {
            "schema_version": 1,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "value_source": "OBSERVED_RCM",
            "source": f"race-control messages in {event_dir.relative_to(ROOT) if str(event_dir).startswith(str(ROOT)) else event_dir}",
            "note": (
                "Tier B. Race control states WHETHER Overtake is available, never WHERE. "
                "Detection and Activation Line positions are not derivable from these "
                "messages and remain Tier A (FIA event note) or Tier C (DRS proxy)."
            ),
            "year": year,
            "event": slug(event),
            "event_display": event,
            "sessions": sessions,
        }
        path = args.output / f"{year}_{slug(event)}.json"
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        written.append(path.name)
        prog.tick()
    prog.close()

    print(json.dumps({
        "events": len(written),
        "output": str(args.output),
        "totals": dict(sorted(totals.items())),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
