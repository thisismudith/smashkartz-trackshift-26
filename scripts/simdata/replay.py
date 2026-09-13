"""Build a REPLAY pack for one event/session: a binary sample blob plus a JSON
manifest, both consumed by the frontend replay adapter.

Binary format (little-endian), one variable-length run of samples per (driver, lap),
offsets recorded in the manifest so a lap can be fetched independently:

    u16  dtMs        milliseconds since the previous sample (0 for the first sample
                      of a lap); the decoder reconstructs absolute lap-relative time
                      by a running sum
    f32  stationM     arc-length position on the SAME ring the track model renders,
                      so cars never disagree with the track surface.
                      NaN means POSITION UNAVAILABLE -- see "absence" below.
    i16  lateralCm    signed lateral offset from the centreline, centimetres.
                      -32768 (INT16_MIN) means POSITION UNAVAILABLE.
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
  frame A   station/lateral from projecting the lap's own x/y onto the ring, with
            ring.project_path so a self-crossing circuit (Suzuka) cannot alias onto
            the wrong branch. Provenance OBSERVED.
  frame B   the lap has no usable position channel: station is the telemetry distance
            channel scaled onto the ring, lateral 0 (the centreline is the honest
            best estimate when only distance is known). Provenance DERIVED.
  frame NONE  the lap has neither a usable position channel nor a usable distance
            channel. Every sample's position is ABSENT. The speed/gear/throttle/time
            channels are still emitted, because those are measured and fine.

ABSENCE, and why it is not a number
-----------------------------------
This encoder used to turn an unusable projection into a plausible one twice over:
`np.nan_to_num(station, nan=0.0)` put an unprojectable car exactly on the start/finish
line, and `np.clip(lateral, -327, 327)` turned a 1.2 km projection error into a
327 m one that the renderer then drew faithfully. Both are the hidden-fallback pattern
AGENTS.md 42.5 forbids. An unusable position is now ABSENT: stationM NaN and
lateralCm INT16_MIN, counted per lap in the manifest as `positionAbsentSamples` and
per session in `positionIntegrity`. The frontend must skip such a sample, never draw
it. Nothing here invents a coordinate.
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
from simdata.paths import data_root
from simdata.rawio import LapTable, SentinelIndex, load_lap
from simdata.rcm import build_rcm_feed, neutralisation_intervals
from simdata.twin import TwinParams, estimate_ers, lap_summary


def _num(v):
    """The raw feed writes the string \"None\" for a missing number."""
    return None if v is None or isinstance(v, str) else float(v)
SCHEMA_VERSION = 1
SAMPLE_STRUCT = struct.Struct("<HfhHBB")  # dtMs, stationM, lateralCm, speedKph, gearBrake, throttle

# ---------------------------------------------------------------------------
# Position-encoding contract.
#
# LATERAL_ABS_MAX_M is a FIXED, documented bound, deliberately NOT derived from
# pitLane.loopLateral/exitLateral: those are themselves computed from the last raw
# sample of an in-lap, which at Hungary and China IS the frozen sentinel, so they read
# 512.24 m and 557.07 m -- a threshold built on them lands ABOVE the corruption it is
# meant to catch and rejects nothing. The bound has to clear the widest LEGITIMATE pit
# lane instead. Measured |lateral| over all 15 built Race/Sprint packs:
#   Silverstone  max 104.33 m (Race) / 103.90 (Sprint), 1612 samples beyond 60 m --
#                the widest honest pit lane in the set; track.py:433 records the same
#                fact in the codebase's own words ("the lane runs >60 m from the
#                centreline at the same station")
#   Zandvoort    max  90.21 m,  Barcelona max 63.66 m,  Canada max 50.69 m
#   Spa 202.80 m and Monza 154.85 m are ONE SAMPLE EACH -- outliers, not pit lanes
#   Hungary 950.21 m, China 1067.93 m, Miami 1274.90 m, Monaco 1227.24 m are the
#                unplaceable ones this bound exists for
# 120 m therefore rejects 0 samples on every healthy circuit (Silverstone's 104.33 m
# clears it by 16 m) and 2 single-sample outliers at Spa and Monza, while catching all
# 23,548 samples the old +-327 m clip used to draw as if they were positions.
LATERAL_ABS_MAX_M = 120.0
# INT16_MIN is unambiguous: the encodable lateral range is +-32767 cm, so -32768 can
# never be a real measurement. LATERAL_ABS_MAX_M * 100 = 12000 cm is comfortably inside
# int16, so an accepted lateral never needs clamping -- the bound does the rejecting,
# openly, instead of a clip doing it silently.
LATERAL_ABSENT_CM = -32768

