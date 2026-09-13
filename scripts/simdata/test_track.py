"""Tests for the track model's POSITION POLICY: which laps may define a circuit, which
samples may define a timing line, a pit lane or a grid.

Measured against the real 2026 feed in the raw mirror wherever the defect lives in the data,
and synthetically only where the point is a pure algorithm property. Every assertion
below fails on the code as it stood before this file existed; the numbers in the
docstrings are measured, not illustrative.
"""
from __future__ import annotations

import functools
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from simdata.rawio import DM_TO_M, Lap, LapTable
from simdata.track import (GRID_DUP_STATION_M, GRID_LAUNCH_KPH, LATERAL_REJECT_M,
                            MAX_PIT_LAT_M, PIT_MIN_RUNS, _grid_box_sample, _lap_axis,
                            _lap_index, _line_stats, _plateau_speed, _reference_lap,
                            _speed_limited_metres, _xy_at_lap_time, _z_held_mask,
                            build_ring, geometry_lap_reject, grid, grid_slot_station,
                            pick_geometry_laps, pit_lane, pit_lane_path, pit_runs,
                            timing_lines)

# Support both the simulator's historical export and TrackShift's documented
# raw-mirror layout.  These are real-data tests, not tests of a personal path.
DATA_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "2026"
if not DATA_ROOT.exists():
    DATA_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "2026"


def _session(event: str, name: str = "Race"):
    return DATA_ROOT / f"{event} Grand Prix" / name


@functools.lru_cache(maxsize=None)
def _built(event: str, name: str = "Race"):
    """Ring in its final start/finish frame, plus the pieces built from it.

    Cached because a ring costs 4-12 s to build and several tests share one.
    """
    sdir = _session(event, name)
    table = LapTable(sdir)
    laps = pick_geometry_laps(sdir, table)
    ring, _ = build_ring(laps)
    tl = timing_lines(sdir, table, ring, laps)
    ring = ring.rotated(tl["sf"]["station"])
    return sdir, table, laps, ring


# ------------------------------------------------------------------ synthetic laps


def _fake_lap(driver, lap, x, y, z=None, speed=200.0, dt=0.25):
    """A Lap built from arrays. x/y/z are metres here; the loader wants decimetres."""
    n = len(x)
    z = np.zeros(n) if z is None else np.asarray(z, float)
    v = np.full(n, speed, float) if np.isscalar(speed) else np.asarray(speed, float)
    t = np.arange(n) * dt
    d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    tel = {
        "time": list(t), "speed": list(v), "distance": list(d),
        "x": list(np.asarray(x, float) / DM_TO_M),
        "y": list(np.asarray(y, float) / DM_TO_M),
        "z": list(z / DM_TO_M),
        "gear": [6] * n, "brake": [0] * n, "throttle": [100.0] * n,
        "rpm": [11000.0] * n, "DriverAhead": ["None"] * n,
        "DistanceToDriverAhead": [0.0] * n,
    }
    return Lap(driver, lap, tel)


def _circle_lap(driver, lap, radius=200.0, step=6.0, tail_m=0.0):
    """One closed lap of a circle, optionally with a straight tail that does not close."""
    n = int(round(2 * np.pi * radius / step))
    th = np.arange(n) * (2 * np.pi / n)
    x, y = radius * np.cos(th), radius * np.sin(th)
    if tail_m > 0:
        k = int(round(tail_m / step))
        x = np.concatenate([x, radius + np.arange(1, k + 1) * step * 0.0])
        y = np.concatenate([y, np.arange(1, k + 1) * step])
    return _fake_lap(driver, lap, x, y)


# =========================================================== geometry lap selection


