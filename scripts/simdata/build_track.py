"""Assemble one track-model artifact for one event.

Which SESSION defines the circuit's geometry is chosen by MEASUREMENT, not by order.
The old rule was "the first session that yields >= 10 usable laps", which never compared
anything: it took the Race at every circuit, including Monaco -- whose Race carries a
position for 106 of its 1452 laps, spanning 5 lap numbers out of 78 -- and Hungary, whose
Race position channel is a stale-anchor sample-and-hold (median unique-(x,y) fraction
0.40, median sample-to-sample step 0.017 m against 6-8 m everywhere else).

So every candidate session is now scored on its own measured position channel and the
best one wins; see `score_session` for the components and `rank_geometry_sessions` for
the comparison. The scores, the margin and the reason are written into the artifact so
the choice is auditable rather than implicit.

Two things are deliberately NOT part of the score:
  * timingLines std. Hungary's S/F scatter gets WORSE (17.6 -> 48.5 m) on the ring that
    is geometrically CORRECT, because the shipped 17.6 m is tight scatter about a wrong
    answer on a ring stretched 6.4 %. Scoring on it would select the corrupt session.
  * ring length. `Ring.length` is now measured from the polyline itself, so it agrees
    with the geometry by construction on every session and discriminates nothing.

Usage: python -m simdata.build_track "British Grand Prix" out_dir
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simdata.glb_surface import (Fit, TriangleIndex, bake_surface, load_surface,
                                 registry_entry, surface_block)
from simdata.paths import ROOT as REPO_ROOT, data_root
from simdata.rawio import MIN_SAMPLES, LapTable, SentinelIndex, load_lap
from simdata.track import (build_ring, corner_stations, grid, pick_geometry_laps,
                            pit_lane, pit_lane_path, reference_speed_profile,
                            timing_lines, width_estimate)

# 2 adds the optional `surface` block: a real circuit model's drive surface baked onto
# this ring, for the circuits that have one. The bump is unconditional because a reader
# must be able to tell "this build could have carried a surface" from "this build predates
# the field"; a circuit without one is otherwise byte-for-byte what version 1 emitted.
SCHEMA_VERSION = 2

# Sessions that may define the circuit's geometry. Practice is deliberately NOT here:
# a Practice lap 1 is an out-lap, so admitting Practice would buy a geometry gain at the
# cost of everything else that is read from the same session.
CANDIDATE_SESSIONS = ("Race", "Qualifying")
# Sessions whose lap 1 is a standing start, i.e. sessions that physically HAVE a grid.
STANDING_START_SESSIONS = ("Race", "Sprint")

MIN_GEOMETRY_LAPS = 30   # the documented target for a well-sampled ring
MIN_RING_LAPS = 5        # below this a ring cannot be built at all
TARGET_DRIVERS = 10      # enough cars that one car's line cannot define the circuit

# ---------------------------------------------------------------------------
# Per-lap position gate.
#
# These are the discriminators rawio.Lap.position_quality() measures, with thresholds
# taken from the measured separation between a healthy session and a corrupt one:
#   uniqueFrac     Hungary Race 0.40 median  vs 0.994-1.000 at every other session
#   repeatBackFrac Hungary Race 0.46         vs 0.000-0.002 at every other session
#   medianStepM    Hungary Race 0.017 m      vs 4.3-8.0 m   at every other session
# pathOverSpan is kept only as a loose guard: 15.8 % of Hungary's corrupt laps land
# inside [0.97, 1.03] by chance, because a zigzag about a held anchor adds no length.
# endGapM guards the other failure mode -- build_ring welds the first sample to the last,
# so an unclosed lap silently adds a phantom straight (measured up to 1147.6 m).
GATE_XY_FRACTION = 0.95
GATE_UNIQUE_FRAC = 0.95
GATE_REPEAT_BACK_FRAC = 0.02
GATE_MEDIAN_STEP_M = 1.0
GATE_PATH_OVER_SPAN = (0.90, 1.15)
GATE_END_GAP_M = 50.0

# ---------------------------------------------------------------------------
# Session score weights. They sum to 1.0 and every term is a measured fraction in
# [0, 1], so a score reads directly as "how much of what this session would have to
# supply does it actually supply".
W_LAP_QUALITY = 0.40        # do the laps that would build the ring carry a real trace?
W_POSITION_COVERAGE = 0.20  # is the position channel live across the whole run?
W_POSITION_INTEGRITY = 0.20 # how much of that channel is the "position unknown" marker?
W_VOLUME = 0.15             # are there enough usable laps to median away jitter?
W_DRIVER_SPREAD = 0.05      # are they spread over enough cars?
# Two candidates inside this margin are a tie, and the earlier one in CANDIDATE_SESSIONS
# wins -- so a circuit whose sessions are equally good keeps the Race, which is also the
# session the grid, the pit lane and the replay are read from.
SCORE_TIE_MARGIN = 0.02

_COMPONENT_WEIGHTS = {
    "lapQuality": W_LAP_QUALITY,
    "positionCoverage": W_POSITION_COVERAGE,
    "positionIntegrity": W_POSITION_INTEGRITY,
    "volume": W_VOLUME,
    "driverSpread": W_DRIVER_SPREAD,
}

_SCAN_CACHE: dict[str, dict] = {}
_SCORE_CACHE: dict[str, dict] = {}
# Built rings, keyed by event. build_sim_data does track model then Race pack then
# Sprint pack for ONE event before moving on, so a very small cache turns three full
# ring builds per event into one. It is bounded because each entry pins 60 Lap objects
# (~25 MB), and holding all thirteen events would cost ~300 MB for nothing.
_PREPARE_CACHE: dict[str, tuple] = {}
_PREPARE_CACHE_MAX = 2


def slugify(event: str) -> str:
    return event.lower().replace(" ", "-")


def _f(v):
    """A float that is safe to serialise: non-finite becomes null, never a stand-in."""
    if v is None:
        return None
    v = float(v)
    return None if (math.isnan(v) or math.isinf(v)) else v


def _frac(num, den) -> float:
    return float(num) / float(den) if den else 0.0


# ---------------------------------------------------------------------------
# Session-level position measurement


def scan_session(session_dir: Path, max_laps: int | None = None) -> dict:
    """Read every lap of a session once: discover its sentinel and measure the channel.

    The "position unknown" sentinel is a property of the SESSION, not of a lap -- one car
    sitting still is a parked car, the same coordinate reused by many cars across many
    laps is the feed's marker for "we lost this car". So it is discovered here, once,
    over the whole session, and then applied to every lap the build touches.

    Scanning the whole session rather than a sample matters twice over: the sentinel's
    acceptance test in rawio is a SHARE of all stuck samples, which a biased sample would
    distort, and the coverage counts below are the only honest way to say that Monaco's
    Race has positions on 106 of 1452 laps. Measured cost 2.3-7.3 s per session, cached.

    It also runs the geometry position gate over every CLEAN lap the session offers,
    not over the handful a picker happens to survive. That is the load-bearing
    measurement: Hungary's Race fails the gate on 80 % of its clean laps while every
    other session fails on ~0 %, and scoring only the survivors would hide exactly that,
    because the picker upstream already discards them.

    Counts are taken AFTER the sentinel is withdrawn, which is the honest order: a lap
    whose only positions were the sentinel has no positions.
    """
    key = str(session_dir.resolve())
    hit = _SCAN_CACHE.get(key)
    if hit is not None:
        return hit

    table = LapTable(session_dir)
    rows = list(table.rows())
    clean_flags = table.clean_mask()
    if max_laps is not None:
        rows, clean_flags = rows[:max_laps], clean_flags[:max_laps]
    laps, clean = [], []
    for r, ok in zip(rows, clean_flags):
        lap = load_lap(session_dir, r["drv"], r["lap"])
        if lap is not None:
            laps.append(lap)
            clean.append(bool(ok))

    sentinel = SentinelIndex.from_laps(laps)
    finite_before = sum(int(np.isfinite(lap.x).sum()) for lap in laps)
    withdrawn = apply_sentinel(laps, sentinel)

    # Stub laps (a retirement leaves a handful of samples behind) are not laps and do not
    # belong in either side of the ratio.
    pool = [lap for lap, ok in zip(laps, clean) if ok and lap.n >= MIN_SAMPLES]
    rejects: dict[str, int] = {}
    n_usable = 0
    for lap in pool:
        why = position_gate_reason(lap.position_quality())
        if why is None:
            n_usable += 1
        else:
            rejects[why] = rejects.get(why, 0) + 1

    with_pos = [lap for lap in laps if lap.has_xy]
    out = {
        "session": session_dir.name,
        "laps": len(laps),
        "lapsWithPositions": len(with_pos),
        "lapNumbers": len({lap.lap for lap in laps}),
        "lapNumbersWithPositions": len({lap.lap for lap in with_pos}),
        "drivers": len({lap.driver for lap in laps}),
        "driversWithPositions": len({lap.driver for lap in with_pos}),
        "cleanLaps": len(pool),
        "cleanLapsUsable": n_usable,
        "lapQuality": _frac(n_usable, len(pool)),
        "gateRejects": dict(sorted(rejects.items())),
        "positionSamples": int(finite_before),
        "positionsWithdrawn": int(withdrawn),
        "withdrawnFraction": _frac(withdrawn, finite_before),
        "positionCoverage": _frac(len(with_pos), len(laps)),
        "sentinelPoints": [[float(px), float(py)] for px, py in sentinel.points],
        "sentinelCount": len(sentinel),
        "_sentinel": sentinel,
        "provenance": "OBSERVED (lap and sample counts) + DERIVED (sentinel discovery, "
                      "position gate)",
    }
    _SCAN_CACHE[key] = out
    return out


def session_sentinel(session_dir: Path) -> SentinelIndex:
    """The session's discovered "position unknown" coordinates, computed once.

    Public so that anything else loading laps from this session -- grid(), pit_lane() --
    can withdraw the same coordinates instead of re-detecting them per lap, where the
    share test that separates a SENTINEL from a stale hold cannot be evaluated.
    """
    return scan_session(session_dir)["_sentinel"]


def apply_sentinel(laps, sentinel: SentinelIndex) -> int:
    """Withdraw every sentinel position from `laps` in place; return the count withdrawn.

    Returns 0 and touches nothing when the session has no sentinel, which is the measured
    case at ten of the thirteen 2026 circuits.
    """
    if not sentinel:
        return 0
    total = 0
    for lap in laps:
        total += lap.drop_positions(sentinel.mask(lap.x, lap.y))
    return total


# ---------------------------------------------------------------------------
# Per-lap gate and per-session score


def position_gate_reason(q: dict) -> str | None:
    """Why this lap's positions may not help DEFINE the circuit, or None if they may.

    `q` is rawio.Lap.position_quality(), which returns measurements and no verdict --
    the verdict is a policy, and this is the geometry policy. A reason rather than a bool
    so a session can report WHAT it lost; a gate that silently shrinks its own sample is
    how Hungary shipped a ring stretched 6.4 %.
    """
    xyf = q.get("xyFraction")
    if xyf is None or xyf < GATE_XY_FRACTION:
        return "xy-fraction"
    uniq = q.get("uniqueFrac")
    if uniq is None or uniq < GATE_UNIQUE_FRAC:
        return "unique-frac"
    back = q.get("repeatBackFrac")
    if back is not None and back > GATE_REPEAT_BACK_FRAC:
        return "repeat-back"
    step = q.get("medianStepM")
    if step is None or step < GATE_MEDIAN_STEP_M:
        return "median-step"
    pos = q.get("pathOverSpan")
    if pos is None or not (GATE_PATH_OVER_SPAN[0] <= pos <= GATE_PATH_OVER_SPAN[1]):
        return "path-over-span"
    gap = q.get("endGapM")
    if gap is None or gap > GATE_END_GAP_M:
        return "end-gap"
    return None


def lap_position_usable(q: dict) -> bool:
    """True when nothing in position_gate_reason objects to this lap."""
    return position_gate_reason(q) is None


def _median(values):
    vals = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.median(vals)) if vals else None


def score_session(session_dir: Path, session_name: str | None = None) -> dict:
    """Measure one candidate session and reduce it to a score in [0, 1].

    Every component is a measured fraction and all of them are reported, so the artifact
    can say WHY a session won rather than only that it did.
    """
    key = str(session_dir.resolve())
    hit = _SCORE_CACHE.get(key)
    if hit is not None:
        return hit

    name = session_name or session_dir.name
    scan = scan_session(session_dir)
    table = LapTable(session_dir)
    laps = pick_geometry_laps(session_dir, table)
    withdrawn_here = apply_sentinel(laps, scan["_sentinel"])

    quality = [lap.position_quality() for lap in laps]
    n_usable = int(sum(lap_position_usable(q) for q in quality))
    drivers = {lap.driver for lap in laps}
    lap_numbers = {lap.lap for lap in laps}

    components = {
        # measured over the session's WHOLE clean-lap pool, not over the laps that
        # survived the picker -- see scan_session
        "lapQuality": float(scan["lapQuality"]),
        "positionCoverage": float(scan["positionCoverage"]),
        "positionIntegrity": 1.0 - float(scan["withdrawnFraction"]),
        "volume": min(1.0, _frac(n_usable, MIN_GEOMETRY_LAPS)),
        "driverSpread": min(1.0, _frac(len(drivers), TARGET_DRIVERS)),
    }
    score = sum(_COMPONENT_WEIGHTS[k] * v for k, v in components.items())

    out = {
        "session": name,
        "score": round(float(score), 4),
        "eligible": len(laps) >= MIN_RING_LAPS,
        "components": {k: round(float(v), 4) for k, v in components.items()},
        "measured": {
            "geometryLaps": len(laps),
            "geometryLapsUsable": n_usable,
            "geometryLapDrivers": len(drivers),
            "geometryLapNumbers": len(lap_numbers),
            "geometryPositionsWithdrawn": int(withdrawn_here),
            "meetsGeometryLapFloor": bool(n_usable >= MIN_GEOMETRY_LAPS),
            "cleanLaps": scan["cleanLaps"],
            "cleanLapsUsable": scan["cleanLapsUsable"],
            "gateRejects": scan["gateRejects"],
            "sessionLaps": scan["laps"],
            "sessionLapsWithPositions": scan["lapsWithPositions"],
            "sessionLapNumbers": scan["lapNumbers"],
            "sessionLapNumbersWithPositions": scan["lapNumbersWithPositions"],
            "positionSamples": scan["positionSamples"],
            "positionsWithdrawn": scan["positionsWithdrawn"],
            "withdrawnFraction": _f(round(scan["withdrawnFraction"], 6)),
            "sentinelPoints": scan["sentinelPoints"],
            "medianXyFraction": _f(_median(q["xyFraction"] for q in quality)),
            "medianUniqueFrac": _f(_median(q["uniqueFrac"] for q in quality)),
            "medianRepeatBackFrac": _f(_median(q["repeatBackFrac"] for q in quality)),
            "medianStepM": _f(_median(q["medianStepM"] for q in quality)),
            "medianPathOverSpan": _f(_median(q["pathOverSpan"] for q in quality)),
            "medianEndGapM": _f(_median(q["endGapM"] for q in quality)),
        },
        "provenance": "DERIVED (measured from this session's own position channel)",
    }
    _SCORE_CACHE[key] = out
    return out


_COMPONENT_REASON = {
    "lapQuality": ("the laps that would build the ring carry a real position trace "
                   "({a:.0%} usable against {b:.0%})"),
    "positionCoverage": ("its position channel is live across the run "
                         "({a:.1%} of laps against {b:.1%})"),
    "positionIntegrity": ("less of its position channel is the feed's "
                          "'position unknown' sentinel ({aw:.2%} withdrawn against "
                          "{bw:.2%})"),
    "volume": ("it supplies more usable geometry laps ({a:.0%} against {b:.0%} of "
               "target)"),
    "driverSpread": ("its laps are spread over more cars ({a:.0%} against {b:.0%} of "
                     "target)"),
}


def _explain(best: dict, runner: dict | None) -> str:
    if runner is None:
        return (f"{best['session']} is the only candidate session with at least "
                f"{MIN_RING_LAPS} usable geometry laps")
    gap = best["score"] - runner["score"]
    if gap <= SCORE_TIE_MARGIN:
        return (f"{best['session']} and {runner['session']} score within "
                f"{SCORE_TIE_MARGIN} of each other ({best['score']:.4f} vs "
                f"{runner['score']:.4f}); {best['session']} keeps it because the grid, "
                f"the pit lane and the replay are read from it")
    # Rank by CONTRIBUTION to the margin (weight x gap), not by the raw gap: the reason
    # has to name the term that actually moved the score.
    diffs = sorted(((_COMPONENT_WEIGHTS[k] * (best["components"][k]
                                              - runner["components"][k]), k)
                    for k in _COMPONENT_REASON), reverse=True)
    contribution, key = diffs[0]
    a, b = best["components"][key], runner["components"][key]
    detail = _COMPONENT_REASON[key].format(a=a, b=b, aw=1.0 - a, bw=1.0 - b)
    return (f"{best['session']} beats {runner['session']} by {gap:.4f} "
            f"(mainly {key}, {contribution:+.4f} of it): {detail}")


def rank_geometry_sessions(event_dir: Path,
                           candidates=CANDIDATE_SESSIONS) -> list[dict]:
    """Every candidate session that exists on disk, scored, best first.

    Scores inside SCORE_TIE_MARGIN are a tie and keep the order of `candidates`, so an
    event whose sessions measure equally well stays on the Race.
    """
    scored = []
    for pos, name in enumerate(candidates):
        sdir = event_dir / name
        if not sdir.exists():
            continue
        row = dict(score_session(sdir, name))
        row["_dir"] = sdir
        row["_order"] = pos
        scored.append(row)
    scored.sort(key=lambda r: (-r["score"], r["_order"]))
    # Promote the earlier candidate out of every near-tie rather than bucketing scores:
    # a leader and anything within SCORE_TIE_MARGIN of it are indistinguishable, and the
    # order of CANDIDATE_SESSIONS decides which of them is preferred.
    ranked, pool = [], list(scored)
    while pool:
        top = pool[0]["score"]
        tied = sorted((r for r in pool if r["score"] >= top - SCORE_TIE_MARGIN),
                      key=lambda r: r["_order"])
        winner = tied[0]
        ranked.append(winner)
        pool.remove(winner)
    for i, row in enumerate(ranked):
        row["rank"] = i + 1
    return ranked


def geometry_choice(event_dir: Path, candidates=CANDIDATE_SESSIONS) -> dict:
    """The ranked candidates plus the winner, its margin, and the reason it won."""
    ranked = rank_geometry_sessions(event_dir, candidates)
    eligible = [r for r in ranked if r["eligible"]]
    if not eligible:
        raise RuntimeError(f"no session with usable geometry under {event_dir}")
    best = eligible[0]
    runner = eligible[1] if len(eligible) > 1 else None
    margin = round(best["score"] - runner["score"], 4) if runner else None
    return {
        "chosen": best["session"],
        "chosenDir": best["_dir"],
        "runnerUp": runner["session"] if runner else None,
        # chosen minus runner-up. NEGATIVE means the two scored inside SCORE_TIE_MARGIN
        # and the order of CANDIDATE_SESSIONS, not the score, decided it.
        "margin": margin,
        "tieBreak": bool(margin is not None and margin <= SCORE_TIE_MARGIN),
        "reason": _explain(best, runner),
        "candidates": ranked,
        "provenance": "DERIVED (measured score per session; see candidates[].components)",
    }


def geometry_session(event_dir: Path) -> tuple[Path, str]:
    """The session whose position channel best defines this circuit's shape."""
    choice = geometry_choice(event_dir)
    return choice["chosenDir"], choice["chosen"]


