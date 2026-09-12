"""Contract tests for the two primitives every other pipeline module builds on:
the Ring's arc-length parametrisation, and the "position unknown" sentinel detector.

These pin behaviour that several downstream modules depend on, so they are deliberately
about the CONTRACT rather than about implementation detail.
"""
from __future__ import annotations

import numpy as np
import pytest

from simdata.geom import Ring
from simdata.rawio import SentinelIndex, stuck_mask


# --------------------------------------------------------------------- Ring


def _circle(radius=100.0, ds=1.0):
    th = np.arange(0.0, 2 * np.pi, ds / radius)
    return Ring(radius * np.cos(th), radius * np.sin(th), np.zeros_like(th), ds)


def test_ring_length_is_measured_not_asserted():
    """length must be the polyline's own closed length, not n * ds.

    The old code declared length = n * ds, which was wrong by 0.03-0.24 % on eleven
    circuits, 0.50 % at Monaco and 6.37 % at Hungary, because build_ring smooths the
    ring after fixing the sample count.
    """
    r = _circle(100.0)
    assert r.length == pytest.approx(2 * np.pi * 100.0, rel=1e-4)


def test_ring_invariant_n_times_ds_equals_length():
    """The frontend computes ds = lengthMetres / n. That must be exact, on any ring."""
    for radius in (50.0, 100.0, 733.0):
        r = _circle(radius)
        assert r.n * r.ds == pytest.approx(r.length, rel=1e-12)
        assert r.s[-1] + r.ds == pytest.approx(r.length, rel=1e-12)


def test_ring_station_spans_exactly_one_lap():
    r = _circle(100.0)
    st, lat = r.project(np.array([100.0, -100.0]), np.array([0.0, 0.0]))
    assert 0.0 <= st[0] < r.length
    assert st[1] == pytest.approx(r.length / 2, abs=2.0)
    assert abs(lat).max() < 1.0


def test_ring_length_survives_a_non_uniform_polyline():
    """A ring whose vertices are NOT evenly spaced must still declare its true length."""
    th = np.concatenate([np.linspace(0, np.pi, 400), np.linspace(np.pi, 2 * np.pi, 80)[1:]])
    r = Ring(100 * np.cos(th), 100 * np.sin(th), np.zeros_like(th), 1.0)
    seg = np.hypot(np.diff(r.x), np.diff(r.y)).sum()
    wrap = np.hypot(r.x[0] - r.x[-1], r.y[0] - r.y[-1])
    assert r.length == pytest.approx(seg + wrap, rel=1e-12)
    assert r.n * r.ds == pytest.approx(r.length, rel=1e-12)


def test_project_path_does_not_alias_across_a_self_crossing():
    """A figure-8 ring passes within centimetres of itself. A stateless nearest-point
    query jumps between the branches; project_path must not. Measured on the real
    Suzuka ring, the stateless worst single-sample jump is 2381 m against a 219 m
    worst case here."""
    t = np.linspace(0, 2 * np.pi, 1200, endpoint=False)
    ring = Ring(200 * np.sin(t), 120 * np.sin(2 * t), np.zeros_like(t), 1.0)

    # a trace that runs along one branch straight through the crossover at the origin
    tt = np.linspace(0.2, 1.2, 200)
    px, py = 200 * np.sin(tt), 120 * np.sin(2 * tt)

    def worst_jump(st):
        st = st[np.isfinite(st)]
        d = np.abs(np.diff(st))
        return float(np.minimum(d, ring.length - d).max()) if d.size else 0.0

    loose = worst_jump(ring.project(px, py)[0])
    tight = worst_jump(ring.project_path(px, py)[0])
    assert tight <= loose
    assert tight < ring.length / 8


def test_project_path_matches_project_when_there_is_no_crossing():
    r = _circle(100.0)
    th = np.linspace(0.1, 3.0, 150)
    px, py = 100 * np.cos(th), 100 * np.sin(th)
    a, _ = r.project(px, py)
    b, _ = r.project_path(px, py)
    assert np.abs(a - b).max() < 1.5


# ---------------------------------------------------------------- sentinels