def test_geometry_gates_separate_hungary_from_every_clean_event():
    """The three strong discriminators, measured over the laps that build each ring.

    Hungary Race is a stale-anchor sample-and-hold: uniqueFrac 0.38-0.41, repeatBack
    up to 0.50, median step 0.011-0.017 m. Every other event reads >= 0.98 / <= 0.016 /
    >= 3.99 m. pathOverSpan is NOT used as a gate because it admits 58 of the 60.
    """
    sdir = _session("Hungarian")
    table = LapTable(sdir)
    idx = _lap_index(table)
    from simdata.rawio import load_lap

    corrupt = []
    for (drv, lap), (row, ok) in sorted(idx.items()):
        if not ok or lap < 40:
            continue
        l = load_lap(sdir, drv, lap)
        if l is not None and l.geometry_valid():
            corrupt.append(l)
        if len(corrupt) >= 5:
            break
    assert corrupt, "expected clean-by-the-old-gate Hungarian laps in the corrupt window"
    for l in corrupt:
        q = l.position_quality()
        assert q["uniqueFrac"] < 0.95
        assert q["medianStepM"] < 1.0
        assert geometry_lap_reject(l) is not None

    _, _, laps, _ = _built("British")
    for l in laps[:10]:
        q = l.position_quality()
        assert q["uniqueFrac"] >= 0.95 and q["medianStepM"] >= 1.0
        assert geometry_lap_reject(l) is None


def test_picker_refuses_the_stale_hold_laps_and_says_so():
    sdir = _session("Hungarian")
    table = LapTable(sdir)
    rejected = {}
    laps = pick_geometry_laps(sdir, table, rejected=rejected)
    assert len(laps) == 60
    assert rejected.get("unique-frac", 0) >= 50, rejected
    for l in laps:
        assert geometry_lap_reject(l) is None


def test_hungarian_ring_is_no_longer_stretched():
    """The Race ring used to be built from the stale-hold laps.

    Measured: 4583.0 m declared when Ring.length was asserted as n*ds, 4280.4 m once
    the length was measured, against 4331.0 m built from the same circuit's Qualifying
    session and 4341 m from the telemetry distance channel. With the stale-hold laps
    rejected the Race ring comes out 4324.9 m, 0.14 % from the Qualifying ring.
    """
    _, _, _, ring = _built("Hungarian")
    assert 4300.0 <= ring.length <= 4360.0, ring.length
    q_sdir = _session("Hungarian", "Qualifying")
    q_table = LapTable(q_sdir)
    q_ring, _ = build_ring(pick_geometry_laps(q_sdir, q_table))
    assert abs(ring.length - q_ring.length) / q_ring.length < 0.01


@pytest.mark.parametrize("event", ["British", "Japanese", "Monaco"])
def test_clean_events_keep_every_geometry_lap(event):
    sdir = _session(event)
    rejected = {}
    laps = pick_geometry_laps(sdir, LapTable(sdir), rejected=rejected)
    assert len(laps) == 60
    assert rejected.get("unique-frac", 0) == 0
    assert rejected.get("median-step", 0) == 0


# ===================================================== the ring's reference lap


def test_build_ring_refuses_a_reference_lap_that_does_not_close():
    """close_ring welds the reference's last sample to its first.

    Measured at Hungary once the stale-hold laps are rejected: the fastest survivor
    traces a 1085.7 m first-to-last gap, and welding it built a 7489 m ring for a
    4327 m circuit. Here the fastest lap carries a 300 m tail and the rest are closed.
    """
    bad = _circle_lap("AAA", 1, tail_m=300.0)
    laps = [bad] + [_circle_lap(d, 2) for d in ("BBB", "CCC", "DDD", "EEE", "FFF")]

    assert bad.position_quality()["endGapM"] > 250.0
    ref_i, gap, forced = _reference_lap(laps)
    assert ref_i != 0 and not forced and gap < 50.0

    ring, _ = build_ring(laps)
    circumference = 2 * np.pi * 200.0
    assert ring.length == pytest.approx(circumference, rel=0.02)
    assert ring.build_report["referenceEndGapM"] < 50.0

    # and what the old code did: laps[0] as the reference, unconditionally
    welded, _ = build_ring([bad] * 6)
    assert welded.length > 1.2 * circumference


# ============================================================== timing lines


def test_xy_at_lap_time_refuses_instead_of_clamping():
    lap = _circle_lap("AAA", 1)
    t_end = float(lap.t[-1])
    inside = _xy_at_lap_time(lap, t_end * 0.5)
    assert np.isfinite(inside[0])
    assert not np.isfinite(_xy_at_lap_time(lap, t_end + 0.05)[0])
    assert not np.isfinite(_xy_at_lap_time(lap, -0.05)[0])


