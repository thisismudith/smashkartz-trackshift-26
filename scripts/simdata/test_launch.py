"""Tests for the standing-start model.

Two kinds of test, deliberately kept apart:

  * CONTRACT tests, synthetic and exact, for the pure kinematics and the estimators.
    These pin the properties the New Race engine was violating -- every car on station 0,
    every car already at speed, consecutive gaps of 0.00-0.34 m against a 5.6 m car --
    so each of them fails on the engine as it stood and passes on this model.
  * MEASURED tests, run against the real feed in the raw mirror, for every number this module
    claims. The bands below are wide enough not to be a copy of today's output and tight
    enough that a regression to an assumed textbook value would break them.

Every number quoted in a docstring here was measured, not illustrated.
"""
from __future__ import annotations

import functools
import json
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from simdata.launch import (LAUNCH_FIT_CEILING_KPH,
                            PITCH_MIN_COHERENCE_RATIO, SLOT_MIN_ROWS, LaunchParams,
                            assign_slots, cluster_mean, delete_one_cluster_se,
                            fit_constant_acceleration,
                            fit_pitch, fit_standing_start,
                            geometric_seconds_per_slot, grid_slope_fit, lap1_distance_m,
                            lattice_noise_floor, lattice_period, launch_profile,
                            launch_state, launch_time_loss_s, pooled_lattice_period,
                            principal_axis, race_sessions, regression_to_mean,
                            robust_location_scale, session_start, slot_offset_m,
                            slot_station_m)
from simdata.paths import data_root

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
DATA_ROOT = data_root()

# The car the frontend actually draws: frontend/src/sim/render/carGeometry.ts uses
# CAR_RENDER_LENGTH_M = 5.6. Two cars closer than this along the ring are drawn inside
# one another, which is the defect this whole module exists to remove.
CAR_LENGTH_M = 5.6