def grid_session(event_dir: Path, fallback_dir: Path) -> tuple[Path, str]:
    """The session the STARTING GRID is read from.

    The grid is a property of a standing start, not of the ring, so it must not follow
    the geometry session: at Hungary and Monaco the geometry now comes from Qualifying,
    whose lap 1 is an out-lap and contains no grid at all. Where a Race exists the grid
    is read from the Race and projected onto whichever ring won.
    """
    for name in STANDING_START_SESSIONS:
        sdir = event_dir / name
        if sdir.exists():
            return sdir, name
    return fallback_dir, fallback_dir.name


# ---------------------------------------------------------------------------
# Ring assembly


def _build_from_session(event: str, sdir: Path):
    """Ring + timing lines from one session. Raises RuntimeError if it cannot be built."""
    table = LapTable(sdir)
    laps = pick_geometry_laps(sdir, table)
    if len(laps) < MIN_RING_LAPS:
        raise RuntimeError(f"only {len(laps)} usable geometry laps for {event}/{sdir.name}")
    # The sentinel is withdrawn BEFORE any geometry is derived: a coordinate the feed
    # never measured must not be allowed to bend the centreline.
    withdrawn = apply_sentinel(laps, session_sentinel(sdir))

    ring, _ = build_ring(laps)
    tl = timing_lines(sdir, table, ring, laps)
    if not tl["sf"]:
        raise RuntimeError(f"could not locate start/finish for {event}/{sdir.name}")
    ring = ring.rotated(tl["sf"]["station"])
    tl = timing_lines(sdir, table, ring, laps)  # re-measure in the new frame
    return ring, laps, tl, withdrawn


