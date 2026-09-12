#!/usr/bin/env python3
"""Apply a static segment map to the lake and emit data/processed/segments/ (CP-05, C1).

Reads ``config/geometry/<circuit>.yaml`` and the 20 m lake, assigns every row to
a segment, and aggregates to one row per (lap, segment). The map is applied
unchanged to every lap: that is what makes ``segment_id`` mean the same piece of
tarmac in every row of every table that joins on it.

Live versus offline columns
---------------------------
Fields available at segment **entry** are live-safe. Anything summarising the
whole segment -- its minimum speed, its total time -- is only knowable once the
segment is over, so it is suffixed ``_offline`` and marked OFFLINE_ONLY
(AGENTS.md sections 13, 44). Mixing the two is how a model ends up trained on
information it will not have at the decision point.

Usage:
    python scripts/features/build_segments.py --circuit british
    python scripts/features/build_segments.py --all --jobs 4
"""
from __future__ import annotations

import argparse
import bisect
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.progress import Progress  # noqa: E402

LAKE = ROOT / "data" / "processed" / "telemetry_20m"
GEOMETRY_DIR = ROOT / "config" / "geometry"
OUT = ROOT / "data" / "processed" / "segments"

#: Carried through from the lake unchanged, one value per segment taken at entry.
ENTRY_FIELDS = ("tyre_compound", "tyre_life_laps", "tyre_is_new", "stint", "team",
                "race_position", "track_status", "is_accurate", "lap_deleted",
                "lap_time_s", "lap_start_session_s", "driver_ahead_number")