def _trace(n=60, dt=0.25, speed=200.0):
    t = np.arange(n) * dt
    step = speed / 3.6 * dt
    return t, np.full(n, speed), np.arange(n) * step, np.zeros(n)


def test_stuck_mask_is_silent_on_a_clean_trace():
    t, v, x, y = _trace()
    assert not stuck_mask(t, v, x, y).any()


def test_stuck_mask_finds_a_held_position_at_racing_speed():
    """The defining property: the position stops while the SPEED channel keeps running.
    A detector keyed on "the car looks stopped" cannot see this."""
    t, v, x, y = _trace()
    x[20:30] = x[20]
    m = stuck_mask(t, v, x, y)
    assert m[21:30].all()
    assert not m[:19].any() and not m[32:].any()


def test_stuck_mask_ignores_a_genuinely_stationary_car():
    """A car actually stopped demands no movement, so it is not 'stuck'."""
    t, v, x, y = _trace()
    v[20:40] = 0.0
    x[20:40] = x[20]
    assert not stuck_mask(t, v, x, y).any()


def test_stuck_mask_needs_a_run_not_one_sample():
    t, v, x, y = _trace()
    x[20] = x[19]
    assert not stuck_mask(t, v, x, y).any()


def test_stuck_mask_survives_float_noise_on_the_held_value():
    """The provider writes -8325.0, -8325.000000000002, -8325.000000000004 for one
    point; exact-equality detection sees 0.704 % where the truth is ~2 %."""
    t, v, x, y = _trace()
    x[20:30] = x[20] + np.array([0, 2e-12, 4e-12, 0, 2e-12, 0, 4e-12, 0, 2e-12, 0])
    assert stuck_mask(t, v, x, y)[21:30].all()


class _FakeLap:
    def __init__(self, driver, x, y, t, v):
        self.driver, self.n = driver, len(x)
        self.x, self.y, self.t, self.speed = x, y, t, v
        self._stuck = None

    @property
    def stuck(self):
        if self._stuck is None:
            self._stuck = stuck_mask(self.t, self.speed, self.x, self.y)
        return self._stuck


def _sentinel_laps(drivers, hold_at, n_laps=12):
    laps = []
    for d in drivers:
        for _ in range(n_laps):
            t, v, x, y = _trace(n=60)
            x[20:40] = hold_at[0]
            y[20:40] = hold_at[1]
            laps.append(_FakeLap(d, x.copy(), y.copy(), t, v))
    return laps


def test_sentinel_found_when_many_drivers_share_one_point():
    idx = SentinelIndex.from_laps(_sentinel_laps(['A', 'B', 'C', 'D'], (-832.5, -705.8)))
    assert len(idx) >= 1
    assert np.hypot(idx.points[:, 0] + 832.5, idx.points[:, 1] + 705.8).min() < 2.0


def test_sentinel_rejected_when_only_one_driver_uses_it():
    """One car parked is a parked car, not the feed's 'unknown' marker."""
    assert not SentinelIndex.from_laps(_sentinel_laps(['A'], (-832.5, -705.8), n_laps=40))


def test_sentinel_rejected_when_holds_are_scattered():
    """A STALE HOLD repeats the car's last known position, so it lands wherever the car
    was. This is Hungary's fault, and it must not be masked as a sentinel: measured, a
    detector without this test withdraws 62.8 % of Hungary's positions."""
    laps = []
    for i, d in enumerate(['A', 'B', 'C', 'D', 'E']):
        for k in range(20):
            t, v, x, y = _trace(n=60)
            x[20:40] = 500.0 * ((i * 20 + k) % 25)     # a different place every time
            y[20:40] = 300.0 * ((i * 20 + k) % 17)
            laps.append(_FakeLap(d, x.copy(), y.copy(), t, v))
    assert not SentinelIndex.from_laps(laps)


def test_sentinel_mask_is_local_to_the_point():
    idx = SentinelIndex.from_laps(_sentinel_laps(['A', 'B', 'C'], (0.0, 0.0)))
    x = np.array([0.0, 1.0, 50.0, 500.0])
    y = np.zeros(4)
    m = idx.mask(x, y)
    assert m[0] and m[1]
    assert not m[2] and not m[3]