def prepare_ring(event: str):
    """Build the event's ring once, in its final (start/finish-rotated) frame.

    Shared by build_track_model (which serialises it) and the replay pack builder
    (which projects every car's telemetry onto this SAME ring, so the rendered track
    and the rendered cars never disagree about where station 0 is).

    Returns (ring, geometry_session_dir, session_name, laps, timing_lines).
    """
    hit = _PREPARE_CACHE.get(event)
    if hit is not None:
        return hit[:5]

    event_dir = data_root() / event
    choice = geometry_choice(event_dir)
    failures = []
    for cand in choice["candidates"]:
        if not cand["eligible"]:
            failures.append(f"{cand['session']}: fewer than {MIN_RING_LAPS} usable laps")
            continue
        sdir = cand["_dir"]
        try:
            ring, laps, tl, withdrawn = _build_from_session(event, sdir)
        except RuntimeError as exc:
            failures.append(f"{cand['session']}: {exc}")
            continue
        used = dict(choice)
        if cand["session"] != choice["chosen"]:
            reasons = "; ".join(failures)
            used["chosen"] = cand["session"]
            used["chosenDir"] = sdir
            used["margin"] = None
            used["reason"] = (f"{choice['reason']}; fell back to {cand['session']} "
                              f"because the higher-scoring session could not be "
                              f"built ({reasons})")
        used["geometryPositionsWithdrawn"] = withdrawn
        while len(_PREPARE_CACHE) >= _PREPARE_CACHE_MAX:
            _PREPARE_CACHE.pop(next(iter(_PREPARE_CACHE)))
        _PREPARE_CACHE[event] = (ring, sdir, cand["session"], laps, tl, used)
        return ring, sdir, cand["session"], laps, tl
    raise RuntimeError(f"no session with usable geometry under {event_dir}: "
                       + "; ".join(failures))