# Frame A needs positions that actually trace a lap. `xy_fraction` alone does not test
# that: at Monaco, laps 42-45 of every driver have a numerically collapsed x channel
# (x runs -2.50 down to -1.0e-61) with xy_fraction 1.00, so they passed the old gate,
# projected to one fixed point 35 m off the centreline, and shipped 22 laps of PARKED
# cars tagged OBSERVED. Path length over integrated-distance span is the measured test
# that separates them: ~0.0002 there against ~1.00 on a real lap. The band matches
# rawio.geometry_valid()'s, so the pipeline has one definition of "these positions
# describe a lap" rather than two.
#
# Measured cost of the band over all 15 Race/Sprint packs. Below 0.5 (collapsed
# positions): 33 laps -- Monaco 22, Canada 3, Austria 2, Barcelona 2, Hungary 2,
# Zandvoort 1, Miami 1. Above 1.5 (jitter around a stale anchor inflating the path):
# 97 laps -- Hungary 92, Monaco 3, Miami 2. Every other session's whole field sits
# inside 0.60..1.46, so no healthy circuit loses a single lap to this gate.
FRAME_A_MIN_XY = 0.5
FRAME_A_MIN_PATH_RATIO = 0.5
FRAME_A_MAX_PATH_RATIO = 1.5

# Frame B: the distance channel is per-driver integrated wheel speed, so its metres are
# within a few tenths of a percent of ring metres but not exactly ring metres. Dividing
# by the lap's OWN span calibrates that out -- but ONLY if the lap really is one full
# lap. Applied to a partial lap it is a rubber sheet: Monaco's shortest frame-B lap
# spans 1482.9 m and was stretched 2.2167x to fill the ring, i.e. the encoder invented
# 1804 m of motion the car never made. A lap is taken as a full lap only inside this
# band; outside it the session's median full-lap scale is used and the lap STAYS
# PARTIAL, ending where the car's distance channel ended.
FRAME_B_FULL_LAP_BAND = (0.95, 1.05)


def _round_quality(q):
    """position_quality() rounded for the wire. Every value stays a measurement or
    None; nothing is filled in."""
    if not q:
        return None
    return {k: (None if v is None else round(float(v), 5)) for k, v in q.items()}


def _session_distance_scale(laps, length: float):
    """Median ring-metres-per-distance-metre, measured over this session's full laps.

    Returns (scale, source). `source` is DERIVED when at least one lap in the session
    was long enough to calibrate against, and UNCALIBRATED when none was -- in which
    case the distance channel's own metres are used unscaled, which is an honest
    identity rather than a fabricated correction.
    """
    lo, hi = FRAME_B_FULL_LAP_BAND
    ks = []
    for lap in laps:
        span = lap.distance_span()
        if lo * length <= span <= hi * length:
            ks.append(length / span)
    if not ks:
        return 1.0, "UNCALIBRATED"
    return float(np.median(ks)), "DERIVED"


def _lap_distance_scale(span: float, length: float, session_scale, session_source):
    lo, hi = FRAME_B_FULL_LAP_BAND
    if lo * length <= span <= hi * length:
        # this lap is itself a full lap, so it calibrates itself
        return length / span, "DERIVED"
    return session_scale, session_source


