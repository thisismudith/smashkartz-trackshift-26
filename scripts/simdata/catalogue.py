"""Build the entity/track/weather catalogue that bounds the New Race configurator.

Every value here traces to a source file. The configurator may only offer what is in
this artifact.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "2026"
SCHEMA_VERSION = 1


def discover_events() -> list[str]:
    """Events that have a Race session, discovered from the directory listing."""
    out = []
    for d in sorted(DATA_ROOT.iterdir()):
        if not d.is_dir() or d.name in ("schemas", "cache", "cache_preseason"):
            continue
        if (d / "Race").exists():
            out.append(d.name)
    return out


def event_sessions(event: str) -> list[str]:
    d = DATA_ROOT / event
    return sorted(s.name for s in d.iterdir() if s.is_dir())


def driver_registry(events: list[str]) -> dict:
    """Union of telemetry directory names, keyed on (code, team) so a reserve driver
    who changes team AND number mid-season (measured: ARO 97 at Audi -> 61 at Alpine,
    IWA 90 at Racing Bulls -> 36 at Red Bull) gets two entries. drivers.json alone is
    incomplete in several sessions (measured: Chinese Race lists 18 drivers but has 22
    telemetry directories), so a code seen with no team attributes yet is folded into
    the driver's most recently known (code, team) entry rather than creating a
    phantom (code, None) duplicate.
    """
    entries = {}       # (code, team) -> attrs
    order = []
    last_team = {}      # code -> most recently observed team, chronologically

    for event in events:
        for session in event_sessions(event):
            sdir = DATA_ROOT / event / session
            drv_file = sdir / "drivers.json"
            attrs_by_code = {}
            if drv_file.exists():
                try:
                    data = json.loads(drv_file.read_text(encoding="utf-8"))
                    for row in data.get("drivers", []):
                        code = row.get("driver")
                        if code and row.get("team") not in (None, "None"):
                            attrs_by_code[code] = row
                except (json.JSONDecodeError, AttributeError):
                    pass
            for d in sorted(sdir.iterdir()):
                if not d.is_dir() or not (d.name.isalpha() and len(d.name) == 3):
                    continue
                code = d.name
                a = attrs_by_code.get(code)
                team = a.get("team") if a else last_team.get(code)
                colour = a.get("tc") if a else None
                number = a.get("dn") if a else None
                if team not in (None, "None"):
                    last_team[code] = team
                key = (code, team)
                first = a.get("fn") if a else None
                last = a.get("ln") if a else None
                if key not in entries:
                    entries[key] = {"code": code, "team": team, "colour": colour,
                                     "number": number, "firstName": first,
                                     "lastName": last, "sessions": 0}
                    order.append(key)
                entries[key]["sessions"] += 1
                for field, value in (("colour", colour), ("number", number),
                                      ("firstName", first), ("lastName", last)):
                    if entries[key][field] in (None, "None") and value not in (None, "None"):
                        entries[key][field] = value
    # Fold any (code, None) entry into the driver's real (code, team) entry: it only
    # exists because that code's team was unresolved on its first sighting (e.g. a
    # Practice-1 session whose drivers.json row lacked a team), and the real team is
    # known from a later session.
    by_code_real = {}
    for code, team in order:
        if team not in (None, "None"):
            by_code_real.setdefault(code, (code, team))

    merged = {}
    merged_order = []
    for key in order:
        code, team = key
        target = by_code_real.get(code, key) if team in (None, "None") else key
        if target not in merged:
            seed = entries.get(target, entries[key])
            merged[target] = {**seed, "sessions": 0}
            merged_order.append(target)
        merged[target]["sessions"] += entries[key]["sessions"]
        for field in ("colour", "number", "firstName", "lastName"):
            if merged[target][field] in (None, "None") and entries[key][field] not in (None, "None"):
                merged[target][field] = entries[key][field]
    return {"drivers": [merged[k] for k in merged_order]}


def team_registry(driver_reg: dict) -> dict:
    colours = {}
    for d in driver_reg["drivers"]:
        if d["team"] and d["colour"] not in (None, "None"):
            colours.setdefault(d["team"], d["colour"])
    return {"teams": [{"team": t, "colour": c} for t, c in sorted(colours.items())]}


def _clean(v):
    """drivers.json writes a literal "None" string where a field is absent."""
    return None if v in (None, "None", "") else v


def race_entries(event: str, registry: dict) -> list[dict]:
    """The cars that actually started this Grand Prix, in car-number order.

    The New Race configurator may only offer these: the global driver registry is the
    union over EVERY session, so it also carries reserve and rookie drivers who only
    ran a Friday practice (measured: 35 registry entries against a 22-car grid), and
    those cars were never on the grid on Sunday.

    An entrant is a code seen in the Race session, taken as the union of two sources
    because neither is complete on its own: drivers.json (full name, car number, team
    colour) drops entrants in several events (measured: Chinese Race lists 18 rows
    against 22 telemetry directories), and a telemetry directory carries no attributes
    at all. Attributes come from the event's own drivers.json first, then from the
    registry, so a code present only as a directory still arrives fully described.
    """
    race_dir = DATA_ROOT / event / "Race"
    if not race_dir.exists():
        return []

    rows: dict[str, dict] = {}
    drv_file = race_dir / "drivers.json"
    if drv_file.exists():
        try:
            for row in json.loads(drv_file.read_text(encoding="utf-8")).get("drivers", []):
                code = row.get("driver")
                if code:
                    rows[code] = row
        except (json.JSONDecodeError, AttributeError):
            pass

    codes = set(rows)
    for d in race_dir.iterdir():
        if d.is_dir() and len(d.name) == 3 and d.name.isalpha():
            codes.add(d.name)

    by_code = {d["code"]: d for d in registry["drivers"]}
    out = []
    for code in codes:
        row = rows.get(code, {})
        fallback = by_code.get(code, {})
        out.append({
            "code": code,
            "number": _clean(row.get("dn")) or _clean(fallback.get("number")),
            "team": _clean(row.get("team")) or _clean(fallback.get("team")),
            "colour": _clean(row.get("tc")) or _clean(fallback.get("colour")),
            "firstName": _clean(row.get("fn")) or _clean(fallback.get("firstName")),
            "lastName": _clean(row.get("ln")) or _clean(fallback.get("lastName")),
        })
    # Car number is the official entry-list order, and the configurator hands the list
    # to the engine as the starting grid, so the order has to be stable and meaningful.
    out.sort(key=lambda e: (int(e["number"]) if (e["number"] or "").isdigit() else 999, e["code"]))
    return out


def weather_envelope(event: str) -> dict:
    import statistics as st
    fields = ("wAT", "wTT", "wH", "wP", "wWS")
    acc = defaultdict(list)
    rain_total = 0
    rain_n = 0
    for session in event_sessions(event):
        wpath = DATA_ROOT / event / session / "weather.json"
        if not wpath.exists():
            continue
        try:
            w = json.loads(wpath.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for f in fields:
            for v in w.get(f, []):
                if not isinstance(v, str):
                    acc[f].append(v)
        for v in w.get("wR", []):
            rain_n += 1
            rain_total += 1 if v is True else 0

    def winsorised_range(vals):
        if len(vals) < 5:
            return None
        s = sorted(vals)
        lo = s[max(0, int(0.01 * len(s)))]
        hi = s[min(len(s) - 1, int(0.99 * len(s)))]
        return {"min": lo, "median": st.median(s), "max": hi}

    return {
        "airTempC": winsorised_range(acc["wAT"]),
        "trackTempC": winsorised_range(acc["wTT"]),
        "humidityPct": winsorised_range(acc["wH"]),
        "pressureMbar": winsorised_range(acc["wP"]),
        "windMps": winsorised_range(acc["wWS"]),
        "rainObservedFraction": (rain_total / rain_n) if rain_n else 0.0,
        "provenance": "DERIVED, winsorised at the 1st/99th percentile over all sessions of the event",
    }


def tyre_allocation(event: str) -> dict:
    from collections import Counter
    race_dir = DATA_ROOT / event / "Race"
    if not race_dir.exists():
        return {"compounds": ["SOFT", "MEDIUM", "HARD"], "provenance": "DEFAULT (no Race session)"}
    p = race_dir / "session_laptimes.json"
    counts = Counter()
    if p.exists():
        try:
            sl = json.loads(p.read_text(encoding="utf-8"))
            counts.update(c for c in sl.get("compound", []) if isinstance(c, str))
        except json.JSONDecodeError:
            pass
    observed = sorted(c for c in counts if c in ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE"))
    return {
        "compounds": observed or ["SOFT", "MEDIUM", "HARD"],
        "counts": dict(counts),
        "provenance": "OBSERVED (from Race session compound column); WET never offered, not observed in 2026",
    }


def race_length(event: str) -> dict:
    p = DATA_ROOT / event / "Race" / "session_laptimes.json"
    if not p.exists():
        return {"raceLaps": None}
    try:
        sl = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"raceLaps": None}
    laps = [l for l in sl.get("lap", []) if isinstance(l, int)]
    sprint_dir = DATA_ROOT / event / "Sprint"
    sprint_laps = None
    if sprint_dir.exists():
        sp = sprint_dir / "session_laptimes.json"
        if sp.exists():
            try:
                spd = json.loads(sp.read_text(encoding="utf-8"))
                sprint_laps = max((l for l in spd.get("lap", []) if isinstance(l, int)), default=None)
            except json.JSONDecodeError:
                pass
    return {"raceLaps": max(laps) if laps else None, "sprintLaps": sprint_laps,
            "hasSprint": sprint_dir.exists()}


def build_catalogue() -> dict:
    events = discover_events()
    driver_reg = driver_registry(events)
    team_reg = team_registry(driver_reg)
    year = int(DATA_ROOT.name) if DATA_ROOT.name.isdigit() else None
    tracks = []
    for event in events:
        rl = race_length(event)
        tracks.append({
            "event": event,
            "slug": event.lower().replace(" ", "-"),
            "year": year,
            "sessions": event_sessions(event),
            **rl,
            "entries": race_entries(event, driver_reg),
            "weather": weather_envelope(event),
            "tyres": tyre_allocation(event),
        })
    return {
        "schemaVersion": SCHEMA_VERSION,
        "drivers": driver_reg["drivers"],
        "teams": team_reg["teams"],
        "tracks": tracks,
        "provenance": "Built by scripts/simdata/catalogue.py from data/2026; see per-field notes",
    }


def main():
    import argparse
    import hashlib
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    args = ap.parse_args()
    cat = build_catalogue()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cat, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:10]
    path = out_dir / f"catalogue.{digest}.json"
    path.write_bytes(payload)
    print(f"catalogue: {path.name}  {len(payload)/1024:.1f} KB  "
          f"{len(cat['drivers'])} drivers  {len(cat['teams'])} teams  {len(cat['tracks'])} tracks  "
          f"(fields: {', '.join(str(len(t['entries'])) for t in cat['tracks'])})")


if __name__ == "__main__":
    main()