# ---------------------------------------------------------------------------
# Capabilities


GRID_TRUST_FRACTION = 0.6   # the share of the field above which the frontend trusts an
                            # order it was given (timeline.ts)
CAP_MIN_COVERAGE = 0.5      # positions on at least half the laps...
CAP_MIN_INTEGRITY = 0.75    # ...at most a quarter of them withdrawn as sentinel...
CAP_MIN_LAP_QUALITY = 0.5   # ...and at least half its clean laps clearing the gate


def _session_capability(row: dict) -> dict:
    """What one session's position channel can support. Measured, never assumed."""
    m, c = row["measured"], row["components"]
    return {
        "hasPositions": bool(c["positionCoverage"] >= CAP_MIN_COVERAGE
                             and c["positionIntegrity"] >= CAP_MIN_INTEGRITY
                             and c["lapQuality"] >= CAP_MIN_LAP_QUALITY),
        "positionCoverage": c["positionCoverage"],
        "positionIntegrity": c["positionIntegrity"],
        "lapQuality": c["lapQuality"],
        "lapsWithPositions": m["sessionLapsWithPositions"],
        "laps": m["sessionLaps"],
        "lapNumbersWithPositions": m["sessionLapNumbersWithPositions"],
        "lapNumbers": m["sessionLapNumbers"],
        "positionsWithdrawn": m["positionsWithdrawn"],
        "withdrawnFraction": m["withdrawnFraction"],
        "score": row["score"],
    }


