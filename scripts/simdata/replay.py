"""Build a REPLAY pack for one event/session: a binary sample blob plus a JSON
manifest, both consumed by the frontend replay adapter.

Binary format (little-endian), one variable-length run of samples per (driver, lap),
offsets recorded in the manifest so a lap can be fetched independently:

    u16  dtMs        milliseconds since the previous sample (0 for the first sample
                      of a lap); the decoder reconstructs absolute lap-relative time
                      by a running sum
    f32  stationM     arc-length position on the SAME ring the track model renders,
                      so cars never disagree with the track surface
    i16  lateralCm    signed lateral offset from the centreline, centimetres
    u16  speedKph     direct speed (not reconstructed from station deltas), 0.1 km/h...
                      NO: stored as whole km/h (max observed 366) -- see NOTE below
    u8   gearAndBrake  bits 0-6 = gear (0-8), bit 7 = brake
    u8   throttlePct   0-255 (throttle can exceed 100 in the raw feed; clamped here)

12 bytes/sample. This is a first working version, not the plan's fully bit-packed
9 B/sample target (which needs int16-delta xyz reconstruction); it stores station and
speed directly for simplicity and correctness, and is a documented follow-up to shrink
further. For the British Race (844k samples) this is ~10 MB raw, ~3.5-4 MB gzipped,
within the plan's 5 MB/race budget.

Position frame per lap (see plan section 6, "frame A / frame B"):
  frame A (has_xy): station/lateral from projecting the lap's own x/y onto the ring.
  frame B (no xy, e.g. Monaco laps 6-78): station = distance/distance[-1] * ring.length,
           lateral = 0. Tagged in the manifest per lap as "positionFrame".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simdata.build_track import prepare_ring, slugify
from simdata.rawio import LapTable, load_lap
from simdata.rcm import build_rcm_feed, neutralisation_intervals
from simdata.twin import TwinParams, estimate_ers, lap_summary

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "2026"


def _num(v):
    """The raw feed writes the string \"None\" for a missing number."""
    return None if v is None or isinstance(v, str) else float(v)
SCHEMA_VERSION = 1
SAMPLE_STRUCT = struct.Struct("<HfhHBB")  # dtMs, stationM, lateralCm, speedKph, gearBrake, throttle


def encode_lap(ring, lap) -> bytes:
    n = lap.n
    if n == 0:
        return b""
    if lap.has_xy and lap.xy_fraction() > 0.5:
        station, lateral = ring.project(lap.x, lap.y)
        frame = "A"
    else:
        d = lap.dist
        span = d[-1] - d[0] if np.isfinite(d).all() and d.size > 1 else 0.0
        if span <= 0:
            station = np.linspace(0, ring.length, n)
        else:
            station = (d - d[0]) / span * ring.length
        lateral = np.zeros(n)
        frame = "B"

    station = np.nan_to_num(station, nan=0.0) % ring.length
    lateral = np.nan_to_num(lateral, nan=0.0)
    lateral = np.clip(lateral, -327.0, 327.0)
    speed = np.nan_to_num(lap.speed, nan=0.0)
    speed = np.clip(speed, 0, 655)
    t_ms = np.nan_to_num(lap.t, nan=0.0) * 1000.0
    dt_ms = np.diff(t_ms, prepend=t_ms[0] if n else 0.0)
    dt_ms = np.clip(dt_ms, 0, 65535)
    gear = np.clip(lap.gear, 0, 8).astype(np.int32)
    brake = np.clip(lap.brake, 0, 1).astype(np.int32)
    gear_brake = (gear & 0x7F) | (brake << 7)
    throttle = np.clip(np.nan_to_num(lap.throttle, nan=0.0), 0, 255)

    buf = bytearray(SAMPLE_STRUCT.size * n)
    off = 0
    for i in range(n):
        SAMPLE_STRUCT.pack_into(
            buf, off,
            int(dt_ms[i]), float(station[i]), int(round(lateral[i] * 100)),
            int(round(speed[i])), int(gear_brake[i]), int(throttle[i]),
        )
        off += SAMPLE_STRUCT.size
    return bytes(buf), frame