def encode_lap(ring, lap, session_scale: float = 1.0,
               session_scale_source: str = "UNCALIBRATED"):
    """Pack one lap into the sample blob.

    Returns (bytes, report). `report` is a per-lap measurement of what the encoder
    could and could not honestly place:
        frame               "A" | "B" | "NONE"
        provenance          "OBSERVED" | "DERIVED" | None
        absent              samples with no position at all
        lateralRejected     samples whose projected lateral exceeded the bound
        distanceScale       ring metres per distance metre, frame B only
        distanceScaleSource "DERIVED" | "UNCALIBRATED", frame B only
        quality             rawio.Lap.position_quality() -- the measurements the frame
                            decision was made from, forwarded so the decision is
                            auditable from the artifact instead of only here
    """
    n = lap.n
    report = {"frame": "NONE", "provenance": None, "absent": 0,
              "lateralRejected": 0, "distanceScale": None,
              "distanceScaleSource": None, "quality": None}
    if n == 0:
        return b"", report

    quality = lap.position_quality()
    report["quality"] = quality
    ratio = quality["pathOverSpan"]
    positions_trace_a_lap = (
        quality["xyFraction"] >= FRAME_A_MIN_XY
        and (ratio is None or FRAME_A_MIN_PATH_RATIO <= ratio <= FRAME_A_MAX_PATH_RATIO)
    )

    if positions_trace_a_lap:
        # project_path, not project: `project` is a stateless nearest-point query and
        # Suzuka's figure-8 crossover brings two branches 0.37 m apart while 2359 m
        # apart in station, so a stateless query flips between them (measured on ALB
        # lap 51: 2516.3 -> 4881.5 -> 2525.7 m in two samples). A lap trace is
        # time-ordered, so the continuity-aware projection is the correct one.
        station, lateral = ring.project_path(lap.x, lap.y)
        frame, provenance = "A", "OBSERVED"
        # A projection this far off the centreline is not a position, it is a failure
        # to place the car. Reject it -- do NOT clip it to something plausible.
        too_far = np.isfinite(lateral) & (np.abs(lateral) > LATERAL_ABS_MAX_M)
        report["lateralRejected"] = int(too_far.sum())
        if too_far.any():
            station = np.where(too_far, np.nan, station)
            lateral = np.where(too_far, np.nan, lateral)
    else:
        d = np.asarray(lap.dist, dtype=np.float64)
        finite_d = np.isfinite(d)
        span = lap.distance_span()
        if finite_d.sum() >= 2 and span > 0:
            scale, source = _lap_distance_scale(span, ring.length,
                                                session_scale, session_scale_source)
            # `d` is already referenced to the start/finish line (measured: d == 0 sits
            # at ring station -0.13 m at Monaco), so it is used AS the origin. The old
            # `(d - d[0])` pinned the lap's first sample to station 0 instead, which at
            # Monaco threw every frame-B lap a median 7.8 m and up to 79.3 m forward
            # because the trace starts BEFORE the line.
            station = d * scale
            station[~finite_d] = np.nan
            # Lateral is genuinely unknown in frame B. 0 (the centreline) is the honest
            # estimate given only a distance, and the frame tag says so; synthesising a
            # lateral would be worse.
            lateral = np.zeros(n)
            frame, provenance = "B", "DERIVED"
            report["distanceScale"] = float(scale)
            report["distanceScaleSource"] = source
        else:
            # No usable position channel AND no usable distance channel. There is
            # nothing to place this car with. The old code ran
            # `np.linspace(0, ring.length, n)` here, fabricating one complete lap of
            # motion out of nothing -- the exact pattern AGENTS.md 42.5 forbids.
            station = np.full(n, np.nan)
            lateral = np.full(n, np.nan)
            frame, provenance = "NONE", None

    absent = ~np.isfinite(station) | ~np.isfinite(lateral)
    report["frame"] = frame
    report["provenance"] = provenance
    report["absent"] = int(absent.sum())
    if report["absent"] == n:
        # every sample unplaceable: the frame tag would be a claim the data cannot back
        report["frame"] = "NONE"
        report["provenance"] = None

    with np.errstate(invalid="ignore"):
        station = np.mod(station, ring.length)   # NaN survives np.mod unchanged
    lateral_cm = np.where(
        absent, LATERAL_ABSENT_CM,
        np.round(np.nan_to_num(lateral, nan=0.0) * 100.0),
    ).astype(np.int32)
    station = np.where(absent, np.nan, station)

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
            int(dt_ms[i]), float(station[i]), int(lateral_cm[i]),
            int(round(speed[i])), int(gear_brake[i]), int(throttle[i]),
        )
        off += SAMPLE_STRUCT.size
    return bytes(buf), report