def _capabilities(choice, grid_model, grid_field, grid_name, tl, corners, pit) -> dict:
    """What this artifact can honestly support, with the measurement behind each claim.

    `hasPositions` is deliberately a CONJUNCTION with `hasGrid`. A session whose grid is
    unrecoverable -- China, where every lap-1 first sample is the sentinel and 0 of 18
    cars can be placed; Monaco, 2 of 22 -- must not advertise a working position channel
    just because its on-track trace is clean. `hasTrackPositions` carries that narrower
    claim separately, so neither key overstates what was measured.
    """
    ordered = len(grid_model.get("order") or [])
    coverage = _frac(ordered, grid_field)
    has_grid = bool(grid_field and coverage >= GRID_TRUST_FRACTION)

    per_session = {row["session"]: _session_capability(row)
                   for row in choice["candidates"]}
    chosen = per_session.get(choice["chosen"], {})
    has_track_positions = bool(chosen.get("hasPositions"))

    notes = []
    if not has_grid:
        notes.append(f"grid order recovered for {ordered} of {grid_field} cars in "
                     f"{grid_name} ({coverage:.0%}), below the "
                     f"{GRID_TRUST_FRACTION:.0%} share the frontend trusts, so the "
                     f"starting order is unavailable rather than partial")
    for name, cap in per_session.items():
        if cap["hasPositions"]:
            continue
        why = []
        if cap["positionCoverage"] < CAP_MIN_COVERAGE:
            why.append(f"positions on only {cap['lapsWithPositions']} of "
                       f"{cap['laps']} laps, across "
                       f"{cap['lapNumbersWithPositions']} of {cap['lapNumbers']} "
                       f"lap numbers")
        if cap["positionIntegrity"] < CAP_MIN_INTEGRITY:
            why.append(f"{cap['withdrawnFraction']:.2%} of position samples withdrawn "
                       f"as the feed's sentinel")
        if cap["lapQuality"] < CAP_MIN_LAP_QUALITY:
            why.append(f"only {cap['lapQuality']:.1%} of its clean laps carry a "
                       f"position trace that passes the geometry gate")
        notes.append(f"{name}: no usable position channel -- " + "; ".join(why))
    return {
        "hasRing": True,
        "hasTimingLines": bool(tl.get("sf")),
        "hasSectorLines": bool(tl.get("s1") and tl.get("s2")),
        "hasCorners": bool(corners and corners.get("corners")),
        "hasPitLane": bool(pit.get("entryStation") is not None
                           and pit.get("mergeStation") is not None),
        "hasGrid": has_grid,
        "hasTrackPositions": has_track_positions,
        "hasPositions": bool(has_track_positions and has_grid),
        "grid": {
            "session": grid_name,
            "ordered": ordered,
            "fieldSize": grid_field,
            "coverage": round(coverage, 4),
            "trustFraction": GRID_TRUST_FRACTION,
        },
        "sessions": per_session,
        "notes": notes,
        "provenance": "DERIVED (every claim here is a measured fraction, never a default)",
    }


