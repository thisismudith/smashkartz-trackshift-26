"""Tests for the replay encoder's POSITION contract.

Everything here is about one rule from AGENTS.md 42.5: the encoder may not invent a
position. Before these tests the encoder invented one in four different ways --

  * `np.clip(lateral, -327, 327)` turned an unplaceable car into a plausible int16
    (23,548 samples across five packs, up to a 1227 m true error compressed into 327 m),
  * `np.nan_to_num(station, nan=0.0)` put a car with no position exactly on the
    start/finish line,
  * `(d - d[0]) / span * ring.length` rebased every frame-B lap onto its own first
    sample and stretched a 1482.9 m partial lap 2.2167x to fill the ring,
  * `np.linspace(0, ring.length, n)` fabricated one complete lap of motion for a lap
    with no usable distance channel at all.

-- and `ring.project` aliased a Suzuka lap across the figure-8 crossover. Each test
below fails on that code and passes on the current encoder. Where the fault is visible
in data/2026 the test measures the real lap rather than a fixture.
"""
from __future__ import annotations

import functools
from pathlib import Path

import numpy as np
import pytest

from simdata import replay
from simdata.geom import Ring
from simdata.rawio import LapTable, SentinelIndex, load_lap
from simdata.replay import (FRAME_B_FULL_LAP_BAND, LATERAL_ABSENT_CM,
                            LATERAL_ABS_MAX_M, encode_lap)

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "2026"

SAMPLE_DTYPE = np.dtype([("dtMs", "<u2"), ("stationM", "<f4"), ("lateralCm", "<i2"),
                         ("speedKph", "<u2"), ("gearBrake", "u1"), ("throttle", "u1")])


def decode(blob: bytes) -> np.ndarray:
    """Decode the blob exactly as frontend/src/sim/data/codec.ts does."""
    assert len(blob) % replay.SAMPLE_STRUCT.size == 0
    return np.frombuffer(blob, dtype=SAMPLE_DTYPE)


def circle_ring(radius: float = 520.0) -> Ring:
    """A closed circular ring, ~3267 m round -- Monaco's order of magnitude."""
    th = np.arange(0.0, 2 * np.pi, 1.0 / radius)
    return Ring(radius * np.cos(th), radius * np.sin(th), np.zeros_like(th), 1.0)


def synthetic_lap(x_m, y_m, dist_m, *, speed_kph=200.0, dt=0.25, driver="TST", lap=1):
    """A Lap built from arrays. x/y/z are handed over in DECIMETRES, as the feed does."""
    x_m = np.asarray(x_m, dtype=float)
    y_m = np.asarray(y_m, dtype=float)
    dist_m = np.asarray(dist_m, dtype=float)
    n = x_m.size
    tel = {
        "time": list(np.arange(n) * dt),
        "speed": [speed_kph] * n,
        "distance": list(dist_m),
        "x": list(x_m * 10.0),
        "y": list(y_m * 10.0),
        "z": [0.0] * n,
        "gear": [7] * n,
        "brake": [0] * n,
        "throttle": [100.0] * n,
        "rpm": [11000.0] * n,
        "DriverAhead": ["None"] * n,
        "DistanceToDriverAhead": ["None"] * n,
    }
    from simdata.rawio import Lap
    return Lap(driver, lap, tel)


def on_ring_lap(ring: Ring, *, start=0.0, length=None, n=400, lateral=0.0):
    """A lap that genuinely drives `length` metres of the ring from station `start`."""
    length = ring.length if length is None else length
    s = start + np.linspace(0.0, length, n)
    px, py, _ = ring.point_at(s)
    if lateral:
        idx = (np.floor((s % ring.length) / ring.ds).astype(int)) % ring.n
        px = px + lateral * ring.nx[idx]
        py = py + lateral * ring.ny[idx]
    return synthetic_lap(px, py, s - start), s


# --------------------------------------------------------------- the clip -> absence


