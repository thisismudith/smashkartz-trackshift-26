"""Assemble one track-model artifact for one event from its Race (or Qualifying,
for events where the Race has no usable positions, e.g. Monaco) session.

Usage: python -m simdata.build_track "British Grand Prix" out_dir
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simdata.rawio import LapTable
from simdata.track import (build_ring, corner_stations, grid, pick_geometry_laps,
                            pit_lane, pit_lane_path, reference_speed_profile,
                            timing_lines, width_estimate)

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "2026"
SCHEMA_VERSION = 1
MIN_GEOMETRY_LAPS = 30


def slugify(event: str) -> str:
    return event.lower().replace(" ", "-")


def geometry_session(event_dir: Path) -> tuple[Path, str]:
    """Race if it has >=30 clean laps with valid positions, else Qualifying."""
    for name in ("Race", "Qualifying"):
        sdir = event_dir / name
        if not sdir.exists():
            continue
        table = LapTable(sdir)
        laps = pick_geometry_laps(sdir, table, limit=MIN_GEOMETRY_LAPS)
        if len(laps) >= min(MIN_GEOMETRY_LAPS, 10):
            return sdir, name
    raise RuntimeError(f"no session with usable geometry under {event_dir}")


def prepare_ring(event: str):
    """Build the event's ring once, in its final (start/finish-rotated) frame.

    Shared by build_track_model (which serialises it) and the replay pack builder
    (which projects every car's telemetry onto this SAME ring, so the rendered track
    and the rendered cars never disagree about where station 0 is).

    Returns (ring, geometry_session_dir, session_name, laps, timing_lines).
    """
    event_dir = DATA_ROOT / event
    sdir, session_name = geometry_session(event_dir)
    table = LapTable(sdir)
    laps = pick_geometry_laps(sdir, table)
    if len(laps) < 5:
        raise RuntimeError(f"only {len(laps)} usable geometry laps for {event}/{session_name}")

    ring, _ = build_ring(laps)
    tl = timing_lines(sdir, table, ring, laps)
    if not tl["sf"]:
        raise RuntimeError(f"could not locate start/finish for {event}")
    ring = ring.rotated(tl["sf"]["station"])
    tl = timing_lines(sdir, table, ring, laps)  # re-measure in the new frame
    return ring, sdir, session_name, laps, tl


def build_track_model(event: str) -> dict:
    ring, sdir, session_name, laps, tl = prepare_ring(event)
    table = LapTable(sdir)

    corners = corner_stations(sdir, ring)
    pit = pit_lane(sdir, table, ring)
    pit_path = pit_lane_path(sdir, table, ring, pit)
    grid_model = grid(sdir, table, ring, 0.0)
    width = width_estimate(sdir, table, ring, laps, corners, pit)
    profile = reference_speed_profile(ring, laps)

    curvature = ring.curvature(smooth_m=15.0)
    apex_idx = np.where(np.abs(curvature) > 1.0 / 250.0)[0]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "event": event,
        "slug": slugify(event),
        "geometrySession": session_name,
        "generatedFromLaps": len(laps),
        "ring": {
            "dsMetres": ring.ds,
            "lengthMetres": round(ring.length, 2),
            # centimetre-quantised integers: far more compact than float text and it
            # compresses better besides. Heading is recomputed at runtime from x/y
            # (atan2 of the forward difference) rather than shipped.
            "xCm": [int(round(v * 100)) for v in ring.x],
            "yCm": [int(round(v * 100)) for v in ring.y],
            "zCm": [int(round(v * 100)) for v in ring.z],
        },
        "timingLines": tl,
        "corners": corners,
        "pitLane": pit,
        "pitLanePath": pit_path,
        "grid": grid_model,
        "width": width,
        "referenceProfile": profile,
        "curvature": {
            "smoothMetres": 15.0,
            "apexStationCount": int(apex_idx.size),
        },
        "provenance": {
            "ring": "DERIVED (median of clean laps, circularly smoothed)",
            "timingLines": "DERIVED (sector-timestamp positions, median over clean laps)",
            "corners": "DERIVED (marker X/Y projected onto ring); numbering OBSERVED",
            "pitLane": pit["provenance"],
            "grid": grid_model["provenance"],
            "width": width["provenance"],
            "referenceProfile": profile["provenance"],
        },
    }


def write_artifact(model: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(model, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:10]
    path = out_dir / f"{model['slug']}.{digest}.json"
    path.write_bytes(payload)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("event")
    ap.add_argument("out_dir")
    args = ap.parse_args()

    t0 = time.time()
    model = build_track_model(args.event)
    path = write_artifact(model, Path(args.out_dir))
    dt = time.time() - t0
    size_kb = path.stat().st_size / 1024
    print(f"{args.event}: {path.name}  {size_kb:.1f} KB  built in {dt:.1f}s  "
          f"ring={model['ring']['lengthMetres']}m  session={model['geometrySession']}")


if __name__ == "__main__":
    main()
