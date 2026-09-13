"""
GET /track/{event} — real data from config/geometry/<circuit>.yaml.

No public loader exists for this in src/trackshift/track/ (segmentation.py's
build_segments is not wired to a per-event artifact reader), so this reads the
static per-circuit geometry file directly, matched to the requested event slug
via its own `event_config` field (the only place that mapping is recorded).

centreline (x/y polyline) is NOT available from this artifact — no raw x/y is
persisted here, only segment boundaries in arc-length. Returned as an empty
list rather than fabricated points; the response says so in `centreline_note`.
"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
GEOMETRY_DIR = REPO_ROOT / "config" / "geometry"


def _read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


@functools.lru_cache(maxsize=None)
def _geometry_by_event() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in GEOMETRY_DIR.glob("*.yaml"):
        data = _read_yaml(path)
        event_config = data.get("event_config")
        if event_config:
            out[event_config] = data
    return out


def available_track_events() -> list[str]:
    return sorted(_geometry_by_event().keys())


def track_response(event: str) -> dict | None:
    geo = _geometry_by_event().get(event)
    if geo is None:
        return None

    segments = []
    for seg in geo.get("segments", []) or []:
        segments.append({
            "segment_id": seg.get("segment_id"),
            "start_distance_m": seg.get("start_distance_m"),
            "end_distance_m": seg.get("end_distance_m"),
            "segment_length_m": seg.get("segment_length_m"),
            "kind": seg.get("kind"),
            "sector": seg.get("sector"),
            "zone": seg.get("zone"),
            "corner_id": seg.get("corner_id"),
            "corner_type": seg.get("corner_type"),
            "corner_phase": seg.get("corner_phase"),
            "track_heading_deg": seg.get("track_heading_deg"),
            "brake_onset_m": None,
            "mean_gradient": 0.0,
            "geometry_version": geo.get("geometry_version"),
            "provenance": "DERIVED",
        })

    return {
        "event": event,
        "track": geo.get("circuit"),
        "lap_length_m": geo.get("lap_length_m"),
        "centreline": [],
        "centreline_note": "No x/y polyline is persisted in config/geometry/*.yaml — this artifact carries segment boundaries in arc-length only. An empty centreline is honest here; it is not a zero-length track.",
        "segments": segments,
        "lines": [],
        "zones": [],
        "geometry_version": geo.get("geometry_version"),
        "corner_count": geo.get("corner_count"),
        "rotation_deg": geo.get("rotation_deg"),
        "segment_count": geo.get("segment_count"),
    }