def test_unplaceable_lateral_is_absent_not_clipped():
    """A 400 m projection is not a position. The old encoder clipped it to 327.00 m
    and the renderer drew that faithfully; it must now be ABSENT."""
    ring = circle_ring()
    lap, _ = on_ring_lap(ring, n=200)
    # push ten samples 400 m radially outward, i.e. 400 m off the centreline
    grow = (520.0 + 400.0) / 520.0
    lap.x[50:60] *= grow
    lap.y[50:60] *= grow
    blob, report = encode_lap(ring, lap)
    s = decode(blob)

    assert report["frame"] == "A"
    assert report["lateralRejected"] == 10
    assert report["absent"] == 10
    assert (s["lateralCm"][50:60] == LATERAL_ABSENT_CM).all()
    assert np.isnan(s["stationM"][50:60]).all()
    # the old behaviour, pinned so it cannot come back
    assert not (np.abs(s["lateralCm"]) == 32700).any(), "lateral was clipped, not rejected"
    # every surviving sample is a real measurement
    keep = s["lateralCm"] != LATERAL_ABSENT_CM
    assert np.isfinite(s["stationM"][keep]).all()


def test_an_accepted_lateral_is_never_clamped():
    """The bound rejects; nothing silently clamps. 100 m of pit lane survives intact."""
    ring = circle_ring()
    lap, _ = on_ring_lap(ring, n=200, lateral=100.0)
    blob, report = encode_lap(ring, lap)
    s = decode(blob)
    assert report["absent"] == 0
    assert np.abs(np.abs(s["lateralCm"] / 100.0) - 100.0).max() < 0.5
    assert 100.0 < LATERAL_ABS_MAX_M


def test_the_lateral_bound_clears_silverstones_measured_pit_lane():
    """Measured max |lateral| at the British GP is 104.33 m (Race) / 103.90 (Sprint) --
    a real pit lane, not a failure. The bound must not blank it."""
    assert LATERAL_ABS_MAX_M > 104.33
    # ...and must still be far below the int16 ceiling the old clip used as a bound
    assert LATERAL_ABS_MAX_M * 100 < 32767
    assert LATERAL_ABS_MAX_M < 327.0


def test_a_masked_position_is_absent_not_teleported_to_the_start_finish_line():
    """`np.nan_to_num(station, nan=0.0)` used to put every position-less sample exactly
    on the start/finish line. Withdrawn positions must be absent instead."""
    ring = circle_ring()
    lap, stations = on_ring_lap(ring, n=300)
    hole = np.zeros(lap.n, bool)
    hole[120:150] = True
    assert lap.drop_positions(hole) == 30
    blob, report = encode_lap(ring, lap)
    s = decode(blob)

    assert report["frame"] == "A"
    assert report["absent"] == 30
    assert (s["lateralCm"][hole] == LATERAL_ABSENT_CM).all()
    assert np.isnan(s["stationM"][hole]).all()
    # nothing landed on station 0 that was not genuinely there
    placed = ~np.isnan(s["stationM"])
    assert np.abs(s["stationM"][placed] - (stations[placed] % ring.length)).max() < 1.0


# ----------------------------------------------------------------- frame B rubber sheet


def test_frame_b_keeps_the_distance_channel_origin():
    """Monaco's distance channel starts a median 7.8 m (max 79.3 m) BEFORE the line.
    Subtracting d[0] pinned that point to station 0 and shoved the whole lap forward."""
    ring = circle_ring()
    L = ring.length
    d = np.linspace(-20.0, L - 20.0, 500)
    lap = synthetic_lap(np.full(500, np.nan), np.full(500, np.nan), d)
    blob, report = encode_lap(ring, lap, 1.0, "DERIVED")
    s = decode(blob)

    assert report["frame"] == "B"
    assert report["provenance"] == "DERIVED"
    # d = -20 is 20 m BEFORE the line, i.e. station L - 20, not station 0
    assert s["stationM"][0] == pytest.approx((L - 20.0), abs=1.0)
    assert s["stationM"][0] > L / 2, "the lap was rebased onto its own first sample"


def test_frame_b_does_not_stretch_a_partial_lap_to_a_full_one():
    """Monaco's shortest frame-B lap spans 1482.9 m and was stretched 2.2167x to fill
    the ring -- 1804 m of motion the car never made. A partial lap stays partial."""
    ring = circle_ring()
    L = ring.length
    span = 0.45 * L
    d = np.linspace(0.0, span, 400)
    lap = synthetic_lap(np.full(400, np.nan), np.full(400, np.nan), d)
    blob, report = encode_lap(ring, lap, 1.0, "DERIVED")
    s = decode(blob)

    assert report["frame"] == "B"
    assert report["distanceScale"] == pytest.approx(1.0)
    assert s["stationM"][-1] == pytest.approx(span, abs=2.0)
    assert s["stationM"][-1] < 0.5 * L, "a 45 % lap was rubber-sheeted onto the full ring"
    # the old code's answer, pinned so it cannot come back
    assert abs(float(s["stationM"][-1]) - L) > 0.5 * L