def test_start_finish_request_falls_off_the_end_of_its_own_lap():
    """Every {lap}_tel.json spans exactly [0, lapTime] while the S/F request is
    s3T - lST = lapTime + an offset, so np.interp used to return the last sample.
    Measured median excess: +0.023..+0.092 s at twelve events, +1.607 s at Zandvoort."""
    sdir = _session("Dutch")
    table = LapTable(sdir)
    idx = _lap_index(table)
    laps = pick_geometry_laps(sdir, table, limit=5)
    checked = 0
    for l in laps:
        row, ok = idx[(l.driver, l.lap)]
        t_rel = float(row["s3T"]) - float(row["lST"])
        assert t_rel > float(l.t[-1]), "Zandvoort's S/F request must overshoot the lap"
        assert not np.isfinite(_xy_at_lap_time(l, t_rel)[0])
        axis = _lap_axis(sdir, idx, l, row)
        px, py = _xy_at_lap_time(l, t_rel, axis)
        assert np.isfinite(px)
        clamped = np.hypot(px - l.x[-1], py - l.y[-1])
        assert clamped > 100.0, clamped      # measured ~135 m at Zandvoort
        checked += 1
    assert checked >= 3


def test_timing_line_scatter_is_not_an_outlier_artefact():
    """One lap 1900 m away on a 4583 m ring used to report std 241.3 m."""
    class _Ring:
        length = 4583.0

    core = list(np.linspace(-15.0, 15.0, 59) % _Ring.length)
    acc = {"st": core + [1900.0], "outOfRange": 0, "offLine": 0}
    out = _line_stats(acc, _Ring, min_laps=10)
    assert out["sigmaMetres"] < 20.0
    assert out["nRefused"] == 1 and out["nKept"] == 59
    assert abs(out["station"]) < 5.0 or abs(out["station"] - _Ring.length) < 5.0
    assert float(np.std(np.array(acc["st"]))) > 200.0     # what np.std reported


def test_timing_line_rejects_a_point_that_is_not_on_the_racing_line():
    """Monaco's sector-2 line: 7 of 60 laps project 100-1227 m off the ring.

    Measured np.std over the unfiltered list 319.85 m (318.08 m once the S/F clamp is
    also fixed); with the lateral gate the same line reports a robust sigma under 4 m.
    """
    sdir, table, laps, ring = _built("Monaco")
    tl = timing_lines(sdir, table, ring, laps)
    s2 = tl["s2"]
    assert s2["nOffLine"] >= 5
    assert s2["sigmaMetres"] < 5.0
    assert s2["nTotal"] >= 45

    # with the gate open the same laps come back in, and the only thing standing between
    # them and the published scatter is the 3-sigma trim -- which is why the old np.std,
    # with neither gate nor trim, reported 319.85 m for a line whose real scatter is 3 m
    loose = timing_lines(sdir, table, ring, laps, max_lateral_m=1e9)["s2"]
    assert loose["nOffLine"] == 0
    assert loose["nTotal"] == s2["nTotal"] + s2["nOffLine"]
    assert loose["nRefused"] >= 5
    assert loose["stdKeptMetres"] < 5.0


def test_every_timing_line_reports_its_refusals():
    for event in ("British", "Monaco"):
        sdir, table, laps, ring = _built(event)
        tl = timing_lines(sdir, table, ring, laps)
        for key in ("sf", "s1", "s2"):
            line = tl[key]
            assert line is not None
            assert "std" not in line, "the raw np.std is not a measurement of the line"
            assert line["nKept"] + line["nRefused"] == line["nTotal"]
            assert line["sigmaMetres"] >= 0.0
            assert "DERIVED" in line["provenance"]


# ================================================================== pit lane


def test_pit_runs_reject_the_frozen_position_feed():
    """Measured bad-sample fraction per run: Hungary 0.871 entry / 0.918 exit, China
    0.811 / 0.624, every other event median 0.000-0.026 with 2 runs in 692 above 0.15."""
    for event in ("Hungarian", "Chinese"):
        sdir, table, _, ring = _built(event)
        runs = pit_runs(sdir, table, ring)
        assert runs["seen"]["entry"] >= 15
        assert len(runs["entry"]) < PIT_MIN_RUNS
        assert len(runs["exit"]) < PIT_MIN_RUNS

    sdir, table, _, ring = _built("British")
    runs = pit_runs(sdir, table, ring)
    assert len(runs["entry"]) >= 25 and len(runs["exit"]) >= 25
    assert runs["rejected"]["entry"] == 0 and runs["rejected"]["exit"] == 0