def load_geometry(circuit: str) -> dict:
    import yaml
    path = GEOMETRY_DIR / f"{circuit}.yaml"
    if not path.exists():
        raise SystemExit(
            f"no segment map at {path}. Derive it first:\n"
            f"  python scripts/features/derive_track_geometry.py --circuit {circuit}"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


def build_for_event(geometry: dict, event_display: str, years: tuple[str, ...] | None = None,
                    lake_root: Path | None = None):
    """One row per (year, event, session, driver, lap, segment_id).

    ``lake_root`` defaults to the canonical lake. It is a parameter rather than a
    constant so a smoke test or a second machine can point the same producer at
    another lake without writing into the canonical one (AGENTS.md section 5).
    """
    import pandas as pd

    lake = lake_root or LAKE
    safe = event_display.replace(" ", "_")
    pattern = "year=*" if not years else None
    if years:
        files = []
        for year in years:
            files.extend(lake.glob(f"year={year}/event={safe}/session=*/telemetry_20m.parquet"))
        files = sorted(files)
    else:
        files = sorted(lake.glob(f"{pattern}/event={safe}/session=*/telemetry_20m.parquet"))
    if not files:
        return None, {"reason": "no lake data"}

    segments = geometry["segments"]
    edges = [s["start_distance_m"] for s in segments]
    by_id = {s["segment_id"]: s for s in segments}

    frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    frame = frame[frame["validation_status"].eq("ACCEPTED")].copy()
    if frame.empty:
        return None, {"reason": "no accepted rows"}

    # bisect against the boundary array: exact, and no dependence on row order.
    frame["segment_id"] = [
        max(1, bisect.bisect_right(edges, float(d))) for d in frame["distance_m"]
    ]
    frame = frame.sort_values(["year", "event", "session", "driver", "lap", "distance_m"])

    grouped = frame.groupby(["year", "event", "session", "driver", "lap", "segment_id"], sort=True)
    rows: list[dict] = []
    for (year, event, session, driver, lap, segment_id), block in grouped:
        static = by_id.get(segment_id, {})
        first = block.iloc[0]
        last = block.iloc[-1]
        speeds = block["speed_kmh"].dropna()
        times = block["lap_elapsed_s"].dropna()
        brake = block["brake_on"].dropna()
        throttle = block["throttle_pct"].dropna()
        # First point in the segment where the driver is on the brakes. Null on
        # segments with no braking at all (straights) -- that is a real absence,
        # not a data gap, and CP-09 counts n per metric so the group stays valid.
        braking = (block[block["brake_on"].fillna(False).astype(bool)]
                   if "brake_on" in block.columns else None)

        row = {
            "year": year, "event": event, "session": session, "driver": driver, "lap": int(lap),
            "segment_id": int(segment_id),
            "geometry_version": geometry["geometry_version"],
            "boundary_hash": geometry["boundary_hash"],
            # static circuit geometry
            "start_distance_m": static.get("start_distance_m"),
            "end_distance_m": static.get("end_distance_m"),
            "segment_length_m": static.get("segment_length_m"),
            "kind": static.get("kind"),
            "corner_id": static.get("corner_id"),
            "corner_type": static.get("corner_type"),
            "sector": static.get("sector"),
            "zone": static.get("zone"),
            "track_heading_deg": static.get("track_heading_deg"),
            # live: known at segment entry
            "entry_speed_kmh": float(first["speed_kmh"]) if pd.notna(first.get("speed_kmh")) else None,
            "entry_distance_m": float(first["distance_m"]),
            "entry_lap_elapsed_s": float(first["lap_elapsed_s"]) if pd.notna(first.get("lap_elapsed_s")) else None,
            "gap_ahead_m_entry": float(first["gap_ahead_m"]) if pd.notna(first.get("gap_ahead_m")) else None,
            "rows": int(len(block)),
            # offline: only knowable once the segment is complete
            "exit_speed_kmh_offline": float(last["speed_kmh"]) if pd.notna(last.get("speed_kmh")) else None,
            "min_speed_kmh_offline": float(speeds.min()) if len(speeds) else None,
            "max_speed_kmh_offline": float(speeds.max()) if len(speeds) else None,
            "mean_speed_kmh_offline": float(speeds.mean()) if len(speeds) else None,
            "segment_time_s_offline": (float(times.iloc[-1] - times.iloc[0]) if len(times) > 1 else None),
            "brake_fraction_offline": float(brake.mean()) if len(brake) else None,
            "brake_onset_m_offline": (float(braking.iloc[0]["distance_m"])
                                      if braking is not None and len(braking) else None),
            "full_throttle_fraction_offline": float((throttle >= 95).mean()) if len(throttle) else None,
            "gap_ahead_m_exit_offline": float(last["gap_ahead_m"]) if pd.notna(last.get("gap_ahead_m")) else None,
        }
        for field in ENTRY_FIELDS:
            if field in block.columns:
                value = first.get(field)
                row[field] = None if pd.isna(value) else value
        rows.append(row)

    return pd.DataFrame(rows), {"laps": grouped.ngroups, "rows": len(rows)}


def build_circuit(circuit: str, output_root: Path, years: tuple[str, ...] | None = None,
                  lake_root: Path | None = None) -> dict:
    """Build one circuit's segment table. Module-level so it pickles."""
    geometry = load_geometry(circuit)
    frame, info = build_for_event(geometry, geometry["event_display"], years, lake_root)
    if frame is None:
        return {"circuit": circuit, "skipped": True, **info}
    destination = output_root / f"circuit={circuit}" / "segments.parquet"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if years and destination.exists():
        # Additive: keep the years this run did not touch, replace the ones it did.
        import pandas as pd

        existing = pd.read_parquet(destination)
        keep = existing[~existing["year"].astype(str).isin([str(y) for y in years])]
        frame = pd.concat([keep, frame], ignore_index=True)
    frame.to_parquet(destination, index=False)
    return {
        "circuit": circuit,
        "geometry_version": geometry["geometry_version"],
        "segments_per_lap": geometry["segment_count"],
        "laps": info["laps"] // geometry["segment_count"] if geometry["segment_count"] else 0,
        "rows": len(frame),
        "event_display": geometry["event_display"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--circuit", default=None)
    parser.add_argument("--all", action="store_true", help="Every circuit with a segment map")
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--lake", type=Path, default=LAKE,
                        help="Telemetry-20m lake to read (default: data/processed/telemetry_20m)")
    parser.add_argument("--years", default=None,
                        help="CSV of years to rebuild, e.g. 2024. Other years already in "
                             "the table are kept, so a new season does not cost a full rebuild.")
    parser.add_argument("--jobs", type=int, default=1, help="Circuits built in parallel")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    if not args.circuit and not args.all:
        parser.error("give --circuit KEY or --all")

    maps = sorted(GEOMETRY_DIR.glob("*.yaml")) if args.all else [GEOMETRY_DIR / f"{args.circuit}.yaml"]
    maps = [m for m in maps if m.exists()]
    if not maps:
        parser.error("no segment maps found; run derive_track_geometry.py first")

    years = tuple(y.strip() for y in args.years.split(",") if y.strip()) if args.years else None
    circuits = [m.stem for m in maps]
    args.output.mkdir(parents=True, exist_ok=True)
    prog = Progress(len(circuits), enabled=not args.no_progress)
    written, skipped = [], []

    def record(result):
        if result.get("skipped"):
            skipped.append({k: v for k, v in result.items() if k != "skipped"})
        else:
            written.append({k: v for k, v in result.items() if k != "event_display"})

    if args.jobs > 1 and len(circuits) > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(build_circuit, c, args.output, years, args.lake): c for c in circuits}
            pending = set(futures)
            while pending:
                finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in finished:
                    result = future.result()
                    record(result)
                    prog.set_label(str(result.get("event_display") or result.get("circuit")))
                    prog.tick()
                if pending:
                    prog.set_label(f"{min(len(pending), args.jobs)} building, "
                                   f"{max(0, len(pending) - args.jobs)} queued")
                    prog.heartbeat()
    else:
        for circuit in circuits:
            prog.set_label(circuit)
            record(build_circuit(circuit, args.output, years, args.lake))
            prog.tick()
    prog.close()

    manifest = {
        "schema_version": "c1_segments_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "years_rebuilt": list(years) if years else "all",
        "lake_root": str(args.lake),
        "written": sorted(written, key=lambda r: str(r.get("circuit"))),
        "skipped": sorted(skipped, key=lambda r: str(r.get("circuit"))),
    }
    (args.output / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