def test_frame_b_calibrates_a_genuine_full_lap_onto_the_ring():
    """The per-lap scale is a real calibration of an integrated wheel speed -- it stays,
    for the laps that are actually one lap long."""
    ring = circle_ring()
    L = ring.length
    span = 1.01 * L                      # inside FRAME_B_FULL_LAP_BAND
    assert FRAME_B_FULL_LAP_BAND[0] * L <= span <= FRAME_B_FULL_LAP_BAND[1] * L
    d = np.linspace(0.0, span, 600)
    lap = synthetic_lap(np.full(600, np.nan), np.full(600, np.nan), d)
    _, report = encode_lap(ring, lap, 0.5, "DERIVED")
    assert report["distanceScale"] == pytest.approx(L / span)
    assert report["distanceScaleSource"] == "DERIVED"


def test_a_partial_lap_uses_the_session_scale_not_its_own_span():
    ring = circle_ring()
    d = np.linspace(0.0, 0.4 * ring.length, 300)
    lap = synthetic_lap(np.full(300, np.nan), np.full(300, np.nan), d)
    _, report = encode_lap(ring, lap, 0.998, "DERIVED")
    assert report["distanceScale"] == pytest.approx(0.998)
    assert report["distanceScaleSource"] == "DERIVED"


def test_session_distance_scale_is_measured_from_full_laps_only():
    ring = circle_ring()
    L = ring.length
    full = [synthetic_lap(np.full(10, np.nan), np.full(10, np.nan),
                          np.linspace(0.0, k * L, 10)) for k in (0.99, 1.0, 1.01)]
    partial = [synthetic_lap(np.full(10, np.nan), np.full(10, np.nan),
                             np.linspace(0.0, 0.3 * L, 10))]
    scale, source = replay._session_distance_scale(full + partial, L)
    assert source == "DERIVED"
    assert scale == pytest.approx(1.0, abs=1e-6)          # median of 1/0.99, 1, 1/1.01

    scale, source = replay._session_distance_scale(partial, L)
    assert source == "UNCALIBRATED"
    assert scale == 1.0


# ----------------------------------------------------------------- no fabrication left


def test_no_lap_of_motion_is_fabricated_without_a_distance_channel():
    """`np.linspace(0, ring.length, n)` invented one complete lap for a lap with no
    usable distance at all. There must be no position, and it must say so."""
    ring = circle_ring()
    n = 120
    lap = synthetic_lap(np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan))
    blob, report = encode_lap(ring, lap)
    s = decode(blob)

    assert report["frame"] == "NONE"
    assert report["provenance"] is None
    assert report["absent"] == n
    assert np.isnan(s["stationM"]).all()
    assert (s["lateralCm"] == LATERAL_ABSENT_CM).all()
    # the measured channels are untouched -- only the position is missing
    assert (s["speedKph"] == 200).all()
    assert len(s) == n


def test_an_empty_lap_still_returns_a_pair():
    """encode_lap used to `return b""` on n == 0 while the caller unpacked two values."""
    ring = circle_ring()
    lap = synthetic_lap(np.zeros(0), np.zeros(0), np.zeros(0))
    blob, report = encode_lap(ring, lap)
    assert blob == b""
    assert report["frame"] == "NONE"


def test_absence_encoding_is_unambiguous():
    """INT16_MIN cannot collide with a real measurement, and NaN cannot be a station."""
    assert LATERAL_ABSENT_CM == -32768
    assert LATERAL_ABSENT_CM < -LATERAL_ABS_MAX_M * 100


# ------------------------------------------------------------------ real 2026 telemetry


@functools.lru_cache(maxsize=4)
def real_ring(event: str):
    from simdata.build_track import prepare_ring
    ring, _, _, _, _ = prepare_ring(event)
    return ring


def _require(path: Path):
    if not path.exists():
        pytest.skip(f"raw telemetry not present: {path}")