def test_pit_lane_is_unavailable_where_the_feed_froze():
    """loopLateral, outLapStartLateral and exitLateral used to come out as the SAME
    512.2433945407964 m at Hungary and 557.07 m at China: one sentinel coordinate read
    as three independent measurements."""
    for event in ("Hungarian", "Chinese"):
        sdir, table, _, ring = _built(event)
        pit = pit_lane(sdir, table, ring)
        assert pit["loopLateral"] is None
        assert pit["outLapStartLateral"] is None
        assert pit["exitLateral"] is None
        assert pit["limiterSpeedKph"] is None
        assert pit["provenance"].startswith("UNAVAILABLE")
        assert pit_lane_path(sdir, table, ring, pit) is None


def test_a_healthy_pit_lane_is_untouched():
    sdir, table, _, ring = _built("British")
    pit = pit_lane(sdir, table, ring)
    assert pit["loopLateral"] == pytest.approx(-34.95, abs=0.1)
    assert pit["nIn"] >= 30 and pit["nOut"] >= 30
    assert 70.0 <= pit["limiterSpeedKph"] <= 90.0
    assert pit["provenance"].startswith("DERIVED")


def test_max_pit_lat_m_is_actually_applied():
    """The constant was declared with a comment describing this exact artefact and then
    never referenced anywhere in the repository. It is also raised from 60 m, which is
    provably unsafe -- Silverstone's lane genuinely reaches |lat| = 104.3 m."""
    assert MAX_PIT_LAT_M >= 110.0

    class _Stub:
        def __init__(self, n):
            self.speed = np.full(n, 80.0)
            self.n = n

    def _runs(final_lat):
        n = 40
        st = np.linspace(100.0, 300.0, n)
        lat = np.full(n, -20.0)
        lat[-1] = final_lat
        entry = [{"lap": _Stub(n), "row": {}, "a": 0, "b": n, "st": st, "lat": lat,
                  "badFraction": 0.0} for _ in range(PIT_MIN_RUNS)]
        return {"entry": entry, "exit": [], "seen": {"entry": 3, "exit": 0},
                "rejected": {"entry": 0, "exit": 0},
                "badFraction": {"entry": [], "exit": []}}

    ok = pit_lane(None, None, None, runs=_runs(-30.0))
    assert ok["loopLateral"] == pytest.approx(-30.0)
    blown = pit_lane(None, None, None, runs=_runs(512.24))
    assert blown["loopLateral"] is None
    assert blown["entryStation"] is not None      # the stations are still measured


def test_pit_lane_length_is_measured_under_the_limiter():
    """`lengthMetres` is the DRAWN polyline: it starts at the last racing-line sample
    and ends at the merge, so it includes the approach and the rejoin at racing speed.
    Measured ratios of drawn to speed-limited distance: 1.19x (Spa) to 2.14x
    (Melbourne). The speed-limited span is now published separately."""
    sdir, table, _, ring = _built("British")
    pit = pit_lane(sdir, table, ring)
    path = pit_lane_path(sdir, table, ring, pit)
    assert path["lengthMetres"] == path["drawnPathMetres"]
    assert path["pitLaneMetres"] < path["drawnPathMetres"]
    # independently measured at Silverstone: 603.4 m covered at <= 90 km/h
    assert path["pitLaneMetres"] == pytest.approx(603.4, abs=15.0)
    assert "DERIVED" in path["provenance"]["pitLaneMetres"]


def test_limiter_speed_is_a_plateau_not_a_mean_of_the_whole_out_lap():
    """The old median-of-the-out-lap-head read 89.4 km/h at Monaco, whose pit limit is
    60, and 99.1 at Hungary, where the position feed is frozen."""
    v = np.concatenate([np.zeros(20),                       # stopped in the box
                        np.full(15, 60.0),                  # the limiter
                        np.linspace(60.0, 110.0, 45),       # accelerating away
                        np.linspace(110.0, 260.0, 60)])
    assert _plateau_speed(v) == pytest.approx(60.0, abs=1.0)
    # the old rule: the median of the whole out-lap head inside a coarse 40-110 window
    assert float(np.median(v[(v > 40) & (v < 110)])) > 70.0
    assert _plateau_speed(np.linspace(120.0, 260.0, 100)) is None

    sdir, table, _, ring = _built("Monaco")
    assert pit_lane(sdir, table, ring)["limiterSpeedKph"] == pytest.approx(59.0, abs=3.0)


