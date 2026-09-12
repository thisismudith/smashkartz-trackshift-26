"""Build a track model from telemetry alone.

Every step here is one of the measured recommendations in the plan. Nothing about the
circuit is typed in; the only RULE values are the grid anchor and stagger, which the
position feed provably does not contain.

This module owns the POSITION POLICY for the track model. `rawio` measures
(`Lap.position_quality`, `Lap.stuck`) and `geom` projects; deciding which laps and which
samples are good enough to define a circuit, a timing line, a pit lane or a grid is done
here and nowhere else, so there is one place to argue with.
"""
from __future__ import annotations

import math
import warnings

import numpy as np

from .geom import Ring, close_ring, resample_by_arclength, smooth_circular
from .rawio import LapTable, load_lap, read_json

XY_SMOOTH_M = 11.0    # measured: <=1 m deviation, keeps 20 m hairpins
Z_SMOOTH_M = 31.0     # measured: 2nd-diff std <=0.0007 m, max dev <=0.27 m
MEDIAN_LAPS = 60      # measured: halves position and heading jitter
LATERAL_REJECT_M = 5.0
DS = 1.0

# --------------------------------------------------------------------------------
# Position-quality policy for GEOMETRY laps.
#
# `Lap.position_quality()` measures; the cuts below are this module's policy. Measured
# over the 60 laps `pick_geometry_laps` returns at each of the 13 events (pinned by
# test_track.py::test_geometry_gates_separate_hungary_from_every_clean_event):
#
#   uniqueFrac      Hungarian Race p05 0.381 / median 0.405 | every other event >= 0.980
#   repeatBackFrac  Hungarian Race p95 0.500                | every other event <= 0.016
#   medianStepM     Hungarian Race 0.011-0.017 m            | every other event >= 3.99 m
#
# `pathOverSpan` is deliberately NOT one of the gates. A zigzag around a stale anchor
# adds almost no length, so the ratio is a weak discriminator: 15.8 % of Hungary's
# corrupt laps land inside [0.97, 1.03] by chance and a ratio-only gate still admits 58
# of the 60 laps that build the ring. The three above separate by an order of magnitude.
GEOM_MIN_UNIQUE_FRAC = 0.95
GEOM_MAX_REPEAT_BACK_FRAC = 0.10
GEOM_MIN_STEP_M = 1.0

# `build_ring` welds the reference lap's last sample to its first, so a reference that
# does not close adds a phantom straight to the circuit. Measured at Hungary: once the
# stale-hold laps are rejected the fastest survivor traces a 1085.7 m first-to-last gap,
# and welding it produced a 7489 m "ring" for a 4327 m circuit. The reference laps
# actually chosen at the 13 events close to 0.3-11.2 m.
REF_MAX_END_GAP_M = 50.0


def _lap_index(table: LapTable):
    """(driver, lap) -> row dict, plus the clean mask."""
    rows = list(table.rows())
    clean = table.clean_mask()
    idx = {}
    for r, ok in zip(rows, clean):
        idx[(r["drv"], r["lap"])] = (r, ok)
    return idx


def geometry_lap_reject(lap, min_unique=GEOM_MIN_UNIQUE_FRAC,
                        max_repeat_back=GEOM_MAX_REPEAT_BACK_FRAC,
                        min_step=GEOM_MIN_STEP_M):
    """Why this lap's POSITIONS may not define the circuit's shape, or None if they may.

    A reason string rather than a bool so a caller can report what it discarded; a gate
    that silently shrinks its own sample is how Hungary shipped a 5.6 % stretched ring.
    """
    q = lap.position_quality()
    uf, rb, ms = q["uniqueFrac"], q["repeatBackFrac"], q["medianStepM"]
    if uf is None or ms is None:
        return "no-positions"
    if uf < min_unique:
        return f"unique-frac {uf:.3f}"
    if rb is not None and rb > max_repeat_back:
        return f"repeat-back {rb:.3f}"
    if ms < min_step:
        return f"median-step {ms:.3f}m"
    return None


