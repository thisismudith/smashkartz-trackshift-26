"""CP-18 energy twin, CP-20 calibration, CP-21 transition, CP-22 uncertainty.

The gates these checkpoints pin: units and signs, causality, Energy Store and
budget accounting kept separate, envelope violations recorded and never clamped,
physical constraints on the fit, a_k positive and varying with geometry, and
interval coverage near nominal.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.twin.api import (  # noqa: E402
    DRAWS,
    EnergyState,
    PARAMETER_BOUNDS,
    PhysicsParameters,
    RungResult,
    SegmentResponse,
    SegmentTimeError,
    TwinError,
    advance_energy_state,
    check_monotonic_in_energy,
    check_physical_constraints,
    check_sensitivity_by_segment_type,
    compare_rungs,
    coverage,
    draw_parameters,
    drag_power_kw,
    error_by_group,
    extrapolation_sanity,
    gradient_power_kw,
    inertial_power_kw,
    integrate_segment,
    interval,
    kmh_to_mps,
    mean_absolute_error,
    propagate,
    residual_share,
    segment_power,
    segment_time_s,
    trailing_mean,
)

PARAMS = PhysicsParameters(
    mass_kg=830.0, cda_m2=1.2, crr=0.012, eta_drivetrain=0.92,
    p_ice_max_kw=400.0, eta_deploy=0.95, eta_harvest=0.90,
)
RHO = 1.225


# --------------------------------------------------------------- CP-18 units

def test_speed_converts_once_at_the_boundary():
    """The documented order-of-magnitude failure is km/h where m/s is wanted."""
    assert kmh_to_mps(360.0) == pytest.approx(100.0)
    assert kmh_to_mps(0.0) == 0.0
    assert kmh_to_mps(300.0) == pytest.approx(83.3333, abs=1e-3)


def test_drag_scales_with_the_cube_of_air_speed():
    base = drag_power_kw(50.0, RHO, 1.2)
    assert drag_power_kw(100.0, RHO, 1.2) == pytest.approx(base * 8, rel=1e-9)


def test_headwind_increases_drag_and_tailwind_reduces_it():
    still = segment_power(300.0, 0.0, PARAMS, rho_kgm3=RHO)
    head = segment_power(300.0, 0.0, PARAMS, rho_kgm3=RHO, wind_head_component_mps=5.0)
    tail = segment_power(300.0, 0.0, PARAMS, rho_kgm3=RHO, wind_head_component_mps=-5.0)
    assert head.p_drag_kw > still.p_drag_kw > tail.p_drag_kw


def test_thinner_air_reduces_drag():
    """A hot day at altitude is materially less dense, straight onto the cube term."""
    dense = segment_power(300.0, 0.0, PARAMS, rho_kgm3=1.225)
    thin = segment_power(300.0, 0.0, PARAMS, rho_kgm3=1.05)
    assert thin.p_drag_kw < dense.p_drag_kw


def test_missing_weather_uses_a_flagged_fallback_not_a_silent_one():
    result = segment_power(300.0, 0.0, PARAMS, rho_kgm3=None)
    assert result.air_density_is_fallback is True
    assert segment_power(300.0, 0.0, PARAMS, rho_kgm3=RHO).air_density_is_fallback is False


# --------------------------------------------------------------- CP-18 signs

def test_sign_conventions():
    """Positive acc_x under acceleration; positive gradient uphill."""
    assert inertial_power_kw(80.0, 3.0, 830.0) > 0
    assert inertial_power_kw(80.0, -3.0, 830.0) < 0
    assert gradient_power_kw(80.0, 830.0, 0.05, 9.80665) > 0
    assert gradient_power_kw(80.0, 830.0, -0.05, 9.80665) < 0
    assert gradient_power_kw(80.0, 830.0, 0.0, 9.80665) == pytest.approx(0.0)


def test_braking_produces_harvest_not_negative_deployment():
    """Real F1 braking is -30 m/s2 or harder.

    A gentle -4 m/s2 at 300 km/h is a lift, not a stop: drag alone is over
    400 kW there and still exceeds the demand, so the balance correctly reports
    no recovery. Testing harvest at that deceleration would be testing a
    coast.
    """
    braking = segment_power(300.0, -30.0, PARAMS, rho_kgm3=RHO)
    assert braking.ers_deploy_power_est_kw == 0.0
    assert braking.ers_harvest_power_est_kw > 0.0

    coasting = segment_power(300.0, -4.0, PARAMS, rho_kgm3=RHO)
    assert coasting.ers_harvest_power_est_kw == 0.0, (
        "at 300 km/h a -4 m/s2 lift is overcome by drag; reporting recovery here "
        "would mean the drag term is being under-counted"
    )


def test_harvest_magnitude_is_bounded():
    """Recovery is bounded: it cannot exceed the wheel power being absorbed."""
    braking = segment_power(250.0, -35.0, PARAMS, rho_kgm3=RHO)
    assert 0 < braking.ers_harvest_power_est_kw <= abs(braking.p_wheel_kw)


def test_wheel_power_on_a_straight_is_physically_plausible():
    """Roughly 700-1,000 kW at full deployment on a long straight."""
    flat_out = segment_power(320.0, 1.5, PARAMS, rho_kgm3=RHO)
    assert 500.0 < flat_out.p_wheel_kw < 1200.0, flat_out.p_wheel_kw


def test_everything_is_tagged_simulated():
    assert segment_power(200.0, 0.0, PARAMS, rho_kgm3=RHO).provenance == "SIMULATED"
    assert EnergyState(ers_soc_est_mj=1.0).provenance == "SIMULATED"


# ------------------------------------------------------- CP-18 envelope (28.1)

def test_envelope_violation_is_recorded_and_the_estimate_is_not_clamped():
    """Section 28.1. Clipping converts a visible calibration bug into an invisible one."""
    result = segment_power(340.0, 5.0, PARAMS, rho_kgm3=RHO, envelope_cap_kw=50.0)
    assert result.envelope_violation is True
    assert result.violation_margin_kw > 0
    # The raw estimate survives untouched -- that is the whole point.
    assert result.ers_deploy_power_est_kw > 50.0


def test_no_violation_recorded_when_under_the_cap():
    result = segment_power(200.0, 0.0, PARAMS, rho_kgm3=RHO, envelope_cap_kw=350.0)
    assert result.envelope_violation is False
    assert result.violation_margin_kw <= 0


def test_deployment_is_not_flat_along_a_straight():
    """Flat output means a constant is being read instead of the CP-11 evaluator."""
    powers = {segment_power(v, 1.0, PARAMS, rho_kgm3=RHO).ers_deploy_power_est_kw
              for v in (250.0, 280.0, 310.0, 340.0)}
    assert len(powers) > 1


# ------------------------------------------------------- CP-18 energy store

def test_store_deploy_and_harvest_budgets_stay_separate():
    """A car can be out of deploy budget with a full store, or the reverse."""
    state = EnergyState(ers_soc_est_mj=4.0, ers_deploy_budget_remaining_est_mj=2.0,
                        ers_harvest_budget_remaining_est_mj=2.0)
    after = advance_energy_state(state, deploy_mj=0.5, harvest_mj=0.2, parameters=PARAMS)
    assert after.ers_deploy_budget_remaining_est_mj == pytest.approx(1.5)
    assert after.ers_harvest_budget_remaining_est_mj == pytest.approx(1.8)
    assert after.ers_energy_used_est_mj == pytest.approx(0.5)
    assert after.ers_energy_harvested_est_mj == pytest.approx(0.2)
    # The store moved by neither raw figure: both efficiencies applied.
    assert after.ers_soc_est_mj < 4.0 + 0.2 - 0.5


def test_round_trip_is_lossy_in_both_directions():
    state = EnergyState(ers_soc_est_mj=2.0)
    out = advance_energy_state(state, deploy_mj=1.0, harvest_mj=0.0, parameters=PARAMS)
    back = advance_energy_state(out, deploy_mj=0.0, harvest_mj=1.0, parameters=PARAMS)
    assert back.ers_soc_est_mj < 2.0, "a lossless round trip means one efficiency is missing"


def test_store_clipping_is_recorded_not_silent():
    empty = advance_energy_state(EnergyState(ers_soc_est_mj=0.1), 5.0, 0.0, PARAMS)
    assert empty.ers_soc_est_mj == 0.0 and empty.soc_clipped is True
    full = advance_energy_state(EnergyState(ers_soc_est_mj=3.9), 0.0, 2.0, PARAMS,
                                store_capacity_mj=4.0)
    assert full.ers_soc_est_mj == pytest.approx(4.0) and full.soc_clipped is True


def test_energy_integration_units():
    assert integrate_segment(1000.0, 1.0) == pytest.approx(1.0)  # 1000 kW for 1 s = 1 MJ


def test_impossible_parameters_are_refused():
    with pytest.raises(TwinError):
        PhysicsParameters(mass_kg=830, cda_m2=-1.0, crr=0.012, eta_drivetrain=0.92, p_ice_max_kw=400)
    with pytest.raises(TwinError, match="energy from nowhere"):
        PhysicsParameters(mass_kg=830, cda_m2=1.2, crr=0.012, eta_drivetrain=1.4, p_ice_max_kw=400)


# ----------------------------------------------------------- CP-18 causality

def test_estimate_at_a_distance_is_unchanged_by_later_data():
    """Truncating the lap at d gives the identical estimate at d."""
    full = [segment_power(v, 0.5, PARAMS, rho_kgm3=RHO) for v in (200.0, 250.0, 300.0, 320.0)]
    truncated = [segment_power(v, 0.5, PARAMS, rho_kgm3=RHO) for v in (200.0, 250.0)]
    for early, late in zip(truncated, full):
        assert early.ers_deploy_power_est_kw == late.ers_deploy_power_est_kw


def test_smoothing_is_trailing_only():
    """A centred window reads the future into a live feature (section 12)."""
    assert trailing_mean([1.0, 2.0, 3.0, 10.0], window=3) == pytest.approx(5.0)
    assert trailing_mean([1.0, 2.0, 3.0], window=1) == pytest.approx(3.0)
    assert trailing_mean([], window=3) is None


# --------------------------------------------------------------- CP-20 rungs

def test_parameters_at_a_bound_are_flagged_separately_from_violations():
    at_bound, violations = check_physical_constraints(
        {"cda_m2": PARAMETER_BOUNDS["cda_m2"][1], "crr": 0.012,
         "eta_drivetrain": 0.92, "p_ice_max_kw": 400.0}
    )
    assert at_bound and not violations
    assert "missing physics" in at_bound[0]


def test_outright_violations_are_reported():
    _, violations = check_physical_constraints({"cda_m2": 5.0, "eta_drivetrain": 1.2})
    assert len(violations) >= 2


def test_a_rung_that_does_not_improve_is_rejected():
    results = compare_rungs([
        RungResult("global", {}, mae_s=0.14, rmse_s=0.2, n_segments=1000),
        RungResult("event", {}, mae_s=0.20, rmse_s=0.3, n_segments=1000),
    ])
    assert results[0].accepted is True
    assert results[1].accepted is False
    assert "does not improve" in results[1].rejection_reason


def test_a_rung_at_a_bound_is_rejected_however_good_its_score():
    results = compare_rungs([
        RungResult("global", {}, mae_s=0.01, rmse_s=0.02, n_segments=1000,
                   at_bound=["cda_m2=1.8 at a bound"]),
    ])
    assert results[0].accepted is False
    assert "bound" in results[0].rejection_reason


def test_a_residual_model_doing_too_much_work_is_rejected():
    results = compare_rungs([
        RungResult("physics_residual", {}, mae_s=0.02, rmse_s=0.03,
                   n_segments=1000, residual_share=0.55),
    ])
    assert results[0].accepted is False
    assert "too little work" in results[0].rejection_reason


def test_residual_share_and_grouped_error():
    assert residual_share([10.0, 10.0], [0.0, 0.0]) == 0.0
    assert residual_share([5.0, 5.0], [5.0, 5.0]) == pytest.approx(0.5)
    grouped = error_by_group([1.0, 2.0, 3.0], [1.1, 2.2, 2.7], ["STRAIGHT", "CORNER", "CORNER"])
    assert set(grouped) == {"STRAIGHT", "CORNER"}
    assert grouped["CORNER"]["n"] == 2
    assert mean_absolute_error([1.0, 2.0], [1.5, 2.5]) == pytest.approx(0.5)


# -------------------------------------------------------- CP-21 transition

STRAIGHT = SegmentResponse("s1", t_base_s=8.0, a_k_s_per_mj=0.45, c_k_s_per_unit_lift=0.6, kind="STRAIGHT")
CORNER = SegmentResponse("c1", t_base_s=4.0, a_k_s_per_mj=0.05, c_k_s_per_unit_lift=0.9, kind="CORNER")


def test_deploying_energy_never_makes_a_segment_slower():
    with pytest.raises(SegmentTimeError, match="not positive"):
        SegmentResponse("bad", t_base_s=8.0, a_k_s_per_mj=-0.1, c_k_s_per_unit_lift=0.5)


def test_lifting_never_saves_time():
    with pytest.raises(SegmentTimeError, match="lifting saves time"):
        SegmentResponse("bad", t_base_s=8.0, a_k_s_per_mj=0.4, c_k_s_per_unit_lift=-0.2)


def test_time_is_strictly_monotone_in_energy_across_a_dense_sweep():
    """Checked densely: a tree can be monotone at the ends and fold in the middle."""
    result = check_monotonic_in_energy(STRAIGHT, energy_range_mj=(0.0, 4.0), samples=400)
    assert result["monotonic"] is True
    assert result["time_at_max_energy_s"] < result["time_at_min_energy_s"]


def test_energy_sensitivity_is_higher_on_straights_than_in_slow_corners():
    """A flat profile means an average was learned, not the geometry."""
    summary = check_sensitivity_by_segment_type([STRAIGHT, CORNER])
    assert summary["straight_exceeds_corner"] is True
    assert summary["flat"] is False


def test_extrapolation_does_not_invert_time():
    sane = extrapolation_sanity(CORNER, observed_max_mj=2.0)
    assert sane["goes_negative"] is False
    reckless = extrapolation_sanity(STRAIGHT, observed_max_mj=2.0, probe_multiple=20.0)
    assert reckless["goes_negative"] is True
    assert reckless["safe_energy_ceiling_mj"] == pytest.approx(8.0 / 0.45)


def test_lift_costs_time():
    assert segment_time_s(STRAIGHT, 1.0, 0.5) > segment_time_s(STRAIGHT, 1.0, 0.0)


# ------------------------------------------------------- CP-22 uncertainty

def test_draws_respect_physical_bounds():
    import numpy as np

    covariance = np.diag([0.5, 0.5])
    bounds = [(0.6, 1.8), (0.005, 0.025)]
    sample = draw_parameters([1.2, 0.012], covariance, draws=DRAWS, bounds=bounds, seed=1)
    assert sample.shape == (DRAWS, 2)
    assert (sample[:, 0] >= 0.6).all() and (sample[:, 0] <= 1.8).all()
    assert (sample[:, 1] > 0).all(), "no draw may produce negative drag or rolling resistance"


def test_interval_widens_with_model_misspecification():
    values = [1.0, 1.1, 0.9, 1.05, 0.95] * 40
    tight = interval(values)
    wide = interval(values, extra_variance=0.25)
    assert wide["spread"] > tight["spread"]
    assert wide["low"] < tight["low"] and wide["high"] > tight["high"]


def test_coverage_reports_the_share_inside_the_interval():
    bands = [{"low": 0.0, "high": 2.0, "spread": 2.0} for _ in range(10)]
    observed = [1.0] * 8 + [5.0, -5.0]
    result = coverage(observed, bands)
    assert result["coverage"] == pytest.approx(0.8)
    assert result["n"] == 10 and result["covered"] == 8


def test_propagation_preserves_one_coherent_parameter_set_per_draw():
    import numpy as np

    draws = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert propagate(draws, lambda p: p[0] + p[1]) == [3.0, 7.0]


def test_ice_power_reported_is_what_was_used_not_the_maximum():
    """CP-19 integrates this to get fuel burned.

    Reporting the engine's maximum on a part-throttle segment would burn fuel
    the car never used, and the fuel curve would run dry before the flag.
    """
    cruising = segment_power(120.0, 0.0, PARAMS, rho_kgm3=RHO)
    assert 0 < cruising.p_ice_est_kw < PARAMS.p_ice_max_kw

    flat_out = segment_power(330.0, 3.0, PARAMS, rho_kgm3=RHO)
    assert flat_out.p_ice_est_kw == pytest.approx(PARAMS.p_ice_max_kw)

    braking = segment_power(300.0, -30.0, PARAMS, rho_kgm3=RHO)
    assert braking.p_ice_est_kw == 0.0, "a braking car is not burning fuel for propulsion"


def test_deployment_is_the_shortfall_after_the_engine():
    result = segment_power(330.0, 3.0, PARAMS, rho_kgm3=RHO)
    demand = result.p_wheel_kw / PARAMS.eta_drivetrain
    assert result.p_ice_est_kw + result.ers_deploy_power_est_kw == pytest.approx(demand)
