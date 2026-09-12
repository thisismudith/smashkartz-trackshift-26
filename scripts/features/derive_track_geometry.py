#!/usr/bin/env python3
"""Derive the static segment map for a circuit and write config/geometry/ (CP-05).

Run this once per circuit. It reads the 20 m lake, builds a median profile over
clean reference laps, and writes the boundaries to
``config/geometry/<circuit>.yaml`` with a ``geometry_version`` stamp.

`build_segments.py` then applies that file to every lap. The split matters:
deriving here and applying there is what makes `segment_id` a property of the
circuit rather than of a lap, which is the whole point of CP-05. Re-running this
is therefore a deliberate act that invalidates every downstream table -- bump
``geometry_version`` and rebuild, never edit in place.

Reference lap selection (CP-05 step 1): laps that are `is_accurate`, ran under
track status '1', were not deleted by the stewards, and are not in- or out-laps.

Usage:
    python scripts/features/derive_track_geometry.py --circuit british
    python scripts/features/derive_track_geometry.py --all --version-suffix v1
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.track.segmentation import (  # noqa: E402
    DEFAULTS,
    boundary_hash,
    build_segments,
    median_profile,
)

LAKE = ROOT / "data" / "processed" / "telemetry_20m"
GEOMETRY_DIR = ROOT / "config" / "geometry"
RULES_DIR = ROOT / "config" / "rules" / "2026"
RAW = ROOT / "data" / "raw" / "tracinginsights"
RAW_FALLBACK = ROOT / "data" / "raw"


def raw_root() -> Path:
    """Locate the configured raw mirror without changing its layout.

    The CP-04 handover uses ``data/raw/tracinginsights``.  The repository's
    Phase-1 mirror convention is ``data/raw/<year>``.  Supporting both keeps
    corner apexes available to the static-map derivation; silently missing them
    collapses the map to sparse brake/throttle boundaries.
    """
    return RAW if RAW.is_dir() else RAW_FALLBACK


def load_corners(event_display: str, source_root: Path | None = None) -> list[dict]:
    """corners.json for an event, from whichever session carries it."""
    source_root = source_root or raw_root()
    for session in ("Race", "Qualifying", "Sprint", "Practice 1"):
        path = source_root / "2026" / event_display / session / "corners.json"
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        numbers = raw.get("CornerNumber") or []
        distances = raw.get("Distance") or []
        rotation = raw.get("Rotation")
        out = []
        seen: set[float] = set()
        for i in range(min(len(numbers), len(distances))):
            value = distances[i]
            if value in (None, "None"):
                continue
            value = float(value)
            # The Hungaroring repeats distances: one marker is one boundary.
            if value in seen:
                continue
            seen.add(value)
            out.append({"CornerNumber": numbers[i], "Distance": value})
        try:
            source = str(path.relative_to(ROOT))
        except ValueError:
            source = str(path)
        return sorted(out, key=lambda c: c["Distance"]), rotation, source.replace("\\", "/")
    return [], None, None


def reference_rows(event_display: str):
    """Clean laps for one event, across every session in the lake."""
    import pandas as pd

    safe = event_display.replace(" ", "_")
    files = sorted(LAKE.glob(f"year=*/event={safe}/session=*/telemetry_20m.parquet"))
    if not files:
        return None, 0, []
    frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    mask = frame["validation_status"].eq("ACCEPTED")
    for column, value in (("is_accurate", True), ("lap_deleted", False)):
        if column in frame.columns:
            mask &= frame[column].eq(value)
    if "track_status" in frame.columns:
        mask &= frame["track_status"].astype(str).eq("1")
    # In- and out-laps take the pit path and do not represent the circuit.
    for column in ("pit_in_session_s", "pit_out_session_s"):
        if column in frame.columns:
            mask &= frame[column].isna()

    clean = frame[mask]
    sessions = sorted(clean["session"].unique().tolist()) if len(clean) else []
    return clean, clean.groupby(["session", "driver", "lap"]).ngroups if len(clean) else 0, sessions


def lap_length_for(circuit: str) -> tuple[float | None, str]:
    """Measured lap length from the CP-03 rule config, which is DERIVED_TELEMETRY."""
    import yaml

    for path in RULES_DIR.glob("*.yaml"):
        if path.stem == "common":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if data.get("circuit") == circuit:
            block = data.get("lap_length_m") or {}
            return block.get("value"), path.stem
    return None, ""


def derive(circuit: str, event_display: str, version_suffix: str, options: dict) -> dict | None:
    clean, lap_count, sessions = reference_rows(event_display)
    if clean is None or lap_count == 0:
        return {"circuit": circuit, "error": f"no lake data for {event_display}; build it in CP-04"}

    lap_length, event_key = lap_length_for(circuit)
    if not lap_length:
        return {"circuit": circuit, "error": f"no measured lap_length_m for circuit '{circuit}' in CP-03 config"}

    corners, rotation, corners_source = load_corners(event_display)
    profile = median_profile(clean.to_dict("records"))
    segments = build_segments(profile, float(lap_length), corners=corners, options=options)
    if not segments:
        return {"circuit": circuit, "error": "no segments produced"}

    boundaries = [s.start_distance_m for s in segments]
    version = f"{circuit}-geom-{version_suffix}"
    return {
        "schema_version": 1,
        "geometry_version": version,
        "boundary_hash": boundary_hash(boundaries),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "circuit": circuit,
        "event_display": event_display,
        "event_config": event_key,
        "lap_length_m": float(lap_length),
        "lap_length_source": "config/rules/2026 lap_length_m (DERIVED_TELEMETRY)",
        "rotation_deg": rotation,
        "corners_source": corners_source,
        "corner_count": len(corners),
        "reference_laps": lap_count,
        "reference_sessions": sessions,
        "options": options,
        "segment_count": len(segments),
        "segments": [s.to_dict() for s in segments],
        "note": (
            "Static segment map. Boundaries are a property of the circuit, derived once "
            "from the median profile over the reference laps above and applied unchanged "
            "to every lap. segment_id is frozen for this geometry_version: changing it "
            "invalidates every table that joins on it, so bump the version and rebuild "
            "rather than editing in place."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--circuit", default=None, help="Circuit key, e.g. british")
    parser.add_argument("--all", action="store_true", help="Every circuit with lake data")
    parser.add_argument("--output", type=Path, default=GEOMETRY_DIR)
    parser.add_argument("--version-suffix", default="v1")
    parser.add_argument("--min-segment-length-m", type=float, default=None)
    parser.add_argument("--envelope-taper-kmh", type=float, default=None)
    args = parser.parse_args()

    if not args.circuit and not args.all:
        parser.error("give --circuit KEY or --all")

    import yaml

    options = dict(DEFAULTS)
    if args.min_segment_length_m is not None:
        options["min_segment_length_m"] = args.min_segment_length_m
    if args.envelope_taper_kmh is not None:
        options["envelope_taper_kmh"] = args.envelope_taper_kmh

    targets: list[tuple[str, str]] = []
    for path in sorted(RULES_DIR.glob("*.yaml")):
        if path.stem == "common":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        circuit = data.get("circuit")
        display = data.get("event_display")
        if args.all or circuit == args.circuit:
            targets.append((circuit, display))
    if not targets:
        parser.error(f"no rule config for circuit '{args.circuit}'")

    args.output.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for circuit, display in targets:
        result = derive(circuit, display, args.version_suffix, options)
        if result.get("error"):
            skipped.append({"circuit": circuit, "reason": result["error"]})
            continue
        path = args.output / f"{circuit}.yaml"
        path.write_text(yaml.safe_dump(result, sort_keys=False, default_flow_style=False, allow_unicode=True),
                        encoding="utf-8")
        written.append({
            "circuit": circuit,
            "segments": result["segment_count"],
            "reference_laps": result["reference_laps"],
            "geometry_version": result["geometry_version"],
            "boundary_hash": result["boundary_hash"],
        })

    print(json.dumps({"written": written, "skipped": skipped, "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
