"""Build a track model from telemetry alone.

Every step here is one of the measured recommendations in the plan. Nothing about the
circuit is typed in; the only RULE values are the grid anchor and stagger, which the
position feed provably does not contain.
"""
from __future__ import annotations

import math

import numpy as np

from .geom import Ring, close_ring, resample_by_arclength, smooth_circular
from .rawio import LapTable, load_lap, read_json

XY_SMOOTH_M = 11.0    # measured: <=1 m deviation, keeps 20 m hairpins
Z_SMOOTH_M = 31.0     # measured: 2nd-diff std <=0.0007 m, max dev <=0.27 m
MEDIAN_LAPS = 60      # measured: halves position and heading jitter
LATERAL_REJECT_M = 5.0
DS = 1.0


def _lap_index(table: LapTable):
    """(driver, lap) -> row dict, plus the clean mask."""
    rows = list(table.rows())
    clean = table.clean_mask()
    idx = {}
    for r, ok in zip(rows, clean):
        idx[(r["drv"], r["lap"])] = (r, ok)
    return idx


def pick_geometry_laps(session_dir, table: LapTable, limit=MEDIAN_LAPS):
    """Clean laps with usable positions, fastest first, spread across drivers."""
    idx = _lap_index(table)
    cands = [(r["time"], drv, lap) for (drv, lap), (r, ok) in idx.items() if ok]
    cands.sort()
    picked, per_driver = [], {}
    # first pass: at most 4 per driver so one car cannot define the line
    for t, drv, lap in cands:
        if per_driver.get(drv, 0) >= 4:
            continue
        l = load_lap(session_dir, drv, lap)
        if l is None or not l.geometry_valid():
            continue
        picked.append(l)
        per_driver[drv] = per_driver.get(drv, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def build_ring(laps):
    """Median-of-N centreline. `laps` must be ordered fastest first."""
    ref = laps[0]
    x, y, z, _ = resample_by_arclength(ref.x, ref.y, ref.z, DS)
    x, y, z, _ = close_ring(x, y, z, DS)
    prov = Ring(x, y, z, DS)

    n = prov.n
    lat_acc = [[] for _ in range(n)]
    z_acc = [[] for _ in range(n)]
    for l in laps:
        lx, ly, lz, _ = resample_by_arclength(l.x, l.y, l.z, DS)
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
    return Ring(sx, sy, sz, DS), prov


def _xy_at_lap_time(lap, t_rel):
    """Interpolate a lap's position at a lap-relative time."""
    m = np.isfinite(lap.x) & np.isfinite(lap.y) & np.isfinite(lap.t)
    if m.sum() < 2:
        return np.nan, np.nan
    return (float(np.interp(t_rel, lap.t[m], lap.x[m])),
            float(np.interp(t_rel, lap.t[m], lap.y[m])))


def timing_lines(session_dir, table: LapTable, ring: Ring, laps, min_laps=10):
    """Start/finish and sector stations from the SECTOR clock.

    The lap clock (lST) is 1.6 s early for 20 of 22 drivers at Zandvoort, which puts
    station 0 135 m from the physical line. The sector timestamps are consistent with
    the position feed at every circuit, so they are the authority here.
    """
    idx = _lap_index(table)
    out = {}
    for key, field in (("sf", "s3T"), ("s1", "s1T"), ("s2", "s2T")):
        stations = []
        for l in laps:
            row, ok = idx.get((l.driver, l.lap), (None, False))
            if not ok or row is None:
                continue
            tt, lst = row.get(field), row.get("lST")
            if isinstance(tt, str) or isinstance(lst, str):
                continue
            px, py = _xy_at_lap_time(l, tt - lst)
            if not np.isfinite(px):
                continue
            st, _ = ring.project(np.array([px]), np.array([py]))
            stations.append(float(st[0]))
        if len(stations) >= min_laps:
            a = np.unwrap(np.array(stations) / ring.length * 2 * np.pi) * ring.length / (2 * np.pi)
            out[key] = {"station": float(np.median(a) % ring.length),
                        "std": float(np.std(a)), "n": len(stations)}
        else:
            out[key] = None
    return out


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
MAX_PIT_LAT_M = 60.0     # beyond this it is a projection artefact, not a pit lane
                          # (measured lane offsets: -35 Silverstone, -12 Spa/Zandvoort)
MERGE_RUN = 30           # consecutive on-track samples that end an out-lap head
CAR_WIDTH_M = 2.0        # HaasCarTop footprint, already in the loader constants
WIDTH_FLOOR_M = CAR_WIDTH_M / 2 + 0.25
# A modern F1 circuit must be at least 12 m wide and is typically 12-15 m, with pit
# straights reaching ~18 m. These bound the RULE-scaled half-width below.
HALF_WIDTH_MIN_M = 6.0
HALF_WIDTH_MAX_M = 7.5


def pit_lane(session_dir, table: LapTable, ring: Ring, max_laps=80):
    """Stitch the lane from in-lap tails and out-lap heads.

    Telemetry never traces the lane between entry and exit: the in-lap ends at the pit
    timing loop and the out-lap starts already inside at the limiter. The middle is
    therefore interpolated and tagged INFERRED.
    """
    rows = list(table.rows())
    n_in = n_out = 0
    div_st, loop_pt, exit_pt, out_start, merge_st, limiter = [], [], [], [], [], []

    for r in rows:
        if n_in + n_out > max_laps:
            break
        is_in = r["pin"] != "None"
        is_out = r["pout"] != "None"
        if not (is_in or is_out):
            continue
        l = load_lap(session_dir, r["drv"], r["lap"])
        if l is None or not l.has_xy:
            continue
        st, lat = ring.project(l.x, l.y)
        on = np.isfinite(lat) & (np.abs(lat) < ON_TRACK_LAT_M)
        if is_in:
            idx_on = np.where(on)[0]
            if idx_on.size and int(idx_on[-1]) < l.n - 3:
                k = int(idx_on[-1])
                n_in += 1
                div_st.append(float(st[k]))
                if np.isfinite(st[-1]):
                    loop_pt.append((float(st[-1]), float(lat[-1])))
        if is_out:
            run, merge_i = 0, None
            for i in range(l.n):
                run = run + 1 if on[i] else 0
                if run >= MERGE_RUN:
                    merge_i = i - MERGE_RUN + 1
                    break
            if merge_i:
                n_out += 1
                merge_st.append(float(st[merge_i]))
                if np.isfinite(st[0]):
                    out_start.append((float(st[0]), float(lat[0])))
                # the exit LINE is where the car is at the pout timestamp
                lst = r.get("lST")
                if not isinstance(r["pout"], str) and not isinstance(lst, str):
                    ex, ey = _xy_at_lap_time(l, r["pout"] - lst)
                    if np.isfinite(ex):
                        e_st, e_lat = ring.project(np.array([ex]), np.array([ey]))
                        if np.isfinite(e_st[0]):
                            exit_pt.append((float(e_st[0]), float(e_lat[0])))
                pl = l.speed[:merge_i]
                pl = pl[np.isfinite(pl) & (pl > 40) & (pl < 110)]
                if pl.size:
                    limiter.append(float(np.median(pl)))

    def med(vals):
        return float(np.median(vals)) if vals else None

    return {
        "entryStation": med(div_st),
        "loopStation": med([p[0] for p in loop_pt]),
        "loopLateral": med([p[1] for p in loop_pt]),
        "outLapStartStation": med([p[0] for p in out_start]),
        "outLapStartLateral": med([p[1] for p in out_start]),
        "exitStation": med([p[0] for p in exit_pt]),
        "exitLateral": med([p[1] for p in exit_pt]),
        "mergeStation": med(merge_st),
        "limiterSpeedKph": med(limiter),
        "nIn": n_in, "nOut": n_out,
        "provenance": "DERIVED (entry/loop/exit/merge) + INFERRED (path between them)",
    }


def grid(session_dir, table: LapTable, ring: Ring, sf_station: float, pitch_m=8.0):
    """Grid ORDER is derived; the anchor and stagger are RULE values.

    Measured: lap-1 station ranking reproduces the real grid (21/21 at Zandvoort, 16/21
    at Silverstone where the rest are penalties) and spacing is 8.0 m, but pole sits
    100-116 m PAST the timing line and every stationary car is snapped to the centreline,
    so the feed contains neither an anchor nor a lateral stagger.
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
                        "rawLateral": float(lat[0])})
    if not obs:
        return {"order": [], "pitchMetres": pitch_m, "provenance": "UNAVAILABLE"}

    L = ring.length
    med = float(np.median([o["rawStation"] for o in obs]))
    for o in obs:
        o["rel"] = (o["rawStation"] - med + L / 2) % L - L / 2
    obs.sort(key=lambda o: -o["rel"])
    pit_starters = [o["driver"] for o in obs if abs(o["rawLateral"]) > 5.0]
    order = [o["driver"] for o in obs if abs(o["rawLateral"]) <= 5.0]

    slots = [{"position": i + 1, "driver": drv,
              "station": (sf_station - (i + 1) * pitch_m) % L,
              "lateralSign": 1 if i % 2 == 0 else -1}
             for i, drv in enumerate(order)]
    rels = sorted(o["rel"] for o in obs if abs(o["rawLateral"]) <= 5.0)
    spacing = np.diff(rels) if len(rels) > 1 else np.array([])
    return {
        "order": order, "slots": slots, "pitStarters": pit_starters,
        "pitchMetres": pitch_m,
        "observedSpacingMedian": float(np.median(np.abs(spacing))) if spacing.size else None,
        # np.std of an empty slice is NaN, which is not representable in JSON
        "observedLateralStd": _finite_or_none(np.std([o["rawLateral"] for o in obs
                                            if abs(o["rawLateral"]) <= 5.0])),
        "provenance": "order DERIVED; anchor and stagger RULE (absent from the feed)",
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
    pit_offset = abs((pit or {}).get("exitLateral") or 0.0)
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
        "pitOffsetMetres": float(pit_offset),
        "pitOffsetFraction": pit_offset_fraction,
        "multiplier": multiplier,
        "measuredLateralSpreadMedian": float(np.median(ext)),
        "measuredLateralSpreadMax": float(ext.max()),
        "provenance": {
            "shape": "DERIVED from per-station lateral extremes and corner markers",
            "scale": "DEFAULT: pitOffsetFraction of the measured pit-exit lateral offset",
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

def _resample_run(x, y, z, n_pts):
    """Resample one pit-lane run onto n_pts evenly spaced by its own arc length."""
    d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    if d[-1] <= 1.0:
        return None
    t = np.linspace(0.0, d[-1], n_pts)
    return np.interp(t, d, x), np.interp(t, d, y), np.interp(t, d, z)


def pit_lane_path(session_dir, table: LapTable, ring: Ring, pit: dict,
                   n_pts: int = 80, max_laps: int = 60):
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
    """
    ins, outs = [], []
    used = 0
    for r in table.rows():
        if used > max_laps:
            break
        is_in, is_out = r["pin"] != "None", r["pout"] != "None"
        if not (is_in or is_out):
            continue
        l = load_lap(session_dir, r["drv"], r["lap"])
        if l is None or not l.has_xy:
            continue
        _st, lat = ring.project(l.x, l.y)
        on = np.isfinite(lat) & (np.abs(lat) < ON_TRACK_LAT_M)
        if is_in:
            idx_on = np.where(on)[0]
            if idx_on.size and int(idx_on[-1]) < l.n - 4:
                k = int(idx_on[-1])
                run = _resample_run(l.x[k:], l.y[k:], l.z[k:], n_pts)
                if run is not None:
                    ins.append(run); used += 1
        if is_out:
            run_len, merge_i = 0, None
            for i in range(l.n):
                run_len = run_len + 1 if on[i] else 0
                if run_len >= MERGE_RUN:
                    merge_i = i - MERGE_RUN + 1
                    break
            if merge_i and merge_i > 4:
                run = _resample_run(l.x[:merge_i], l.y[:merge_i], l.z[:merge_i], n_pts)
                if run is not None:
                    outs.append(run); used += 1

    if not ins and not outs:
        return None

    def median_path(runs):
        if not runs:
            return None
        xs = np.median(np.array([r[0] for r in runs]), axis=0)
        ys = np.median(np.array([r[1] for r in runs]), axis=0)
        zs = np.median(np.array([r[2] for r in runs]), axis=0)
        return xs, ys, zs

    def smooth(a, w=9):
        """Small moving average along the run. The per-point median still carries the
        feed's own jitter, which showed up as a visibly buckled ribbon once drawn."""
        if a.size < w:
            return a
        k = np.ones(w) / w
        pad = np.concatenate([np.full(w // 2, a[0]), a, np.full(w // 2, a[-1])])
        return np.convolve(pad, k, mode="valid")[: a.size]

    segments = []
    for seg, role in ((median_path(ins), "entry"), (median_path(outs), "exit")):
        if seg is None:
            continue
        x, y, z = smooth(seg[0]), smooth(seg[1]), smooth(seg[2], 15)
        segments.append({
            "role": role,
            "xCm": [int(round(v * 100)) for v in x],
            "yCm": [int(round(v * 100)) for v in y],
            "zCm": [int(round(v * 100)) for v in z],
            "lengthMetres": round(float(np.sum(np.hypot(np.diff(x), np.diff(y)))), 1),
        })
    if not segments:
        return None

    return {
        # TWO separate roads, never stitched into one: the in-lap traces the entry
        # road and the out-lap traces the exit road. Concatenating them produced a
        # ribbon that doubled back on itself, which rendered as a folded, creased
        # surface. Nothing traces the box itself (the car is stopped), so the gap
        # between them is left as a gap rather than invented.
        "segments": segments,
        "lengthMetres": round(sum(s["lengthMetres"] for s in segments), 1),
        "lapsUsed": used,
        "provenance": ("DERIVED: per-point median of real pit in/out telemetry, resampled "
                        "by arc length and smoothed; the box itself is not traced"),
    }
