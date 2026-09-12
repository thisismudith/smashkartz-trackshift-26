"""The STANDING START, as pure functions, fitted from the real 2026 race starts.

Why this module exists
----------------------
The New Race engine had no standing start at all. Measured across all 13 circuits, at
t = 0 every generated car sat at stationM EXACTLY 0 (station spread 0.00 m) already
doing 138.9 kph (Belgian) to 247.2 kph (Australian), and the cars stayed physically
interpenetrating for a minute: 57 of 210 overlapping pairs at t = 0 (Australian), 60 of
231 (Monaco / Chinese / Italian), 36-60 at t = 10 s, 9-53 at t = 30 s, 0-7 at t = 60 s,
clean only from t = 120 s. Consecutive station gaps were 0.00-0.34 m against a 5.6 m car.

What is measured here, and what is not
-------------------------------------
  MEASURED (DERIVED)   grid slot pitch; the anchor (where pole sits relative to the
                       timing line, per circuit); per-car reaction time and launch
                       acceleration; the lap-1 time penalty and how it varies down the
                       grid; the grid-to-lap-1 position change and its regression to
                       the mean.
  NOT IN THE FEED      the lateral stagger of the two grid columns. Stationary cars are
                       snapped to the centreline -- measured here, the median per-session
                       lateral SD about the grid's own axis is 0.19 m over 208 cars, and
                       scripts/simdata/track.py reads 0.07 m about its ring -- so the feed
                       carries no metric offset at all. The alternating-column STRUCTURE
                       is a regulation fact and is tagged RULE; the metric offset ships as
                       null, because absence is null.

Everything in the "pure kinematics" and "pure estimators" sections is a function of its
arguments only: no file reads, no module state, no clock, no RNG. File access lives in
the clearly-marked collector section below, which is the only part that knows the raw
mirror exists. The browser renders what these functions return; it does not compute them.

Units: metres, seconds, m/s inside; the raw feed's km/h is converted at the boundary.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# so `python scripts/simdata/launch.py` works the same way as every other module here
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simdata.fit_pace import _ols_hc1, leaf
from simdata.laptable import build_tidy_table, clean_mask
from simdata.paths import data_root, relative_root
from simdata.rawio import LapTable, load_lap
from simdata.track import GRID_DUP_STATION_M

# --------------------------------------------------------------------------------------
# Measurement policy. Each constant below is a decision about the SAMPLE, never about the
# answer: none of them thresholds the fitted quantity itself.
# --------------------------------------------------------------------------------------

# The rise is fitted only while a constant-acceleration law is measurably true. 120 km/h
# is where the launch still sits on one straight everywhere (even Monaco, whose first
# corner is the closest on the calendar) and the fit is excellent: median R^2 0.994 over
# the 234 accepted launches, 5th percentile 0.971. Above it the cars are already braking
# for turn 1 at the short circuits, so a higher ceiling would fit a brake, not a launch.
LAUNCH_FIT_CEILING_KPH = 120.0
LAUNCH_MIN_SAMPLES = 5           # fewer cannot separate a slope from an intercept
LAUNCH_MIN_SPAN_S = 0.8          # the feed samples at ~7.7 Hz; shorter is quantisation
LAUNCH_MIN_END_SPEED_MPS = 15.0  # the window must actually reach racing speed
LAUNCH_MIN_R2 = 0.90             # below this it is not a constant-acceleration launch
LAUNCH_SPEED_DROP_KPH = 3.0      # dip tolerated inside the rise before the window closes
AT_REST_KPH = 1.0                # "stationary in the box" at the lap-1 time origin

# A grid slot's own mean excess is only worth comparing against the fitted line when
# enough starts filled that box. The back rows empty out (2026's slot 22 was occupied at
# one start out of seven), so a slot below this is still counted in the all-slots figure
# but is kept out of the headline residual, where a single car would otherwise dominate.
SLOT_MIN_ROWS = 5

# A car sitting in its box is within a metre of the centreline the feed snaps it to; a
# pit-lane box or a sentinel coordinate is not. 8 m is well outside any grid box and well
# inside the pit-lane offsets (Silverstone's pit lane sits 31-104 m off the centreline).
GRID_MAX_LATERAL_M = 8.0
GRID_MIN_CARS = 8                # fewer placed cars cannot establish a pitch or an axis
LINE_MAX_T_S = 0.05              # a lap's first sample counts as "on the line" only here
LINE_MIN_POINTS = 6

# The pitch is searched over this window only. It has to be narrower than a factor of two
# either side of the answer, because a lattice's coherence also peaks at half and a third
# of its true period; 5-12 m brackets every plausible box spacing without admitting 4 m
# (the half-harmonic of 8) or 16 m (the double).
PITCH_SEARCH_MIN_M = 5.0
PITCH_SEARCH_MAX_M = 12.0
# A session's grid counts as RESOLVED when its lattice coherence clears this multiple of
# the no-periodicity noise floor sqrt(pi)/(2 sqrt(n)). Sessions below it still get a
# reported pitch, but they are excluded from the pooled figure and named in `unresolved`,
# because a feed that cannot separate 8 m boxes should say so rather than vote.
PITCH_MIN_COHERENCE_RATIO = 2.0


# ======================================================================================
# Pure kinematics -- the consumable model. Arguments in, numbers out.
# ======================================================================================

@dataclass(frozen=True)
class LaunchParams:
    """One car's launch, in SI. Every field is a number the caller owns.

    reactionS          delay from the start signal to first motion, seconds
    accelMps2          constant acceleration from rest, m/s^2
    handoverSpeedMps   the speed at which this model stops and the caller's own pace
                       model takes over. The constant-acceleration law is only MEASURED
                       to LAUNCH_FIT_CEILING_KPH, so a caller handing over above that is
                       extrapolating, and `validToSpeedKph` in the fitted block says so.
    """
    reactionS: float
    accelMps2: float
    handoverSpeedMps: float


def slot_offset_m(anchor_m: float, pitch_m: float, slot: int) -> float:
    """Signed distance from the timing line to grid `slot` (1 = pole), along travel.

    Positive means the slot is PAST the line -- the car has already crossed it and must
    cover ringLength - offset to reach it again. Negative means the slot is short of the
    line; the crossing seconds after the start does not complete lap 1, so lap-1 distance
    is ringLength - offset either way. Both signs occur in 2026: measured, the Canadian
    front box sits 32.2 m short of the line and the Italian one 291.1 m past it.
    """
    if slot < 1:
        raise ValueError(f"grid slot is 1-based, got {slot}")
    return anchor_m - (slot - 1) * pitch_m


def slot_station_m(anchor_m: float, pitch_m: float, slot: int, ring_length_m: float) -> float:
    """Ring station of a grid slot, on a ring whose station 0 is the timing line."""
    if not ring_length_m > 0:
        raise ValueError("ring_length_m must be positive")
    return slot_offset_m(anchor_m, pitch_m, slot) % ring_length_m


def lap1_distance_m(anchor_m: float, pitch_m: float, slot: int, ring_length_m: float) -> float:
    """How far this slot actually drives on lap 1: one ring minus its head start."""
    return ring_length_m - slot_offset_m(anchor_m, pitch_m, slot)


def launch_profile(t_s: float, p: LaunchParams) -> tuple[float, float, str]:
    """(distance travelled, speed, phase) for one car at time `t_s` after the signal.

    Three phases, all closed-form, continuous in both distance and speed at each join:
      GRID      t < reaction            stationary in the box
      LAUNCH    until handoverSpeed     v = a (t - reaction), s = a (t - reaction)^2 / 2
      HANDOVER  after that              constant handoverSpeed; the caller's own pace
                                        model owns everything past this point
    """
    if not p.accelMps2 > 0:
        raise ValueError("accelMps2 must be positive")
    if not p.handoverSpeedMps > 0:
        raise ValueError("handoverSpeedMps must be positive")
    tau = t_s - p.reactionS
    if tau <= 0.0:
        return 0.0, 0.0, "GRID"
    t_hand = p.handoverSpeedMps / p.accelMps2
    if tau < t_hand:
        return 0.5 * p.accelMps2 * tau * tau, p.accelMps2 * tau, "LAUNCH"
    d_hand = 0.5 * p.handoverSpeedMps * t_hand        # = v^2 / (2a)
    return d_hand + p.handoverSpeedMps * (tau - t_hand), p.handoverSpeedMps, "HANDOVER"


def launch_time_loss_s(p: LaunchParams) -> float:
    """Seconds this launch costs against a car already at handoverSpeed at t = 0.

    reaction + v/a to reach the speed, minus the v/(2a) a cruising car would have spent
    covering the same v^2/(2a) metres, so the whole loss is reaction + v/(2a). This is
    the ONLY part of the lap-1 penalty that follows from the launch physics; the rest is
    measured separately (see the `lap1` block) and the two must not be double counted.
    """
    return p.reactionS + p.handoverSpeedMps / (2.0 * p.accelMps2)


def launch_state(grid_order, params, t_s: float, *, anchor_m: float, pitch_m: float,
                 ring_length_m: float, min_gap_m: float | None = None) -> list[dict]:
    """THE consumable function: where every car is, and how fast, at time `t_s`.

    grid_order  drivers in grid order, pole first. The order is all a caller needs; it
                does not have to know anything about how the order was derived.
    params      mapping driver -> LaunchParams.
    t_s         seconds after the start signal. t = 0 is also the feed's own lap-1 time
                origin, so a replay clock lines up without an offset.

    Returns one dict per car:
      stationM    position on the ring, wrapped to [0, ringLength) the way the renderer
                  wants it
      progressM   the SAME position unwrapped -- signed metres past the timing line,
                  monotonic in t and free to be negative. Gaps between cars must be taken
                  from this, never from stationM: the back of a 22-car grid sits ~52 m
                  BEHIND the line at most circuits, so its wrapped station is ~5774 and a
                  naive subtraction reports a 5.8 km gap
      speedMps, distanceM, phase, gridPosition
    Pure -- same arguments, same answer, no state, no I/O.

    At t = 0 the progress spread this returns is (len(grid_order) - 1) * pitch and the
    minimum consecutive gap is pitch, which is the whole point: the engine it replaces
    put every car on station 0 with a 0.00 m gap against a 5.6 m car.

    min_gap_m, when given, is a hard NON-INTERPENETRATION constraint: no car may be
    closer than this to the one ahead of it in grid order. Two solid bodies cannot share
    a metre of track, so this is physics, not a fitted behaviour -- and it is deliberately
    NOT a car-following model: it holds a clamped car at the gap and tags it QUEUED so the
    caller can see that its free-air launch was interrupted rather than believing a
    number that was quietly changed. Measured with the real fitted launches, leaving it
    off produces 0 overlapping pairs at t = 0 and t = 1 s but 1-4 by t = 2 s and 5-9 by
    t = 5 s (British / Italian / Australian, 5.6 m car), because two cars accelerating at
    9.0 and 10.4 m/s^2 from boxes 8 m apart really do close that gap. Setting it gives 0
    at every t, at the cost of preserving grid order through the launch, which this
    function does not attempt to change.
    """
    if not ring_length_m > 0:
        raise ValueError("ring_length_m must be positive")
    if min_gap_m is not None and min_gap_m < 0:
        raise ValueError("min_gap_m cannot be negative")
    out = []
    for i, driver in enumerate(grid_order):
        p = params[driver]
        slot = i + 1
        dist, speed, phase = launch_profile(t_s, p)
        progress = slot_offset_m(anchor_m, pitch_m, slot) + dist
        if min_gap_m is not None and out:
            limit = out[-1]["progressM"] - min_gap_m
            if progress > limit:
                progress = limit
                speed = min(speed, out[-1]["speedMps"])
                dist = progress - slot_offset_m(anchor_m, pitch_m, slot)
                phase = "QUEUED"
        out.append({
            "driver": driver,
            "gridPosition": slot,
            "stationM": progress % ring_length_m,
            "progressM": progress,
            "speedMps": speed,
            "distanceM": dist,
            "phase": phase,
        })
    return out


# ======================================================================================
# Pure estimators -- arrays in, numbers out.
# ======================================================================================

def robust_location_scale(values) -> tuple[float, float, float]:
    """(median, MAD-sigma, standard error of the median).

    The launch sample has a long LEFT tail -- a handful of cars per season creep off the
    line under anti-stall, or have their speed channel held -- so a mean would report a
    launch nobody performed. The SE uses the 1.253 factor for the median of a normal core.
    """
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan"), float("nan"), float("nan")
    med = float(np.median(v))
    sigma = float(1.4826 * np.median(np.abs(v - med)))
    se = float(1.2533 * sigma / math.sqrt(v.size))
    return med, sigma, se


def cluster_mean(values, clusters) -> tuple[float, float, int, int]:
    """(mean, cluster-robust se, n observations, n clusters).

    Lap-1 observations inside one race start are not independent -- one first corner
    delays twenty cars at once -- so the SE is the spread of the CLUSTER means, not of
    the rows. Ignoring that understates it by roughly sqrt(cars per race).
    """
    v = np.asarray(values, dtype=np.float64)
    c = np.asarray(list(clusters), dtype=object)
    ok = np.isfinite(v)
    v, c = v[ok], c[ok]
    if v.size == 0:
        return float("nan"), float("nan"), 0, 0
    keys = sorted(set(c.tolist()))
    means = np.array([v[c == k].mean() for k in keys])
    m = float(v.mean())
    if len(keys) < 2:
        return m, float("nan"), int(v.size), len(keys)
    return m, float(np.std(means, ddof=1) / math.sqrt(len(keys))), int(v.size), len(keys)


def fit_constant_acceleration(t_s, speed_kph, ceiling_kph: float = LAUNCH_FIT_CEILING_KPH):
    """Fit v = a (t - t0) to ONE car's rise off the line. Returns a dict, or None.

    Deliberately NOT a textbook curve: the shape is measured. The rise window is the
    first contiguous run of non-decreasing speed, starting at the first sample the car is
    moving and ending at `ceiling_kph`, so a car that launches, reaches the first corner
    and brakes cannot contribute its brake to the slope. Over the 2026 starts that window
    holds a median of 24 samples and the straight line explains a median R^2 of 0.994
    (5th percentile 0.971) -- which is the EVIDENCE that a constant acceleration is right
    here, rather than an assumption that it is.

    The returned reaction time is the intercept in the feed's own lap-1 time base. Any
    constant offset between that base and the actual lights-out instant is not
    identifiable from this feed, so the level carries that caveat while the spread across
    cars is identified regardless.
    """
    t = np.asarray(t_s, dtype=np.float64)
    v = np.asarray(speed_kph, dtype=np.float64)
    if t.shape != v.shape or t.size < LAUNCH_MIN_SAMPLES:
        return None
    finite = np.isfinite(t) & np.isfinite(v)
    moving = np.where(finite & (v > 0.5))[0]
    if moving.size == 0:
        return None
    i0 = int(moving[0])
    i1, peak = i0, float(v[i0])
    n = t.size
    while (i1 + 1 < n and finite[i1 + 1] and v[i1 + 1] <= ceiling_kph
           and v[i1 + 1] >= peak - LAUNCH_SPEED_DROP_KPH):
        i1 += 1
        peak = max(peak, float(v[i1]))
    tt = t[i0:i1 + 1]
    vv = v[i0:i1 + 1] / 3.6
    if (tt.size < LAUNCH_MIN_SAMPLES or (tt[-1] - tt[0]) < LAUNCH_MIN_SPAN_S
            or vv[-1] < LAUNCH_MIN_END_SPEED_MPS):
        return None
    X = np.column_stack([np.ones(tt.size), tt])
    beta, se, resid, r2 = _ols_hc1(X, vv)
    a = float(beta[1])
    if a <= 0 or not np.isfinite(r2) or r2 < LAUNCH_MIN_R2:
        return None
    return {
        "accelMps2": a,
        "accelSe": float(se[1]),
        "reactionS": float(-beta[0] / a),
        "r2": float(r2),
        "nSamples": int(tt.size),
        "windowStartS": float(tt[0]),
        "windowEndSpeedMps": float(vv[-1]),
    }


def principal_axis(points_xy, travel_hint=None) -> np.ndarray:
    """Unit vector along a line of grid points, oriented by `travel_hint` when given.

    The stationary grid is the straightest object in the whole feed -- residual lateral
    scatter about this axis is 0.03-0.3 m at a clean circuit -- so its first principal
    component IS the track direction there, with no ring required.
    """
    P = np.asarray(points_xy, dtype=np.float64).reshape(-1, 2)
    if P.shape[0] < 2:
        raise ValueError("need at least two points for an axis")
    C = P - P.mean(axis=0)
    w, V = np.linalg.eigh(C.T @ C)
    u = V[:, int(np.argmax(w))]
    if travel_hint is not None:
        h = np.asarray(travel_hint, dtype=np.float64)
        if np.isfinite(h).all() and float(u @ h) < 0:
            u = -u
    return u / float(np.hypot(*u))


def lattice_period(values, p_min: float = PITCH_SEARCH_MIN_M,
                   p_max: float = PITCH_SEARCH_MAX_M, step: float = 0.001):
    """(period, coherence) of a 1-D lattice, found without assigning slot numbers first.

    A grid is a lattice with gaps in it -- a pit-lane start leaves its BOX empty, since
    F1 does not close the grid up -- so the naive estimator (chain the consecutive gaps
    and round each to a whole number of pitches) has to guess the slot numbers from a
    pitch it does not yet know. Measured, that guess is fragile exactly where the feed is
    noisiest: at the Australian GP it invented four phantom empty boxes among twenty cars
    and dragged the pitch down to 7.01 m.

    This asks the question directly: which period p makes every car sit near a multiple of
    p? R(p) = |mean(exp(2*pi*i*a/p))| is 1 for a perfect lattice and about 0.886/sqrt(n)
    for points with no periodicity at all, so the coherence is both the estimator's
    objective AND its own evidence -- a session that does not resolve an 8 m box says so
    instead of returning a confident wrong number. Empty boxes cost nothing here: a
    missing lattice point does not move the peak.

    The search window is deliberately narrower than a factor of two either side of the
    answer, because R also peaks at p/2 and p/3.
    """
    a = np.asarray(values, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size < 3 or not (0 < p_min < p_max):
        return None
    ps = np.arange(p_min, p_max + step, step)
    phase = (2.0 * np.pi) * np.outer(1.0 / ps, a)
    R = np.abs(np.exp(1j * phase).sum(axis=1)) / a.size
    k = int(np.argmax(R))
    return float(ps[k]), float(R[k])


def lattice_noise_floor(n: int) -> float:
    """Expected coherence of n points with no periodicity: sqrt(pi)/(2 sqrt(n))."""
    return float(math.sqrt(math.pi) / (2.0 * math.sqrt(n))) if n > 0 else float("nan")


def pooled_lattice_period(groups, p_min: float = PITCH_SEARCH_MIN_M,
                          p_max: float = PITCH_SEARCH_MAX_M, step: float = 0.001):
    """One pitch from many grids at once, plus a leave-one-grid-out standard error.

    Each grid has its own phase -- the circuits put their boxes in different places
    relative to the timing line -- so the coherences are added as MAGNITUDES, one per
    grid, and the shared period is the p that maximises their mean. Pooling matters: a
    single noisy grid can lock onto a spurious short period (the Australian feed's own
    peak is at 5.01 m), while across ten grids the true period is the only one that lines
    up everywhere. Measured, the pooled objective peaks at 8.03 m with mean coherence
    0.469 against 0.255 for the next-best period -- not a close call.

    The SE is a delete-one-grid jackknife, which is the honest one here: the uncertainty
    that matters is whether another circuit would move the answer, not how tightly one
    grid's twenty cars sit on their own boxes.
    """
    arrays = [np.asarray(a, dtype=np.float64) for a in groups]
    arrays = [a[np.isfinite(a)] for a in arrays]
    arrays = [a for a in arrays if a.size >= 3]
    if not arrays:
        return None
    ps = np.arange(p_min, p_max + step, step)
    per = np.vstack([np.abs(np.exp(1j * (2.0 * np.pi) * np.outer(1.0 / ps, a)).sum(axis=1))
                     / a.size for a in arrays])
    total = per.mean(axis=0)
    k = int(np.argmax(total))
    pitch = float(ps[k])

    se = float("nan")
    if len(arrays) > 1:
        loo = []
        for i in range(len(arrays)):
            keep = [j for j in range(len(arrays)) if j != i]
            loo.append(float(ps[int(np.argmax(per[keep].mean(axis=0)))]))
        loo = np.array(loo)
        n = loo.size
        se = float(math.sqrt((n - 1) / n * np.sum((loo - loo.mean()) ** 2)))

    # the runner-up peak, at least half a metre away, is the evidence the answer is sharp
    far = np.abs(ps - pitch) > 0.5
    runner = float(total[far].max()) if far.any() else float("nan")
    return {
        "pitchM": pitch,
        "se": se,
        "meanCoherence": float(total[k]),
        "runnerUpCoherence": runner,
        "perGroupCoherence": [float(v) for v in per[:, k]],
        "nGroups": len(arrays),
        "nPoints": int(sum(a.size for a in arrays)),
    }


def assign_slots(along_desc, pitch_m: float) -> np.ndarray:
    """Slot numbers (1-based) for cars ordered front-to-back, given a KNOWN pitch.

    Called after `lattice_period` has measured the pitch, so the rounding is no longer
    guessing with a number it is about to estimate.
    """
    a = np.asarray(along_desc, dtype=np.float64)
    if a.size == 0:
        return np.zeros(0, dtype=int)
    if pitch_m <= 0:
        return np.arange(1, a.size + 1, dtype=int)
    return 1 + np.rint((a[0] - a) / pitch_m).astype(int)


def fit_pitch(along_desc, pitch_hint_m: float = 8.0):
    """Grid pitch, pole's axis coordinate and the lattice coherence, from placed cars.

    Two stages: `lattice_period` measures the pitch with no slot numbers involved, then
    those slot numbers (now unambiguous) support an OLS refit that supplies an HC1 SE and
    a residual SD. The residual SD is the within-box placement scatter -- a real and
    separate quantity, since a driver may stop anywhere inside his own box.
    """
    a = np.asarray(along_desc, dtype=np.float64)
    if a.size < 3:
        return None
    found = lattice_period(a)
    pitch0, coherence = found if found else (pitch_hint_m, float("nan"))
    slots = assign_slots(a, pitch0)
    if len(set(slots.tolist())) < 2:
        return None
    X = np.column_stack([np.ones(a.size), (slots - 1).astype(np.float64)])
    beta, se, resid, r2 = _ols_hc1(X, a)
    pitch = float(-beta[1])
    if pitch <= 0:
        return None
    return {
        "pitchM": pitch,
        "pitchSe": float(se[1]),
        "latticePitchM": float(pitch0),
        "coherence": float(coherence),
        "noiseFloor": lattice_noise_floor(int(a.size)),
        "poleAlong": float(beta[0]),
        "poleAlongSe": float(se[0]),
        "slots": slots,
        "boxScatterM": float(np.std(resid, ddof=1)) if resid.size > 2 else float("nan"),
        "n": int(a.size),
        "emptySlots": int(slots[-1]) - int(a.size),
    }


def grid_slope_fit(rows):
    """Lap-1 excess down the grid: the within-session slope, the intercept AT POLE, and
    the sample's own mean grid rank. Dicts in, numbers out -- no file access.

    `rows` carry excessS, gridRank and cluster; one cluster is one race start. The design
    is session fixed effects plus (gridRank - 1), so the slope is a WITHIN-session
    comparison and cannot be contaminated by one circuit simply being slower than another.

    A fixed-effects design has no single intercept -- it has one per session -- so the
    pole value is recovered from the identity that holds inside ONE sample:

        mean(excess) = (n-weighted mean session effect) + slope * (mean(gridRank) - 1)

    which makes `intercept` the expected lap-1 excess of a SLOT-1 car at an average start.
    `level` and `meanGridSlot` are the two means that identity is built from, and both are
    over THESE rows only. Combining a level taken over a different row set with this slope
    breaks the identity silently, which is the double count this function exists to stop.
    """
    rr = [r for r in rows
          if r.get("gridRank") and np.isfinite(r.get("excessS", float("nan")))]
    if len(rr) < 10:
        return None
    keys = sorted({r["cluster"] for r in rr})
    cols = [np.array([1.0 if r["cluster"] == k else 0.0 for r in rr]) for k in keys]
    cols.append(np.array([float(r["gridRank"] - 1) for r in rr]))
    X = np.column_stack(cols)
    y = np.array([r["excessS"] for r in rr], dtype=np.float64)
    beta, se, _resid, r2 = _ols_hc1(X, y)
    slope = float(beta[-1])
    kbar = float(np.mean([float(r["gridRank"]) for r in rr]))
    level = float(y.mean())
    return {"slope": slope, "slopeSe": float(se[-1]),
            "intercept": level - slope * (kbar - 1),
            "level": level, "meanGridSlot": kbar,
            "n": len(rr), "nClusters": len(keys), "r2": float(r2)}


def delete_one_cluster_se(rows, statistic, min_clusters: int = 3):
    """(delete-one-CLUSTER jackknife SE, number of clusters) for any statistic of `rows`.

    The clusters are race starts. This is the honest SE for a quantity built from BOTH a
    level and a slope: the two are fitted on the SAME starts, so their covariance is real,
    and a delta method would have to assume a value for it. Dropping a whole start and
    refitting carries that covariance without anyone having to name it. Returns a null SE
    rather than a number when there are too few starts, or when any refit refuses.
    """
    keys = sorted({r["cluster"] for r in rows})
    if len(keys) < min_clusters:
        return None, len(keys)
    vals = []
    for k in keys:
        v = statistic([r for r in rows if r["cluster"] != k])
        if v is None or not np.isfinite(v):
            return None, len(keys)
        vals.append(float(v))
    a = np.array(vals, dtype=np.float64)
    n = a.size
    return float(math.sqrt((n - 1) / n * float(np.sum((a - a.mean()) ** 2)))), len(keys)


def geometric_seconds_per_slot(pitch_m: float, line_speed_mps: float) -> float:
    """Lap-1 seconds that one grid slot of extra DISTANCE costs, by itself.

    Slot k must drive (k - 1) x pitch metres further than pole to complete lap 1 (see
    lap1_distance_m). Those metres are appended at the END of the lap, where the car is
    doing line_speed, so they turn into time at that speed -- d(lap-1 time) / d(lap-1
    distance) = 1 / v at the crossing. Not the lap AVERAGE speed, which is 20-30% lower
    and would overstate the geometric share by the same factor.
    """
    if not pitch_m > 0:
        raise ValueError("pitch_m must be positive")
    if not line_speed_mps > 0:
        raise ValueError("line_speed_mps must be positive")
    return pitch_m / line_speed_mps


def regression_to_mean(grid_rank, end_rank):
    """OLS of places gained on grid rank, centred so the intercept is the field mean.

    gained = (grid rank) - (rank at the end of lap 1): positive means places GAINED. The
    slope is the regression-to-the-mean coefficient; it is POSITIVE when places gained
    rises down the grid, i.e. when the front loses places on lap 1 and the back gains
    them. HC1 SEs.
    """
    g = np.asarray(grid_rank, dtype=np.float64)
    e = np.asarray(end_rank, dtype=np.float64)
    ok = np.isfinite(g) & np.isfinite(e)
    g, e = g[ok], e[ok]
    if g.size < 5:
        return None
    gained = g - e
    X = np.column_stack([np.ones(g.size), g - g.mean()])
    beta, se, resid, r2 = _ols_hc1(X, gained)
    return {"intercept": float(beta[0]), "interceptSe": float(se[0]),
            "slope": float(beta[1]), "slopeSe": float(se[1]),
            "n": int(g.size), "r2": float(r2)}


# ======================================================================================
# Collectors -- the only code below that touches the disk.
# ======================================================================================

def _travel_hint(lap):
    """Direction the car left its box, from its own first 30-60 m of motion."""
    d = np.hypot(lap.x - lap.x[0], lap.y - lap.y[0])
    k = np.where(np.isfinite(d) & (d > 30.0) & (d < 60.0))[0]
    if k.size == 0:
        return None
    i = int(k[0])
    return np.array([lap.x[i] - lap.x[0], lap.y[i] - lap.y[0]]) / d[i]


def timing_line_point(session_dir: Path, drivers, max_drivers: int = 14):
    """The timing line, as a coordinate, with no ring involved.

    A lap's telemetry time origin IS its lap-start session time, so the first sample of
    lap 2 sits on the line the car has just crossed. Measured, those first samples land at
    t = 0.000 for every car in every 2026 session, and their spread about the median is
    the line's own measurement scatter, which is reported rather than assumed away.
    """
    pts, speeds = [], []
    for drv in list(drivers)[:max_drivers]:
        lap = load_lap(session_dir, drv, 2)
        if lap is None or lap.n == 0:
            continue
        m = np.isfinite(lap.x) & np.isfinite(lap.y)
        if not m.any():
            continue
        i = int(np.where(m)[0][0])
        if lap.t[i] > LINE_MAX_T_S:
            continue
        pts.append((lap.x[i], lap.y[i]))
        # The speed at that SAME sample is the speed the car was doing as it completed
        # the lap before, which is the rate at which extra lap-1 METRES become lap-1
        # seconds -- the geometric part of the per-slot penalty. Only "actually moving"
        # is gated; nothing here thresholds the answer, and the location is taken as a
        # median because a car may cross under a neutralisation or with damage.
        if np.isfinite(lap.speed[i]) and lap.speed[i] > AT_REST_KPH:
            speeds.append(float(lap.speed[i]))
    if len(pts) < LINE_MIN_POINTS:
        return None
    P = np.array(pts, dtype=np.float64)
    return {"point": np.median(P, axis=0), "n": int(P.shape[0]), "points": P,
            "speedsKph": speeds}


def session_start(event: str, session: str) -> dict:
    """Everything measurable about one session's start. Reads telemetry, returns numbers.

    The return value is a dict of measurements plus explicit refusals: a session whose
    grid is the feed's "position unknown" sentinel produces grid=None and a stated reason,
    never a plausible-looking grid.
    """
    sdir = data_root() / event / session
    out = {"event": event, "session": session, "grid": None, "launches": [],
           "standingStart": False, "refusals": [], "nCars": 0, "nAtRest": 0,
           "nLaunchRefused": 0}
    if not (sdir / "session_laptimes.json").exists():
        out["refusals"].append("no session_laptimes.json")
        return out
    table = LapTable(sdir)
    rows = [r for r in table.rows() if r["lap"] == 1]
    if not rows:
        out["refusals"].append("no lap-1 rows")
        return out

    obs, hints = [], []
    for r in rows:
        lap = load_lap(sdir, r["drv"], 1)
        if lap is None or lap.n == 0:
            continue
        if not (np.isfinite(lap.x[0]) and np.isfinite(lap.y[0])):
            continue
        v0 = float(lap.speed[0]) if np.isfinite(lap.speed[0]) else float("nan")
        hint = _travel_hint(lap)
        if hint is not None:
            hints.append(hint)
        obs.append({"driver": r["drv"], "xy": np.array([lap.x[0], lap.y[0]]),
                    "v0Kph": v0, "atRest": bool(np.isfinite(v0) and v0 <= AT_REST_KPH),
                    "pitStart": r.get("pout", "None") != "None",
                    "fit": fit_constant_acceleration(lap.t, lap.speed)})
    out["nCars"] = len(obs)
    if not obs:
        out["refusals"].append("no lap-1 position for any car")
        return out

    n_rest = sum(1 for o in obs if o["atRest"])
    out["nAtRest"] = n_rest
    out["standingStart"] = n_rest >= max(GRID_MIN_CARS, len(obs) // 2)
    if not out["standingStart"]:
        out["refusals"].append(
            f"the feed's lap-1 telemetry does not start from rest: only {n_rest} of "
            f"{len(obs)} cars are stationary at the lap-1 time origin")
        return out

    for o in obs:
        if o["atRest"] and not o["pitStart"] and o["fit"] is not None:
            out["launches"].append({"driver": o["driver"], **o["fit"]})
    out["nLaunchRefused"] = sum(
        1 for o in obs if o["atRest"] and not o["pitStart"] and o["fit"] is None)

    # Grid geometry. The positive test for the "position unknown" sentinel is that several
    # cars report the SAME coordinate, which no real grid can produce. np.isfinite cannot
    # see this: the sentinel is a perfectly finite coordinate.
    #
    # The affected CARS are withdrawn, not the whole session -- one accidental collision
    # should not throw away eighteen honest boxes -- and the session is refused only when
    # too few honest boxes are left. Measured on 2026 that refuses exactly the two rounds
    # whose sentinel covers the grid (Chinese 18 of 18, Monaco 19 of 22) and keeps every
    # other one intact.
    P = np.array([o["xy"] for o in obs])
    degenerate = []
    for i in range(P.shape[0]):
        d = np.hypot(P[:, 0] - P[i, 0], P[:, 1] - P[i, 1])
        degenerate.append(int((d <= GRID_DUP_STATION_M).sum()) > 1)
    n_dup = int(sum(degenerate))
    out["unplaced"] = [o["driver"] for o, bad in zip(obs, degenerate) if bad]
    if n_dup:
        out["refusals"].append(
            f"{n_dup} of {len(obs)} lap-1 coordinates are shared to within "
            f"{GRID_DUP_STATION_M} m: the feed's 'position unknown' sentinel rather than "
            f"a measurement, so those cars are unplaced")

    grid_obs = [o for o, bad in zip(obs, degenerate)
                if o["atRest"] and not o["pitStart"] and not bad]
    if len(grid_obs) < GRID_MIN_CARS:
        out["refusals"].append(
            f"only {len(grid_obs)} usable stationary boxes remain of {len(obs)} cars, "
            f"so no grid is emitted: none was observed")
        return out
    hint = np.median(np.array(hints), axis=0) if hints else None
    u = principal_axis([o["xy"] for o in grid_obs], hint)
    nvec = np.array([-u[1], u[0]])
    lat = np.array([float(o["xy"] @ nvec) for o in grid_obs])
    keep = np.abs(lat - np.median(lat)) <= GRID_MAX_LATERAL_M
    if int(keep.sum()) < GRID_MIN_CARS:
        out["refusals"].append("the grid points are not collinear enough to define an axis")
        return out
    grid_obs = [o for o, k in zip(grid_obs, keep) if k]
    u = principal_axis([o["xy"] for o in grid_obs], hint)
    nvec = np.array([-u[1], u[0]])
    along = np.array([float(o["xy"] @ u) for o in grid_obs])
    order_idx = np.argsort(-along)
    ordered = [grid_obs[i] for i in order_idx]
    fit = fit_pitch(along[order_idx])
    if fit is None:
        out["refusals"].append("fewer than three placed cars: no pitch is identifiable")
        return out

    line = timing_line_point(sdir, [o["driver"] for o in obs])
    anchor = anchor_se = None
    line_se = None
    line_v = line_v_se = None
    line_v_n = 0
    if line is not None and line.get("speedsKph"):
        med_v, _sigma_v, se_v = robust_location_scale(line["speedsKph"])
        line_v = float(med_v) if np.isfinite(med_v) else None
        line_v_se = float(se_v) if np.isfinite(se_v) and se_v > 0 else None
        line_v_n = len(line["speedsKph"])
    if line is not None:
        line_along = float(line["point"] @ u)
        anchor = fit["poleAlong"] - line_along
        # the line is a MEASURED coordinate with its own scatter (cars cross it on
        # different racing lines and the feed samples them a few centimetres apart), so
        # its standard error has to travel into every anchor derived from it
        line_se = float(np.std(line["points"] @ u, ddof=1) / math.sqrt(line["n"]))
        anchor_se = float(math.hypot(fit["poleAlongSe"], line_se))
    else:
        out["refusals"].append(
            "no usable lap-2 first sample: the timing line has no coordinate in this "
            "session, so the anchor is unavailable")

    line_along = float(line["point"] @ u) if line is not None else 0.0
    out["grid"] = {
        "order": [o["driver"] for o in ordered],
        "pitchM": fit["pitchM"], "pitchSe": fit["pitchSe"],
        "latticePitchM": fit["latticePitchM"],
        "coherence": fit["coherence"], "noiseFloor": fit["noiseFloor"],
        "resolved": bool(fit["coherence"] >= PITCH_MIN_COHERENCE_RATIO * fit["noiseFloor"]),
        "boxScatterM": fit["boxScatterM"], "emptySlots": fit["emptySlots"],
        "nPlaced": fit["n"],
        "anchorM": anchor, "anchorSe": anchor_se,
        # box positions relative to the timing line, front of the grid first; this is the
        # raw measurement every fitted grid number above is derived from
        "offsetsM": [float(a - line_along) for a in along[order_idx]] if line else None,
        "lateralSd": float(np.std(np.array([float(o["xy"] @ nvec) for o in ordered]), ddof=1)),
        "pitStarters": [o["driver"] for o in obs if o["pitStart"]],
        "slots": [int(s) for s in fit["slots"]],
        "lineN": line["n"] if line else 0,
        "lineSeM": line_se,
        # speed OBSERVED at the line at the end of lap 1, from the first sample of lap 2
        "lineSpeedKph": line_v, "lineSpeedSeKph": line_v_se, "lineSpeedN": line_v_n,
    }
    return out


def race_sessions() -> list[tuple[str, str]]:
    """Every (event, session) that could hold a standing start, in a stable order."""
    out = []
    if not data_root().is_dir():
        return out
    for d in sorted(data_root().iterdir()):
        if not d.is_dir() or d.name.startswith(".") or "Testing" in d.name:
            continue
        for session in ("Race", "Sprint"):
            if (d / session / "session_laptimes.json").exists():
                out.append((d.name, session))
    return out


def clean_lap_prediction(fit: dict, lap: int, life: float, compound: str,
                         driver: str) -> float:
    """Evaluate fit_pace.fit_track_model's OWN fitted clean-lap model at a lap.

    This CONSUMES the established producer rather than fitting a second lap-time model:
    the coefficients are exactly the ones fit_params already ships. Lap 1 is never inside
    that fit (clean_mask requires lap > 1), so this is an out-of-sample prediction.
    """
    p = fit["params"]
    y = p["intercept"]["beta"] + p["fuel"]["beta"] * (lap - 1)
    if f"k_{compound}" in p:
        y += p[f"k_{compound}"]["beta"]
    if f"g_{compound}" in p:
        y += p[f"g_{compound}"]["beta"] * (life - 1)
    if f"drv_{driver}" in p:
        y += p[f"drv_{driver}"]["beta"]
    return float(y)


def lap1_observations(starts: dict) -> dict:
    """Lap-1 time penalty and grid-to-lap-1 position change, per green race start.

    Only status == "1" laps count: a lap-1 row already carrying a yellow, a safety car or
    a red flag is not "a clean green lap slower by X", it is a neutralised lap, and
    pooling the two would hand the caller a penalty that is mostly Spa's safety car.
    """
    from simdata.fit_pace import fit_track_model

    t = build_tidy_table()
    mask = clean_mask(t)
    dutch_rain = (t["event"] == "Dutch Grand Prix") & t["wR"]

    rows, place_rows = [], []
    skipped = {"noLapTime": 0, "notGreen": 0, "pitStart": 0, "noModel": 0}
    for (event, session), start in sorted(starts.items()):
        sm = (t["event"] == event) & (t["session"] == session)
        idx = np.where(sm & (t["lap"] == 1))[0]
        fit = fit_track_model(t, mask & (t["session"] == session) & ~dutch_rain, sm)
        grid_order = (start.get("grid") or {}).get("order") or []
        rank = {d: i + 1 for i, d in enumerate(grid_order)}
        end_pos = {t["drv"][i]: float(t["pos"][i]) for i in idx if np.isfinite(t["pos"][i])}
        # rank the placed cars among THEMSELVES, so a pit-lane starter cannot shift the
        # whole field's apparent position change by one
        placed_end = sorted(end_pos[d] for d in grid_order if d in end_pos)
        end_rank = {d: placed_end.index(end_pos[d]) + 1 for d in grid_order if d in end_pos}

        # PLACES are counted at every start, neutralised or not: a lap-1 safety car is
        # usually the CONSEQUENCE of a first-corner incident, so dropping those starts
        # would measure only the calm ones and understate the whole effect.
        for drv in grid_order:
            if drv in end_rank:
                place_rows.append({
                    "event": event, "session": session, "driver": drv,
                    "cluster": f"{event}|{session}",
                    "gridRank": rank[drv], "endRank": end_rank[drv],
                    "green": all(t["status"][i] == "1" for i in idx if t["drv"][i] == drv),
                })

        if fit is None:
            skipped["noModel"] += int(idx.size)
            continue
        for i in idx:
            drv = t["drv"][i]
            if t["pout"][i] != "None":
                skipped["pitStart"] += 1
                continue
            if not np.isfinite(t["time"][i]):
                skipped["noLapTime"] += 1
                continue
            if t["status"][i] != "1":
                skipped["notGreen"] += 1
                continue
            comp = t["compound"][i]
            if comp not in fit["compounds"]:
                comp = fit["refCompound"]
            base = clean_lap_prediction(fit, 1, 1.0, comp, drv)
            rows.append({
                "event": event, "session": session, "driver": drv,
                "cluster": f"{event}|{session}",
                "excessS": float(t["time"][i]) - base,
                "gridRank": rank.get(drv), "endRank": end_rank.get(drv),
            })
    return {"rows": rows, "placeRows": place_rows, "skipped": skipped}


# ======================================================================================
# The fitted block, in fit_params.py's own leaf shape.
# ======================================================================================

# Provenance strings name the root the numbers were actually read from, so an artifact
# built from a different year cannot claim it came from the default one. Functions, not
# constants: the active year is set after import (build_sim_data.py --year).
def source_tel() -> str:
    return f"{relative_root()}/<event>/{{Race,Sprint}}/<driver>/1_tel.json (lap-1 telemetry)"


def source_laps() -> str:
    return f"{relative_root()}/<event>/{{Race,Sprint}}/session_laptimes.json"


def _key(event: str, session: str) -> str:
    return event if session == "Race" else f"{event} {session}"


def _grid_block(with_grid: dict) -> dict:
    # A grid can only be placed relative to a timing line that was itself measured; a
    # session missing one is dropped here and has already said so in its own refusals.
    keys = [k for k in with_grid if with_grid[k]["grid"].get("offsetsM")]
    if not keys:
        return {"provenance": "DERIVED",
                "note": "unavailable: a grid was measured but the timing line was not, "
                        "so no box position can be stated relative to it"}
    offsets = [np.asarray(with_grid[k]["grid"]["offsetsM"], dtype=np.float64)
               for k in keys]
    pooled = pooled_lattice_period(offsets)
    pitch = pooled["pitchM"]
    lat_sd = np.array([with_grid[k]["grid"]["lateralSd"] for k in keys])
    n_cars = int(sum(with_grid[k]["grid"]["nPlaced"] for k in keys))

    # Re-measure every session at the POOLED pitch: slot numbers are then unambiguous,
    # and each session reports its own pitch, box scatter, anchor and coherence against a
    # common lattice rather than against one it chose for itself.
    per_session, resolved, unresolved, per_track = {}, [], [], {}
    box_scatter, anchors = [], []
    for k, a, coh in zip(keys, offsets, pooled["perGroupCoherence"]):
        name = _key(*k)
        slots = assign_slots(a, pitch)
        X = np.column_stack([np.ones(a.size), (slots - 1).astype(np.float64)])
        beta, se, resid, _r2 = _ols_hc1(X, a)
        floor = lattice_noise_floor(int(a.size))
        own = lattice_period(a)
        ok = bool(own) and coh >= PITCH_MIN_COHERENCE_RATIO * floor
        (resolved if ok else unresolved).append(name)
        # An INDEPENDENT per-session number has to be the session's own lattice peak.
        # Re-regressing on slot numbers that were themselves rounded at the pooled pitch
        # returns the pooled pitch almost by construction, so it is reported as an SE and
        # a residual only, never as a second opinion about the pitch.
        per_session[name] = leaf(
            own[0] if ok else None, float(se[1]) if ok else None,
            n=int(a.size), provenance="DERIVED",
            note=f"this session's OWN lattice peak; coherence at the pooled pitch "
                 f"{coh:.3f} against a {floor:.3f} noise floor"
                 + ("" if ok else ". UNAVAILABLE as a measurement: the coherence is at "
                                  "the noise floor, so these lap-1 coordinates do not sit "
                                  "on a box lattice at all and this session's own peak"
                                  + (f" ({own[0]:.2f} m)" if own else "")
                                  + " is an artefact, not a pitch"))
        # the anchor is robust to a wrong slot for one car: median of every car's box
        # position carried back to slot 1
        anchor = float(np.median(a + (slots - 1) * pitch))
        # box scatter about the lattice, AND the timing line's own measurement error --
        # the anchor is a difference of two measured coordinates, so both belong in it
        anchor_se = float(math.hypot(
            np.std(a + (slots - 1) * pitch, ddof=1) / math.sqrt(a.size),
            with_grid[k]["grid"].get("lineSeM") or 0.0))
        # the shared definition lives once on the parent, not ten times over; only what
        # is specific to THIS circuit is repeated here
        caveats = []
        if not ok:
            caveats.append("does not resolve its boxes: read the SE, not a sharp number")
        if with_grid[k]["grid"]["pitStarters"]:
            caveats.append(f"{len(with_grid[k]['grid']['pitStarters'])} pit-lane "
                           f"start(s), so the front OCCUPIED box may not be pole's")
        per_track[name] = leaf(anchor, anchor_se, n=int(a.size), provenance="DERIVED",
                               note="; ".join(caveats) or None)
        if ok:
            box_scatter.append(float(np.std(resid, ddof=1)))
            anchors.append(anchor)
    anchors = np.array(anchors)

    n_resolved_cars = int(sum(with_grid[k]["grid"]["nPlaced"]
                              for k in keys if _key(*k) in resolved))
    return {
        "slotPitchMetres": leaf(
            pitch, pooled["se"], n=n_cars, provenance="DERIVED", source=source_tel(),
            note=f"the period of the box lattice, measured jointly over "
                 f"{pooled['nGroups']} grids and {pooled['nPoints']} cars with no slot "
                 f"numbers assumed; mean coherence {pooled['meanCoherence']:.3f} against "
                 f"{pooled['runnerUpCoherence']:.3f} for the next-best period, and the "
                 f"SE is a delete-one-circuit jackknife"),
        "perSessionPitchMetres": per_session,
        "sessionsResolvingTheirBoxes": {
            "resolved": resolved, "unresolved": unresolved,
            "provenance": "DERIVED",
            "note": "an unresolved session's lap-1 coordinates do not sit on a box "
                    "lattice at all -- the feed placed those cars somewhere other than "
                    "their boxes -- so it is NAMED rather than quietly averaged in. It "
                    "still enters the pooled fit, where a flat coherence curve moves the "
                    "peak by nothing (the delete-one-circuit jackknife SE is the check); "
                    "it is excluded from the box scatter and from the anchor summary, "
                    "which would be corrupted by it",
        },
        "boxPlacementScatterMetres": leaf(
            float(np.median(box_scatter)) if box_scatter else None,
            n=n_resolved_cars, provenance="DERIVED", source=source_tel(),
            note="SD of a car's box position about the lattice, over the sessions that "
                 "resolve their boxes: a driver may stop anywhere inside his box, and "
                 "this is how far he does"),
        "anchorMetresPastTimingLine": {
            "provenance": "DERIVED", "source": source_tel(),
            "definition": "signed distance from the timing line to the front occupied "
                          "box along travel. Positive means it sits PAST the line, so "
                          "lap 1 covers ringLength - anchor + (slot - 1) * pitch; "
                          "negative means it is short of the line, and the crossing "
                          "seconds after the start does not complete lap 1, so the same "
                          "formula holds",
            "perTrack": per_track,
            "summary": leaf(
                float(np.median(anchors)) if anchors.size else None,
                n=int(anchors.size), provenance="DERIVED", source=source_tel(),
                note=(f"the anchor is a PER-CIRCUIT fact, not a constant: measured "
                      f"{anchors.min():.1f} to {anchors.max():.1f} m over {anchors.size} "
                      f"resolved grids, so a caller must read perTrack and this median is "
                      f"a summary only") if anchors.size else "unavailable"),
        },
        "lateralStagger": {
            "columnSign": {
                "value": "alternating +1 / -1 by slot parity, pole on +1",
                "provenance": "RULE",
                "source": "FIA Formula 1 Sporting Regulations: the starting grid is "
                          "marked in two staggered columns",
                "note": "the STRUCTURE is a regulation fact; the metric offset below is "
                        "not, and the two are kept apart on purpose",
            },
            "offsetMetres": leaf(
                None, provenance="RULE", n=n_cars, source=source_tel(),
                note=f"NOT IN THE FEED, so null rather than a plausible number: every "
                     f"stationary car is snapped to the centreline, median per-session "
                     f"lateral SD {float(np.median(lat_sd)):.2f} m over {n_cars} cars, "
                     f"which is the evidence the offset is ABSENT rather than zero. A "
                     f"renderer that wants a stagger must take it from the track model's "
                     f"measured half-width and say that is where it came from"),
        },
        "observedGridOrder": {
            "provenance": "DERIVED", "source": source_tel(),
            "note": "grid order DERIVED by ranking the stationary lap-1 boxes along the "
                    "grid's own axis, with no ring involved. `pitLaneStarters` is "
                    "OBSERVED from each driver's own lap-1 pout row; `unplaced` is a "
                    "driver whose lap-1 coordinate was the feed's sentinel, so he has no "
                    "measured box and is NOT silently appended to the order",
            "perSession": {
                _key(e, s): {
                    "order": v["grid"]["order"],
                    "pitLaneStarters": v["grid"]["pitStarters"],
                    "unplaced": v.get("unplaced", []),
                } for (e, s), v in with_grid.items()},
        },
    }


def _launch_block(standing: dict) -> dict:
    launches = [(k, l) for k, v in standing.items() for l in v["launches"]]
    if not launches:
        return {"provenance": "DERIVED",
                "note": "unavailable: no standing start in the feed carried a usable "
                        "lap-1 speed trace"}
    acc = np.array([l["accelMps2"] for _, l in launches])
    rea = np.array([l["reactionS"] for _, l in launches])
    r2 = np.array([l["r2"] for _, l in launches])
    acc_med, acc_sigma, acc_se = robust_location_scale(acc)
    rea_med, rea_sigma, rea_se = robust_location_scale(rea)

    per_driver = {}
    for driver in sorted({l["driver"] for _, l in launches}):
        a = np.array([l["accelMps2"] for _, l in launches if l["driver"] == driver])
        r = np.array([l["reactionS"] for _, l in launches if l["driver"] == driver])
        if a.size < 3:
            continue
        am, _, ase = robust_location_scale(a)
        rm, _, rse = robust_location_scale(r)
        # source is stated once on the block above rather than 44 times over
        per_driver[driver] = {
            "accelMps2": leaf(am, ase, n=int(a.size), provenance="DERIVED"),
            "reactionSeconds": leaf(rm, rse, n=int(r.size), provenance="DERIVED"),
        }
    slow = int((acc < acc_med - 3 * acc_sigma).sum())
    n_sessions = len({k for k, _ in launches})

    # How much of the car-to-car spread is the DRIVER and how much is the same driver
    # varying race to race. Without this a caller would read `perDriver` as the whole
    # story; measured, most of it is not.
    signal = {"provenance": "DERIVED",
              "note": "unavailable: fewer than five drivers have three or more launches"}
    if len(per_driver) >= 5:
        signal["note"] = ("how much of the car-to-car spread belongs to the driver and "
                          "how much is the same driver varying from start to start")
        for field, key, pop in (("accelMps2", "accelerationMps2", acc_sigma),
                                ("reactionSeconds", "reactionSeconds", rea_sigma)):
            v = np.array([d[field]["value"] for d in per_driver.values()])
            e = np.array([d[field]["se"] for d in per_driver.values()])
            raw = float(np.std(v, ddof=1))
            mean_se = float(np.sqrt(np.mean(e ** 2)))
            true = float(np.sqrt(max(0.0, raw ** 2 - mean_se ** 2)))
            signal[key] = {
                "betweenDriverSd": round(raw, 6),
                "meanEstimateSe": round(mean_se, 6),
                "betweenDriverSdCorrected": round(true, 6),
                "populationSd": round(float(pop), 6),
                "nDrivers": len(per_driver),
                "provenance": "DERIVED",
                "note": "the raw between-driver SD minus the estimation error in "
                        "quadrature. Compare it with populationSd: what is left is the "
                        "SAME driver varying from start to start, which is the larger "
                        "part and must not be attributed to the driver",
            }

    return {
        "accelerationMps2": leaf(
            acc_med, acc_se, n=int(acc.size), provenance="DERIVED", source=source_tel(),
            note=f"median of per-car constant-acceleration fits over {n_sessions} "
                 f"standing starts; robust population SD {acc_sigma:.2f}, p5 "
                 f"{np.percentile(acc, 5):.2f}, p95 {np.percentile(acc, 95):.2f}, range "
                 f"{acc.min():.2f}-{acc.max():.2f}. {slow} launches sit more than 3 "
                 f"robust SD below the median (a car creeping off the line, or a held "
                 f"speed channel); they are KEPT in the sample, which is why the location "
                 f"is a median and not a mean"),
        "reactionSeconds": leaf(
            rea_med, rea_se, n=int(rea.size), provenance="DERIVED", source=source_tel(),
            note=f"intercept of the same fit, in the feed's lap-1 time base; robust SD "
                 f"{rea_sigma:.3f} s, p5 {np.percentile(rea, 5):.2f}, p95 "
                 f"{np.percentile(rea, 95):.2f}, range {rea.min():.2f}-{rea.max():.2f}. "
                 f"Any constant offset between that time base and the actual lights-out "
                 f"instant is NOT identifiable from this feed, so the car-to-car SPREAD "
                 f"is measured and the LEVEL carries that caveat"),
        "populationSigma": {
            "accelerationMps2": leaf(
                acc_sigma, n=int(acc.size), provenance="DERIVED", source=source_tel(),
                note="1.4826 x MAD: the car-to-car spread a caller should draw from, "
                     "never the SE of the median"),
            "reactionSeconds": leaf(rea_sigma, n=int(rea.size), provenance="DERIVED",
                                    source=source_tel()),
        },
        "perDriver": per_driver,
        "perDriverSignal": signal,
        "modelFit": {
            "form": "v(t) = a * (t - t0), fitted per car on the first contiguous rise "
                    "from rest up to the validity ceiling",
            "r2Median": round(float(np.median(r2)), 4),
            "r2P5": round(float(np.percentile(r2, 5)), 4),
            "nLaunches": int(acc.size),
            "nSessions": n_sessions,
            "provenance": "DERIVED",
            "note": "the shape was MEASURED, not assumed: this is how much of the rise a "
                    "straight line through it explains",
        },
        "validToSpeedKph": leaf(
            LAUNCH_FIT_CEILING_KPH, provenance="DERIVED", source=source_tel(),
            note="the constant-acceleration law is fitted only to here; above it the "
                 "caller's own pace model owns the car, and launch_profile reports the "
                 "HANDOVER phase from exactly this speed"),
        "refusedFits": {
            "value": int(sum(v.get("nLaunchRefused", 0) for v in standing.values())),
            "provenance": "DERIVED",
            "note": "stationary, non-pit-lane cars whose lap-1 speed trace did not meet "
                    "every fit gate (too few samples, too short a rise, or an R^2 below "
                    "the floor). They are counted, not quietly dropped",
        },
    }


GEOMETRY_DEFINITION = (
    "slotPitchMetres divided by the speed a car is actually doing when it crosses the "
    "timing line at the END of lap 1. Slot k drives (k - 1) x pitch metres further than "
    "pole on lap 1 (see lap1_distance_m), and those metres are appended where the car is "
    "at line speed, so 1 / that speed -- NOT 1 / the lap average speed, which is 20-30% "
    "lower -- is the rate at which they become seconds. The crossing speed is OBSERVED "
    "from the first telemetry sample of lap 2, which sits on the line the car has just "
    "crossed; the per-session location is a median over its cars"
)


def _slot_geometry_block(starts: dict, grid_block: dict, slope_rows: list):
    """(block, seconds per slot, se) for the PURE GEOMETRY part of the lap-1 grid slope.

    The seconds are None, with the reason stated, whenever the feed cannot support them.
    Only sessions that actually contribute a green lap-1 row to the slope are pooled: a
    session with no such row is reported for completeness and said to be excluded, rather
    than quietly averaged into a figure it does not belong to.
    """
    pitch_leaf = (grid_block or {}).get("slotPitchMetres") or {}
    pitch, pitch_se = pitch_leaf.get("value"), pitch_leaf.get("se")
    block = {"provenance": "DERIVED", "source": source_tel(),
             "definition": GEOMETRY_DEFINITION}
    if not pitch:
        block["summary"] = leaf(
            None, provenance="DERIVED",
            note="unavailable: no grid pitch was measured, so the extra distance a slot "
                 "drives has no length and its time cost cannot be stated")
        return block, None, None

    clusters = {r["cluster"] for r in slope_rows}
    per_track, pooled = {}, []
    for (event, session), st in sorted(starts.items()):
        grid = st.get("grid") or {}
        v_kph, v_se_kph = grid.get("lineSpeedKph"), grid.get("lineSpeedSeKph")
        v_n = int(grid.get("lineSpeedN") or 0)
        if not v_kph or v_n < 1:
            continue
        g = geometric_seconds_per_slot(pitch, v_kph / 3.6)
        rel = [pitch_se / pitch] if pitch_se else []
        if v_se_kph:
            rel.append(v_se_kph / v_kph)
        g_se = g * math.sqrt(sum(x * x for x in rel)) if rel else None
        in_slope = f"{event}|{session}" in clusters
        per_track[_key(event, session)] = leaf(
            g, g_se, n=v_n, provenance="DERIVED", source=source_tel(),
            note=f"crossing speed {v_kph:.1f} kph over {v_n} cars"
                 + ("" if in_slope else "; contributes no green lap-1 row, so it is "
                                        "reported but NOT pooled below"))
        if in_slope:
            pooled.append(g)

    if not pooled:
        block["summary"] = leaf(
            None, provenance="DERIVED",
            note="unavailable: no session in the lap-1 slope sample has a measured "
                 "timing-line crossing speed, so the geometric share cannot be separated")
        block["perTrack"] = per_track
        return block, None, None

    med, _sigma, med_se = robust_location_scale(pooled)
    across = float(med_se) if med_se is not None and np.isfinite(med_se) else 0.0
    from_pitch = med * (pitch_se / pitch) if pitch_se else 0.0
    se = float(math.hypot(across, from_pitch)) or None
    lo, hi = float(min(pooled)), float(max(pooled))
    every = [v["value"] for v in per_track.values() if v.get("value")]
    block["summary"] = leaf(
        med, se, n=len(pooled), provenance="DERIVED", source=source_tel(),
        note=f"median over the {len(pooled)} race starts the lap-1 slope is fitted on, "
             f"which is the only sample it may be subtracted from. It is a PER-CIRCUIT "
             f"fact, not a constant: {lo:.3f}-{hi:.3f} s per slot over those starts, and "
             f"{min(every):.3f}-{max(every):.3f} s (a factor of "
             f"{max(every) / min(every):.2f}) over all {len(every)} sessions with a "
             f"measured crossing speed. A caller holding ONE circuit should read perTrack "
             f"rather than this median. The SE combines the spread across the pooled "
             f"starts with the pitch's own SE")
    block["perTrack"] = per_track
    return block, float(med), se


def _lap1_block(starts: dict, launch_block: dict, grid_block: dict | None = None) -> dict:
    obs = lap1_observations(starts)
    rows = obs["rows"]
    if not rows:
        return {"provenance": "DERIVED",
                "note": "unavailable: no green lap-1 row with a recorded lap time",
                "excluded": {**obs["skipped"], "provenance": "DERIVED"}}
    ex = np.array([r["excessS"] for r in rows])
    clusters = [r["cluster"] for r in rows]
    ex_mean, ex_se, ex_n, ex_k = cluster_mean(ex, clusters)
    session_means = {c: float(np.mean([r["excessS"] for r in rows if r["cluster"] == c]))
                     for c in sorted(set(clusters))}

    slope_rows = [r for r in rows if r["gridRank"]]
    slope_fit = grid_slope_fit(slope_rows)
    lap1_slope = ((slope_fit["slope"], slope_fit["slopeSe"], slope_fit["n"],
                   slope_fit["nClusters"]) if slope_fit else None)

    # The intercept is a function of BOTH the level and the slope, fitted on the SAME
    # race starts, so its uncertainty is neither the mean's SE nor the slope's. The
    # delete-one-start jackknife refits the whole thing without each start in turn and
    # therefore carries their covariance; the delta method with that covariance assumed
    # zero is reported beside it in the note as a cross-check, never as the shipped SE.
    pole_se, pole_k, delta_se = None, 0, float("nan")
    kbar_se = kbar_lo = kbar_hi = None
    if slope_fit:
        pole_se, pole_k = delete_one_cluster_se(
            slope_rows, lambda rr: (grid_slope_fit(rr) or {}).get("intercept"))
        cl = [r["cluster"] for r in slope_rows]
        _lm, level_se, _ln, _lk = cluster_mean([r["excessS"] for r in slope_rows], cl)
        if np.isfinite(level_se):
            delta_se = math.hypot(
                level_se, (slope_fit["meanGridSlot"] - 1) * slope_fit["slopeSe"])
        ranks = [float(r["gridRank"]) for r in slope_rows]
        _kb, kbar_se_raw, _kn, _kk = cluster_mean(ranks, cl)
        kbar_se = float(kbar_se_raw) if np.isfinite(kbar_se_raw) else None
        kbar_lo, kbar_hi = min(ranks), max(ranks)

    geometry_block, geom_s, geom_se = _slot_geometry_block(starts, grid_block or {},
                                                           slope_rows)

    # Does the straight line actually reproduce the feed? The fit is a line through 145
    # correlated rows; what a caller will USE it for is "the excess at slot k", so the
    # residual that matters is per slot, and it ships rather than being asserted.
    quality = {"provenance": "DERIVED",
               "note": "unavailable: no grid slope was fitted, so there is nothing to "
                       "check it against"}
    if slope_fit:
        by_slot = {}
        for r in slope_rows:
            by_slot.setdefault(int(r["gridRank"]), []).append(float(r["excessS"]))
        a, b = slope_fit["intercept"], slope_fit["slope"]
        resid = {k: float(np.mean(v)) - (a + b * (k - 1)) for k, v in by_slot.items()}
        dense = {k: v for k, v in by_slot.items() if len(v) >= SLOT_MIN_ROWS}
        rd = np.array([resid[k] for k in dense], dtype=np.float64)
        ra = np.array(list(resid.values()), dtype=np.float64)
        within = [float(np.std(v, ddof=1)) for v in dense.values() if len(v) > 1]
        thin = sorted(k for k in by_slot if len(by_slot[k]) < SLOT_MIN_ROWS)
        rms = float(np.sqrt(np.mean(rd ** 2))) if rd.size else None
        sd = float(np.median(within)) if within else None
        rms_all = float(np.sqrt(np.mean(ra ** 2))) if ra.size else None
        quality = {
            "provenance": "DERIVED", "source": source_laps(),
            "slotMeanResidualRmsSeconds": round(rms, 4) if rms is not None else None,
            "slotMeanResidualMaxAbsSeconds": (round(float(np.abs(rd).max()), 4)
                                              if rd.size else None),
            "withinSlotSdSeconds": round(sd, 4) if sd is not None else None,
            "nSlots": len(dense),
            "nSlotsAll": len(by_slot),
            "minRowsPerSlot": SLOT_MIN_ROWS,
            "allSlotResidualRmsSeconds": (round(rms_all, 4) if rms_all is not None
                                          else None),
            "r2": round(slope_fit["r2"], 4),
            "note": (f"the shipped line, excessSecondsAtPole + excessSecondsPerGridSlot "
                     f"* (k - 1), against the MEASURED mean excess at each grid slot. "
                     + (f"Over the {len(dense)} slots filled at {SLOT_MIN_ROWS} or more "
                        f"starts the residual RMS is {rms:.3f} s"
                        + (f", well inside the {sd:.3f} s SD of a single slot's own rows,"
                           f" so the line is not hiding a curve" if sd else "")
                        + ". " if rms is not None else
                        f"NO slot is filled at {SLOT_MIN_ROWS} or more starts, so the "
                        f"headline residual is null rather than a figure resting on one "
                        f"car. ")
                     + (f"Including the thin back of the grid (slots "
                        f"{', '.join(str(k) for k in thin)}, fewer than {SLOT_MIN_ROWS} "
                        f"rows each) the RMS is {rms_all:.3f} s -- those slots are "
                        f"REPORTED, not dropped, and their misses are the sampling noise "
                        f"of one or two cars, not evidence against the line"
                        if thin and rms_all is not None else
                        "Every slot carries enough rows to be counted in that figure")),
        }

    pos_rows = obs["placeRows"]
    reg = regression_to_mean([r["gridRank"] for r in pos_rows],
                             [r["endRank"] for r in pos_rows])
    green_rows = [r for r in pos_rows if r["green"]]
    reg_green = regression_to_mean([r["gridRank"] for r in green_rows],
                                   [r["endRank"] for r in green_rows])
    buckets = {}
    for name, lo, hi in (("slots1to5", 1, 5), ("slots6to10", 6, 10),
                         ("slots11to15", 11, 15), ("slot16andBack", 16, 99)):
        g = np.array([float(r["gridRank"] - r["endRank"]) for r in pos_rows
                      if lo <= r["gridRank"] <= hi])
        if g.size < 3:
            continue
        buckets[name] = leaf(
            float(g.mean()), float(np.std(g, ddof=1) / math.sqrt(g.size)),
            n=int(g.size), provenance="DERIVED", source=source_laps(),
            note="mean places GAINED between the grid and the end of lap 1; negative "
                 "means places lost")

    # Split the measured lap-1 penalty into the part the launch physics already explains
    # and the part it does not, so a caller cannot charge the same seconds twice.
    split = {"provenance": "DERIVED",
             "note": "unavailable: no launch was fitted, so the penalty cannot be split"}
    acc_leaf = (launch_block or {}).get("accelerationMps2")
    rea_leaf = (launch_block or {}).get("reactionSeconds")
    if isinstance(acc_leaf, dict) and acc_leaf.get("value"):
        handover = LAUNCH_FIT_CEILING_KPH / 3.6
        p = LaunchParams(rea_leaf["value"], acc_leaf["value"], handover)
        loss = launch_time_loss_s(p)
        split = {
            "launchLossSeconds": leaf(
                loss, n=acc_leaf["n"], provenance="DERIVED", source=source_tel(),
                note=f"reaction + handoverSpeed / (2 * a) at the fitted medians, handing "
                     f"over at the {LAUNCH_FIT_CEILING_KPH:.0f} kph validity ceiling. "
                     f"This is the only part of the lap-1 penalty that FOLLOWS from the "
                     f"launch, and launch_state reproduces it by construction"),
            "remainderSeconds": leaf(
                ex_mean - loss, ex_se, n=ex_n, provenance="DERIVED", source=source_laps(),
                note="what is left of the measured lap-1 penalty once the launch is "
                     "accounted for: first-corner congestion, cold tyres and a full fuel "
                     "load. It is NOT decomposed further, because this data cannot "
                     "separate those three"),
            "provenance": "DERIVED",
        }
        if slope_fit:
            split["remainderSecondsAtPole"] = leaf(
                slope_fit["intercept"] - loss, pole_se, n=slope_fit["n"],
                provenance="DERIVED", source=source_laps(),
                note="the same subtraction at grid slot 1 instead of at the field mean. "
                     "A caller that launches cars from their own slot stations and then "
                     "adds nonGeometricSecondsPerGridSlot per slot must start from THIS "
                     "number: remainderSeconds above still has the whole grid's average "
                     "grid-position penalty inside it, and adding a per-slot term to it "
                     "charges that penalty a second time")

    return {
        "excessSecondsVsCleanLap": leaf(
            ex_mean, ex_se, n=ex_n, provenance="DERIVED", source=source_laps(),
            note=f"lap-1 time minus this driver's own fitted clean-lap time at lap 1, "
                 f"tyre life 1 -- the model fit_params already ships, evaluated out of "
                 f"sample. The SE is cluster-robust over {ex_k} green race starts, not "
                 f"over {ex_n} correlated cars. Per-session means span "
                 f"{min(session_means.values()):.2f}-{max(session_means.values()):.2f} s"),
        "perSessionExcessSeconds": {
            "provenance": "DERIVED", "source": source_laps(),
            "note": "the mean excess at each green start on its own; the spread between "
                    "these is what the cluster-robust SE above is built from",
            "perSession": {c.replace("|", " "): round(v, 4)
                           for c, v in session_means.items()}},
        "excessSecondsAtPole": (
            dict(leaf(slope_fit["intercept"], pole_se, n=slope_fit["n"],
                      provenance="DERIVED", source=source_laps(),
                      note=f"the INTERCEPT of the lap-1 penalty: what a SLOT-1 car loses, "
                           f"with no grid-position term inside it. This -- never "
                           f"excessSecondsVsCleanLap, which is a MEAN over the whole grid "
                           f"-- is the number to pair with a per-slot slope. Using that "
                           f"mean as the intercept and then adding a slope per slot "
                           f"charges every car a further "
                           f"{ex_mean - slope_fit['intercept']:.3f} s it never lost. "
                           f"Derived from the identity that holds inside ONE sample, "
                           f"mean = intercept + slope * (meanGridSlot - 1), so it equals "
                           f"the n-weighted mean of the fitted session effects. "
                           f"levelSeconds below is that mean over the same "
                           f"{slope_fit['n']} ranked green rows the slope is fitted on, "
                           f"NOT the {ex_n}-row grand mean {ex_mean:.4f} s: substituting "
                           f"the grand mean would move this intercept by "
                           f"{ex_mean - slope_fit['level']:+.3f} s, because the identity "
                           f"is only true within one row set. The SE is a "
                           f"delete-one-race-start jackknife over {pole_k} starts -- the "
                           f"level and the slope come from the SAME starts, so their "
                           f"covariance is real, and refitting without each start in turn "
                           f"carries it; the delta method with that covariance assumed "
                           f"zero gives {delta_se:.3f} s for comparison"),
                 levelSeconds=round(slope_fit["level"], 6),
                 nRaceStarts=slope_fit["nClusters"],
                 seMethod="delete-one-race-start jackknife")
            if slope_fit else leaf(
                None, provenance="DERIVED",
                note="unavailable: too few ranked green rows to separate a level from a "
                     "slope, so there is no intercept and none is invented")),
        "meanGridSlot": (
            leaf(slope_fit["meanGridSlot"], kbar_se, n=slope_fit["n"],
                 provenance="DERIVED", source=source_laps(),
                 note=f"the mean grid rank of the rows the slope and the intercept are "
                      f"built from: the pivot the identity mean = intercept + slope * "
                      f"(meanGridSlot - 1) turns on. It ships so that the intercept is "
                      f"reconstructible and so that nobody re-derives it against a "
                      f"different sample. Ranks {kbar_lo:.0f} to {kbar_hi:.0f} over "
                      f"{slope_fit['nClusters']} race starts; the SE is the spread of the "
                      f"per-start mean rank, not of the {slope_fit['n']} correlated rows")
            if slope_fit else leaf(
                None, provenance="DERIVED",
                note="unavailable: no ranked green rows, so the sample has no mean rank")),
        "gridSlopeFitQuality": quality,
        "excessSecondsPerGridSlot": (
            leaf(lap1_slope[0], lap1_slope[1], n=lap1_slope[2], provenance="DERIVED",
                 source=source_laps(),
                 note=f"within-session slope down the grid, {lap1_slope[3]} session fixed "
                      f"effects. This is the TOTAL: part of it is pure GEOMETRY -- slot k "
                      f"drives (k - 1) x pitch further on lap 1 -- and the rest is "
                      f"traffic. A caller that advances cars from their own slot stations "
                      f"(as launch_state does) already reproduces the geometric part, so "
                      f"it must NOT add this figure. That subtraction has already been "
                      f"done here: read geometricSecondsPerGridSlot for the part the "
                      f"geometry supplies and nonGeometricSecondsPerGridSlot for the part "
                      f"it does not, and add the second of the two")
            if lap1_slope else leaf(None, provenance="DERIVED",
                                    note="unavailable: too few ranked green rows")),
        "geometricSecondsPerGridSlot": geometry_block,
        "nonGeometricSecondsPerGridSlot": (
            leaf(lap1_slope[0] - geom_s, math.hypot(lap1_slope[1], geom_se or 0.0),
                 n=lap1_slope[2], provenance="DERIVED", source=source_laps(),
                 note=f"THE per-slot number to add when cars are advanced from their own "
                      f"slot stations: excessSecondsPerGridSlot minus "
                      f"geometricSecondsPerGridSlot, i.e. the traffic and first-corner "
                      f"part the geometry does not already produce. Measured, geometry is "
                      f"{geom_s:.3f} s of the {lap1_slope[0]:.3f} s total "
                      f"({100.0 * geom_s / lap1_slope[0]:.0f}%), so a caller that adds the "
                      f"whole slope on top of its own slot stations charges each slot "
                      f"{geom_s:.3f} s twice. The two terms are measured from different "
                      f"channels -- lap times and the speed trace -- and are treated as "
                      f"independent in this SE")
            if (lap1_slope and geom_s is not None) else leaf(
                None, provenance="DERIVED",
                note="unavailable: " + ("no grid slope was fitted" if not lap1_slope else
                                        "the geometric share could not be measured, so "
                                        "the non-geometric remainder cannot be stated "
                                        "and is not guessed"))),
        "penaltySplit": split,
        "excessSampleSpread": {
            "sd": round(float(np.std(ex, ddof=1)), 4),
            "median": round(float(np.median(ex)), 4),
            "min": round(float(ex.min()), 4),
            "max": round(float(ex.max()), 4),
            "provenance": "DERIVED",
        },
        "excluded": {
            "noLapOneTime": obs["skipped"]["noLapTime"],
            "notGreen": obs["skipped"]["notGreen"],
            "pitLaneStart": obs["skipped"]["pitStart"],
            "noCleanLapModel": obs["skipped"]["noModel"],
            "provenance": "DERIVED",
            "note": "a neutralised lap-1 row is not a slow green lap; pooling the two "
                    "would report one circuit's safety car as everybody's launch",
        },
        "placesGainedByGridSlot": {
            "interceptPlaces": (
                leaf(reg["intercept"], reg["interceptSe"], n=reg["n"],
                     provenance="DERIVED", source=source_laps(),
                     note="places gained at the field's mean grid slot; 0 by "
                          "construction, which is the check that the two rankings are "
                          "consistent with each other")
                if reg else leaf(None, provenance="DERIVED", note="unavailable")),
            "slopePlacesPerGridSlot": (
                leaf(reg["slope"], reg["slopeSe"], n=reg["n"], provenance="DERIVED",
                     source=source_laps(),
                     note="regression to the mean. POSITIVE means places gained rises "
                          "with grid slot -- the front of the grid loses places on lap 1 "
                          "and the back of it gains them. Every start counts here, "
                          "neutralised or not, because a lap-1 safety car is usually the "
                          "CONSEQUENCE of the first-corner incident that moved everybody")
                if reg else leaf(None, provenance="DERIVED", note="unavailable")),
            "slopeGreenStartsOnly": (
                leaf(reg_green["slope"], reg_green["slopeSe"], n=reg_green["n"],
                     provenance="DERIVED", source=source_laps(),
                     note="the same slope over the green starts alone, so a caller can "
                          "see how much of the effect is the incidents")
                if reg_green else leaf(None, provenance="DERIVED", note="unavailable")),
            "buckets": buckets,
        },
    }


def fit_standing_start(sessions=None) -> dict:
    """Every standing-start parameter, each leaf carrying value/n/se/provenance/source."""
    pairs = list(sessions) if sessions is not None else race_sessions()
    starts = {(event, session): session_start(event, session) for event, session in pairs}
    standing = {k: v for k, v in starts.items() if v["standingStart"]}
    with_grid = {k: v for k, v in standing.items() if v["grid"]}

    refusals = {_key(e, s): v["refusals"] for (e, s), v in starts.items() if v["refusals"]}

    launch_block = _launch_block(standing)
    grid_block = _grid_block(with_grid) if with_grid else {
        "provenance": "DERIVED",
        "note": "unavailable: no session in the feed has a usable stationary grid"}
    return {
        "grid": grid_block,
        "launch": launch_block,
        # the lap-1 block needs the measured pitch to separate the geometric part of the
        # per-slot penalty from the traffic part, so the grid is built first and passed in
        "lap1": _lap1_block(starts, launch_block, grid_block),
        "coverage": {
            "sessionsScanned": len(starts),
            "standingStartsInFeed": len(standing),
            "sessionsWithUsableGrid": len(with_grid),
            "provenance": "DERIVED",
            "note": "a session counts as a standing start only if most of its cars are "
                    "stationary at the lap-1 time origin in the feed itself",
        },
        "unavailable": refusals,
    }


def main():
    """Print the fitted block as a report. Writes nothing: fit_params.py owns the artifact."""
    import json
    print(json.dumps(fit_standing_start(), indent=1, default=str))


if __name__ == "__main__":
    main()