# ---------------------------------------------------------------------------
# Artifact


def _ring_emission_error(ring, x_cm, y_cm) -> float:
    """Metres between the declared lengthMetres and the polyline actually written.

    lengthMetres is what the frontend divides by n to get ds, so it has to describe the
    centimetre-quantised integers in the artifact and not a float array that was rounded
    away after anyone could check. Hungary shipped 4583.0 m for a 4308.5 m polyline.
    """
    x = np.asarray(x_cm, dtype=np.float64) / 100.0
    y = np.asarray(y_cm, dtype=np.float64) / 100.0
    seg = float(np.hypot(np.diff(x), np.diff(y)).sum())
    wrap = math.hypot(x[0] - x[-1], y[0] - y[-1])
    return abs((seg + wrap) - ring.length)


# ---------------------------------------------------------------------------
# The optional 3D drive surface
#
# A circuit with a real GLB model that PASSES the quality gate gains a `surface` sibling
# of `ring`: the model's road height, slope and camber sampled under every ring station.
# Every other circuit gains nothing and keeps the procedural ribbon, which is exactly
# today's behaviour -- "for available use the 3d environment, otherwise normal sim as it
# is working".
#
# So the gate is a REGISTRY ADMISSION TEST, not a build failure. env_sim.md Part A says
# the build should fail below the gate; that is deliberately overridden here, because a
# failing circuit already has a correct renderer path and stopping the whole build would
# cost the other twelve circuits their artifacts. There are four ways to decline, and all
# four degrade to the same place:
#
#   * no config/circuits.yaml entry            -- the eleven circuits with no model
#   * the entry's measured.gate is not "pass"  -- chinese-grand-prix, residual 0.31 m
#   * the asset is not on disk                 -- data/ is gitignored, so this is normal
#   * the bake raises, disagrees with the registry, or fails the gate on THIS build
#
# Each one logs the reason and returns None. None means the `surface` key is absent --
# absence, not a default, and not a seventh provenance word.

# The published-asset contract, frozen so the build, the publisher and the renderer could
# be written in parallel:
#     published path   frontend/public/sim/glb/<slug>.<sha10>.glb
#     served URL       /sim/glb/<slug>.<sha10>.glb
# <sha10> is the first 10 hex characters of the sha256 of the PUBLISHED bytes. Publishing
# is a byte-for-byte copy of the asset that was fitted and baked, so that is the same hash
# this module measures off the file it read -- and `_surface_for` refuses to emit a block
# when the file on disk is not the one the recorded transform was fitted to, which is what
# keeps the two halves of the contract from drifting apart. An asset-prep step that ever
# rewrites the bytes has to re-fit and re-record the registry; it cannot just republish.
GLB_URL_PREFIX = "/sim/glb"
GLB_SHA_CHARS = 10