def build_replay_pack(event: str, session: str, *, final_mode: bool = False):
    """Build the development visual replay pack.

    The visual encoder is not the shared Chain-V route/model path. Keep that
    distinction explicit: a caller asking for final replay evidence is refused
    until a complete, proxy-free strategic bundle is wired through the same
    public C3/C4/C5 path.
    """
    if final_mode:
        raise ValueError(
            "final replay is blocked: this encoder is development-only and is "
            "not the shared proxy-free Chain-V route/model path"
        )
    ring, geom_dir, geom_session, _, _ = prepare_ring(event)
    sdir = data_root() / event / session
    table = LapTable(sdir)
    rows = list(table.rows())

    # -- pass 1: load the session, then discover its "position unknown" sentinels ----
    # The feed does not write null when the positioning system loses a car, it writes a
    # CONSTANT COORDINATE while speed/gear/throttle/distance keep running (rawio's
    # module docstring has the measurements). Whether a repeated point is a sentinel or
    # a parked car can only be decided with the WHOLE session in hand, which is why the
    # index is built here and applied per lap rather than inside Lap.__init__.
    loaded = []
    for r in rows:
        lap = load_lap(sdir, r["drv"], r["lap"])
        if lap is None or lap.n == 0:
            continue
        loaded.append((r, lap))
    sentinels = SentinelIndex.from_laps(lap for _, lap in loaded)
    session_scale, session_scale_source = _session_distance_scale(
        [lap for _, lap in loaded], ring.length)

    blob = bytearray()
    drivers = {}
    soc_carry: dict[str, float] = {}   # charge carried lap to lap, per driver
    totals = {"samples": 0, "absent": 0, "lateralRejected": 0,
              "sentinelMasked": 0, "A": 0, "B": 0, "NONE": 0}
    for r, lap in loaded:
        drv, lap_no = r["drv"], r["lap"]

        # Energy twin (MODELS.md M14). Computed HERE, in Python, and shipped as a
        # per-lap summary: the browser renders these numbers and never derives them.
        # The same pure functions will back the HTTP API later -- only the transport
        # differs. Everything produced is INFERRED/SIMULATED and carries its warnings.
        #
        # Run BEFORE the sentinel withdrawal below, because drop_positions blanks z as
        # well as x/y and the twin's only use of z is the road gradient. A frozen
        # sentinel z is the elevation the feed reported and is what the twin has always
        # been fed; withdrawing it here would silently change every ERS number on every
        # circuit, which is a twin-owner decision, not an encoder one.
        energy = None
        try:
            est = estimate_ers(
                # `lap.z` is ALREADY metres: rawio.Lap applies DM_TO_M when it reads the
                # decimetre feed. An extra /10 here was dividing elevation a second time
                # and handing the twin decametres, which shrank every road gradient by
                # 10x (measured on HAM British GP lap 10: gradient power p99 1.17 kW
                # against the correct 11.73 kW, over an elevation span of 1.15 m where
                # Silverstone actually climbs 11.45 m).
                lap.t, lap.speed, lap.dist, lap.z, lap.brake, lap.throttle,
                TwinParams(),
                air_temp_c=_num(r.get("wAT")), pressure_hpa=_num(r.get("wP")),
                initial_soc_mj=soc_carry.get(drv, 2.0),
                # The feed HAS rpm, so the twin gets its real ICE power map rather than
                # degrading to flat rated power. Measured on British GP laps: the
                # rpm-less path happens to BALANCE better (HAM lap 15 residual -0.31 vs
                # -2.41 MJ) because flat rated power over-credits the ICE at low rpm by
                # ~9.5 %. Picking it for the tidier number would be fitting the model to
                # the answer, so the better-physics path is used and the residual it
                # exposes is reported through the lap's warnings instead.
                rpm=lap.rpm,
            )
            energy = lap_summary(est)
            soc_carry[drv] = energy["socEndMj"]
        except (ValueError, IndexError, ZeroDivisionError):
            energy = None

        if sentinels:
            totals["sentinelMasked"] += lap.drop_positions(sentinels.mask(lap.x, lap.y))
        sample_bytes, pos = encode_lap(ring, lap, session_scale, session_scale_source)

        totals["samples"] += lap.n
        totals["absent"] += pos["absent"]
        totals["lateralRejected"] += pos["lateralRejected"]
        totals[pos["frame"]] += 1

        offset = len(blob)
        blob.extend(sample_bytes)
        entry = {
            "lap": lap_no, "byteOffset": offset, "sampleCount": lap.n,
            "positionFrame": pos["frame"],
            # The frame tag alone is not enough for the frontend to be honest about
            # what it draws: a frame-A lap can still have holes. Both travel per lap.
            "positionProvenance": pos["provenance"],
            "positionAbsentSamples": pos["absent"],
            # rawio.Lap.position_dropped: how many of this lap's samples the session
            # sentinel detector withdrew. Distinct from positionAbsentSamples, which
            # also counts a projection rejected by the lateral bound and every sample
            # of a frame-NONE lap.
            "positionDropped": lap.position_dropped,
            "positionQuality": _round_quality(pos["quality"]),
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
        if pos["distanceScale"] is not None:
            entry["distanceScale"] = round(pos["distanceScale"], 6)
            entry["distanceScaleSource"] = pos["distanceScaleSource"]
        drivers.setdefault(drv, {"driver": drv, "team": r["team"], "laps": []})
        drivers[drv]["laps"].append(entry)

    rcm_feed = build_rcm_feed(sdir, table)
    neutral = neutralisation_intervals(rcm_feed) if rcm_feed else []

    weather_path = sdir / "weather.json"
    weather = None
    if weather_path.exists():
        w = json.loads(weather_path.read_text(encoding="utf-8"))
        weather = {k: w[k] for k in ("wT", "wAT", "wTT", "wH", "wR", "wWS", "wWD") if k in w}

    samples = totals["samples"] or 1
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
        # How much of this pack's position channel is real, stated in numbers so the UI
        # can show the loss instead of the loss being invisible.
        "positionIntegrity": {
            "absentStationM": "NaN",
            "absentLateralCm": LATERAL_ABSENT_CM,
            "lateralBoundM": LATERAL_ABS_MAX_M,
            "samples": totals["samples"],
            "samplesPositionAbsent": totals["absent"],
            "samplesPositionAbsentPct": round(100.0 * totals["absent"] / samples, 4),
            "samplesLateralRejected": totals["lateralRejected"],
            "samplesSentinelWithdrawn": totals["sentinelMasked"],
            "sentinelPoints": [[round(float(px), 2), round(float(py), 2)]
                               for px, py in sentinels.points],
            "lapsFrameA": totals["A"],
            "lapsFrameB": totals["B"],
            "lapsFrameNone": totals["NONE"],
            "distanceScale": round(session_scale, 6),
            "distanceScaleSource": session_scale_source,
            "provenance": "DERIVED (measured per session; see replay.py)",
        },
        "capabilities": {
            "positionSamplesDropped": totals["sentinelMasked"],
            "hasPositions": totals["A"] > 0,
            "hasDerivedPositions": totals["B"] > 0,
            "hasPositionGaps": totals["absent"] > 0,
            "hasDriverAhead": True,
        },
        "provenance": {
            "positions": "per lap: positionFrame 'A' -> OBSERVED (telemetry x/y "
                        "projected onto the ring); 'B' -> DERIVED (distance channel "
                        "scaled onto the ring, lateral 0, no measured position); "
                        "'NONE' -> unavailable. A sample with stationM NaN / "
                        "lateralCm -32768 has NO position and must not be drawn.",
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

    integrity = manifest["positionIntegrity"]
    dt = time.time() - t0
    n_samples = len(blob) // SAMPLE_STRUCT.size
    print(f"{args.event}/{args.session}: {blob_name} ({len(blob)/1024:.1f} KB) + "
          f"{man_name} ({len(payload)/1024:.1f} KB)  {n_samples} samples  "
          f"built in {dt:.1f}s\n"
          f"  frames A/B/NONE = {integrity['lapsFrameA']}/{integrity['lapsFrameB']}/"
          f"{integrity['lapsFrameNone']}  "
          f"position absent {integrity['samplesPositionAbsent']} "
          f"({integrity['samplesPositionAbsentPct']}%)  "
          f"sentinel-withdrawn {integrity['samplesSentinelWithdrawn']}")


if __name__ == "__main__":
    main()