def pick_geometry_laps(session_dir, table: LapTable, limit=MEDIAN_LAPS, rejected=None):
    """Clean laps with usable positions, fastest first, spread across drivers.

    `rejected`, if given, is a dict that receives reason -> count for every lap thrown
    away, so the caller can report the loss instead of hiding it.
    """
    idx = _lap_index(table)
    cands = [(r["time"], drv, lap) for (drv, lap), (r, ok) in idx.items() if ok]
    cands.sort()
    picked, per_driver = [], {}
    log = rejected if rejected is not None else {}
    # first pass: at most 4 per driver so one car cannot define the line
    for t, drv, lap in cands:
        if per_driver.get(drv, 0) >= 4:
            continue
        l = load_lap(session_dir, drv, lap)
        if l is None:
            log["missing"] = log.get("missing", 0) + 1
            continue
        if not l.geometry_valid():
            log["geometry-valid"] = log.get("geometry-valid", 0) + 1
            continue
        why = geometry_lap_reject(l)
        if why is not None:
            key = why.split()[0]
            log[key] = log.get(key, 0) + 1
            continue
        picked.append(l)
        per_driver[drv] = per_driver.get(drv, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def _reference_lap(laps, max_end_gap_m=REF_MAX_END_GAP_M):
    """Index of the fastest lap that actually CLOSES, plus its gap and whether it had
    to be forced (no lap closed inside the bound, so the tightest one is used)."""
    gaps = []
    for l in laps:
        g = l.position_quality()["endGapM"]
        gaps.append(float("inf") if g is None else float(g))
    for i, g in enumerate(gaps):
        if g <= max_end_gap_m:
            return i, g, False
    i = int(np.argmin(gaps)) if gaps else 0
    return i, (gaps[i] if gaps else float("nan")), True


def build_ring(laps, max_end_gap_m=REF_MAX_END_GAP_M):
    """Median-of-N centreline. `laps` must be ordered fastest first.

    The reference is the fastest lap that CLOSES, not simply `laps[0]`: `close_ring`
    joins the reference's last sample to its first, so an over-long or truncated
    reference silently welds its own end gap into the circuit.

    Returns (ring, provisional_ring). The returned ring carries `.build_report`, a dict
    naming the reference lap, its closure gap and how many laps fed the median.
    """
    ref_i, ref_gap, forced = _reference_lap(laps, max_end_gap_m)
    ref = laps[ref_i]
    x, y, z, _ = resample_by_arclength(ref.x, ref.y, ref.z, DS)
    x, y, z, _ = close_ring(x, y, z, DS)
    prov = Ring(x, y, z, DS)

    # A lap whose positions cannot define the shape cannot define the median either.
    # `pick_geometry_laps` already applies this; repeating it here makes `build_ring`
    # safe for any caller instead of trusting one.
    usable = [l for l in laps if geometry_lap_reject(l) is None]
    filtered = len(usable) >= 5
    if not filtered:
        usable = list(laps)

    n = prov.n
    lat_acc = [[] for _ in range(n)]
    z_acc = [[] for _ in range(n)]
    for l in usable:
        lx, ly, lz, _ = resample_by_arclength(l.x, l.y, l.z, DS)
        # The stateless projection is kept here on purpose. It DOES alias across
        # Suzuka's crossover (529 ring vertex pairs within 12 m of each other but
        # 2359 m apart in station), but the per-station median over 60 laps absorbs
        # it: measured, swapping in Ring.project_path moves the built ring by at most
        # 0.001 m at Suzuka and 0.011 m at Silverstone, for +5.1 s per circuit. The
        # continuity-aware projection is used where it changes an answer instead --
        # see pit_runs, where a single run is not medianed against anything.
        st, lat = prov.project(lx, ly)
        ok = np.isfinite(st) & (np.abs(lat) < LATERAL_REJECT_M)
        bins = (np.round(st[ok] / DS).astype(int)) % n
        for b, la, zz in zip(bins, lat[ok], lz[ok]):
            lat_acc[b].append(la)
            z_acc[b].append(zz)

    med_lat = np.array([np.median(v) if v else 0.0 for v in lat_acc])
    med_z = np.array([np.median(v) if v else np.nan for v in z_acc])
    # stations no lap reached keep the provisional elevation
    miss = ~np.isfinite(med_z)
    med_z[miss] = prov.z[miss]

    mx = prov.x + med_lat * prov.nx
    my = prov.y + med_lat * prov.ny
    mx, my, mz, _ = close_ring(mx, my, med_z, DS)

    sx = smooth_circular(mx, XY_SMOOTH_M, DS)
    sy = smooth_circular(my, XY_SMOOTH_M, DS)
    sz = smooth_circular(mz, Z_SMOOTH_M, DS)
    ring = Ring(sx, sy, sz, DS)
    ring.build_report = {
        "referenceDriver": ref.driver, "referenceLap": int(ref.lap),
        "referenceEndGapM": round(float(ref_gap), 2) if np.isfinite(ref_gap) else None,
        "referenceWasForced": bool(forced),
        "lapsOffered": len(laps), "lapsInMedian": len(usable),
        "medianLapsFiltered": bool(filtered),
        "provenance": ("DERIVED: median-of-N centreline; reference lap is the fastest "
                        "lap whose trace closes"),
    }
    return ring, prov


def _lap_axis(session_dir, idx, lap, row):
    """This lap's (t, x, y) finite samples, EXTENDED with the next lap's telemetry.

    Every {lap}_tel.json spans exactly [0, lapTime], while the start/finish request is
    t_rel = s3T - lST = lapTime + a small offset, so it falls past the end of the array
    on 2551 of 2610 sampled clean laps (median excess +0.023..+0.092 s, +1.607 s at
    Zandvoort where lST itself is early). np.interp does not extrapolate -- it CLAMPS --
    so the S/F line was measuring "the last telemetry sample", not the sector clock.
    Appending lap N+1 on lap N's clock puts the request inside real telemetry.
    """
    m = np.isfinite(lap.x) & np.isfinite(lap.y) & np.isfinite(lap.t)
    t, x, y = lap.t[m], lap.x[m], lap.y[m]
    if t.size < 2:
        return t, x, y
    nxt_row, _ok = idx.get((lap.driver, lap.lap + 1), (None, False))
    offset = None
    if nxt_row is not None:
        lst, nlst = row.get("lST"), nxt_row.get("lST")
        if not isinstance(lst, str) and not isinstance(nlst, str) \
                and lst is not None and nlst is not None:
            offset = float(nlst) - float(lst)
        elif not isinstance(row.get("time"), str) and row.get("time") is not None:
            offset = float(row["time"])
    if offset is None or not (offset > 0):
        return t, x, y
    nxt = load_lap(session_dir, lap.driver, lap.lap + 1)
    if nxt is None:
        return t, x, y
    m2 = np.isfinite(nxt.x) & np.isfinite(nxt.y) & np.isfinite(nxt.t)
    if m2.sum() < 2:
        return t, x, y
    t = np.concatenate((t, nxt.t[m2] + offset))
    x = np.concatenate((x, nxt.x[m2]))
    y = np.concatenate((y, nxt.y[m2]))
    order = np.argsort(t, kind="stable")
    return t[order], x[order], y[order]


def _xy_at_lap_time(lap, t_rel, axis=None):
    """Interpolate a lap's position at a lap-relative time, REFUSING out of range.

    np.interp clamps silently, which turns "the car at the sector timestamp" into "the
    last sample of the file" without anything in the result saying so. Outside
    [t[0], t[-1]] this returns NaN and the caller counts the refusal.
    """
    if axis is None:
        m = np.isfinite(lap.x) & np.isfinite(lap.y) & np.isfinite(lap.t)
        if m.sum() < 2:
            return np.nan, np.nan
        t, x, y = lap.t[m], lap.x[m], lap.y[m]
    else:
        t, x, y = axis
    if t.size < 2 or not np.isfinite(t_rel) or t_rel < t[0] or t_rel > t[-1]:
        return np.nan, np.nan
    return float(np.interp(t_rel, t, x)), float(np.interp(t_rel, t, y))


TIMING_SIGMA_FLOOR_M = 1.0   # sigma can be 0 on a very tight line; never divide by it


def _line_stats(acc, ring: Ring, min_laps: int):
    """Robust circular scatter for one timing line.

    np.std over the raw station list is not a measurement of the line: it is dominated
    by whichever lap's position feed failed. Measured before/after on the same 60 laps:
    Japanese S/F 24.60 -> sigma 4.45 (5 laps refused), Monaco sector 2 319.85 -> 3.09
    (7 refused). The median the ring rotation actually uses is unchanged; what changes
    is that a degenerate line now shows up as a refusal count instead of hiding behind
    a big number nothing reads.
    """
    stations = acc["st"]
    if len(stations) < min_laps:
        return None
    a = np.unwrap(np.array(stations) / ring.length * 2 * np.pi) * ring.length / (2 * np.pi)
    med = float(np.median(a))
    dev = (a - med + ring.length / 2) % ring.length - ring.length / 2
    mad = float(np.median(np.abs(dev)))
    sigma = 1.4826 * mad
    keep = np.abs(dev) <= max(3.0 * sigma, TIMING_SIGMA_FLOOR_M)
    return {
        "station": float(med % ring.length),
        "sigmaMetres": float(sigma),
        "stdKeptMetres": float(np.std(dev[keep])) if keep.any() else None,
        "nTotal": int(len(stations)),
        "nKept": int(keep.sum()),
        "nRefused": int((~keep).sum()),
        "nOutOfRange": int(acc["outOfRange"]),
        "nOffLine": int(acc["offLine"]),
        "provenance": ("DERIVED: sector-timestamp position projected onto the ring, "
                        "circular median over clean laps; scatter is a robust sigma "
                        "(1.4826 x MAD) with the refused laps counted, never np.std "
                        "of an unrejected tail"),
    }


def timing_lines(session_dir, table: LapTable, ring: Ring, laps, min_laps=10,
                 max_lateral_m=LATERAL_REJECT_M):
    """Start/finish and sector stations from the SECTOR clock.

    The lap clock (lST) is 1.6 s early for 20 of 22 drivers at Zandvoort, which puts
    station 0 135 m from the physical line. The sector timestamps are consistent with
    the position feed at every circuit, so they are the authority here -- but only if
    they are actually USED: see `_lap_axis` for why the S/F request used to fall off the
    end of the lap's telemetry and silently return the last sample instead.

    Three refusals, all counted in the result rather than absorbed:
      * the requested time is outside the (extended) telemetry -> no station;
      * the position at that time is more than `max_lateral_m` off the ring, i.e. it is
        not on the racing line at all (measured at Monaco: 15 of 60 S/F points, worst
        1227 m off) -> no station;
      * the station is more than 3 robust sigma from the median -> counted as refused
        in the scatter, and excluded from it.
    """
    idx = _lap_index(table)
    keys = (("sf", "s3T"), ("s1", "s1T"), ("s2", "s2T"))
    acc = {k: {"st": [], "outOfRange": 0, "offLine": 0} for k, _ in keys}
    for l in laps:
        row, ok = idx.get((l.driver, l.lap), (None, False))
        if not ok or row is None:
            continue
        lst = row.get("lST")
        if isinstance(lst, str) or lst is None:
            continue
        axis = _lap_axis(session_dir, idx, l, row)
        for key, field in keys:
            tt = row.get(field)
            if isinstance(tt, str) or tt is None:
                continue
            px, py = _xy_at_lap_time(l, float(tt) - float(lst), axis)
            if not np.isfinite(px):
                acc[key]["outOfRange"] += 1
                continue
            st, lat = ring.project(np.array([px]), np.array([py]))
            if not np.isfinite(st[0]) or not np.isfinite(lat[0]):
                acc[key]["outOfRange"] += 1
                continue
            if abs(float(lat[0])) > max_lateral_m:
                acc[key]["offLine"] += 1
                continue
            acc[key]["st"].append(float(st[0]))
    return {key: _line_stats(acc[key], ring, min_laps) for key, _ in keys}


def corner_stations(session_dir, ring: Ring):
    """Corners located by projecting their X/Y, never by their Distance field.

    Measured: projection error 0.1-6 m; the Distance field drifts up to 37 m at Spa and
    by thousands of metres between sessions at China.
    """
    p = session_dir / "corners.json"
    if not p.exists():
        return None
    c = read_json(p)
    cx = np.array(c["X"], dtype=np.float64) * 0.1
    cy = np.array(c["Y"], dtype=np.float64) * 0.1
    st, lat = ring.project(cx, cy)
    return {
        "rotationDeg": c.get("Rotation"),
        "corners": [
            {"number": int(n), "station": float(s), "markerLateral": float(la),
             "labelAngleDeg": float(a)}
            for n, s, la, a in zip(c["CornerNumber"], st, lat, c["Angle"])
        ],
    }


def _finite_or_none(v):
    """NaN/inf are not valid JSON; an unmeasurable statistic is null, not a number."""
    v = float(v)
    return None if (math.isnan(v) or math.isinf(v)) else v


ON_TRACK_LAT_M = 3.0     # inside this the car is on the racing line, not in the lane
# Beyond this a "pit lane" is a projection artefact, not a road. 60 m was declared here
# for years and never used, and it is provably unsafe: Silverstone's lane genuinely
# reaches |lat| = 104.3 m because the circuit loops around Club while the lane runs
# straight. Measured healthy maxima: Silverstone 104.3, Canada 50.7, Austria 40.8, Spa
# 31.8, Monaco 31.9 m -- against 950 m (Hungary) and 1068 m (China) when the position
# feed freezes. 120 m clears every real lane by 15 % and still catches both failures.
MAX_PIT_LAT_M = 120.0
MERGE_RUN = 30           # consecutive on-track samples that end an out-lap head
CAR_WIDTH_M = 2.0        # HaasCarTop footprint, already in the loader constants
WIDTH_FLOOR_M = CAR_WIDTH_M / 2 + 0.25
# A modern F1 circuit must be at least 12 m wide and is typically 12-15 m, with pit
# straights reaching ~18 m. These bound the RULE-scaled half-width below.
HALF_WIDTH_MIN_M = 6.0
HALF_WIDTH_MAX_M = 7.5

# --------------------------------------------------------------------------------
# Pit-run integrity.
#
# The feed does not stop writing positions when it loses a car in the pit lane: it
# writes the same coordinate over and over while speed, gear and distance keep running.
# `rawio.stuck_mask` is the coordinate-free detector for exactly that ("the car did not
# move though its own speed channel says it must have"); a teleport test covers the
# other half, where the position jumps further than the car could possibly have gone.
#
# Measured bad-sample fraction per run, over the runs these two functions consume:
#   Hungarian entry median 0.871 / exit 0.918   (30 of 31 and 29 of 30 runs > 0.15)
#   Chinese   entry median 0.811 / exit 0.624   (21 of 21 and 19 of 19 runs > 0.15)
#   every other event       median 0.000-0.026, and 2 runs in 692 above 0.15
# So 0.15 separates cleanly in both directions. Monaco has only 6 entry and 7 exit runs
# (every other event has 19-33), which is why the minimum surviving-run count is 3 and
# not the 8 originally proposed -- Monaco's pit data is clean by every test here.
PIT_STEP_SLACK_M = 20.0       # allowance over speed*dt before a step is a teleport
PIT_MAX_BAD_FRACTION = 0.15
PIT_MIN_RUNS = 3

# The pit-lane ELEVATION channel is held while the car drives the lane: measured std
# 0.018-0.057 m over 300-600 m of road, while the ring's own z moves up to 18.6 m over
# the same stations -- which is what drew the lane 22.7 m above the tarmac at Suzuka.
#
# Two scales, because one is not enough. The FINE test catches a pin inside the feed's
# own noise; on its own it leaves the Suzuka lane looking "measured" for 60 of 80 points,
# because the held value still wobbles +-0.2 m, which is more than a tight band allows.
# The COARSE test is the one that states the defect properly: over 120 m of road the
# elevation did not change at all. Measured over the same stretch, Suzuka's lane moves
# 0.7 m while the ring beside it drops 23 m.
#
# A genuinely flat lane (Melbourne, Miami) is flagged too, and that is the right trade:
# the renderer then drapes it onto a surface that is also flat, so nothing is lost, and
# the alternative -- publishing an unverifiable elevation as OBSERVED -- is what the
# provenance contract forbids.
Z_HOLD_WINDOW = 21
Z_HOLD_BAND_M = 0.12
Z_HOLD_MOVE_M = 30.0
Z_HOLD_SPAN_M = 120.0
Z_HOLD_NET_M = 0.5

# Pit-lane speed plateau: the stretch a car covers at the limiter. Taking the median of
# the whole out-lap head instead (the old rule) included the post-exit acceleration and
# read 99.1 km/h at Hungary and 89.4 at Monaco against 79-80 elsewhere, so anything
# derived from it inherited the error.
PLATEAU_BAND_KPH = 4.0
PLATEAU_MIN_SAMPLES = 12
PLATEAU_LO_KPH = 30.0
PLATEAU_HI_KPH = 110.0
PIT_SPEED_MARGIN_KPH = 10.0   # above the measured plateau the car is no longer in the lane


def _pit_position_bad(lap, slack_m=PIT_STEP_SLACK_M):
    """Samples on a pit lap whose POSITION is not a measurement."""
    bad = np.asarray(lap.stuck, dtype=bool).copy()
    x, y, t, v = lap.x, lap.y, lap.t, lap.speed
    if lap.n >= 2:
        dt = np.abs(np.diff(t))
        step = np.hypot(np.diff(x), np.diff(y))
        allow = np.abs(v[:-1]) / 3.6 * dt + slack_m
        tele = np.isfinite(step) & np.isfinite(allow) & (step > allow)
        bad[:-1] |= tele
        bad[1:] |= tele
    bad |= ~(np.isfinite(x) & np.isfinite(y))
    return bad


def _plateau_speed(v, band=PLATEAU_BAND_KPH, min_samples=PLATEAU_MIN_SAMPLES,
                   lo=PLATEAU_LO_KPH, hi=PLATEAU_HI_KPH):
    """Median speed of the longest near-constant run inside [lo, hi], or None."""
    v = np.asarray(v, dtype=np.float64)
    ok = np.isfinite(v) & (v > lo) & (v < hi)
    n = v.size
    best = None
    i = 0
    while i < n:
        if not ok[i]:
            i += 1
            continue
        j = i + 1
        vmin = vmax = v[i]
        while j < n and ok[j] and max(vmax, v[j]) - min(vmin, v[j]) <= band:
            vmin, vmax = min(vmin, v[j]), max(vmax, v[j])
            j += 1
        if j - i >= min_samples and (best is None or (j - i) > (best[1] - best[0])):
            best = (i, j)
        i = j if j > i else i + 1
    if best is None:
        return None
    return float(np.median(v[best[0]:best[1]]))


def pit_runs(session_dir, table: LapTable, ring: Ring, max_laps=80,
             max_bad_fraction=PIT_MAX_BAD_FRACTION):
    """Every usable pit in-lap tail ("entry") and out-lap head ("exit").

    ONE gate in ONE place. `pit_lane` publishes the stations the renderer places
    pit-starting cars at and `pit_lane_path` publishes the drawn ribbon; they used to
    accept runs independently, so the frozen-position sentinel still reached the
    artifact through the first when only the second was guarded.
    """
    out = {"entry": [], "exit": [],
           "seen": {"entry": 0, "exit": 0},
           "rejected": {"entry": 0, "exit": 0},
           "badFraction": {"entry": [], "exit": []}}
    used = 0
    for r in table.rows():
        if used > max_laps:
            break
        is_in = r["pin"] != "None"
        is_out = r["pout"] != "None"
        if not (is_in or is_out):
            continue
        l = load_lap(session_dir, r["drv"], r["lap"])
        if l is None or not l.has_xy:
            continue
        bad = _pit_position_bad(l)
        # the lap is a time-ordered trace, so project it with continuity
        st, lat = ring.project_path(l.x, l.y)
        # `on` locates the divergence and the merge; it deliberately does NOT exclude
        # the bad samples. A frozen coordinate projects hundreds of metres off the ring
        # and so is never "on" anyway, while treating an isolated bad sample as
        # off-track resets the 30-sample merge streak and runs the out-lap head on for
        # kilometres (measured at Hungary: a 2015 m "pit exit" at 132-226 km/h).
        on = np.isfinite(lat) & (np.abs(lat) < ON_TRACK_LAT_M)
        if is_in:
            idx_on = np.where(on)[0]
            if idx_on.size and int(idx_on[-1]) < l.n - 4:
                k = int(idx_on[-1])
                out["seen"]["entry"] += 1
                used += 1
                frac = float(bad[k:].mean())
                out["badFraction"]["entry"].append(frac)
                if frac > max_bad_fraction:
                    out["rejected"]["entry"] += 1
                else:
                    out["entry"].append({"lap": l, "row": r, "a": k, "b": l.n,
                                          "st": st, "lat": lat, "badFraction": frac})
        if is_out:
            run, merge_i = 0, None
            for i in range(l.n):
                run = run + 1 if on[i] else 0
                if run >= MERGE_RUN:
                    merge_i = i - MERGE_RUN + 1
                    break
            if merge_i is not None and merge_i > 4:
                out["seen"]["exit"] += 1
                used += 1
                frac = float(bad[:merge_i].mean())
                out["badFraction"]["exit"].append(frac)
                if frac > max_bad_fraction:
                    out["rejected"]["exit"] += 1
                else:
                    out["exit"].append({"lap": l, "row": r, "a": 0, "b": merge_i,
                                         "merge": merge_i, "st": st, "lat": lat,
                                         "badFraction": frac})
    return out


def _med(vals):
    return float(np.median(vals)) if vals else None


def pit_lane(session_dir, table: LapTable, ring: Ring, max_laps=80, runs=None):
    """Stitch the lane from in-lap tails and out-lap heads.

    Telemetry never traces the lane between entry and exit: the in-lap ends at the pit
    timing loop and the out-lap starts already inside at the limiter. The middle is
    therefore interpolated and tagged INFERRED.

    Every published station and lateral comes from a run that passed the integrity gate
    in `pit_runs`. Where too few runs survive, the fields are null and the provenance
    says the feed was frozen -- they are not filled with the frozen coordinate, which is
    how `loopLateral`, `outLapStartLateral` and `exitLateral` all came out as the same
    512.24 m at Hungary and 557.07 m at China, three "independent measurements" of one
    sentinel.
    """
    if runs is None:
        runs = pit_runs(session_dir, table, ring, max_laps=max_laps)

    div_st, loop_pt, exit_pt, out_start, merge_st = [], [], [], [], []
    limiter_in, limiter_out = [], []
    for run in runs["entry"]:
        st, lat, k, l = run["st"], run["lat"], run["a"], run["lap"]
        div_st.append(float(st[k]))
        if np.isfinite(st[-1]) and np.isfinite(lat[-1]) and abs(lat[-1]) <= MAX_PIT_LAT_M:
            loop_pt.append((float(st[-1]), float(lat[-1])))
        # the plateau is measured on BOTH halves of the visit. An out-lap head is often
        # only 7 s long and accelerates straight through the limiter band, so taking the
        # limiter from out-laps alone left Monaco with two usable samples, one of them
        # 95 km/h, and a median of 76.7 against a measured 59.0 on its six in-laps.
        plateau = _plateau_speed(l.speed[k:])
        if plateau is not None:
            limiter_in.append(plateau)
    for run in runs["exit"]:
        l, r, st, lat, merge_i = run["lap"], run["row"], run["st"], run["lat"], run["merge"]
        merge_st.append(float(st[merge_i]))
        if np.isfinite(st[0]) and np.isfinite(lat[0]) and abs(lat[0]) <= MAX_PIT_LAT_M:
            out_start.append((float(st[0]), float(lat[0])))
        lst, pout = r.get("lST"), r.get("pout")
        if lst is not None and pout is not None                 and not isinstance(pout, str) and not isinstance(lst, str):
            ex, ey = _xy_at_lap_time(l, float(pout) - float(lst))
            if np.isfinite(ex):
                e_st, e_lat = ring.project(np.array([ex]), np.array([ey]))
                if np.isfinite(e_st[0]) and abs(float(e_lat[0])) <= MAX_PIT_LAT_M:
                    exit_pt.append((float(e_st[0]), float(e_lat[0])))
        plateau = _plateau_speed(l.speed[:merge_i])
        if plateau is not None:
            limiter_out.append(plateau)

    enough_in = len(runs["entry"]) >= PIT_MIN_RUNS
    enough_out = len(runs["exit"]) >= PIT_MIN_RUNS
    if not enough_in:
        div_st, loop_pt, limiter_in = [], [], []
    if not enough_out:
        exit_pt, out_start, merge_st, limiter_out = [], [], [], []
    limiter = limiter_in + limiter_out

    if enough_in or enough_out:
        prov = "DERIVED (entry/loop/exit/merge) + INFERRED (path between them)"
        if not (enough_in and enough_out):
            missing = "entry" if not enough_in else "exit"
            prov += (f"; {missing} UNAVAILABLE: position feed frozen inside the pit lane "
                     f"({runs['rejected'][missing]} of {runs['seen'][missing]} runs "
                     f"rejected)")
    else:
        prov = ("UNAVAILABLE: position feed frozen inside the pit lane "
                f"({runs['rejected']['entry']} of {runs['seen']['entry']} entry and "
                f"{runs['rejected']['exit']} of {runs['seen']['exit']} exit runs rejected)")

    return {
        "entryStation": _med(div_st),
        "loopStation": _med([p[0] for p in loop_pt]),
        "loopLateral": _med([p[1] for p in loop_pt]),
        "outLapStartStation": _med([p[0] for p in out_start]),
        "outLapStartLateral": _med([p[1] for p in out_start]),
        "exitStation": _med([p[0] for p in exit_pt]),
        "exitLateral": _med([p[1] for p in exit_pt]),
        "mergeStation": _med(merge_st),
        "limiterSpeedKph": _med(limiter),
        "nIn": len(runs["entry"]), "nOut": len(runs["exit"]),
        "nInSeen": runs["seen"]["entry"], "nOutSeen": runs["seen"]["exit"],
        "nInRejected": runs["rejected"]["entry"], "nOutRejected": runs["rejected"]["exit"],
        "provenance": prov,
    }


# Two stationary cars cannot occupy the same point of road. The smallest HONEST gap
# between two lap-1 stations measured across the 13 events is 0.224 m (Zandvoort,
# ALB/OCO side by side in one row; Melbourne COL/ALB 0.707 m, Zandvoort LAW/VER
# 0.852 m), while a shared sentinel repeats to ~1e-13 m -- 19 cars at Monaco and 18 at
# China report a station identical to the last decimal. 0.05 m sits between the two by
# a factor of four on one side and eleven orders of magnitude on the other.
GRID_DUP_STATION_M = 0.05
# A car on the grid sits within a couple of metres of the racing line (measured
# stationary spread 0.17 m; the worst honest lap-1 lateral across the 13 events is
# 10.5 m, on a ring that was 5.6 % long). 25 m is far outside any grid slot and far
# inside the sentinel laterals (35.6 m at Monaco, 557.1 m at China).
GRID_MAX_LATERAL_M = 25.0


def grid(session_dir, table: LapTable, ring: Ring, sf_station: float, pitch_m=8.0):
    """Grid ORDER is derived; the anchor and stagger are RULE values.

    Measured: lap-1 station ranking reproduces the real grid (21/21 at Zandvoort, 16/21
    at Silverstone where the rest are penalties) and spacing is 8.0 m, but pole sits
    100-116 m PAST the timing line and every stationary car is snapped to the centreline,
    so the feed contains neither an anchor nor a lateral stagger.

    Who started from the pit lane is OBSERVED, from the driver's own lap-1 `pout` row --
    not inferred from ring lateral. The lateral proxy (|lat| > 5 m) mis-classified
    exactly three events: 19 false pit starters at Monaco and 18 at China (a shared
    sentinel coordinate projecting 35.6 m and 557.1 m off the ring) and 6 at Hungary
    (real grid cars on a stretched ring). It also truncated `order`, which the frontend
    does read, appending those 6 Hungarian cars in Map order instead of their slots.

    A driver whose lap-1 position is not a measurement goes to `unplaced`, never to
    `pitStarters` and never into `order`: `np.isfinite` cannot see this, because the
    sentinel is a finite coordinate. The positive test is that several cars report the
    SAME station, which no real grid can produce.
    """
    rows = [r for r in table.rows() if r["lap"] == 1]
    obs = []
    for r in rows:
        l = load_lap(session_dir, r["drv"], 1)
        if l is None or not l.has_xy:
            continue
        i = 0
        stopped = np.where(np.isfinite(l.speed) & (l.speed == 0))[0]
        if stopped.size:
            i = int(stopped[0])
        st, lat = ring.project(l.x[i:i + 1], l.y[i:i + 1])
        if np.isfinite(st[0]):
            obs.append({"driver": r["drv"], "rawStation": float(st[0]),
                        "rawLateral": float(lat[0]),
                        # OBSERVED: the driver's own lap-1 pit-exit timestamp
                        "pitOut": r.get("pout", "None") != "None"})
    if not obs:
        return {"order": [], "slots": [], "pitStarters": [], "unplaced": [],
                "pitchMetres": pitch_m,
                "provenance": "UNAVAILABLE: no lap-1 position in this session"}

    L = ring.length
    st_arr = np.array([o["rawStation"] for o in obs])
    for o in obs:
        d = np.abs(st_arr - o["rawStation"])
        d = np.minimum(d, L - d)
        shared = int((d <= GRID_DUP_STATION_M).sum()) - 1      # excluding itself
        o["sharesStation"] = shared
        o["degenerate"] = shared > 0 or abs(o["rawLateral"]) > GRID_MAX_LATERAL_M

    pit_starters = [o["driver"] for o in obs if o["pitOut"]]
    unplaced = [o["driver"] for o in obs if not o["pitOut"] and o["degenerate"]]
    placed = [o for o in obs if not o["pitOut"] and not o["degenerate"]]

    if not placed:
        return {
            "order": [], "slots": [], "pitStarters": pit_starters, "unplaced": unplaced,
            "pitchMetres": pitch_m,
            "observedSpacingMedian": None, "observedLateralStd": None,
            "provenance": (
                "UNAVAILABLE: the grid order is not recoverable from this feed. "
                f"{len(unplaced)} of {len(obs)} lap-1 positions are one shared "
                "coordinate, which is the feed's 'position unknown' marker rather than "
                "a measurement, and the real grid coordinates were never transmitted. "
                "No order is emitted because none was observed."),
        }

    med = float(np.median([o["rawStation"] for o in placed]))
    for o in placed:
        o["rel"] = (o["rawStation"] - med + L / 2) % L - L / 2
    placed.sort(key=lambda o: -o["rel"])
    order = [o["driver"] for o in placed]

    slots = [{"position": i + 1, "driver": drv,
              "station": (sf_station - (i + 1) * pitch_m) % L,
              "lateralSign": 1 if i % 2 == 0 else -1}
             for i, drv in enumerate(order)]
    rels = sorted(o["rel"] for o in placed)
    spacing = np.diff(rels) if len(rels) > 1 else np.array([])
    prov = ("order DERIVED from lap-1 stations; pit starters OBSERVED from the lap-1 "
            "pout row; anchor and stagger RULE (absent from the feed)")
    if unplaced:
        prov += (f"; {len(unplaced)} driver(s) unplaced: their lap-1 position is a "
                 "shared placeholder coordinate, not a measurement")
    return {
        "order": order, "slots": slots, "pitStarters": pit_starters,
        "unplaced": unplaced,
        "pitchMetres": pitch_m,
        "observedSpacingMedian": float(np.median(np.abs(spacing))) if spacing.size else None,
        # np.std of an empty slice is NaN, which is not representable in JSON
        "observedLateralStd": _finite_or_none(np.std([o["rawLateral"] for o in placed])),
        "provenance": prov,
    }


def width_estimate(session_dir, table: LapTable, ring: Ring, laps,
                   corners=None, pit=None, bin_m=25.0,
                   pit_offset_fraction=0.35, multiplier=1.0):
    """Per-station half-width INFERRED from the three signals that do exist.

    The position feed contains no track width: across all clean laps the lateral spread
    between cars has a median of 0.33-0.61 m, and stationary grid cars sit within 0.17 m
    of one line. So this is an inference from proxies, not a measurement.

    Construction, in two separable parts so each can be argued with:

      SHAPE, from the data. Per-station lateral extremes over the clean laps (which pick
      up genuine off-line excursions) combined with the perpendicular offset of each
      corner marker, normalised to its own maximum. This says where the track is
      relatively wider or narrower, which is the part the telemetry really does carry.

      SCALE, a RULE constant (HALF_WIDTH_MIN_M..HALF_WIDTH_MAX_M), because the feed
      carries no absolute width at all. The pit-lane offset was tried as a proxy and
      rejected: it under-sized nearly every circuit and blew up completely wherever
      the position feed is corrupt. The measured pit offset is still reported for
      reference, but it no longer drives the result.

    Both are reported separately in the artifact, and `multiplier` tunes the result.
    """
    nb = int(np.ceil(ring.length / bin_m))
    ext = np.zeros(nb)
    for l in laps:
        st, lat = ring.project(l.x, l.y)
        ok = np.isfinite(st) & np.isfinite(lat)
        b = (np.floor(st[ok] / bin_m).astype(int)) % nb
        np.maximum.at(ext, b, np.abs(lat[ok]))
    ext = np.clip(ext, 0.0, 25.0)   # single-sample stutters reach 10-20 m

    marker = np.zeros(nb)
    if corners:
        for c in corners["corners"]:
            b = int(np.floor(c["station"] / bin_m)) % nb
            marker[b] = max(marker[b], abs(c["markerLateral"]))
        marker = smooth_circular(marker, bin_m * 5, bin_m)

    shape = np.maximum(ext, marker)
    shape = smooth_circular(shape, bin_m * 5, bin_m)
    peak = float(shape.max()) if shape.max() > 0 else 1.0
    # keep the shape in [0.6, 1.0] so a narrow station never collapses to the floor
    shape_n = 0.6 + 0.4 * (shape / peak)

    # SCALE is a RULE constant, not a proxy. Deriving it from the pit-lane offset was
    # tried and abandoned: it produced roads 2.9-8.6 m wide on most circuits (a real
    # one is never under 12 m, so cars visibly hung off the edge) and, on the two
    # sessions whose position feed the audit flagged as corrupt, a pit offset of
    # 512-557 m scaled the track to 215-236 m across. The shape below still comes
    # from the data; only its absolute size is now a stated rule.
    # Reported for reference only -- it no longer drives `scale` (see above). None
    # when the pit model could not measure an exit lateral (Chinese GP): `or 0.0`
    # here turned that honest null into a measured-looking 0.0 m in the artifact.
    _exit_lat = (pit or {}).get("exitLateral")
    pit_offset = None if _exit_lat is None else abs(float(_exit_lat))
    shape_01 = (shape_n - 0.6) / 0.4                      # back to 0..1
    half = HALF_WIDTH_MIN_M + shape_01 * (HALF_WIDTH_MAX_M - HALF_WIDTH_MIN_M)
    half = np.maximum(half * multiplier, WIDTH_FLOOR_M)
    scale = HALF_WIDTH_MAX_M

    return {
        "binMetres": bin_m,
        "halfWidth": [round(float(v), 3) for v in half],
        "halfWidthMin": float(half.min()),
        "halfWidthMax": float(half.max()),
        "scaleMetres": float(scale),
        "pitOffsetMetres": None if pit_offset is None else float(pit_offset),
        "pitOffsetFraction": pit_offset_fraction,
        "multiplier": multiplier,
        "measuredLateralSpreadMedian": float(np.median(ext)),
        "measuredLateralSpreadMax": float(ext.max()),
        "provenance": {
            "shape": "DERIVED from per-station lateral extremes and corner markers",
            "scale": ("RULE: HALF_WIDTH_MIN_M..HALF_WIDTH_MAX_M "
                      "(6.0-7.5 m), a stated constant. The feed carries no absolute "
                      "width, so the shape above is scaled by a rule, NOT by the "
                      "pit-exit offset -- deriving it from that offset was tried and "
                      "abandoned. pitOffsetMetres/pitOffsetFraction are reported for "
                      "reference and do not enter the result."),
            "note": "The position feed does not measure track width. Tune `multiplier`.",
        },
    }

def reference_speed_profile(ring: Ring, laps, bin_m: float = 5.0):
    """Per-station MEDIAN speed across many laps (never a single fast lap): this is
    the synthetic spine the New Race engine warps by sector time. Measured: a
    pointwise-median spine plus a 3-sector time-axis warp reproduces a real lap within
    5.2-5.7 km/h RMS, at or below the same-driver noise floor; a single fast lap is
    7.6-9.6 km/h RMS. Also emits median gear, a brake indicator (deceleration
    threshold), and engine speed, all derived from speed/station rather than the raw
    (noisy, non-physical-tailed) acceleration channels.
    """
    n_bins = int(np.ceil(ring.length / bin_m))
    speed_acc = [[] for _ in range(n_bins)]
    gear_acc = [[] for _ in range(n_bins)]
    rpm_acc = [[] for _ in range(n_bins)]

    for l in laps:
        st, lat = ring.project(l.x, l.y)
        ok = np.isfinite(st) & np.isfinite(l.speed) & (np.abs(lat) < 8.0)
        b = (np.floor(st[ok] / bin_m).astype(int)) % n_bins
        for bi, sp, ge, rp in zip(b, l.speed[ok], l.gear[ok], l.rpm[ok]):
            speed_acc[bi].append(sp)
            if ge > 0:
                gear_acc[bi].append(int(ge))
            if np.isfinite(rp):
                rpm_acc[bi].append(rp)

    speed = np.array([np.median(v) if v else np.nan for v in speed_acc])
    # fill any empty bin (a station no lap sampled) by interpolating around the ring
    miss = ~np.isfinite(speed)
    if miss.any() and not miss.all():
        idxs = np.arange(n_bins)
        speed[miss] = np.interp(idxs[miss], idxs[~miss], speed[~miss], period=n_bins)
    gear = np.array([int(round(np.median(v))) if v else 0 for v in gear_acc])
    rpm = np.array([np.median(v) if v else np.nan for v in rpm_acc])
    if np.isfinite(rpm).any():
        rpm[~np.isfinite(rpm)] = float(np.nanmedian(rpm))
    else:
        rpm[:] = 0.0

    return {
        "binMetres": bin_m,
        "speedKph": [round(float(v), 1) for v in speed],
        "gear": [int(v) for v in gear],
        "rpm": [round(float(v)) for v in rpm],
        "provenance": "DERIVED: per-station median speed/gear/rpm across many clean laps "
                      "(the synthetic spine the engine warps by sector time), never a "
                      "single fast lap",
    }


def _z_held_mask(z, x, y, window=Z_HOLD_WINDOW, band=Z_HOLD_BAND_M, move=Z_HOLD_MOVE_M,
                 span=Z_HOLD_SPAN_M, net=Z_HOLD_NET_M):
    """Samples whose ELEVATION is pinned while the car is covering ground.

    Not an equality test: the channel wobbles inside the decimetre quantisation, so a
    strict equal-value run finds a median run of 1-2 samples where the truth is 300-600
    metres of road. Two measured statements instead, either of which condemns a sample:
      fine   -- z stayed inside `band` m while the car moved `move` m;
      coarse -- z stayed inside `net` m across `span` m of road, which no real road does
                unless it is genuinely flat over that distance.
    """
    z = np.asarray(z, dtype=np.float64)
    n = z.size
    out = np.zeros(n, dtype=bool)
    if n < 5:
        return out
    half = window // 2
    path = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    if not np.isfinite(path[-1]):
        return out
    half_span = span / 2.0
    lo = np.searchsorted(path, path - half_span, side="left")
    hi = np.searchsorted(path, path + half_span, side="right")
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        zz = z[a:b]
        zz = zz[np.isfinite(zz)]
        if zz.size >= 5:
            moved = path[b - 1] - path[a]
            if moved >= move and (zz.max() - zz.min()) <= band:
                out[i] = True
                continue
        a2, b2 = int(lo[i]), int(hi[i])
        if path[b2 - 1] - path[a2] < span:
            continue                      # not enough road to make the statement
        zz = z[a2:b2]
        zz = zz[np.isfinite(zz)]
        # a ROBUST spread, not max-min: the channel spikes once or twice per run (a
        # single sample reading 59.5 m where the held value is 83.1 m), and max-min
        # lets two such spikes certify 120 m of pinned road as measured. Measured on
        # Suzuka's exit runs, max-min flags 0.65-0.72 of the run and the 10-90 spread
        # flags 0.97-1.00.
        if zz.size >= 5 and float(np.percentile(zz, 90) - np.percentile(zz, 10)) <= net:
            out[i] = True

    # Carry the pin across the stretch where neither test can speak: in the box the car
    # does not move, so no window contains enough road to say anything about elevation,
    # and the value sitting there is the same pinned number as the road either side of
    # it (measured on a Suzuka out-lap, 40 consecutive samples at 82.10 m while the car
    # advanced 11 m). A sample that repeats a held value inside `band` is held; one that
    # differs by more has been released, and it re-seeds.
    for rng in (range(n), range(n - 1, -1, -1)):
        anchor = None
        for i in rng:
            if out[i]:
                anchor = z[i] if np.isfinite(z[i]) else anchor
            elif anchor is not None and np.isfinite(z[i]):
                if abs(z[i] - anchor) <= band:
                    out[i] = True
                else:
                    anchor = None
    return out


def _resample_run(x, y, z, n_pts, held=None):
    """Resample one pit-lane run onto n_pts evenly spaced by its own arc length.

    Returns (x, y, z, held_fraction) where `held_fraction` is the resampled weight of
    the input `held` flag, so a point built only from held samples can be published as
    "no elevation" rather than as a number.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 2:
        return None
    x, y, z = x[m], y[m], z[m]
    held = np.asarray(held, dtype=bool)[m] if held is not None else np.zeros(x.size, bool)
    d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    if d[-1] <= 1.0:
        return None
    t = np.linspace(0.0, d[-1], n_pts)
    zm = np.isfinite(z)
    rz = (np.interp(t, d[zm], z[zm]) if zm.sum() >= 2 else np.full(n_pts, np.nan))
    return (np.interp(t, d, x), np.interp(t, d, y), rz,
            np.interp(t, d, held.astype(np.float64)))


def pit_lane_path(session_dir, table: LapTable, ring: Ring, pit: dict,
                   n_pts: int = 80, max_laps: int = 60, runs=None):
    """A drawable pit lane, as an explicit XY(Z) polyline in the same frame as the ring.

    NOT a lateral offset against station: a real pit lane shortcuts the corner it
    bypasses (measured at Silverstone, the lane runs >60 m from the centreline at the
    same station because the circuit loops around Club while the lane goes straight),
    so an offset profile cannot describe it. Instead the off-track runs are resampled
    by their OWN arc length and median-averaged across laps, exactly as the racing
    ring is built.

    Two segments are stitched: in-lap tails cover entry -> box, out-lap heads cover
    box -> merge. The car is stationary in the box, so the feed traces nothing there
    and the join is a straight interpolation.

    Three things this does NOT do:
      * it does not draw a run whose position feed froze (see `pit_runs`), which is what
        turned Hungary's and China's "pit lane" into a 1.4-1.9 km spike to a dead
        coordinate 512-557 m off the circuit;
      * it does not publish a held elevation as a measurement -- a point with no
        measured z is emitted as null (`zCm`), and the renderer drapes it;
      * it does not call the drawn polyline's length the pit lane's length. The drawn
        length runs from the last racing-line sample to the merge, so it includes the
        approach and the rejoin at racing speed: measured 1.20-2.14x the distance
        actually covered under the pit limit. `pitLaneMetres` is that speed-limited
        distance, measured separately.
    """
    if runs is None:
        runs = pit_runs(session_dir, table, ring, max_laps=max_laps)

    limiter = (pit or {}).get("limiterSpeedKph")
    segments_in = {"entry": [], "exit": []}
    pit_metres = {"entry": [], "exit": []}
    used = 0
    for role in ("entry", "exit"):
        for run in runs[role]:
            l, a, b = run["lap"], run["a"], run["b"]
            x, y, z = l.x[a:b], l.y[a:b], l.z[a:b]
            held = _z_held_mask(z, x, y)
            rs = _resample_run(x, y, z, n_pts, held)
            if rs is None:
                continue
            segments_in[role].append(rs)
            used += 1
            if limiter is not None:
                m = _speed_limited_metres(x, y, l.speed[a:b], limiter, role)
                if m is not None:
                    pit_metres[role].append(m)

    if not segments_in["entry"] and not segments_in["exit"]:
        return None

    def median_path(rs):
        if len(rs) < PIT_MIN_RUNS:
            return None
        xs = np.median(np.array([r[0] for r in rs]), axis=0)
        ys = np.median(np.array([r[1] for r in rs]), axis=0)
        # a point built only from held samples has no elevation at all, and nanmedian
        # of an all-NaN column is the NaN we want -- only its warning is unwanted
        zz = np.array([np.where(r[3] >= 0.5, np.nan, r[2]) for r in rs])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            zs = np.nanmedian(zz, axis=0)
        return xs, ys, zs

    def smooth(a, w=9):
        """Small moving average along the run. The per-point median still carries the
        feed's own jitter, which showed up as a visibly buckled ribbon once drawn."""
        if a.size < w:
            return a
        k = np.ones(w) / w
        pad = np.concatenate([np.full(w // 2, a[0]), a, np.full(w // 2, a[-1])])
        return np.convolve(pad, k, mode="valid")[: a.size]

    def smooth_keep_gaps(a, w=15):
        """Smooth only the measured entries; an unmeasured point stays unmeasured."""
        finite = np.isfinite(a)
        if finite.sum() < 2:
            return a
        filled = np.interp(np.arange(a.size), np.flatnonzero(finite), a[finite])
        out = smooth(filled, w)
        out[~finite] = np.nan
        return out

    segments = []
    for role in ("entry", "exit"):
        seg = median_path(segments_in[role])
        if seg is None:
            continue
        x, y = smooth(seg[0]), smooth(seg[1])
        z = smooth_keep_gaps(seg[2])
        if np.isfinite(x).sum() < 2:
            continue
        lat = ring.project(x, y)[1]
        if np.isfinite(lat).any() and float(np.nanmedian(np.abs(lat))) > MAX_PIT_LAT_M:
            continue      # not a pit lane, a projection artefact
        n_meas = int(np.isfinite(z).sum())
        segments.append({
            "role": role,
            "xCm": [int(round(v * 100)) for v in x],
            "yCm": [int(round(v * 100)) for v in y],
            # null, not a number: a held elevation is not a measured elevation
            "zCm": [(int(round(v * 100)) if np.isfinite(v) else None) for v in z],
            "zMeasuredPoints": n_meas,
            "zHeldFraction": round(1.0 - n_meas / len(z), 3) if len(z) else None,
            "runs": len(segments_in[role]),
            "lengthMetres": round(float(np.sum(np.hypot(np.diff(x), np.diff(y)))), 1),
        })
    if not segments:
        return None

    drawn = round(sum(s["lengthMetres"] for s in segments), 1)
    # both halves or nothing: the lane's length is entry + exit, and half a lane is not
    # a shorter lane, it is an unmeasured one
    lane_m, lane_n = None, None
    if pit_metres["entry"] and pit_metres["exit"]:
        lane_m = round(float(np.median(pit_metres["entry"]))
                       + float(np.median(pit_metres["exit"])), 1)
        lane_n = min(len(pit_metres["entry"]), len(pit_metres["exit"]))
    return {
        # TWO separate roads, never stitched into one: the in-lap traces the entry
        # road and the out-lap traces the exit road. Concatenating them produced a
        # ribbon that doubled back on itself, which rendered as a folded, creased
        # surface. Nothing traces the box itself (the car is stopped), so the gap
        # between them is left as a gap rather than invented.
        "segments": segments,
        # kept under its original name for compatibility, but it is and always was the
        # length of the DRAWN polyline, approach and rejoin included
        "lengthMetres": drawn,
        "drawnPathMetres": drawn,
        "pitLaneMetres": lane_m,
        "pitLaneMetresN": lane_n,
        "lapsUsed": used,
        "provenance": {
            "path": ("DERIVED: per-point median of real pit in/out telemetry that passed "
                      "the position-integrity gate, resampled by arc length and smoothed; "
                      "the box itself is not traced"),
            "elevation": ("OBSERVED where the z channel tracks the road; null where it is "
                           "held (measured std 0.018-0.057 m over 300-600 m), which is most "
                           "of the lane at most circuits"),
            "lengthMetres": "DERIVED: length of the DRAWN polyline, approach and rejoin included",
            "pitLaneMetres": ("DERIVED: entry + exit distance covered at or below the "
                               "measured limiter plateau + 10 km/h, median over the "
                               "stops; null unless BOTH halves of the lane were "
                               "measured, since half a lane is not a shorter lane"),
        },
    }


def _speed_limited_metres(x, y, v, limiter_kph, role,
                          margin=PIT_SPEED_MARGIN_KPH):
    """Distance of this run covered at pit-lane speed.

    The cut is the LAST crossing of the threshold on an entry run and the FIRST on an
    exit run -- i.e. the last time the car was still going too fast to be in the lane,
    and the first time it is going too fast again. A first-crossing rule on the entry
    fires at index 0 wherever the pit entry follows a braking zone (measured at Spa: it
    removes nothing and reports 632.5 m of a 657.1 m run as "speed limited").
    """
    v = np.asarray(v, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    m = np.isfinite(x) & np.isfinite(y) & np.isfinite(v)
    if m.sum() < 3:
        return None
    x, y, v = x[m], y[m], v[m]
    fast = v > (limiter_kph + margin)
    idx = np.flatnonzero(fast)
    if role == "entry":
        a = int(idx[-1]) + 1 if idx.size else 0
        b = v.size
    else:
        a = 0
        b = int(idx[0]) + 1 if idx.size else v.size
    if b - a < 2:
        return 0.0
    return float(np.hypot(np.diff(x[a:b]), np.diff(y[a:b])).sum())