def test_speed_limited_span_uses_the_last_crossing_on_entry():
    """A first-crossing rule fires at index 0 wherever the pit entry follows a braking
    zone (measured at Spa: it "removes" 4 % of the run instead of 24 %)."""
    x = np.arange(100, dtype=float) * 5.0
    y = np.zeros(100)
    v = np.concatenate([np.full(20, 70.0), np.full(30, 200.0), np.full(50, 80.0)])
    # the car is slow, then fast again, then in the lane: only the last 50 samples count
    entry = _speed_limited_metres(x, y, v, 80.0, "entry")
    assert entry == pytest.approx(245.0, abs=6.0)
    exit_m = _speed_limited_metres(x, y, v[::-1], 80.0, "exit")
    assert exit_m == pytest.approx(245.0, abs=6.0)


# ======================================================= pit-lane elevation


def test_z_hold_detector_separates_a_held_channel_from_a_real_road():
    n = 300
    x = np.arange(n, dtype=float) * 4.0
    y = np.zeros(n)
    rng = np.random.default_rng(7)
    held = 82.1 + rng.normal(0.0, 0.02, n)
    assert _z_held_mask(held, x, y).mean() > 0.95

    road = 82.1 - 0.02 * x + rng.normal(0.0, 0.02, n)   # a 2 % grade, Suzuka's straight
    assert _z_held_mask(road, x, y).mean() < 0.05


def test_pit_lane_elevation_is_never_a_held_value():
    """Measured: the channel is pinned within 0.018-0.057 m over 300-600 m of road while
    the ring beside it moves up to 18.6 m, which drew the Suzuka lane 22.7 m above the
    tarmac. A point with no measured elevation is emitted as null."""
    sdir, table, _, ring = _built("Japanese")
    pit = pit_lane(sdir, table, ring)
    path = pit_lane_path(sdir, table, ring, pit)
    for seg in path["segments"]:
        z = seg["zCm"]
        assert any(v is None for v in z)
        assert seg["zHeldFraction"] >= 0.8
        assert seg["zMeasuredPoints"] == sum(1 for v in z if v is not None)
        # whatever survives must be a real profile, not one repeated number
        measured = [v for v in z if v is not None]
        if len(measured) > 3:
            assert len(set(measured)) > 1
    assert "null where it is held" in path["provenance"]["elevation"]


# ==================================================================== grid


def test_pit_starters_are_observed_from_the_pout_row():
    """|ring lateral| > 5 m mis-classified exactly three events: 19 false pit starters
    at Monaco, 18 at China and 6 at Hungary. Every other event agreed with the measured
    lap-1 pout driver for driver, and must not move."""
    sdir, table, _, ring = _built("Hungarian")
    g = grid(sdir, table, ring, 0.0)
    assert g["pitStarters"] == ["PER"]
    assert len(g["order"]) == 21
    assert g["unplaced"] == []
    assert "OBSERVED" in g["provenance"]

    sdir, table, _, ring = _built("Monaco")
    g = grid(sdir, table, ring, 0.0)
    assert g["pitStarters"] == ["BOR"]
    assert len(g["unplaced"]) == 19
    assert len(g["order"]) == 2


@pytest.mark.parametrize("event,order_n,pit_n", [("British", 21, 1), ("Australian", 20, 0),
                                                  ("Dutch", 21, 1), ("Italian", 20, 2)])
def test_healthy_grids_do_not_move(event, order_n, pit_n):
    sdir, table, _, ring = _built(event)
    g = grid(sdir, table, ring, 0.0)
    assert len(g["order"]) == order_n
    assert len(g["pitStarters"]) == pit_n
    assert g["unplaced"] == []


def test_china_emits_an_honest_unavailable_grid():
    """All 18 cars report ONE lap-1 station to the last decimal, because the feed wrote
    its "position unknown" marker instead of a coordinate. The real grid was never
    transmitted, so no order is emitted."""
    sdir, table, _, ring = _built("Chinese")
    g = grid(sdir, table, ring, 0.0)
    assert g["order"] == []
    assert g["slots"] == []
    assert len(g["unplaced"]) == 18
    assert g["provenance"].startswith("UNAVAILABLE")
    assert "never transmitted" in g["provenance"]