# Arrays the frontend reads per station. They must be exactly as long as ring.xCm, in the
# same order, or a renderer would stand cars on the wrong part of the circuit.
SURFACE_STATION_ARRAYS = ("zCm", "slopePermille", "camberPermille", "validMask")


def glb_asset_url(slug: str, sha256: str) -> str:
    """The served URL for a published circuit asset. One definition of the name."""
    return f"{GLB_URL_PREFIX}/{slug}.{sha256[:GLB_SHA_CHARS]}.glb"


def _no_surface(slug: str, why: str) -> None:
    """Decline, out loud. The build continues; the circuit keeps its procedural ribbon."""
    print(f"  {slug}: no 3D surface ({why}); keeping the procedural ribbon", flush=True)
    return None


def _bake_surface_for(entry: dict, glb_path: Path, ring):
    """Read the asset and bake the drive surface under `ring`. The IO and the geometry.

    Split out from `_surface_for` so the admission decisions above it are testable
    without a 158 MB asset, and so a test can prove a declined circuit never reaches it.
    """
    surface = load_surface(glb_path)
    return bake_surface(TriangleIndex(surface), ring, Fit.from_dict(entry["fit"]))


def _surface_for(slug: str, ring) -> dict | None:
    """The `surface` block for `slug`, or None with a logged reason.

    `ring` must be the FINAL ring -- the one prepare_ring returns, already rotated so
    start/finish is station 0. Baking earlier and rotating afterwards would silently
    desynchronise: Ring.rotated rolls x, y and z and knows nothing about these arrays.
    """
    entry = registry_entry(slug)
    if not entry:
        return _no_surface(slug, "not in config/circuits.yaml")

    gate = (entry.get("measured") or {}).get("gate")
    if gate != "pass":
        return _no_surface(slug, f"registry gate is {gate!r}, not 'pass'")
    if not entry.get("fit"):
        return _no_surface(slug, "registry entry records no fitted transform")
    profile = entry.get("profile")
    if not profile:
        return _no_surface(slug, "registry entry names no profile")

    rel = entry.get("glb")
    glb_path = REPO_ROOT / rel if rel else None
    if glb_path is None or not glb_path.is_file():
        return _no_surface(slug, f"asset {rel or '(unnamed)'} is not on disk")

    try:
        bake = _bake_surface_for(entry, glb_path, ring)
    except Exception as exc:      # noqa: BLE001 - reported, never hidden, never fatal
        return _no_surface(slug, f"bake failed: {type(exc).__name__}: {exc}")

    # The recorded transform belongs to specific bytes. Different bytes, different model:
    # the fit would be meaningless and the published sha10 would name the wrong file.
    recorded = entry.get("sha256")
    if recorded and bake.sha256 != recorded:
        return _no_surface(slug, f"asset sha256 {bake.sha256[:10]} is not the fitted "
                                 f"{str(recorded)[:10]}")

    # The registry says this alignment passed when it was measured. Re-measuring it here
    # costs nothing extra -- the bake has already computed both numbers -- and it is the
    # difference between shipping a surface and shipping a stale claim about one.
    ok, fails = bake.gate()
    if not ok:
        return _no_surface(slug, "this build measures " + "; ".join(fails)
                           + ", although the registry records a pass")

    block = surface_block(bake, profile)
    wrong = [k for k in SURFACE_STATION_ARRAYS if len(block.get(k, ())) != ring.n]
    if wrong:
        return _no_surface(slug, f"baked arrays {wrong} are not {ring.n} stations long")

    # Per the frozen contract: which file to fetch, and the hash that names it.
    block["assetUrl"] = glb_asset_url(slug, bake.sha256)
    block["assetSha256"] = bake.sha256
    print(f"  {slug}: 3D surface from {glb_path.name} -- coverage {bake.coverage:.2%}, "
          f"residual std {bake.residual_std_m:.3f} m, {ring.n} stations", flush=True)
    return block