def test_suzuka_crossover_does_not_alias_across_branches():
    """Ring.project is stateless, so at Suzuka's figure-8 a car flips between branches
    2359 m apart in station (529 ring-vertex pairs there lie under 12 m apart while
    over 150 m apart in station; every other 2026 circuit has zero). Measured with
    ring.project over the Japanese Race: 43 of 1107 laps carry a station jump above
    1500 m, worst 2403.6 m on BOR lap 51. project_path holds one branch: the same 43
    laps then peak at 99.4 m, BOR lap 51 at 65.9 m."""
    sdir = DATA_ROOT / "Japanese Grand Prix" / "Race"
    _require(sdir / "BOR" / "51_tel.json")
    ring = real_ring("Japanese Grand Prix")
    lap = load_lap(sdir, "BOR", 51)
    assert lap is not None

    stateless, _ = ring.project(lap.x, lap.y)
    blob, report = encode_lap(ring, lap)
    assert report["frame"] == "A"
    st = decode(blob)["stationM"].astype(np.float64)

    def worst_jump(s):
        s = s[np.isfinite(s)]
        return float(np.abs((np.diff(s) + ring.length / 2) % ring.length
                            - ring.length / 2).max())

    assert worst_jump(stateless) > 2000.0, "fixture no longer reproduces the alias"
    assert worst_jump(st) < 200.0


def test_monaco_collapsed_position_lap_is_not_shipped_as_observed():
    """Monaco laps 42-45 have a numerically collapsed x channel (x runs -2.50 down to
    -1.0e-61) while `distance` spans a full lap and speed peaks at 267 km/h.
    xy_fraction is 1.00, so the old gate called them frame A and drew a PARKED car
    35 m off the centreline, tagged OBSERVED."""
    sdir = DATA_ROOT / "Monaco Grand Prix" / "Race"
    _require(sdir / "ALB" / "43_tel.json")
    lap = load_lap(sdir, "ALB", 43)
    assert lap is not None
    assert lap.xy_fraction() > 0.99, "fixture no longer reproduces the collapse"
    assert lap.path_length() < 5.0 and lap.distance_span() > 3000.0

    ring = circle_ring()          # the frame decision is made before any projection
    _, report = encode_lap(ring, lap, 1.0, "DERIVED")
    assert report["frame"] == "B"
    assert report["provenance"] == "DERIVED"


def test_chinese_sentinel_is_withdrawn_before_encoding():
    """China's feed writes a constant (-832.5, -705.8) when it loses a car. Projected it
    lands 557 m off the centreline; the old encoder clipped that to 327.00 m and drew
    it. It must be discovered, withdrawn, and reported as absent."""
    sdir = DATA_ROOT / "Chinese Grand Prix" / "Race"
    _require(sdir / "session_laptimes.json")
    ring = real_ring("Chinese Grand Prix")
    table = LapTable(sdir)
    drivers = sorted({r["drv"] for r in table.rows()})[:5]
    laps = [lp for r in table.rows() if r["drv"] in drivers
            for lp in [load_lap(sdir, r["drv"], r["lap"])] if lp is not None and lp.n]
    assert len(laps) > 50

    sentinels = SentinelIndex.from_laps(laps)
    assert len(sentinels) >= 1, "the session sentinel was not discovered"
    px, py = sentinels.points[0]
    assert abs(px - (-832.5)) < 2.0 and abs(py - (-705.8)) < 2.0

    hit = [lp for lp in laps if sentinels.mask(lp.x, lp.y).any()][:12]
    assert hit, "no lap carries the sentinel"
    withdrawn = absent = 0
    for lp in hit:
        withdrawn += lp.drop_positions(sentinels.mask(lp.x, lp.y))
        _, report = encode_lap(ring, lp, 1.0, "DERIVED")
        absent += report["absent"]
    assert withdrawn > 50
    assert absent >= withdrawn


