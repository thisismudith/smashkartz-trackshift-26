"""Static, versioned track segmentation (M03, CP-05).

`segment_id` is the join key for baselines, pairwise features, opportunities and
the DP's state grid. If it moves, every one of those tables is silently wrong, so
this checkpoint freezes it.

The decision that makes that possible
-------------------------------------
**Segment boundaries are a property of the circuit, not of a lap.** They are
derived once from a *median profile* over many clean reference laps, stored in
``config/geometry/<circuit>.yaml``, and then applied unchanged to every lap.

Detecting brake points per lap would be easier and completely wrong: segment 22
would be a different piece of tarmac on every lap, so a "segment baseline" would
average unrelated things and a segment-time residual would measure where the
driver braked rather than how fast they were. The median profile is what makes
the boundaries a circuit property.

Boundary sources, in the order they are trusted
-----------------------------------------------
1. **FIA lines** (Detection, Activation) where CP-03 has them. Currently
   ``UNVERIFIED`` everywhere, so in practice absent; they are an *overlay* and
   adding them later bumps ``geometry_version`` rather than changing the method.
2. **Corner apexes** from ``corners.json`` -- ``OBSERVED``, the most reliable
   geometry available.
3. **Brake onset** -- where the median profile first shows sustained braking.
4. **Throttle return** -- where the median profile regains sustained full
   throttle, which splits long straights that would otherwise be one segment.

Everything here works in **telemetry-distance coordinates**, which read 0.6% to
2.7% short of the homologated lap length because distance is integrated along the
driven path (see ``config/rules/2026/*.yaml``). That is the correct frame: the
lake is indexed the same way.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

__all__ = [
    "SEGMENT_KINDS",
    "CORNER_TYPES",
    "DEFAULTS",
    "Boundary",
    "Segment",
    "median_profile",
    "detect_brake_onsets",
    "detect_throttle_returns",
    "detect_envelope_taper",
    "merge_short_segments",
    "build_segments",
    "classify_corner_type",
    "track_heading_deg",
    "boundary_hash",
    "smooth",
]

SEGMENT_KINDS = ("STRAIGHT", "BRAKING", "CORNER", "EXIT")

CORNER_TYPES = ("STRAIGHT", "HAIRPIN", "SLOW", "MEDIUM", "FAST", "CHICANE")

#: Tunables. Stored in the geometry file alongside the boundaries so a segment
#: map always carries the parameters that produced it; retuning means a new
#: geometry_version, never a silent change.
DEFAULTS = {
    # A boundary must survive this to count, in metres.
    "min_segment_length_m": 40.0,
    # Braking must persist this far before it is an onset rather than a brush.
    "min_brake_run_m": 60.0,
    # Median brake_on above this counts as "the field brakes here".
    "brake_threshold": 0.5,
    # Throttle must exceed this, and hold, to count as a return to power.
    "throttle_return_pct": 95.0,
    "min_throttle_run_m": 60.0,
    # Centreline smoothing window, in samples, before differencing for heading.
    "heading_smooth_points": 5,
    # Apex-speed bins for corner_type, km/h.
    "hairpin_max_kmh": 100.0,
    "slow_max_kmh": 160.0,
    "medium_max_kmh": 220.0,
    # Curvature below this is a straight.
    #
    # NOT in 1/m: the source's x/y are not metres. Silverstone spans 10,097 x/y
    # units over a 5,811 m lap, so curvature is in the source's own units and the
    # threshold has to be calibrated against them rather than reasoned from a
    # radius. Calibrated against the 18 known corner positions in corners.json:
    #
    #   |k| within 60 m of a corner   median 0.000682
    #   |k| away from corners         median 0.000112   (a 6x separation)
    #
    #   threshold   corner samples caught   non-corner flagged
    #   0.0003      86%                     27%
    #   0.0004      74%                     19%   <- chosen
    #   0.0006      58%                     12%
    #
    # Many of the "false" positives are corner entry and exit, which genuinely
    # curve, so the real precision is better than the number suggests. Re-derive
    # this if the source ever changes its coordinate units, and bump
    # geometry_version when it moves.
    "straight_curvature": 0.0004,
    # Two curvature sign changes inside this distance is a chicane, in metres.
    "chicane_window_m": 150.0,
    # Speed at which the normal power envelope begins to taper (section 20.1).
    # Crossing it is a real strategic boundary, not an arbitrary split.
    "envelope_taper_kmh": 290.0,
    "target_segments": (30, 40),
}


@dataclass(frozen=True)
class Boundary:
    """One candidate segment boundary and where it came from."""

    distance_m: float
    source: str  # fia_line | corner | brake_onset | throttle_return | lap_start
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.distance_m:.0f}m ({self.source})"


@dataclass
class Segment:
    """One segment of the static map."""

    segment_id: int
    start_distance_m: float
    end_distance_m: float
    kind: str = "STRAIGHT"
    corner_id: int | None = None
    corner_type: str | None = None
    corner_phase: str | None = None
    sector: int | None = None
    zone: int | None = None
    track_heading_deg: float | None = None
    mean_curvature: float | None = None
    peak_curvature: float | None = None
    entry_speed_kmh: float | None = None
    apex_speed_kmh: float | None = None
    exit_speed_kmh: float | None = None
    boundary_sources: list[str] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return self.end_distance_m - self.start_distance_m

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "start_distance_m": round(self.start_distance_m, 1),
            "end_distance_m": round(self.end_distance_m, 1),
            "segment_length_m": round(self.length_m, 1),
            "kind": self.kind,
            "corner_id": self.corner_id,
            "corner_type": self.corner_type,
            "corner_phase": self.corner_phase,
            "sector": self.sector,
            "zone": self.zone,
            "track_heading_deg": None if self.track_heading_deg is None else round(self.track_heading_deg, 1),
            "mean_curvature": None if self.mean_curvature is None else round(self.mean_curvature, 6),
            "peak_curvature": None if self.peak_curvature is None else round(self.peak_curvature, 6),
            "entry_speed_kmh": None if self.entry_speed_kmh is None else round(self.entry_speed_kmh, 1),
            "apex_speed_kmh": None if self.apex_speed_kmh is None else round(self.apex_speed_kmh, 1),
            "exit_speed_kmh": None if self.exit_speed_kmh is None else round(self.exit_speed_kmh, 1),
            "boundary_sources": self.boundary_sources,
        }


# --------------------------------------------------------------------- helpers
def smooth(values: Sequence[float | None], window: int) -> list[float | None]:
    """Centred moving average, preserving length and tolerating None.

    Centred is correct here and nowhere near a live feature: this runs on a
    static circuit map derived offline, not on a per-lap causal signal.
    """
    if window <= 1:
        return list(values)
    half = window // 2
    out: list[float | None] = []
    for i in range(len(values)):
        chunk = [v for v in values[max(0, i - half): i + half + 1] if v is not None]
        out.append(sum(chunk) / len(chunk) if chunk else None)
    return out


def _median(values: Iterable[float]) -> float | None:
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    mid = len(data) // 2
    return data[mid] if len(data) % 2 else (data[mid - 1] + data[mid]) / 2.0


def median_profile(rows: Iterable[dict], spacing_m: float = 20.0,
                   channels: Sequence[str] = ("brake_on", "throttle_pct", "speed_kmh", "x_m", "y_m")) -> dict:
    """Collapse many laps into one median profile keyed by distance bin.

    This is the step that turns per-lap noise into a circuit property. A brake
    point that one driver takes 30 m early and another 30 m late becomes a single
    distance the field brakes at.
    """
    buckets: dict[int, dict[str, list[float]]] = {}
    for row in rows:
        distance = row.get("distance_m")
        if distance is None:
            continue
        key = int(round(float(distance) / spacing_m))
        slot = buckets.setdefault(key, {c: [] for c in channels})
        for channel in channels:
            value = row.get(channel)
            if value is not None:
                try:
                    slot[channel].append(float(value))
                except (TypeError, ValueError):
                    pass

    distances = sorted(buckets)
    profile: dict[str, list] = {"distance_m": [k * spacing_m for k in distances],
                                "lap_count": [len(buckets[k][channels[0]]) for k in distances]}
    for channel in channels:
        profile[channel] = [_median(buckets[k][channel]) for k in distances]
    return profile


# ---------------------------------------------------------------- detectors
def detect_brake_onsets(profile: dict, options: dict | None = None) -> list[Boundary]:
    """Distances where the median profile starts sustained braking.

    Requires the braking to persist for ``min_brake_run_m`` so that a brush of
    the pedal, or one driver's lift showing through the median, does not create
    a boundary.
    """
    opts = {**DEFAULTS, **(options or {})}
    distance = profile["distance_m"]
    brake = profile.get("brake_on") or []
    if not distance or not brake:
        return []
    step = (distance[1] - distance[0]) if len(distance) > 1 else 20.0
    need = max(1, int(round(opts["min_brake_run_m"] / step)))
    threshold = opts["brake_threshold"]

    out: list[Boundary] = []
    braking = False
    for i, value in enumerate(brake):
        on = value is not None and value >= threshold
        if on and not braking:
            run = sum(1 for v in brake[i:i + need] if v is not None and v >= threshold)
            if run >= need:
                out.append(Boundary(distance[i], "brake_onset", f"sustained {opts['min_brake_run_m']:.0f} m"))
                braking = True
        elif not on:
            braking = False
    return out


def detect_throttle_returns(profile: dict, options: dict | None = None) -> list[Boundary]:
    """Distances where the median profile regains sustained full throttle.

    These split long straights that would otherwise be a single segment, which
    matters because the shadow price varies along a straight as speed climbs
    into the power-envelope taper (AGENTS.md section 20.1).
    """
    opts = {**DEFAULTS, **(options or {})}
    distance = profile["distance_m"]
    throttle = profile.get("throttle_pct") or []
    if not distance or not throttle:
        return []
    step = (distance[1] - distance[0]) if len(distance) > 1 else 20.0
    need = max(1, int(round(opts["min_throttle_run_m"] / step)))
    threshold = opts["throttle_return_pct"]

    out: list[Boundary] = []
    at_power = False
    for i, value in enumerate(throttle):
        on = value is not None and value >= threshold
        if on and not at_power:
            run = sum(1 for v in throttle[i:i + need] if v is not None and v >= threshold)
            if run >= need:
                out.append(Boundary(distance[i], "throttle_return", f"sustained {opts['min_throttle_run_m']:.0f} m"))
                at_power = True
        elif not on:
            at_power = False
    return out


def detect_envelope_taper(profile: dict, options: dict | None = None) -> list[Boundary]:
    """Distances where the median profile crosses into the power-envelope taper.

    Long straights would otherwise be a single segment, which hides the thing
    this project exists to model: above roughly 290 km/h the normal deployment
    envelope tapers, so a megajoule spent past this point buys less time than the
    same megajoule spent before it (AGENTS.md section 20.1). Splitting here makes
    the shadow price resolvable along a straight instead of averaged across it.

    Only the upward crossing is a boundary. The downward one coincides with
    braking, which already has a detector.
    """
    opts = {**DEFAULTS, **(options or {})}
    distance = profile["distance_m"]
    speed = profile.get("speed_kmh") or []
    if not distance or not speed:
        return []
    taper = opts["envelope_taper_kmh"]

    out: list[Boundary] = []
    above = False
    for i, value in enumerate(speed):
        if value is None:
            continue
        if value >= taper and not above:
            out.append(Boundary(distance[i], "envelope_taper", f"median speed crosses {taper:.0f} km/h"))
            above = True
        elif value < taper:
            above = False
    return out


def merge_short_segments(boundaries: Sequence[Boundary], lap_length_m: float,
                         min_length_m: float, priority: Sequence[str] = ()) -> list[Boundary]:
    """Drop boundaries that would create a segment shorter than ``min_length_m``.

    When two boundaries are too close, the lower-priority one is dropped, so a
    cited FIA line is never removed in favour of a detected brake point.
    """
    ordered = sorted(boundaries, key=lambda b: b.distance_m)
    if not ordered:
        return []
    # lap_start is first and is never displaceable: dropping it would leave the
    # metres between the timing line and the next boundary in no segment at all.
    rank = {source: i for i, source in enumerate(
        priority or ("lap_start", "fia_line", "corner", "brake_onset", "throttle_return", "envelope_taper"))}

    kept: list[Boundary] = [ordered[0]]
    for candidate in ordered[1:]:
        if candidate.distance_m - kept[-1].distance_m >= min_length_m:
            kept.append(candidate)
            continue
        # Too close: keep whichever source is trusted more.
        if rank.get(candidate.source, 99) < rank.get(kept[-1].source, 99):
            kept[-1] = candidate
    # The wrap-around segment must also clear the minimum.
    while len(kept) > 1 and (lap_length_m - kept[-1].distance_m) < min_length_m:
        kept.pop()
    return kept


# ------------------------------------------------------------- classification
def track_heading_deg(x: Sequence[float | None], y: Sequence[float | None],
                      rotation_deg: float = 0.0, window: int = 5) -> list[float | None]:
    """Direction of travel along the smoothed centreline, degrees in [0, 360).

    Smoothed before differencing because raw x/y is noisy enough to swing the
    heading wildly between adjacent 20 m samples. ``rotation_deg`` is the
    circuit's ``Rotation`` from corners.json, which aligns the coordinate frame;
    M33 projects wind onto this, so a wrong frame silently corrupts the
    head/cross components.
    """
    xs = smooth(x, window)
    ys = smooth(y, window)
    out: list[float | None] = []
    for i in range(len(xs)):
        j = min(i + 1, len(xs) - 1)
        k = max(i - 1, 0)
        if None in (xs[j], xs[k], ys[j], ys[k]):
            out.append(None)
            continue
        dx = xs[j] - xs[k]
        dy = ys[j] - ys[k]
        if dx == 0 and dy == 0:
            out.append(out[-1] if out else None)
            continue
        out.append((math.degrees(math.atan2(dy, dx)) + rotation_deg) % 360.0)
    return out


def _curvature(x: Sequence[float | None], y: Sequence[float | None], window: int = 5) -> list[float | None]:
    """Signed curvature from three smoothed points. Positive is left."""
    xs = smooth(x, window)
    ys = smooth(y, window)
    out: list[float | None] = []
    for i in range(len(xs)):
        a, b, c = max(i - 2, 0), i, min(i + 2, len(xs) - 1)
        if None in (xs[a], xs[b], xs[c], ys[a], ys[b], ys[c]):
            out.append(None)
            continue
        x1, y1, x2, y2, x3, y3 = xs[a], ys[a], xs[b], ys[b], xs[c], ys[c]
        area2 = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
        d12 = math.hypot(x2 - x1, y2 - y1)
        d23 = math.hypot(x3 - x2, y3 - y2)
        d13 = math.hypot(x3 - x1, y3 - y1)
        denominator = d12 * d23 * d13
        out.append(0.0 if denominator == 0 else 2.0 * area2 / denominator)
    return out


def classify_corner_type(mean_curvature: float | None, apex_speed_kmh: float | None,
                         sign_changes: int = 0, options: dict | None = None) -> str:
    """Geometry-based corner class, not a free-text label (AGENTS.md section 12).

    Curvature decides *whether* it is a corner; apex speed decides *which kind*.
    That order matters: binning on speed alone would call a flat-out kink a
    straight at one circuit and a FAST corner at another.
    """
    opts = {**DEFAULTS, **(options or {})}
    if mean_curvature is None:
        return "STRAIGHT"
    magnitude = abs(mean_curvature)
    if magnitude < opts["straight_curvature"]:
        return "STRAIGHT"
    if sign_changes >= 2:
        base = "CHICANE"
    elif apex_speed_kmh is None:
        base = "MEDIUM"
    elif apex_speed_kmh < opts["hairpin_max_kmh"]:
        base = "HAIRPIN"
    elif apex_speed_kmh < opts["slow_max_kmh"]:
        base = "SLOW"
    elif apex_speed_kmh < opts["medium_max_kmh"]:
        base = "MEDIUM"
    else:
        base = "FAST"
    if base == "CHICANE":
        return base
    return f"{base}_{'LEFT' if mean_curvature > 0 else 'RIGHT'}"


def boundary_hash(boundaries: Sequence[float]) -> str:
    """Stable fingerprint of a boundary array.

    Every artifact records this alongside ``geometry_version``, so a lake built
    against an older map is detectable rather than silently misaligned.
    """
    payload = json.dumps([round(float(b), 2) for b in sorted(boundaries)], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------- assembly
def build_segments(profile: dict, lap_length_m: float, corners: Sequence[dict] = (),
                   fia_lines: Sequence[dict] = (), options: dict | None = None) -> list[Segment]:
    """Assemble the static segment map for one circuit.

    Parameters
    ----------
    profile:
        Output of :func:`median_profile` over the reference lap set.
    corners:
        ``corners.json`` records with ``Distance`` and ``CornerNumber``.
    fia_lines:
        Detection/Activation lines from CP-03, where sourced. Highest priority.
    """
    opts = {**DEFAULTS, **(options or {})}
    distance = profile["distance_m"]
    if not distance:
        return []

    candidates: list[Boundary] = [Boundary(0.0, "lap_start", "start/finish")]
    for line in fia_lines:
        value = line.get("distance_m")
        if value is not None:
            candidates.append(Boundary(float(value), "fia_line", str(line.get("kind", ""))))
    seen_corner_distance: set[float] = set()
    for corner in corners:
        value = corner.get("Distance")
        if value in (None, "None"):
            continue
        value = float(value)
        # The Hungaroring repeats distances; one marker is one boundary.
        if value in seen_corner_distance:
            continue
        seen_corner_distance.add(value)
        candidates.append(Boundary(value, "corner", f"corner {corner.get('CornerNumber')}"))
    candidates += detect_brake_onsets(profile, opts)
    candidates += detect_throttle_returns(profile, opts)
    candidates += detect_envelope_taper(profile, opts)

    kept = merge_short_segments(candidates, lap_length_m, opts["min_segment_length_m"])

    curvature = _curvature(profile.get("x_m") or [], profile.get("y_m") or [],
                           opts["heading_smooth_points"])
    heading = track_heading_deg(profile.get("x_m") or [], profile.get("y_m") or [],
                                rotation_deg=0.0, window=opts["heading_smooth_points"])
    speed = profile.get("speed_kmh") or []

    def window_indices(start: float, end: float) -> list[int]:
        return [i for i, d in enumerate(distance) if start <= d < end]

    segments: list[Segment] = []
    edges = [b.distance_m for b in kept] + [lap_length_m]
    corner_lookup = {float(c["Distance"]): c.get("CornerNumber")
                     for c in corners if c.get("Distance") not in (None, "None")}

    for index in range(len(kept)):
        start = edges[index]
        end = edges[index + 1]
        indices = window_indices(start, end)
        segment = Segment(
            segment_id=index + 1,
            start_distance_m=start,
            end_distance_m=end,
            boundary_sources=[kept[index].source],
        )
        if indices:
            speeds = [speed[i] for i in indices if i < len(speed) and speed[i] is not None]
            curves = [curvature[i] for i in indices if i < len(curvature) and curvature[i] is not None]
            headings = [heading[i] for i in indices if i < len(heading) and heading[i] is not None]
            if speeds:
                segment.entry_speed_kmh = speeds[0]
                segment.exit_speed_kmh = speeds[-1]
                segment.apex_speed_kmh = min(speeds)
            if curves:
                segment.mean_curvature = sum(curves) / len(curves)
                # Classify on the tightest point, not the average. A segment that
                # begins at a corner marker and runs on to a straight averages
                # almost flat, which labelled real corners STRAIGHT.
                segment.peak_curvature = max(curves, key=abs)
                signs = [1 if c > 0 else -1 for c in curves if abs(c) > opts["straight_curvature"]]
                changes = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
            else:
                changes = 0
            if headings:
                segment.track_heading_deg = headings[0]
            segment.corner_type = classify_corner_type(
                segment.peak_curvature if segment.peak_curvature is not None else segment.mean_curvature,
                segment.apex_speed_kmh, changes, opts)

        for corner_distance, number in corner_lookup.items():
            if start <= corner_distance < end:
                segment.corner_id = int(number) if number is not None else None
                break

        # kind follows what the segment does, not merely what bounded it.
        if segment.corner_id is not None or (segment.corner_type or "STRAIGHT") != "STRAIGHT":
            segment.kind = "CORNER"
        elif kept[index].source == "brake_onset":
            segment.kind = "BRAKING"
        elif kept[index].source == "throttle_return":
            segment.kind = "EXIT"
        elif kept[index].source == "envelope_taper":
            segment.kind = "STRAIGHT"
        else:
            segment.kind = "STRAIGHT"
        segments.append(segment)

    return segments
