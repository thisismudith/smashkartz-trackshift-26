#!/usr/bin/env python3
"""Derive candidate Overtake zones from historical DRS activity (CP-03 step 4).

**Tier C. Development only.** Every output is tagged `PROXY_HISTORICAL_DRS` and
must never reach a demo claim.

The 2026 Detection and Activation Line positions are not in the raw mirror: race
control announces *whether* Overtake is available, never *where*, and the FIA
event notes that carry the geometry are not yet sourced. Meanwhile CP-10's state
machine and CP-13's opportunity dataset need plausible zone geometry to be built
against at all.

So this reads where DRS was actually open at the same circuit in 2022-2025 and
emits those distance ranges as candidate zones. The reasoning is that DRS zones
are placed on the same overtaking straights an Overtake zone would use, which
makes them a reasonable *shape* to develop against.

What this is not: evidence about 2026. Historical DRS is not 2026 Overtake
(AGENTS.md section 41), the mechanisms differ, and the FIA may site the 2026
lines differently. Replace these with RULE_FIA values the moment the event notes
are found; do not let a demo rest on them (sections 57, 58).

Also note the 2026 `drs` channel is uniformly zero across the mirror, so this
can only ever read historical seasons.

Usage:
    python scripts/data/derive_drs_zones.py --event "British Grand Prix"
    python scripts/data/derive_drs_zones.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.progress import Progress  # noqa: E402

HISTORIC_YEARS = ("2022", "2023", "2024", "2025")

#: Event names are not stable across seasons and do not identify a circuit.
#: In 2026 the Barcelona circuit is run as the "Barcelona Grand Prix" while the
#: "Spanish Grand Prix" name moves to a different, new circuit. Matching history
#: by name alone therefore fails in both directions: Barcelona finds no history,
#: and the Spanish GP inherits Barcelona's zones, which is worse than finding
#: none. So history is accepted only when the circuit geometry matches.
EVENT_ALIASES = {
    "Barcelona Grand Prix": ["Spanish Grand Prix"],
}

#: Two events are the same circuit when their measured lap lengths agree to
#: within this. Real resurfacing and layout tweaks move it well under 5%;
#: a different circuit moves it far more.
CIRCUIT_MATCH_TOLERANCE_PCT = 5.0

#: A candidate zone must be open on at least this share of sampled laps.
#:
#: Calibrated against Silverstone, not guessed. The DRS channel is sparse and
#: gets sparser: the share of samples with DRS open falls from 5.2% in 2022 to
#: 1.0% in 2025, so a 25% threshold found both real zones in 2022 and nothing at
#: all in 2023-2025. At 8% all four seasons independently recover the same two
#: zones, agreeing to within 40 m:
#:
#:     2022  1280-1840  4120-4860
#:     2023  1300-1840  4140-4860
#:     2024  1280-1820  4140-4860
#:     2025  1320-1800  4160-4820
#:
#: Four seasons converging on the same ranges is the evidence that these are
#: circuit features rather than one race's traffic.
MIN_LAP_SHARE = 0.08
#: And be at least this long. Short fragments are sampling artefacts, not zones.
MIN_ZONE_LENGTH_M = 300.0
#: Distance resolution of the occupancy histogram.
BIN_M = 20.0


def slug(value: str) -> str:
    return "_".join(str(value).strip().lower().split())


def _numeric(value):
    if value is None or isinstance(value, bool) or value == "None":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def drs_occupancy(session_dir: Path, max_laps: int = 400) -> tuple[dict[int, int], int, float]:
    """Count, per distance bin, how many laps had DRS open there.

    Returns (bin -> lap count, laps sampled, max distance seen).
    """
    counts: dict[int, int] = {}
    laps = 0
    max_distance = 0.0
    for driver in sorted(p for p in session_dir.iterdir() if p.is_dir()):
        for tel_path in sorted(driver.glob("*_tel.json")):
            if laps >= max_laps:
                return counts, laps, max_distance
            try:
                payload = json.loads(tel_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            tel = payload.get("tel", payload)
            distance = tel.get("distance") or []
            drs = tel.get("drs") or []
            if len(distance) != len(drs) or len(distance) < 50:
                continue
            laps += 1
            seen: set[int] = set()
            for raw_d, raw_flag in zip(distance, drs):
                d = _numeric(raw_d)
                flag = _numeric(raw_flag)
                if d is None or flag is None:
                    continue
                max_distance = max(max_distance, d)
                if flag > 0:
                    seen.add(int(d // BIN_M))
            for b in seen:
                counts[b] = counts.get(b, 0) + 1
    return counts, laps, max_distance


def zones_from_occupancy(counts: dict[int, int], laps: int) -> list[dict]:
    """Turn the histogram into contiguous ranges meeting both thresholds."""
    if not laps or not counts:
        return []
    threshold = MIN_LAP_SHARE * laps
    active = sorted(b for b, n in counts.items() if n >= threshold)
    if not active:
        return []

    zones: list[dict] = []
    start = previous = active[0]
    for b in active[1:]:
        if b == previous + 1:
            previous = b
            continue
        zones.append((start, previous))
        start = previous = b
    zones.append((start, previous))

    out = []
    for first, last in zones:
        start_m = first * BIN_M
        end_m = (last + 1) * BIN_M
        if end_m - start_m < MIN_ZONE_LENGTH_M:
            continue
        share = max(counts[b] for b in range(first, last + 1)) / laps
        out.append({
            "start_m": round(start_m, 1),
            "end_m": round(end_m, 1),
            "length_m": round(end_m - start_m, 1),
            "peak_lap_share": round(share, 3),
        })
    return out


def median_lap_length(session_dir: Path, sample: int = 12) -> float | None:
    """Circuit fingerprint: the median lap distance over a few laps.

    Cheap and sufficient. Two recordings of the same circuit agree closely;
    two different circuits do not.
    """
    lengths: list[float] = []
    for driver in sorted(p for p in session_dir.iterdir() if p.is_dir())[:4]:
        for tel_path in sorted(driver.glob("*_tel.json"))[2:5]:
            try:
                payload = json.loads(tel_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            tel = payload.get("tel", payload)
            values = [v for v in (tel.get("distance") or []) if isinstance(v, (int, float))]
            if values and values[-1] > 1000:
                lengths.append(float(values[-1]))
            if len(lengths) >= sample:
                break
    if not lengths:
        return None
    lengths.sort()
    return lengths[len(lengths) // 2]


def _reference_length(raw_root: Path, event: str) -> tuple[float | None, str | None]:
    """Length of the 2026 event, from any session that has telemetry."""
    for session in ("Race", "Sprint", "Qualifying", "Practice 1"):
        session_dir = raw_root / "2026" / event / session
        if session_dir.is_dir():
            length = median_lap_length(session_dir)
            if length:
                return length, session
    return None, None


def derive_event(raw_root: Path, event: str) -> dict:
    per_year: dict[str, dict] = {}
    rejected: list[dict] = []
    reference, reference_session = _reference_length(raw_root, event)
    candidates = [event] + EVENT_ALIASES.get(event, [])

    for year in HISTORIC_YEARS:
        session_dir = None
        matched_name = None
        for candidate in candidates:
            trial = raw_root / year / candidate / "Race"
            if not trial.is_dir():
                continue
            historical = median_lap_length(trial)
            if reference and historical:
                delta = abs(historical - reference) / reference * 100.0
                if delta > CIRCUIT_MATCH_TOLERANCE_PCT:
                    rejected.append({
                        "year": year, "candidate": candidate,
                        "historical_lap_length_m": round(historical, 1),
                        "reference_lap_length_m": round(reference, 1),
                        "delta_pct": round(delta, 1),
                        "reason": "different circuit under the same event name",
                    })
                    continue
            session_dir = trial
            matched_name = candidate
            break
        if session_dir is None:
            continue
        counts, laps, max_distance = drs_occupancy(session_dir)
        zones = zones_from_occupancy(counts, laps)
        per_year[year] = {
            "matched_event_name": matched_name,
            "laps_sampled": laps,
            "max_distance_m": round(max_distance, 1),
            "zones": zones,
        }

    # Keep zones that appear in more than one season: a single season could
    # reflect one race's conditions rather than the circuit's layout.
    tally: dict[tuple, list[str]] = {}
    for year, data in per_year.items():
        for zone in data["zones"]:
            key = (round(zone["start_m"] / 100), round(zone["end_m"] / 100))
            tally.setdefault(key, []).append(year)

    consensus = []
    for (start_100, end_100), years in sorted(tally.items()):
        if len(years) < 2:
            continue
        consensus.append({
            "start_m": start_100 * 100.0,
            "end_m": end_100 * 100.0,
            "length_m": (end_100 - start_100) * 100.0,
            "seen_in_years": sorted(years),
            "value_source": "PROXY_HISTORICAL_DRS",
        })

    return {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "event": slug(event),
        "event_display": event,
        "value_source": "PROXY_HISTORICAL_DRS",
        "claimable": False,
        "note": (
            "Tier C. Candidate zones inferred from where DRS was open at this circuit in "
            "2022-2025 Races. DRS zones sit on the same overtaking straights an Overtake "
            "zone would use, so the shape is a reasonable development target, but this is "
            "not evidence about 2026: historical DRS is not 2026 Overtake (section 41). "
            "Replace with RULE_FIA values once the FIA event notes are sourced, and never "
            "let a demo claim rest on these (sections 57, 58)."
        ),
        "thresholds": {
            "min_lap_share": MIN_LAP_SHARE,
            "min_zone_length_m": MIN_ZONE_LENGTH_M,
            "bin_m": BIN_M,
            "consensus_rule": "a zone must appear in at least two seasons",
        },
        "reference_lap_length_m": round(reference, 1) if reference else None,
        "reference_session": reference_session,
        "event_aliases_tried": EVENT_ALIASES.get(event, []),
        "rejected_history": rejected,
        "per_year": per_year,
        "consensus_zones": consensus,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "data" / "raw" / "tracinginsights")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "drs_zones")
    parser.add_argument("--event", default=None, help="Exact event name")
    parser.add_argument("--all", action="store_true", help="Every event present in 2026")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    if not args.event and not args.all:
        parser.error("give --event NAME or --all")

    if args.all:
        year_2026 = args.raw_root / "2026"
        events = sorted(p.name for p in year_2026.iterdir()
                        if p.is_dir() and not p.name.startswith(".") and "Testing" not in p.name)
    else:
        events = [args.event]

    args.output.mkdir(parents=True, exist_ok=True)
    prog = Progress(len(events), enabled=not args.no_progress)
    summary = []
    for event in events:
        prog.set_label(event)
        doc = derive_event(args.raw_root, event)
        (args.output / f"{slug(event)}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
        summary.append({
            "event": event,
            "seasons_with_data": len(doc["per_year"]),
            "consensus_zones": len(doc["consensus_zones"]),
        })
        prog.tick()
    prog.close()

    print(json.dumps({
        "events": len(summary),
        "output": str(args.output),
        "value_source": "PROXY_HISTORICAL_DRS (Tier C, never claimable)",
        "summary": summary,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