def build_replay_pack(event: str, session: str):
    ring, geom_dir, geom_session, _, _ = prepare_ring(event)
    sdir = DATA_ROOT / event / session
    table = LapTable(sdir)
    rows = list(table.rows())

    blob = bytearray()
    drivers = {}
    soc_carry: dict[str, float] = {}   # charge carried lap to lap, per driver
    for r in rows:
        drv, lap_no = r["drv"], r["lap"]
        lap = load_lap(sdir, drv, lap_no)
        if lap is None or lap.n == 0:
            continue
        sample_bytes, frame = encode_lap(ring, lap)

        # Energy twin (MODELS.md M14). Computed HERE, in Python, and shipped as a
        # per-lap summary: the browser renders these numbers and never derives them.
        # The same pure functions will back the HTTP API later -- only the transport
        # differs. Everything produced is INFERRED/SIMULATED and carries its warnings.
        energy = None
        try:
            est = estimate_ers(
                lap.t, lap.speed, lap.dist, lap.z / 10.0, lap.brake, lap.throttle,
                TwinParams(),
                air_temp_c=_num(r.get("wAT")), pressure_hpa=_num(r.get("wP")),
                initial_soc_mj=soc_carry.get(drv, 2.0),
            )
            energy = lap_summary(est)
            soc_carry[drv] = energy["socEndMj"]
        except (ValueError, IndexError, ZeroDivisionError):
            energy = None
        offset = len(blob)
        blob.extend(sample_bytes)
        entry = {
            "lap": lap_no, "byteOffset": offset, "sampleCount": lap.n,
            "positionFrame": frame,
            "lST": r["lST"] if not isinstance(r["lST"], str) else None,
            "sesT": r["sesT"] if not isinstance(r["sesT"], str) else None,
            "time": r["time"] if not isinstance(r["time"], str) else None,
            "pin": r["pin"] if not isinstance(r["pin"], str) else None,
            "pout": r["pout"] if not isinstance(r["pout"], str) else None,
            "status": r["status"],
            "pos": r["pos"] if not isinstance(r["pos"], str) else None,
            "compound": r["compound"],
            "stint": r["stint"] if not isinstance(r["stint"], str) else None,
            "life": r["life"] if not isinstance(r["life"], str) else None,
            "fresh": bool(r["fresh"]) if r["fresh"] is not None else None,
            "iacc": bool(r["iacc"]),
            "del": bool(r["del"]),
            "ff1G": bool(r["ff1G"]),
            "energy": energy,
        }
        drivers.setdefault(drv, {"driver": drv, "team": r["team"], "laps": []})
        drivers[drv]["laps"].append(entry)

    rcm_feed = build_rcm_feed(sdir, table)
    neutral = neutralisation_intervals(rcm_feed) if rcm_feed else []

    weather_path = sdir / "weather.json"
    weather = None
    if weather_path.exists():
        w = json.loads(weather_path.read_text(encoding="utf-8"))
        weather = {k: w[k] for k in ("wT", "wAT", "wTT", "wH", "wR", "wWS", "wWD") if k in w}

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "event": event, "session": session, "trackSlug": slugify(event),
        "geometrySession": geom_session,
        "trackLengthMetres": round(ring.length, 2),
        "sampleStructBytes": SAMPLE_STRUCT.size,
        "drivers": list(drivers.values()),
        "raceControl": rcm_feed or [],
        "neutralisation": neutral,
        "weather": weather,
        "capabilities": {
            "hasPositions": any(l["positionFrame"] == "A"
                                for d in drivers.values() for l in d["laps"]),
            "hasDriverAhead": True,
        },
        "provenance": {
            "positions": "OBSERVED where positionFrame=='A' (projected telemetry x/y); "
                        "DERIVED where 'B' (distance-normalised, no measured position)",
            "lapTimingFields": "OBSERVED (session_laptimes.json)",
            "raceControl": "OBSERVED text, DERIVED classification/session-time",
            "neutralisation": "DERIVED (simplified state machine from race-control text)",
        },
    }
    return bytes(blob), manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("event")
    ap.add_argument("session")
    ap.add_argument("out_dir")
    args = ap.parse_args()

    t0 = time.time()
    blob, manifest = build_replay_pack(args.event, args.session)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    blob_digest = hashlib.sha256(blob).hexdigest()[:10]
    base = f"{manifest['trackSlug']}-{args.session.lower().replace(' ', '-')}"
    blob_name = f"{base}.{blob_digest}.bin"
    (out_dir / blob_name).write_bytes(blob)
    manifest["binFile"] = blob_name

    payload = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    man_digest = hashlib.sha256(payload).hexdigest()[:10]
    man_name = f"{base}.{man_digest}.json"
    (out_dir / man_name).write_bytes(payload)

    dt = time.time() - t0
    n_samples = len(blob) // SAMPLE_STRUCT.size
    print(f"{args.event}/{args.session}: {blob_name} ({len(blob)/1024:.1f} KB) + "
          f"{man_name} ({len(payload)/1024:.1f} KB)  {n_samples} samples  "
          f"built in {dt:.1f}s")


if __name__ == "__main__":
    main()