def build_track_model(event: str) -> dict:
    ring, sdir, session_name, laps, tl = prepare_ring(event)
    choice = _PREPARE_CACHE[event][5]
    table = LapTable(sdir)
    event_dir = data_root() / event

    corners = corner_stations(sdir, ring)
    pit = pit_lane(sdir, table, ring)
    pit_path = pit_lane_path(sdir, table, ring, pit)

    # The grid is read from the session that HAS one, projected onto the winning ring.
    gdir, gname = grid_session(event_dir, sdir)
    gtable = table if gdir == sdir else LapTable(gdir)
    grid_model = grid(gdir, gtable, ring, 0.0)
    grid_field = len({r["drv"] for r in gtable.rows() if r["lap"] == 1})

    width = width_estimate(sdir, table, ring, laps, corners, pit)
    profile = reference_speed_profile(ring, laps)

    curvature = ring.curvature(smooth_m=15.0)
    apex_idx = np.where(np.abs(curvature) > 1.0 / 250.0)[0]

    x_cm = [int(round(v * 100)) for v in ring.x]
    y_cm = [int(round(v * 100)) for v in ring.y]
    z_cm = [int(round(v * 100)) for v in ring.z]
    emission_error = _ring_emission_error(ring, x_cm, y_cm)
    tolerance = max(0.5, 2e-4 * ring.length)
    if emission_error > tolerance:
        raise RuntimeError(
            f"{event}: declared ring length {ring.length:.2f} m disagrees with the "
            f"emitted polyline by {emission_error:.2f} m (tolerance {tolerance:.2f} m)")

    scan = scan_session(sdir)
    capabilities = _capabilities(choice, grid_model, grid_field, gname, tl, corners, pit)
    # After the ring is final AND after the emission check: a ring the build is about to
    # reject must not first spend a minute reading a 158 MB model.
    surface = _surface_for(slugify(event), ring)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "event": event,
        "slug": slugify(event),
        "geometrySession": session_name,
        "gridSession": gname,
        "generatedFromLaps": len(laps),
        "geometrySessionChoice": {
            "chosen": choice["chosen"],
            "runnerUp": choice.get("runnerUp"),
            "margin": choice["margin"],
            "tieBreak": choice.get("tieBreak", False),
            "reason": choice["reason"],
            "tieMargin": SCORE_TIE_MARGIN,
            "weights": dict(_COMPONENT_WEIGHTS),
            "candidates": [{k: v for k, v in row.items() if not k.startswith("_")}
                           for row in choice["candidates"]],
            "provenance": choice["provenance"],
        },
        "positionIntegrity": {
            "geometrySession": session_name,
            "sentinelPoints": scan["sentinelPoints"],
            "sentinelCount": scan["sentinelCount"],
            "sessionPositionSamples": scan["positionSamples"],
            "sessionPositionsWithdrawn": scan["positionsWithdrawn"],
            "sessionWithdrawnFraction": _f(round(scan["withdrawnFraction"], 6)),
            "geometryLapPositionsWithdrawn": choice.get("geometryPositionsWithdrawn", 0),
            "ringEmissionErrorM": _f(round(emission_error, 3)),
            "provenance": ("DERIVED (positions withdrawn where the feed repeated a "
                           "discovered 'position unknown' coordinate; a withdrawn "
                           "sample is ABSENT, never replaced by an estimate)"),
        },
        "capabilities": capabilities,
        "ring": {
            # dsMetres is DERIVED FROM the shipped lengthMetres, not from ring.ds.
            # lengthMetres is rounded for legibility and the frontend recomputes
            # ds = lengthMetres / n (manifest.ts, and four render sites), so shipping the
            # unrounded ring.ds made the two disagree by up to a rounding step. Every
            # station-to-vertex lookup in the renderer uses that quotient, so the shipped
            # pair must be exactly self-consistent; deriving it here makes that true by
            # construction rather than by luck.
            "lengthMetres": round(ring.length, 2),
            "dsMetres": round(ring.length, 2) / ring.n,
            # centimetre-quantised integers: far more compact than float text and it
            # compresses better besides. Heading is recomputed at runtime from x/y
            # (atan2 of the forward difference) rather than shipped.
            "xCm": x_cm,
            "yCm": y_cm,
            "zCm": z_cm,
        },
        # A sibling of `ring`, present only for a circuit whose real model is registered,
        # on disk and passing the gate. Same stations, same order, same cm quantisation.
        # When it is absent the artifact is what version 1 emitted, key for key.
        **({"surface": surface} if surface else {}),
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
            # DERIVED, never OBSERVED: no car measured these heights. They are a
            # third-party model's geometry read under a transform fitted to the OBSERVED
            # ring, and AGENTS.md 13.6 forbids calling that OBSERVED merely because its
            # inputs were. A station the raycast missed is null, not a default.
            **({"surface": surface["provenance"]} if surface else {}),
            "geometrySession": ("DERIVED (highest measured position-quality score of "
                                + ", ".join(CANDIDATE_SESSIONS) + ")"),
            "gridSession": "OBSERVED (the session that has a standing start)",
            "timingLines": "DERIVED (sector-timestamp positions, median over clean laps)",
            "corners": "DERIVED (marker X/Y projected onto ring); numbering OBSERVED",
            "pitLane": pit["provenance"],
            "grid": grid_model["provenance"],
            "width": width["provenance"],
            "referenceProfile": profile["provenance"],
            "capabilities": capabilities["provenance"],
        },
    }


def write_artifact(model: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    from build_sim_data import _json_safe  # one definition of the rule
    payload = json.dumps(_json_safe(model), separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
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
    for row in model["geometrySessionChoice"]["candidates"]:
        parts = "  ".join(f"{k}={v:.3f}" for k, v in row["components"].items())
        print(f"    {row['session']:<12} score={row['score']:.4f}  {parts}")
    print(f"    -> {model['geometrySessionChoice']['reason']}")


if __name__ == "__main__":
    main()