# The six-word provenance vocabulary. There is no seventh, and absence is null.
PROVENANCE = {"OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "RULE", "DEFAULT"}


@functools.lru_cache(maxsize=1)
def fitted():
    """The whole fitted block, read from the real feed once for every measured test."""
    return fit_standing_start()


@functools.lru_cache(maxsize=None)
def start_of(event: str, session: str = "Race"):
    return session_start(event, session)


def _uniform_field(n=20, reaction=0.53, accel=9.78, handover=33.33):
    order = [f"C{i:02d}" for i in range(n)]
    params = {d: LaunchParams(reaction, accel, handover) for d in order}
    return order, params


# ======================================================================================
# CONTRACT: the grid exists at t = 0
# ======================================================================================

def test_cars_start_on_separate_stations_not_all_on_zero():
    """The engine this replaces put every car at stationM EXACTLY 0 (spread 0.00 m)."""
    order, params = _uniform_field(20)
    state = launch_state(order, params, 0.0, anchor_m=116.9, pitch_m=8.029,
                         ring_length_m=5825.74)
    stations = [c["progressM"] for c in state]
    assert len(set(round(s, 6) for s in stations)) == 20
    assert len(set(round(c["stationM"], 6) for c in state)) == 20
    spread = max(stations) - min(stations)
    assert spread == pytest.approx(19 * 8.029, abs=1e-6)
    assert spread > 150.0


def test_consecutive_cars_are_never_inside_one_another_on_the_grid():
    """Measured gaps in the old engine were 0.00-0.34 m against a 5.6 m car."""
    order, params = _uniform_field(22)
    state = launch_state(order, params, 0.0, anchor_m=116.9, pitch_m=8.029,
                         ring_length_m=5825.74)
    gaps = [state[i]["progressM"] - state[i + 1]["progressM"]
            for i in range(len(state) - 1)]
    assert min(gaps) == pytest.approx(8.029, abs=1e-9)
    assert min(gaps) > CAR_LENGTH_M


def test_the_whole_field_is_stationary_until_its_own_reaction_time():
    """The old engine had every car at 138.9-247.2 kph at t = 0."""
    order, params = _uniform_field(20, reaction=0.53)
    for t in (0.0, 0.25, 0.52):
        state = launch_state(order, params, t, anchor_m=0.0, pitch_m=8.0,
                             ring_length_m=5000.0)
        assert all(c["speedMps"] == 0.0 for c in state)
        assert all(c["phase"] == "GRID" for c in state)
        assert all(c["distanceM"] == 0.0 for c in state)
    moving = launch_state(order, params, 0.54, anchor_m=0.0, pitch_m=8.0,
                          ring_length_m=5000.0)
    assert all(c["speedMps"] > 0.0 for c in moving)


def test_a_field_with_identical_launches_holds_its_spacing_all_the_way():
    """Identical parameters must translate the field, never collapse it onto one point."""
    order, params = _uniform_field(22)
    for t in (0.0, 0.6, 1.5, 4.0, 12.0):
        state = launch_state(order, params, t, anchor_m=100.0, pitch_m=8.029,
                             ring_length_m=5825.74)
        gaps = [state[i]["progressM"] - state[i + 1]["progressM"]
                for i in range(len(state) - 1)]
        assert min(gaps) == pytest.approx(8.029, abs=1e-9)


def test_a_slow_starter_is_overrun_but_the_model_says_so_rather_than_teleporting():
    """A car that reacts late genuinely loses ground; the loss is continuous in t."""
    order = ["POLE", "P2"]
    params = {"POLE": LaunchParams(1.40, 9.78, 33.33), "P2": LaunchParams(0.40, 9.78, 33.33)}
    gaps = []
    for t in np.arange(0.0, 6.0, 0.05):
        s = launch_state(order, params, float(t), anchor_m=0.0, pitch_m=8.029,
                         ring_length_m=5000.0)
        gaps.append(s[0]["progressM"] - s[1]["progressM"])
    assert gaps[0] == pytest.approx(8.029, abs=1e-9)
    assert min(gaps) < 0.0                                   # P2 really does get past
    assert max(abs(np.diff(gaps))) < 2.0                     # and does it continuously


def test_the_non_interpenetration_constraint_removes_every_overlap():
    """Two cars cannot share a metre of track. Without the constraint two different
    fitted accelerations really do close an 8 m box gap in a couple of seconds; with it
    the field stays physically admissible and says which cars were held."""
    order = ["A", "B", "C"]
    params = {"A": LaunchParams(0.70, 8.6, 33.33),      # slow away from pole
              "B": LaunchParams(0.42, 10.8, 33.33),     # fastest launch in the field
              "C": LaunchParams(0.50, 10.0, 33.33)}
    free_overlaps = 0
    for t in np.arange(0.0, 6.0, 0.02):
        free = launch_state(order, params, float(t), anchor_m=100.0, pitch_m=8.029,
                            ring_length_m=5000.0)
        p = [c["progressM"] for c in free]
        free_overlaps += sum(1 for i in range(len(p)) for j in range(i + 1, len(p))
                             if abs(p[i] - p[j]) < CAR_LENGTH_M)
        held = launch_state(order, params, float(t), anchor_m=100.0, pitch_m=8.029,
                            ring_length_m=5000.0, min_gap_m=CAR_LENGTH_M)
        q = [c["progressM"] for c in held]
        assert min(q[i] - q[i + 1] for i in range(len(q) - 1)) >= CAR_LENGTH_M - 1e-9
        for a, b in zip(held, held[1:]):
            if b["phase"] == "QUEUED":
                assert b["speedMps"] <= a["speedMps"] + 1e-12
    assert free_overlaps > 0, "the unconstrained model must be able to overlap at all"


def test_the_constraint_is_inert_when_nothing_is_catching_anything():
    order, params = _uniform_field(10)
    for t in (0.0, 1.0, 3.0, 9.0):
        free = launch_state(order, params, t, anchor_m=100.0, pitch_m=8.029,
                            ring_length_m=5000.0)
        held = launch_state(order, params, t, anchor_m=100.0, pitch_m=8.029,
                            ring_length_m=5000.0, min_gap_m=CAR_LENGTH_M)
        assert free == held
    with pytest.raises(ValueError):
        launch_state(["A"], {"A": LaunchParams(0.5, 9.8, 33.3)}, 1.0, anchor_m=0.0,
                     pitch_m=8.0, ring_length_m=5000.0, min_gap_m=-1.0)


# ======================================================================================
# CONTRACT: the kinematics are internally consistent
# ======================================================================================

def test_distance_is_the_integral_of_the_speed_it_reports():
    p = LaunchParams(0.53, 9.78, 33.33)
    ts = np.arange(0.0, 8.0, 0.0005)
    v = np.array([launch_profile(float(t), p)[1] for t in ts])
    integrated = np.cumsum(v) * 0.0005
    reported = np.array([launch_profile(float(t), p)[0] for t in ts])
    assert np.max(np.abs(integrated - reported)) < 0.01


def test_the_phase_joins_are_continuous_in_both_speed_and_distance():
    p = LaunchParams(0.53, 9.78, 33.33)
    for join in (p.reactionS, p.reactionS + p.handoverSpeedMps / p.accelMps2):
        before = launch_profile(join - 1e-7, p)
        after = launch_profile(join + 1e-7, p)
        assert before[0] == pytest.approx(after[0], abs=1e-5)
        assert before[1] == pytest.approx(after[1], abs=1e-5)


def test_launch_time_loss_equals_a_direct_simulation_of_the_same_launch():
    """The closed form must agree with simply racing the two cars to the same point."""
    p = LaunchParams(0.53, 9.78, 33.33)
    target = 800.0
    ts = np.arange(0.0, 40.0, 0.0002)
    d = np.array([launch_profile(float(t), p)[0] for t in ts])
    t_launched = float(ts[int(np.argmax(d >= target))])
    t_cruised = target / p.handoverSpeedMps
    assert launch_time_loss_s(p) == pytest.approx(t_launched - t_cruised, abs=0.002)


def test_launch_state_is_pure():
    order, params = _uniform_field(6)
    a = launch_state(order, params, 2.0, anchor_m=50.0, pitch_m=8.0, ring_length_m=4000.0)
    b = launch_state(order, params, 2.0, anchor_m=50.0, pitch_m=8.0, ring_length_m=4000.0)
    assert a == b
    assert order == [f"C{i:02d}" for i in range(6)]
    assert params["C00"] == LaunchParams(0.53, 9.78, 33.33)


def test_slot_offset_and_lap_one_distance_agree_with_the_ring():
    ring = 5825.74
    for slot in (1, 2, 11, 22):
        off = slot_offset_m(116.9, 8.029, slot)
        assert lap1_distance_m(116.9, 8.029, slot, ring) + off == pytest.approx(ring)
        assert slot_station_m(116.9, 8.029, slot, ring) == pytest.approx(off % ring)
    # the back of the grid drives further on lap 1, by exactly the grid's own length
    assert (lap1_distance_m(116.9, 8.029, 22, ring)
            - lap1_distance_m(116.9, 8.029, 1, ring)) == pytest.approx(21 * 8.029)


def test_impossible_parameters_raise_instead_of_returning_a_plausible_number():
    with pytest.raises(ValueError):
        launch_profile(1.0, LaunchParams(0.5, 0.0, 33.3))
    with pytest.raises(ValueError):
        launch_profile(1.0, LaunchParams(0.5, 9.8, -1.0))
    with pytest.raises(ValueError):
        slot_offset_m(100.0, 8.0, 0)
    with pytest.raises(ValueError):
        launch_state(["A"], {"A": LaunchParams(0.5, 9.8, 33.3)}, 1.0,
                     anchor_m=0.0, pitch_m=8.0, ring_length_m=0.0)


# ======================================================================================
# CONTRACT: the estimators recover what they are told to recover
# ======================================================================================

def test_fit_recovers_a_launch_it_was_given_on_the_feeds_own_sample_grid():
    """The real feed samples at ~7.7 Hz on an irregular grid; recovery must survive it."""
    rng = np.random.default_rng(11)
    t = np.sort(rng.uniform(0.0, 6.0, 46))
    a, t0 = 9.83, 0.537
    v = np.clip(a * (t - t0), 0.0, None) * 3.6
    fit = fit_constant_acceleration(t, v)
    assert fit is not None
    assert fit["accelMps2"] == pytest.approx(a, rel=1e-3)
    assert fit["reactionS"] == pytest.approx(t0, abs=5e-3)
    assert fit["r2"] > 0.999


def test_fit_refuses_to_measure_a_brake_as_a_launch():
    """A car that launches, arrives at turn 1 and brakes must not contribute the brake."""
    t = np.arange(0.0, 12.0, 0.13)
    a, t0 = 9.8, 0.5
    v = np.clip(a * (t - t0), 0.0, 48.0)                      # rise then hold
    v = np.where(t > 7.0, np.clip(48.0 - 20.0 * (t - 7.0), 8.0, None), v)   # then brake
    fit = fit_constant_acceleration(t * 1.0, v * 3.6)
    assert fit is not None
    assert fit["accelMps2"] == pytest.approx(a, rel=0.02)
    assert fit["windowEndSpeedMps"] <= LAUNCH_FIT_CEILING_KPH / 3.6 + 1e-9


def test_fit_returns_none_rather_than_a_number_when_the_car_never_leaves():
    t = np.arange(0.0, 5.0, 0.12)
    assert fit_constant_acceleration(t, np.zeros_like(t)) is None
    assert fit_constant_acceleration(t[:3], np.zeros(3)) is None


def test_lattice_finds_a_grid_that_has_empty_boxes_in_it():
    """A pit-lane start leaves its BOX empty; F1 does not close the grid up.

    The chained-gap estimator has to guess the slot numbers from a pitch it does not yet
    know, and gets this wrong; the lattice does not have to guess at all.
    """
    rng = np.random.default_rng(3)
    slots = np.array([0, 1, 2, 4, 5, 6, 7, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20])
    a = 116.9 - 8.0 * slots + rng.normal(0.0, 0.9, slots.size)
    pitch, coherence = lattice_period(a)
    assert pitch == pytest.approx(8.0, abs=0.1)
    assert coherence > 3 * lattice_noise_floor(a.size)
    fit = fit_pitch(a)
    assert fit["pitchM"] == pytest.approx(8.0, abs=0.15)
    assert fit["emptySlots"] == 3


def test_lattice_coherence_sits_at_the_noise_floor_for_points_with_no_grid_in_them():
    rng = np.random.default_rng(5)
    a = np.sort(rng.uniform(0.0, 170.0, 20))[::-1]
    peak, coherence = lattice_period(a)
    assert coherence < PITCH_MIN_COHERENCE_RATIO * lattice_noise_floor(a.size) * 1.8


def test_pooled_lattice_beats_one_noisy_grid_that_locks_onto_the_wrong_period():
    """Exactly the Australian failure mode: one bad grid peaks at a period that is not
    the pitch, and pooling with grids that do resolve recovers the right one."""
    rng = np.random.default_rng(17)
    good = [116.9 - 8.0 * np.arange(21) + rng.normal(0, 0.9, 21) for _ in range(4)]
    bad = np.sort(rng.uniform(-60.0, 110.0, 20))[::-1]
    assert abs(lattice_period(bad)[0] - 8.0) > 0.5
    pooled = pooled_lattice_period(good + [bad])
    assert pooled["pitchM"] == pytest.approx(8.0, abs=0.08)
    assert pooled["meanCoherence"] > pooled["runnerUpCoherence"] * 1.5


def test_principal_axis_points_the_way_the_cars_are_going():
    pts = np.array([[i * 8.0, 0.2 * i] for i in range(20)])
    u = principal_axis(pts, travel_hint=np.array([-1.0, 0.0]))
    assert u[0] < 0
    u2 = principal_axis(pts, travel_hint=np.array([1.0, 0.0]))
    assert u2[0] > 0
    assert np.hypot(*u2) == pytest.approx(1.0)


def test_cluster_mean_reports_the_spread_of_races_not_of_correlated_cars():
    """Twenty cars in one race are one observation of a race start, not twenty."""
    values, clusters = [], []
    for c, level in enumerate([4.0, 10.0, 16.0]):
        values.extend([level] * 20)
        clusters.extend([f"race{c}"] * 20)
    m, se, n, k = cluster_mean(values, clusters)
    assert m == pytest.approx(10.0)
    assert n == 60 and k == 3
    assert se == pytest.approx(np.std([4.0, 10.0, 16.0], ddof=1) / math.sqrt(3))
    naive = np.std(values, ddof=1) / math.sqrt(60)
    assert se > naive * 2


def test_robust_location_ignores_a_left_tail_a_mean_would_swallow():
    v = [9.8] * 30 + [0.6, 1.1]
    med, sigma, se = robust_location_scale(v)
    assert med == pytest.approx(9.8)
    assert float(np.mean(v)) < 9.3


def test_regression_to_mean_recovers_a_slope_it_was_given():
    grid = np.arange(1, 21, dtype=float)
    end = 10.5 + 0.5 * (grid - 10.5)          # everybody moves halfway to the mean
    reg = regression_to_mean(grid, end)
    assert reg["slope"] == pytest.approx(0.5, abs=1e-9)
    assert reg["intercept"] == pytest.approx(0.0, abs=1e-9)


def test_assign_slots_leaves_a_hole_where_a_box_was_empty():
    a = np.array([100.0, 92.0, 76.0, 68.0])
    assert list(assign_slots(a, 8.0)) == [1, 2, 4, 5]


# ======================================================================================
# CONTRACT: lap 1 is charged ONCE
#
# The defect these pin: the artifact shipped a whole-GRID mean and a per-slot slope, and
# the engine used the mean as the SLOT-1 intercept and then added the full slope on top.
# Every car in every race therefore carried slope * (meanGridSlot - 1) seconds nobody
# measured. The second half of the same defect is geometric: a caller that starts cars
# from their own slot stations already drives the extra (k - 1) x pitch metres, so the
# geometric share of the slope is paid twice if the whole slope is added.
# ======================================================================================

def _grid_rows(intercept=6.0, slope=0.35, sessions=(0.0, 2.0, -1.5), n=20):
    """A synthetic field whose excess is EXACTLY intercept + slope*(k-1) + session effect."""
    return [{"cluster": f"S{i}", "gridRank": k,
             "excessS": intercept + slope * (k - 1) + off}
            for i, off in enumerate(sessions) for k in range(1, n + 1)]


def test_grid_slope_fit_recovers_the_intercept_and_slope_it_was_given():
    f = grid_slope_fit(_grid_rows(intercept=6.0, slope=0.35, sessions=(0.0, 2.0, -1.5)))
    assert f["slope"] == pytest.approx(0.35, abs=1e-9)
    assert f["meanGridSlot"] == pytest.approx(10.5, abs=1e-9)
    # the intercept is the n-weighted mean session effect, i.e. slot 1 at an average start
    assert f["intercept"] == pytest.approx(6.0 + (0.0 + 2.0 - 1.5) / 3.0, abs=1e-9)
    assert f["nClusters"] == 3


def test_the_field_mean_is_not_the_pole_value_and_the_gap_is_the_whole_defect():
    rows = _grid_rows(intercept=6.0, slope=0.35, n=20)
    f = grid_slope_fit(rows)
    mean = float(np.mean([r["excessS"] for r in rows]))
    slot1 = float(np.mean([r["excessS"] for r in rows if r["gridRank"] == 1]))
    # the intercept IS what the slot-1 cars actually did
    assert f["intercept"] == pytest.approx(slot1, abs=1e-9)
    # and the mean is that plus the slope carried to the middle of the grid: exactly the
    # seconds the old engine added to every car before adding the slope again
    assert mean - f["intercept"] == pytest.approx(0.35 * (10.5 - 1), abs=1e-9)
    assert mean - f["intercept"] > 3.0


def test_the_identity_the_intercept_is_built_from_holds_exactly():
    for sessions, n in (((0.0, 1.0), 18), ((0.0,), 22), ((-2.0, 0.5, 4.0, 1.0), 20)):
        f = grid_slope_fit(_grid_rows(sessions=sessions, n=n))
        assert f["level"] == pytest.approx(
            f["intercept"] + f["slope"] * (f["meanGridSlot"] - 1), abs=1e-9)


def test_a_line_fitted_on_one_sample_may_not_be_paired_with_another_sample_mean():
    """Why meanGridSlot has to ship WITH its own n: the identity is per row set."""
    full = grid_slope_fit(_grid_rows(n=20))
    front = grid_slope_fit(_grid_rows(n=10))      # only the front half finished lap 1
    assert front["meanGridSlot"] < full["meanGridSlot"]
    # same true line, different sample mean -- so the SAME intercept comes out
    assert front["intercept"] == pytest.approx(full["intercept"], abs=1e-9)
    # ...but pairing one sample's level with the other's mean rank does not
    wrong = front["level"] - full["slope"] * (full["meanGridSlot"] - 1)
    assert abs(wrong - front["intercept"]) > 0.5


def test_grid_slope_fit_refuses_a_sample_too_small_to_separate_a_level_from_a_slope():
    assert grid_slope_fit([]) is None
    assert grid_slope_fit(_grid_rows(sessions=(0.0,), n=4)) is None
    # a row with no grid rank cannot be placed on the line and is not counted
    unranked = [{"cluster": "S0", "gridRank": None, "excessS": 4.0}] * 40
    assert grid_slope_fit(unranked) is None


def test_delete_one_start_jackknife_is_zero_when_the_starts_agree():
    rows = _grid_rows(sessions=(0.0, 0.0, 0.0, 0.0))
    se, k = delete_one_cluster_se(
        rows, lambda rr: (grid_slope_fit(rr) or {}).get("intercept"))
    assert k == 4
    assert se == pytest.approx(0.0, abs=1e-9)


def test_delete_one_start_jackknife_grows_with_the_spread_between_starts():
    se, _ = delete_one_cluster_se(
        _grid_rows(sessions=(0.0, 3.0, -3.0, 6.0)),
        lambda rr: (grid_slope_fit(rr) or {}).get("intercept"))
    assert se > 1.0


def test_the_jackknife_refuses_rather_than_inventing_an_se_from_two_starts():
    se, k = delete_one_cluster_se(
        _grid_rows(sessions=(0.0, 1.0)),
        lambda rr: (grid_slope_fit(rr) or {}).get("intercept"))
    assert se is None and k == 2


def test_the_geometric_part_of_a_slot_is_pitch_over_the_speed_it_is_covered_at():
    assert geometric_seconds_per_slot(8.029, 76.0) == pytest.approx(8.029 / 76.0)
    # the lap AVERAGE speed is a different, lower denominator and gives a materially
    # bigger number; the two must not be swapped for one another
    assert (geometric_seconds_per_slot(8.029, 55.0)
            > 1.3 * geometric_seconds_per_slot(8.029, 76.0))
    for bad in ((0.0, 76.0), (8.029, 0.0), (-1.0, 76.0), (8.029, -5.0)):
        with pytest.raises(ValueError):
            geometric_seconds_per_slot(*bad)


def test_one_slot_of_extra_lap_one_distance_is_exactly_one_pitch_of_geometry():
    """Ties the geometric seconds back to this module's own lap-1 distance."""
    ring, pitch = 5825.74, 8.029
    d = [lap1_distance_m(116.9, pitch, k, ring) for k in range(1, 23)]
    assert np.allclose(np.diff(d), pitch)
    assert (d[1] - d[0]) / 76.0 == pytest.approx(
        geometric_seconds_per_slot(pitch, 76.0), abs=1e-12)


def test_a_field_launched_from_its_own_slots_already_drives_the_geometric_metres():
    """launch_state puts slot k (k-1) pitches behind pole, so it PAYS that distance. The
    whole slope may not then be added on top: only the part that is not this."""
    order, params = _uniform_field(22)
    pitch = 8.029
    state = launch_state(order, params, 0.0, anchor_m=116.9, pitch_m=pitch,
                         ring_length_m=5825.74)
    behind = [state[0]["progressM"] - c["progressM"] for c in state]
    assert behind[-1] == pytest.approx(21 * pitch, abs=1e-9)
    assert all(b == pytest.approx((i) * pitch, abs=1e-9) for i, b in enumerate(behind))


# ======================================================================================
# MEASURED: the 2026 feed itself
# ======================================================================================

pytestmark_data = pytest.mark.skipif(not DATA_ROOT.is_dir(),
                                     reason=f"no raw mirror at {DATA_ROOT}")


@pytestmark_data
def test_the_2026_grids_really_are_an_eight_metre_lattice():
    g = fitted()["grid"]["slotPitchMetres"]
    assert g["provenance"] == "DERIVED"
    assert 7.7 <= g["value"] <= 8.4, g
    assert g["se"] < 0.2
    assert g["n"] >= 150
    assert "coherence" in g["note"]


@pytestmark_data
def test_every_session_that_resolves_its_boxes_agrees_on_the_pitch():
    per = fitted()["grid"]["perSessionPitchMetres"]
    resolved = fitted()["grid"]["sessionsResolvingTheirBoxes"]["resolved"]
    assert len(resolved) >= 4
    vals = [per[name]["value"] for name in resolved]
    assert all(7.7 <= v <= 8.4 for v in vals), dict(zip(resolved, vals))
    # a session that does NOT resolve its boxes must say so with a null, not a number
    for name in fitted()["grid"]["sessionsResolvingTheirBoxes"]["unresolved"]:
        assert per[name]["value"] is None
        assert "UNAVAILABLE" in per[name]["note"]


@pytestmark_data
def test_the_anchor_is_a_per_circuit_measurement_not_one_constant():
    """Measured: British pole sits ~117 m PAST the timing line, Canadian ~32 m short of
    it. A single anchor for the calendar would be wrong by hundreds of metres."""
    per = fitted()["grid"]["anchorMetresPastTimingLine"]["perTrack"]
    vals = [v["value"] for v in per.values() if v["value"] is not None]
    assert len(vals) >= 8
    assert max(vals) - min(vals) > 100.0
    brit = per.get("British Grand Prix")
    if brit is not None:
        assert 100.0 <= brit["value"] <= 130.0
        assert brit["se"] < 10.0
    assert any(v < 0 for v in vals), "both signs occur in 2026; the sign is not cosmetic"


@pytestmark_data
def test_the_lateral_stagger_is_absent_from_the_feed_and_ships_as_null():
    stagger = fitted()["grid"]["lateralStagger"]
    assert stagger["offsetMetres"]["value"] is None
    assert stagger["offsetMetres"]["provenance"] == "RULE"
    assert stagger["columnSign"]["provenance"] == "RULE"
    assert "NOT IN THE FEED" in stagger["offsetMetres"]["note"]


@pytestmark_data
def test_launch_acceleration_and_reaction_are_measured_with_a_real_sample():
    lb = fitted()["launch"]
    a = lb["accelerationMps2"]
    r = lb["reactionSeconds"]
    assert 8.0 <= a["value"] <= 11.5, a
    assert a["n"] >= 150
    assert a["provenance"] == "DERIVED"
    assert 0.25 <= r["value"] <= 0.85, r
    assert r["n"] == a["n"]
    assert lb["populationSigma"]["accelerationMps2"]["value"] > a["se"] * 3, \
        "the car-to-car spread must be reported separately from the SE of the median"
    assert 0.0 < lb["populationSigma"]["reactionSeconds"]["value"] < 0.5


@pytestmark_data
def test_the_constant_acceleration_shape_was_measured_not_assumed():
    fit = fitted()["launch"]["modelFit"]
    assert fit["r2Median"] > 0.95, fit
    assert fit["r2P5"] > 0.80, fit
    assert fit["nSessions"] >= 8


@pytestmark_data
def test_a_session_whose_feed_never_stands_still_is_refused_and_named():
    """Measured: the Austrian Race's lap-1 telemetry opens with the field already at a
    median 45.8 kph, so there is no standing start in it to fit."""
    s = start_of("Austrian Grand Prix")
    assert s["standingStart"] is False
    assert s["grid"] is None
    assert any("does not start from rest" in r for r in s["refusals"]), s["refusals"]
    assert "Austrian Grand Prix" in fitted()["unavailable"]


@pytestmark_data
@pytest.mark.parametrize("event", ["Chinese Grand Prix", "Monaco Grand Prix"])
def test_a_sentinel_grid_is_refused_rather_than_emitted(event):
    """The feed writes one CONSTANT coordinate when it loses a car, and it covers the
    whole starting grid at these two rounds. No order may be emitted from it."""
    s = start_of(event)
    assert s["grid"] is None
    assert any("sentinel" in r for r in s["refusals"]), s["refusals"]
    assert event not in fitted()["grid"]["observedGridOrder"]["perSession"]


@pytestmark_data
def test_the_derived_grid_order_matches_the_published_track_model():
    """Cross-check against the OTHER producer of grid order (scripts/simdata/track.py,
    via its published artifact), which reaches the same answer through a ring. This
    module gets there with no ring at all, so agreement is real evidence."""
    index = REPO / "frontend" / "public" / "sim" / "index.json"
    if not index.exists():
        pytest.skip("no published track artifacts to cross-check against")
    try:
        pointer = json.loads(index.read_text(encoding="utf-8"))["latest"]
        manifest = json.loads((index.parent / pointer).read_text(encoding="utf-8"))
        track_file = manifest["tracks"]["british-grand-prix"]
        model = json.loads((index.parent / track_file).read_text(encoding="utf-8"))
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        pytest.skip(f"published artifacts unreadable: {exc}")
    published = model["grid"]["order"]
    mine = start_of("British Grand Prix")["grid"]["order"]
    assert mine == published, (mine, published)


@pytestmark_data
def test_lap_one_is_measurably_slower_than_a_clean_green_lap():
    l1 = fitted()["lap1"]["excessSecondsVsCleanLap"]
    assert 5.0 <= l1["value"] <= 14.0, l1
    assert l1["ci95"][0] > 0.0, "the penalty must be significantly different from zero"
    assert l1["n"] >= 120
    assert l1["provenance"] == "DERIVED"
    # neutralised starts really were withheld, not quietly pooled in
    assert fitted()["lap1"]["excluded"]["notGreen"] > 0


@pytestmark_data
def test_the_lap_one_penalty_grows_down_the_grid():
    slope = fitted()["lap1"]["excessSecondsPerGridSlot"]
    assert slope["value"] > 0.0
    assert slope["ci95"][0] > 0.0
    assert "GEOMETRY" in slope["note"], "the caller must be warned not to double count"


@pytestmark_data
def test_the_pole_intercept_ships_and_is_not_the_field_mean():
    """The BLOCKER: TypeScript needs a slot-1 value. Without one it used the whole-grid
    mean as the intercept and then added the slope per slot on top of it."""
    l1 = fitted()["lap1"]
    pole, mean = l1["excessSecondsAtPole"], l1["excessSecondsVsCleanLap"]
    assert pole["provenance"] == "DERIVED"
    assert pole["value"] is not None, pole
    assert 2.0 <= pole["value"] <= 9.0, pole
    assert pole["value"] < mean["value"], "the pole car cannot lose the field average"
    # the size of the defect, in seconds charged to every car in every generated race
    assert 2.0 <= mean["value"] - pole["value"] <= 5.0, (pole, mean)
    assert pole["n"] >= 100 and pole["se"] > 0.0
    assert pole["seMethod"].startswith("delete-one")
    assert "jackknife" in pole["note"]


@pytestmark_data
def test_the_pole_intercept_is_exactly_reconstructible_from_what_ships_beside_it():
    l1 = fitted()["lap1"]
    pole, slope, kbar = (l1["excessSecondsAtPole"], l1["excessSecondsPerGridSlot"],
                         l1["meanGridSlot"])
    # 2e-5 is the arithmetic of three values each rounded to 6 dp by leaf(), the slope's
    # rounding multiplied by kbar - 1; it is not slack for a different definition
    assert pole["value"] == pytest.approx(
        pole["levelSeconds"] - slope["value"] * (kbar["value"] - 1), abs=2e-5)
    # the level, the slope and the mean rank must all come from the SAME rows, or the
    # identity above is silently false
    assert pole["n"] == slope["n"] == kbar["n"]
    assert pole["levelSeconds"] != l1["excessSecondsVsCleanLap"]["value"]


@pytestmark_data
def test_the_intercept_se_is_neither_the_means_nor_the_slopes():
    """It is a function of BOTH, fitted on the same starts. Measured on this feed the
    jackknife lands above either input SE, which is the check that the propagation was
    done rather than one of the two being copied across."""
    l1 = fitted()["lap1"]
    pole, slope = l1["excessSecondsAtPole"], l1["excessSecondsPerGridSlot"]
    assert pole["se"] > l1["excessSecondsVsCleanLap"]["se"]
    assert pole["se"] > slope["se"]
    assert pole["ci95"][0] > 0.0, "the pole penalty is still significantly positive"


@pytestmark_data
def test_the_mean_grid_slot_ships_with_the_sample_the_slope_was_fitted_on():
    l1 = fitted()["lap1"]
    kbar = l1["meanGridSlot"]
    assert 8.0 <= kbar["value"] <= 13.0, kbar
    assert kbar["se"] is not None and 0.0 < kbar["se"] < 1.0, kbar
    assert kbar["n"] == l1["excessSecondsPerGridSlot"]["n"]
    assert kbar["provenance"] == "DERIVED"


@pytestmark_data
def test_the_fitted_line_reproduces_the_measured_excess_at_every_grid_slot():
    """intercept + slope * (k - 1) against what the feed actually did, slot by slot."""
    q = fitted()["lap1"]["gridSlopeFitQuality"]
    assert q["nSlots"] >= 15
    assert q["minRowsPerSlot"] == SLOT_MIN_ROWS
    assert q["slotMeanResidualRmsSeconds"] < 1.5, q
    assert q["slotMeanResidualMaxAbsSeconds"] < 3.0, q
    # the per-slot miss has to be small against the spread INSIDE one slot, or a straight
    # line is hiding structure the caller would inherit
    assert q["slotMeanResidualRmsSeconds"] < q["withinSlotSdSeconds"], q
    assert q["nSlotsAll"] >= q["nSlots"]


@pytestmark_data
def test_the_geometric_share_of_the_grid_slope_is_measured_and_already_subtracted():
    l1 = fitted()["lap1"]
    slope = l1["excessSecondsPerGridSlot"]["value"]
    geom = l1["geometricSecondsPerGridSlot"]["summary"]
    non = l1["nonGeometricSecondsPerGridSlot"]
    assert 0.0 < geom["value"] < slope, geom
    assert non["value"] == pytest.approx(slope - geom["value"], abs=1e-6)
    assert non["ci95"][0] > 0.0, "traffic is not all geometry"
    # the caller is handed the answer, not the arithmetic
    assert "nonGeometricSecondsPerGridSlot" in l1["excessSecondsPerGridSlot"]["note"]
    assert "GEOMETRY" in l1["excessSecondsPerGridSlot"]["note"]


@pytestmark_data
def test_the_geometric_share_is_a_per_circuit_fact_not_one_constant():
    block = fitted()["lap1"]["geometricSecondsPerGridSlot"]
    per = block["perTrack"]
    vals = [v["value"] for v in per.values() if v["value"] is not None]
    assert len(vals) >= 8
    # 8 m at 200-330 kph: nothing here may drift outside a physically possible band
    assert all(0.05 < v < 0.25 for v in vals), per
    assert max(vals) / min(vals) > 1.2, "one constant would be wrong by a fifth"
    # only the starts that actually contribute a green lap-1 row are pooled, and the ones
    # that do not say so rather than being averaged in silently
    pooled = [v for v in per.values() if "NOT pooled" not in (v.get("note") or "")]
    assert len(pooled) == block["summary"]["n"]
    assert 0 < block["summary"]["n"] < len(per)
    assert "END of lap 1" in block["definition"]


@pytestmark_data
def test_the_launch_loss_is_removed_at_pole_as_well_as_at_the_field_mean():
    l1 = fitted()["lap1"]
    split = l1["penaltySplit"]
    loss = split["launchLossSeconds"]["value"]
    at_pole = split["remainderSecondsAtPole"]
    assert at_pole["value"] == pytest.approx(
        l1["excessSecondsAtPole"]["value"] - loss, abs=1e-6)
    assert 0.0 < at_pole["value"] < split["remainderSeconds"]["value"]


@pytestmark_data
def test_lap_one_regresses_the_field_towards_the_mean():
    """Measured: the front of the grid loses places on lap 1 and the back gains them."""
    block = fitted()["lap1"]["placesGainedByGridSlot"]
    assert block["interceptPlaces"]["value"] == pytest.approx(0.0, abs=0.3)
    slope = block["slopePlacesPerGridSlot"]
    assert slope["value"] > 0.0
    assert slope["ci95"][0] > 0.0, slope
    buckets = block["buckets"]
    assert buckets["slots1to5"]["value"] < 0.0
    assert buckets["slot16andBack"]["value"] > 0.0
    assert buckets["slot16andBack"]["value"] > buckets["slots1to5"]["value"]
    assert all(b["n"] >= 20 for b in buckets.values())


@pytestmark_data
def test_a_real_lap_one_trace_feeds_the_model_that_replays_it():
    """End to end on one real start: fit the cars, run launch_state, and check it puts
    them where the feed says they are."""
    s = start_of("British Grand Prix")
    grid = s["grid"]
    params = {l["driver"]: LaunchParams(l["reactionS"], l["accelMps2"], 33.33)
              for l in s["launches"]}
    order = [d for d in grid["order"] if d in params]
    state = launch_state(order, params, 0.0, anchor_m=grid["anchorM"],
                         pitch_m=fitted()["grid"]["slotPitchMetres"]["value"],
                         ring_length_m=5825.74)
    # modelled box positions against the measured ones, front of the grid first
    measured = [o for d, o in zip(grid["order"], grid["offsetsM"]) if d in params]
    modelled = [slot_offset_m(grid["anchorM"],
                              fitted()["grid"]["slotPitchMetres"]["value"], i + 1)
                for i in range(len(order))]
    err = np.abs(np.array(measured) - np.array(modelled))
    assert float(np.median(err)) < 3.0, list(zip(order, measured, modelled))
    gaps = [state[i]["progressM"] - state[i + 1]["progressM"]
            for i in range(len(state) - 1)]
    assert min(gaps) > CAR_LENGTH_M


# ======================================================================================
# MEASURED: the emitted block is honest
# ======================================================================================

def _walk_leaves(node, path=""):
    if isinstance(node, dict):
        if "provenance" in node:
            yield path, node
        for k, v in node.items():
            yield from _walk_leaves(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_leaves(v, f"{path}[{i}]")


@pytestmark_data
def test_every_leaf_carries_a_provenance_from_the_exact_six_word_vocabulary():
    leaves = list(_walk_leaves(fitted()))
    assert len(leaves) > 30
    for path, node in leaves:
        assert node["provenance"] in PROVENANCE, (path, node["provenance"])


@pytestmark_data
def test_no_number_reaches_the_artifact_without_a_provenance_above_it():
    """A bare float anywhere in the block is a value with no story, which is what the
    contract forbids. Every numeric leaf must sit inside a dict that carries one."""
    def check(node, path, covered):
        if isinstance(node, dict):
            covered = covered or "provenance" in node
            for k, v in node.items():
                check(v, f"{path}.{k}", covered)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                check(v, f"{path}[{i}]", covered)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            assert covered, f"bare number at {path}: {node}"
    check(fitted(), "standingStart", False)


@pytestmark_data
def test_every_value_that_is_present_is_finite_and_serialisable():
    payload = json.dumps(fitted(), allow_nan=False)       # raises on NaN / Infinity
    assert len(payload) > 2000
    for path, node in _walk_leaves(fitted()):
        v = node.get("value")
        if isinstance(v, float):
            assert math.isfinite(v), path


@pytestmark_data
def test_what_the_data_could_not_support_is_named_rather_than_defaulted():
    block = fitted()
    assert block["unavailable"], "at least three 2026 sessions cannot support a grid"
    for name, reasons in block["unavailable"].items():
        assert reasons and all(isinstance(r, str) and r for r in reasons), name
    cov = block["coverage"]
    assert (cov["sessionsScanned"] >= cov["standingStartsInFeed"]
            >= cov["sessionsWithUsableGrid"])
    assert cov["sessionsWithUsableGrid"] >= 8


@pytestmark_data
def test_fit_params_ships_the_standing_start_block():
    """The block has to reach the artifact, in fit_params' own leaf shape."""
    from simdata.fit_params import build_params
    params = build_params()
    assert "standingStart" in params
    block = params["standingStart"]
    assert block["grid"]["slotPitchMetres"]["provenance"] == "DERIVED"
    assert block["launch"]["accelerationMps2"]["n"] >= 150
    json.dumps(params, allow_nan=False)


@pytestmark_data
def test_the_scan_covers_every_race_in_the_feed():
    pairs = race_sessions()
    assert len(pairs) >= 13
    assert all((DATA_ROOT / e / s / "session_laptimes.json").exists() for e, s in pairs)
    assert ("British Grand Prix", "Race") in pairs

@pytestmark_data
def test_the_per_driver_launch_effect_is_reported_against_its_own_noise():
    """Measured: the raw between-driver SD of launch acceleration is about the same size
    as the SE of each driver's own estimate, so most of the car-to-car spread is the same
    driver varying from start to start. The block has to say that, or a caller reads
    perDriver as the whole story."""
    sig = fitted()["launch"]["perDriverSignal"]
    for key in ("accelerationMps2", "reactionSeconds"):
        s = sig[key]
        assert s["nDrivers"] >= 10
        assert 0.0 <= s["betweenDriverSdCorrected"] <= s["betweenDriverSd"]
        assert s["betweenDriverSdCorrected"] < s["populationSd"], key