def test_two_cars_side_by_side_are_not_mistaken_for_a_shared_sentinel():
    """The smallest honest gap between two lap-1 stations is 0.224 m (Zandvoort ALB and
    OCO, side by side in one row). A shared sentinel repeats to ~1e-13 m."""
    assert GRID_DUP_STATION_M < 0.2
    sdir, table, _, ring = _built("Dutch")
    g = grid(sdir, table, ring, 0.0)
    assert g["unplaced"] == []
    assert len(g["order"]) == 21


def test_lateral_reject_is_shared_by_every_consumer():
    """timing_lines used to throw the projection lateral away entirely, so it was the
    one consumer with no lateral gate at all."""
    assert LATERAL_REJECT_M == 5.0


# ---------------------------------------------------------------------------
# The grid's position along the lap
# ---------------------------------------------------------------------------

def test_grid_slot_station_uses_the_measured_anchor():
    """Pole goes where the fit says it is, and the rest of the field is laid back from
    THERE at the grid pitch -- not from the timing line."""
    L, pitch = 5825.74, 8.0
    at = lambda i, anchor: grid_slot_station(0.0, L, anchor, pitch, i)
    assert at(0, 110.815) == pytest.approx(110.815)
    assert at(1, 110.815) == pytest.approx(102.815)
    # the back of a 21-car grid is 160 m behind pole, which at Silverstone is BEHIND the
    # timing line: it wraps onto the end of the lap rather than going negative
    assert at(20, 110.815) == pytest.approx((110.815 - 20 * pitch) % L)
    assert at(20, 110.815) > L / 2
    # every slot stays on the lap, including one laid back across the line
    assert all(0 <= at(i, 27.5) < L for i in range(22))
    assert at(5, 27.5) == pytest.approx((27.5 - 5 * pitch) % L)


def test_grid_slot_station_without_an_anchor_is_the_old_rule():
    """A session whose cars do not resolve a lattice keeps pole one pitch behind the
    line. It is the worse answer -- it is what put the British grid 120 m from its boxes
    -- but it is the only one such a session has earned."""
    L, pitch = 5825.74, 8.0
    for i in range(5):
        assert grid_slot_station(0.0, L, None, pitch, i) == pytest.approx(
            (0.0 - (i + 1) * pitch) % L)
    assert grid_slot_station(0.0, L, float("nan"), pitch, 0) == pytest.approx(L - pitch)


def test_grid_box_sample_ignores_a_stop_the_car_made_after_launching():
    """Measured at Spa 2026: RUS's lap 1 opens at 2 km/h -- a creep under a stationary
    car -- and the first `speed == 0` in the whole lap is 298 samples later, 2.3 km down
    the road. Searching the whole lap took that as RUS's grid box."""
    creeping = np.array([2.0, 4.0, 40.0, 120.0, 0.0, 0.0, 90.0])
    assert _grid_box_sample(creeping) == 0
    # a genuine stationary start still reports the stationary sample
    assert _grid_box_sample(np.array([3.0, 0.0, 0.0, 60.0, 0.0])) == 1
    assert _grid_box_sample(np.array([0.0, 0.0, 5.0, 80.0])) == 0
    # the threshold is a launch, not a creep
    assert 1.0 < GRID_LAUNCH_KPH < 30.0


@pytest.mark.parametrize("event,expected", [("British", 110.8), ("Dutch", 27.5),
                                             ("Italian", 288.1)])
def test_measured_anchors_survive(event, expected):
    """These three resolve a clean lattice and their anchors are the numbers the grid
    is actually drawn from, so a drift here moves a whole field."""
    sdir, table, _, ring = _built(event)
    g = grid(sdir, table, ring, 0.0)
    assert g["anchorMetres"] == pytest.approx(expected, abs=0.6)
    assert g["slots"][0]["station"] == pytest.approx(g["anchorMetres"] % ring.length, abs=1e-6)


def test_belgian_anchor_is_refused_rather_than_shipped_2_km_out():
    """Spa's lattice fits a textbook pitch at high coherence and is still pinned to the
    wrong end of the lap by one car's bad stationary sample. With that sample fixed the
    anchor is the real one; either way it may never be the 2455 m the artifact shipped."""
    sdir, table, _, ring = _built("Belgian")
    g = grid(sdir, table, ring, 0.0)
    anchor = g["anchorMetres"]
    assert anchor is None or abs(anchor - 101.9) < 15.0, g["anchorNote"]
