"""Classify race-control messages and convert their timestamps to session seconds.

This is a working subset of the full 25-kind taxonomy the audit identified across all
2,376 messages in 2026 (blue flags, sector flags, safety car, VSC, red flag, chequered,
overtake mode, penalties, deleted laps, incidents, pit lane state, weather notices). It
covers everything the dashboard's race-control feed and timeline need; rarer
administrative message kinds fall through to a generic "other" bucket rather than being
individually named.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

CAR_RE = re.compile(r"CAR (\d+) \(([A-Z]{3})\)")

PATTERNS = [
    ("blueFlag", re.compile(r"^WAVED BLUE FLAG FOR CAR (?P<car>\d+) \((?P<drv>[A-Z]{3})\)")),
    ("sectorFlag", re.compile(r"^(?P<flag>YELLOW|DOUBLE YELLOW|CLEAR) IN TRACK SECTOR (?P<sector>\d+)$")),
    ("lapDeleted", re.compile(r"^CAR (?P<car>\d+) \((?P<drv>[A-Z]{3})\).*DELETED")),
    ("penaltyServed", re.compile(r"^FIA STEWARDS: PENALTY SERVED")),
    ("penalty", re.compile(r"^FIA STEWARDS:.*(SECOND TIME PENALTY|DRIVE THROUGH PENALTY|STOP-AND-GO PENALTY)")),
    ("investigation", re.compile(r"^FIA STEWARDS:.*INCIDENT INVOLVING")),
    ("incidentNoted", re.compile(r"INCIDENT INVOLVING")),
    ("pitLane", re.compile(r"^(PIT EXIT|PIT LANE|ALL CARS THROUGH THE PIT LANE|ALL PASS HOLDERS)")),
    ("overtakeMode", re.compile(r"^OVERTAKE (?P<state>ENABLED|DISABLED)$")),
    ("vsc", re.compile(r"^VSC (?P<state>DEPLOYED|ENDING)$")),
    ("safetyCar", re.compile(r"^SAFETY CAR (?P<state>DEPLOYED|IN THIS LAP|LIGHTS ON|WILL USE)")),
    ("redFlag", re.compile(r"^RED FLAG - RACE SUSPENDED$")),
    ("chequered", re.compile(r"^CHEQUERED FLAG$")),
    ("blackAndWhite", re.compile(r"^BLACK AND WHITE FLAG FOR CAR (?P<car>\d+) \((?P<drv>[A-Z]{3})\)")),
    ("startProcedure", re.compile(r"^(STANDING START|RACE START|EXTRA FORMATION LAP|FORMATION LAP)")),
    ("resumptionOrder", re.compile(r"^(RESUMPTION ORDER|PUSH CARS TO FRONT|LAPPED CARS MAY NOW OVERTAKE)")),
    ("weather", re.compile(r"^(RISK OF RAIN|AIR TEMPERATURE|TRACK SURFACE SLIPPERY|LOW GRIP|NORMAL GRIP)")),
    ("marshalsRecovery", re.compile(r"^(MARSHALS ON TRACK|RECOVERY VEHICLE|MEDICAL CAR)")),
    ("trackClear", re.compile(r"^TRACK CLEAR$")),
    ("greenPitExit", re.compile(r"^GREEN LIGHT - PIT EXIT OPEN$")),
]


def classify(msg: str) -> str:
    m = msg.strip().replace("�", " ")
    for kind, pat in PATTERNS:
        if pat.search(m):
            return kind
    return "other"


def car_numbers(msg: str) -> list[str]:
    return [num for num, _code in CAR_RE.findall(msg)]


def parse_iso(ts: str) -> float:
    """ISO nanosecond timestamp -> POSIX seconds (float)."""
    # "2026-07-04T10:26:24.000000000" -> truncate to microsecond precision
    base, frac = ts.split(".") if "." in ts else (ts, "0")
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    micros = int(frac[:6].ljust(6, "0"))
    return dt.timestamp() + micros / 1e6


def session_time_offset(table) -> float | None:
    """offset = epoch(lSD) - lST, measured exactly constant per session (see plan).
    Returns None if no row has both fields available."""
    lsd = table.col("lSD")
    lst = table.col("lST")
    for i in range(table.n):
        if isinstance(lsd[i], str) and lsd[i] != "None" and not isinstance(lst[i], str):
            try:
                return parse_iso(lsd[i]) - lst[i]
            except ValueError:
                continue
    return None


def build_rcm_feed(session_dir, table) -> list[dict] | None:
    import json
    p = session_dir / "rcm.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    offset = session_time_offset(table)
    if offset is None:
        return None
    out = []
    n = len(raw.get("time", []))
    for i in range(n):
        msg = raw["msg"][i]
        try:
            t = parse_iso(raw["time"][i]) - offset
        except ValueError:
            continue
        out.append({
            "sessionTime": round(t, 1),
            "kind": classify(msg),
            "message": msg,
            "flag": raw.get("flag", ["None"] * n)[i],
            "scope": raw.get("scope", ["None"] * n)[i],
            "sector": raw.get("sector", ["None"] * n)[i],
            "lap": raw.get("lap", [None] * n)[i],
            "cars": car_numbers(msg),
        })
    return out


def neutralisation_intervals(feed: list[dict]) -> list[dict]:
    """A simplified state machine: VSC/SC/RED open an interval, the matching
    ending/lap/chequered message closes it. Good enough for the timeline UI; the full
    green-detection-from-telemetry refinement in the plan is a later precision pass."""
    out = []
    open_iv = None
    for m in sorted(feed, key=lambda x: x["sessionTime"]):
        k = m["kind"]
        if k == "vsc" and "DEPLOYED" in m["message"]:
            open_iv = {"kind": "VSC", "start": m["sessionTime"], "end": None}
            out.append(open_iv)
        elif k == "vsc" and "ENDING" in m["message"] and open_iv and open_iv["kind"] == "VSC":
            open_iv["end"] = m["sessionTime"]
            open_iv = None
        elif k == "safetyCar" and "DEPLOYED" in m["message"]:
            open_iv = {"kind": "SC", "start": m["sessionTime"], "end": None}
            out.append(open_iv)
        elif k == "safetyCar" and "IN THIS LAP" in m["message"] and open_iv and open_iv["kind"] == "SC":
            open_iv["end"] = m["sessionTime"]
            open_iv = None
        elif k == "redFlag":
            if open_iv:
                open_iv["end"] = m["sessionTime"]
            open_iv = {"kind": "RED", "start": m["sessionTime"], "end": None}
            out.append(open_iv)
        elif k == "startProcedure" and "STANDING START" in m["message"] and open_iv and open_iv["kind"] == "RED":
            open_iv["end"] = m["sessionTime"]
            open_iv = None
        elif k == "chequered" and open_iv:
            open_iv["end"] = m["sessionTime"]
            open_iv = None
    for iv in out:
        if iv["end"] is None:
            iv["end"] = iv["start"]
    return out