@pytest.mark.parametrize("event,session", [("British Grand Prix", "Race")])
def test_a_healthy_circuit_loses_no_position(event, session):
    """The bound and the frame gate must cost a working circuit nothing. Silverstone's
    pit lane genuinely reaches 104.33 m off the centreline -- 1612 samples beyond 60 m
    -- and every one of them must survive."""
    sdir = DATA_ROOT / event / session
    _require(sdir / "session_laptimes.json")
    ring = real_ring(event)
    table = LapTable(sdir)
    rows = [r for r in table.rows() if r["drv"] == "NOR"]
    # keep every pit lap (those carry the 60-104 m lateral) plus a few racing laps
    rows = [r for r in rows if r["pin"] != "None" or r["pout"] != "None"] + rows[:6]
    seen_wide = absent = frames_b = 0
    widest = 0.0
    for r in rows:
        lp = load_lap(sdir, r["drv"], r["lap"])
        if lp is None or lp.n == 0:
            continue
        blob, report = encode_lap(ring, lp, 1.0, "DERIVED")
        absent += report["absent"]
        frames_b += report["frame"] != "A"
        lat = decode(blob)["lateralCm"] / 100.0
        seen_wide += int((np.abs(lat) > 60.0).sum())
        widest = max(widest, float(np.abs(lat).max()))
    assert absent == 0, "a healthy circuit lost position data"
    assert frames_b == 0
    assert seen_wide >= 50, "the wide pit-lane samples vanished"
    assert widest > 60.0, "the fixture no longer covers the wide pit-lane case"


def test_manifest_carries_the_frame_tag_and_the_absence_it_measured(monkeypatch):
    """The frame tag and the withdrawn positions have to REACH the frontend, otherwise
    the encoder is honest and the renderer still is not. Measured on the Chinese Sprint,
    whose feed writes the (-832.5, -705.8) sentinel: 5,088 samples used to be clipped to
    +-327.00 m and drawn.

    The energy twin is stubbed out: this is a test of the position contract, and
    estimate_ers costs ~25 s on this session for numbers it does not touch.
    """
    sdir = DATA_ROOT / "Chinese Grand Prix" / "Sprint"
    _require(sdir / "session_laptimes.json")

    def no_twin(*a, **k):
        raise ValueError("twin disabled for this test")
    monkeypatch.setattr(replay, "estimate_ers", no_twin)

    blob, manifest = replay.build_replay_pack("Chinese Grand Prix", "Sprint")
    integrity = manifest["positionIntegrity"]

    assert integrity["absentLateralCm"] == LATERAL_ABSENT_CM
    assert integrity["lateralBoundM"] == LATERAL_ABS_MAX_M
    assert len(integrity["sentinelPoints"]) == 1
    px, py = integrity["sentinelPoints"][0]
    assert abs(px - (-832.5)) < 2.0 and abs(py - (-705.8)) < 2.0
    assert integrity["samplesSentinelWithdrawn"] > 4000
    assert integrity["samplesPositionAbsent"] > 5000
    assert manifest["capabilities"]["hasPositionGaps"] is True
    assert integrity["distanceScaleSource"] in ("DERIVED", "UNCALIBRATED")
    # the field names the frontend already types (data/manifest.ts)
    assert manifest["capabilities"]["positionSamplesDropped"] ==         integrity["samplesSentinelWithdrawn"]

    laps = [l for d in manifest["drivers"] for l in d["laps"]]
    assert laps
    for l in laps:
        assert l["positionFrame"] in ("A", "B", "NONE")
        assert l["positionProvenance"] in ("OBSERVED", "DERIVED", None)
        assert 0 <= l["positionAbsentSamples"] <= l["sampleCount"]
        assert 0 <= l["positionDropped"] <= l["sampleCount"]
        q = l["positionQuality"]
        assert set(q) == {"xyFraction", "pathOverSpan", "uniqueFrac",
                          "repeatBackFrac", "endGapM", "medianStepM"}
    assert sum(l["positionDropped"] for l in laps) ==         integrity["samplesSentinelWithdrawn"]
    assert sum(l["positionAbsentSamples"] for l in laps) == integrity["samplesPositionAbsent"]
    assert sum(1 for l in laps if l["positionFrame"] == "A") == integrity["lapsFrameA"]

    # the absence lives in the blob, never as a NaN in the JSON
    import json as _json
    _json.dumps(manifest, allow_nan=False)
    s = decode(blob)
    assert int(np.isnan(s["stationM"]).sum()) == integrity["samplesPositionAbsent"]
    assert int((s["lateralCm"] == LATERAL_ABSENT_CM).sum()) == integrity["samplesPositionAbsent"]
