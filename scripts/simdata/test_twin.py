"""Physics and contract tests for the energy twin (MODELS.md M14).

Pure functions, so no fixtures needed. Three groups:

  1. the original force-balance physics tests (unchanged behaviour, kept passing),
  2. the three separate section 28 energy quantities,
  3. the section 28.1 rule that the envelope is a DIAGNOSTIC and NEVER a clamp.
"""
import dataclasses
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from simdata import rules as R
from simdata.twin import (
    OVERRIDE_EVIDENCE_K_SIGMA,
    TwinParams,
    air_density,
    electrical_split_kw,
    envelope_diagnostics,
    estimate_ers,
    force_split,
    ice_availability_fraction,
    ice_power_kw,
    integrate_store,
    kinematics,
    lap_summary,
    wheel_power_kw,
)
import simdata.twin as twin_mod

P = TwinParams()
RULES = R.default_event_rules()
CAPACITY = RULES.ers_store_capacity.value_mj
DEPLOY_BUDGET = RULES.deploy_budget.value_mj
HARVEST_BUDGET = RULES.harvest_budget.value_mj


# ===========================================================================
# 1. Force balance / kinematics  (the original physics suite)
# ===========================================================================
def test_drag_rises_with_square_of_speed():
    f1 = force_split([50], [0], [0], P)["drag_n"][0]
    f2 = force_split([100], [0], [0], P)["drag_n"][0]
    assert abs(f2 / f1 - 4.0) < 1e-6, "drag must be quadratic in speed"


def test_gradient_force_sign():
    up = force_split([50], [0], [0.1], P)["gradient_n"][0]
    down = force_split([50], [0], [-0.1], P)["gradient_n"][0]
    assert up > 0 > down, "uphill resists, downhill assists"


def test_coasting_flat_needs_negative_power_to_hold_speed():
    # no acceleration on the flat: traction power must be positive (overcoming drag)
    f = force_split([80], [0], [0], P)
    assert wheel_power_kw(f, [80])[0] > 0


def test_braking_gives_negative_wheel_power():
    f = force_split([80], [-15], [0], P)
    assert wheel_power_kw(f, [80])[0] < 0


def test_air_density_falls_as_temperature_rises():
    cold = air_density(5, 1013.0)
    hot = air_density(40, 1013.0)
    assert cold > hot > 1.0
    assert air_density(None, None) == 1.2  # documented fallback


def test_wheel_power_is_the_documented_sum_of_terms():
    """P_wheel = m*a*v + P_drag + P_rolling + P_gradient, exactly (Math.md 11)."""
    v, a, th = 70.0, 3.0, 0.02
    f = force_split([v], [a], [th], P)
    total = wheel_power_kw(f, [v])[0]
    parts = (P.mass_kg * a * v
             + f["drag_n"][0] * v
             + f["rolling_n"][0] * v
             + f["gradient_n"][0] * v) / 1000.0
    assert abs(total - parts) < 1e-9


def test_kinematics_uses_a_time_window_not_adjacent_samples():
    """A 7 ms sample gap must not manufacture a huge acceleration."""
    t = np.array([0.0, 0.5, 0.507, 1.0, 1.5, 2.0])
    v = np.array([100.0, 120.0, 121.0, 140.0, 160.0, 180.0])  # km/h
    d = np.cumsum(np.r_[0.0, np.diff(t)] * v / 3.6)
    kin = kinematics(t, v, d, np.zeros_like(t))
    assert np.all(np.abs(kin["accel_mps2"]) < 40.0)


# ===========================================================================
# 2. The ICE effective power map (section 28: "ICE effective power map")
# ===========================================================================
def test_ice_map_is_not_a_single_constant():
    """A single ice_peak_kw was the documented cause of the old imbalance."""
    frac = ice_availability_fraction([5000.0, 8000.0, 11000.0, 12000.0, 13000.0], P)
    assert frac[0] < frac[1] < frac[2] < frac[3]
    assert frac[3] == pytest.approx(1.0)
    assert frac[4] == pytest.approx(1.0), "flat at and above the rated speed"
    assert len(set(np.round(frac, 6))) > 1, "the map must vary with engine speed"


def test_ice_map_is_zero_below_idle_and_never_exceeds_rated():
    frac = ice_availability_fraction([0.0, P.ice_idle_rpm - 1.0, 20000.0, np.nan], P)
    assert frac[0] == 0.0 and frac[1] == 0.0
    assert frac[2] == pytest.approx(1.0)
    assert frac[3] == 0.0, "a non-finite rpm must not produce a plausible number"


def test_ice_power_scales_with_throttle_and_is_cut_under_braking():
    rpm = [12000.0, 12000.0, 12000.0]
    p_full, _, basis = ice_power_kw([100.0, 50.0, 100.0], [0, 0, 1], P, rpm=rpm)
    assert basis == "rpm"
    assert p_full[0] == pytest.approx(P.ice_rated_power_kw)
    assert p_full[1] == pytest.approx(0.5 * P.ice_rated_power_kw)
    assert p_full[2] == 0.0, "fuel cut while braking"


def test_missing_rpm_degrades_the_map_and_is_surfaced_not_hidden():
    _, frac, basis = ice_power_kw([100.0], [0], P, rpm=None)
    assert basis == "throttle_only"
    assert frac[0] == 1.0
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, event_rules=RULES)  # no rpm
    assert est["status"]["ice_map_basis"] == "throttle_only"
    assert any("rpm" in w for w in est["status"]["warnings"]), \
        "a degraded model must announce itself"


# ===========================================================================
# 3. The electrical split takes BOTH signs of the residual
# ===========================================================================
def test_split_takes_both_signs_of_the_residual():
    """Math.md 11 has no max() in it. The old twin took only the positive branch."""
    machine = R.max_electrical_power_kw(0.0, R.MODE_OVERRIDE, RULES)
    s = electrical_split_kw([600.0, 200.0], [300.0, 300.0], [0, 0], P, machine)
    assert s["deploy_kw"][0] > 0 and s["harvest_ice_kw"][0] == 0.0
    assert s["deploy_kw"][1] == 0.0 and s["harvest_ice_kw"][1] > 0.0, \
        "ICE surplus while on power must charge the store, not vanish"


def test_brake_harvest_is_limited_by_the_machine_and_the_spill_is_reported():
    machine = R.max_electrical_power_kw(0.0, R.MODE_OVERRIDE, RULES)
    s = electrical_split_kw([-3000.0], [0.0], [1], P, machine)
    assert s["harvest_brake_kw"][0] == pytest.approx(machine)
    assert s["harvest_brake_available_kw"][0] > machine
    assert s["harvest_brake_spill_kw"][0] > 0, "the unusable part must stay visible"


def test_only_the_rear_axle_braking_power_reaches_the_mgu_k():
    machine = 1e9  # take the machine limit out of the picture
    s = electrical_split_kw([-100.0], [0.0], [1], P, machine)
    expected = 100.0 * P.rear_brake_share * P.drivetrain_eff
    assert s["harvest_brake_kw"][0] == pytest.approx(expected)


# ===========================================================================
# 4. Synthetic laps
# ===========================================================================
def _synthetic_lap(n=400, lap_s=90.0):
    t = np.linspace(0, lap_s, n)
    v = 200 + 100 * np.sin(2 * np.pi * t / lap_s)      # km/h, accelerating and braking
    d = np.cumsum(np.r_[0, np.diff(t)] * v / 3.6)
    z = np.zeros(n)
    accel = np.gradient(v / 3.6) / np.gradient(t)
    brake = (accel < -2).astype(float)
    thr = np.where(accel > 0, 100.0, 0.0)
    rpm = 3000.0 + 30.0 * v                            # plausible gear-agnostic proxy
    return t, v, d, z, brake, thr, rpm


def _violent_lap(n=120):
    """A lap the twin cannot possibly explain: 100 -> 320 km/h in 3 s, flat out."""
    t = np.linspace(0, 3.0, n)
    v = np.linspace(100.0, 320.0, n)
    d = np.cumsum(np.r_[0, np.diff(t)] * v / 3.6)
    z = np.zeros(n)
    brake = np.zeros(n)
    thr = np.full(n, 100.0)
    rpm = np.full(n, 12000.0)
    return t, v, d, z, brake, thr, rpm


def test_estimate_is_energy_consistent_and_bounded():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, air_temp_c=25, pressure_hpa=1010,
                       rpm=rpm, event_rules=RULES)
    soc = est["ers_soc_est_mj"]
    assert (soc >= 0).all() and (soc <= CAPACITY).all(), "store must stay within capacity"
    assert est["ers_energy_harvested_est_mj"] >= 0
    assert est["ers_energy_used_est_mj"] >= 0
    assert (est["ers_deploy_power_est_kw"] >= 0).all()
    assert (est["ers_harvest_power_est_kw"] >= 0).all()


def test_braking_harvest_only_happens_under_braking():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    off = brake < 0.5
    assert not np.any(est["ers_harvest_brake_power_est_kw"][off] > 0)
    assert not np.any(est["ers_deploy_power_est_kw"][brake > 0.5] > 0), \
        "no deployment while the driver is on the brakes"


def test_every_ers_field_is_tagged_not_observed():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    prov = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)["provenance"]
    for k, tag in prov.items():
        if k.startswith("ers_") or k.startswith("ice_"):
            assert tag in ("INFERRED", "SIMULATED"), f"{k} must never claim to be measured"
    assert prov["cap_normal_kw"] == "RULE"


def test_lap_summary_shape():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    s = lap_summary(estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES))
    assert s["peakWheelPowerKw"] > 0 > s["peakBrakingKw"]
    assert "socUncertaintyMj" in s
    for key in ("deployBudgetRemainingMj", "harvestBudgetRemainingMj", "socEndMj",
                "storeDeficitMj", "envelopeCapViolations", "iceMapBasis"):
        assert key in s


def test_pure_no_input_mutation():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    before = (v.copy(), thr.copy(), rpm.copy(), brake.copy())
    estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    for got, want in zip((v, thr, rpm, brake), before):
        assert np.array_equal(got, want), "inputs must not be mutated"


def test_same_inputs_give_identical_outputs():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    a = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    b = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    assert np.allclose(a["ers_deploy_power_est_kw"], b["ers_deploy_power_est_kw"])
    assert a["ers_energy_used_est_mj"] == b["ers_energy_used_est_mj"]


# ===========================================================================
# 5. THREE SEPARATE QUANTITIES (AGENTS.md section 28)
# ===========================================================================
def test_the_three_quantities_are_separate_fields_with_separate_windows():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    for key in ("ers_soc_est_mj", "ers_deploy_budget_remaining_est_mj",
                "ers_harvest_budget_remaining_est_mj"):
        assert isinstance(est[key], np.ndarray)
    rl = est["rules"]
    assert rl["ers_store_capacity_mj"] != rl["deploy_budget_mj"] != rl["harvest_budget_mj"]
    assert rl["ers_store_capacity_window"] == "instantaneous_store"
    assert rl["deploy_budget_window"] == "lap"
    assert rl["harvest_budget_window"] == "lap"


def test_budgets_start_full_and_count_down_monotonically():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    dep = est["ers_deploy_budget_remaining_est_mj"]
    har = est["ers_harvest_budget_remaining_est_mj"]
    assert dep[0] <= DEPLOY_BUDGET and har[0] <= HARVEST_BUDGET
    assert np.all(np.diff(dep) <= 1e-12), "deploy budget may only fall"
    assert np.all(np.diff(har) <= 1e-12), "harvest budget may only fall"
    assert DEPLOY_BUDGET - dep[-1] == pytest.approx(est["ers_energy_used_est_mj"], rel=1e-9)
    assert HARVEST_BUDGET - har[-1] == pytest.approx(est["ers_energy_harvested_est_mj"],
                                                     rel=1e-9)


def test_changing_the_deploy_budget_moves_only_the_deploy_budget():
    """Section 28: a car can be short of deploy budget with a nearly full store."""
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    base = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    tight = dataclasses.replace(
        RULES, deploy_budget=dataclasses.replace(RULES.deploy_budget, value_mj=0.5))
    got = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=tight)
    assert got["ers_deploy_budget_remaining_est_mj"][-1] < \
        base["ers_deploy_budget_remaining_est_mj"][-1]
    assert np.allclose(got["ers_soc_est_mj"], base["ers_soc_est_mj"]), \
        "the store is not the deploy budget"
    assert np.allclose(got["ers_harvest_budget_remaining_est_mj"],
                       base["ers_harvest_budget_remaining_est_mj"]), \
        "the harvest budget is not the deploy budget"


def test_changing_the_store_capacity_moves_only_the_store():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    base = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES,
                        initial_soc_mj=1.0)
    small = dataclasses.replace(
        RULES, ers_store_capacity=dataclasses.replace(RULES.ers_store_capacity,
                                                      value_mj=1.2))
    got = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=small,
                       initial_soc_mj=1.0)
    assert got["ers_soc_est_mj"].max() <= 1.2 + 1e-12
    assert np.allclose(got["ers_deploy_budget_remaining_est_mj"],
                       base["ers_deploy_budget_remaining_est_mj"])


def test_budget_remaining_is_allowed_to_go_negative_and_is_reported():
    """No hidden clamp: an over-budget window is information, not something to hide."""
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    tight = dataclasses.replace(
        RULES, deploy_budget=dataclasses.replace(RULES.deploy_budget, value_mj=0.1))
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=tight)
    assert est["ers_deploy_budget_remaining_est_mj"][-1] < 0.0
    assert est["diagnostics"]["deploy_budget_overrun_mj"] > 0.0
    assert any("deploy budget exceeded" in w for w in lap_summary(est)["warnings"])


def test_budget_can_be_carried_in_from_an_earlier_part_of_the_window():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES,
                       initial_deploy_budget_remaining_mj=2.0,
                       initial_harvest_budget_remaining_mj=3.0)
    assert est["ers_deploy_budget_remaining_est_mj"][0] <= 2.0
    assert est["ers_harvest_budget_remaining_est_mj"][0] <= 3.0


def test_each_quantity_carries_its_own_uncertainty():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    soc_u = est["ers_soc_uncertainty_mj"]
    dep_u = est["ers_deploy_budget_uncertainty_mj"]
    har_u = est["ers_harvest_budget_uncertainty_mj"]
    for u in (soc_u, dep_u, har_u):
        assert u.shape == soc_u.shape and (u >= 0).all()
        assert u[-1] >= u[0], "integration error compounds along the window"
    assert soc_u[0] >= P.initial_soc_uncertainty_mj, "the starting charge is unknown"
    assert not np.allclose(dep_u, har_u), "three quantities, three uncertainties"


# ===========================================================================
# 6. ENERGY STORE DYNAMICS AND BOUNDS (Math.md 9)
# ===========================================================================
def test_store_dynamics_match_the_documented_equation():
    """E_k+1 = E_k + eta_h*P_h*dt - P_d*dt/eta_d, term for term."""
    dt = np.array([1.0, 1.0])
    out = integrate_store([0.0, 100.0], [200.0, 0.0], dt, P,
                          initial_soc_mj=2.0, capacity_mj=10.0)
    e1 = 2.0 + P.harvest_eff * 200.0 * 1.0 / 1000.0
    e2 = e1 - 100.0 * 1.0 / P.deploy_eff / 1000.0
    assert out["soc_mj"][0] == pytest.approx(e1)
    assert out["soc_mj"][1] == pytest.approx(e2)


def test_store_is_bounded_and_the_bound_records_what_it_had_to_invent():
    dt = np.full(10, 1.0)
    out = integrate_store(np.full(10, 350.0), np.zeros(10), dt, P,
                          initial_soc_mj=0.5, capacity_mj=4.0)
    assert (out["soc_mj"] >= 0.0).all()
    assert out["soc_mj"][-1] == 0.0
    assert out["store_deficit_mj"] > 0.0, "the deficit is the calibration metric"
    assert out["soc_raw_mj"][-1] < 0.0, "the unbounded integration is preserved"
    assert out["store_empty_samples"] > 0


def test_store_spill_is_recorded_at_a_full_store():
    dt = np.full(10, 1.0)
    out = integrate_store(np.zeros(10), np.full(10, 350.0), dt, P,
                          initial_soc_mj=3.9, capacity_mj=4.0)
    assert out["soc_mj"].max() <= 4.0 + 1e-12
    assert out["store_spill_mj"] > 0.0
    assert out["store_full_samples"] > 0


def test_store_never_leaves_its_bounds_on_a_real_shaped_lap():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES,
                       initial_soc_mj=2.0)
    soc = est["ers_soc_est_mj"]
    assert soc.min() >= -1e-12 and soc.max() <= CAPACITY + 1e-12


def test_an_unbalanced_lap_warns_with_the_magnitude_and_does_not_hide_it():
    """AGENTS.md 34.6: show the warning, do not clip until the plot looks reasonable."""
    t, v, d, z, brake, thr, rpm = _violent_lap()
    s = lap_summary(estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES))
    assert s["storeDeficitMj"] > 0
    text = " ".join(s["warnings"])
    assert "ENERGY BALANCE NOT CLOSED" in text
    assert "MJ" in text, "the warning must carry the magnitude, not just an adjective"
    assert "calibrated ICE effective power map" in text, \
        "the warning must say what would be needed to close it"


# ===========================================================================
# 7. THE ENVELOPE IS A DIAGNOSTIC, NEVER A CLAMP (AGENTS.md section 28.1)
# ===========================================================================
def test_deploy_estimate_is_not_truncated_to_the_cap():
    t, v, d, z, brake, thr, rpm = _violent_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    deploy = est["ers_deploy_power_est_kw"]
    cap = est["cap_override_kw"]
    peak_cap = R.max_electrical_power_kw(0.0, R.MODE_OVERRIDE, RULES)
    assert deploy.max() > peak_cap, \
        "the raw estimate must be allowed above the most permissive cap"
    assert np.any(deploy > cap + 1e-6)
    assert est["status"]["deploy_estimate_is_raw"] is True
    assert est["status"]["envelope_applied_as"] == "diagnostic_only"


def test_violations_are_counted_sized_and_surfaced():
    t, v, d, z, brake, thr, rpm = _violent_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    env = est["diagnostics"]["envelope"]
    assert env["override_cap_violations"] > 0
    assert env["override_cap_excess_mj"] > 0.0
    assert env["override_cap_max_excess_kw"] > 0.0
    assert est["envelope_cap_violations"] == env["override_cap_violations"]
    assert any("deliberately NOT been truncated" in w
               for w in lap_summary(est)["warnings"])


def test_a_calm_lap_produces_no_violations():
    t = np.linspace(0, 30.0, 200)
    v = np.full(200, 120.0)
    d = np.cumsum(np.r_[0, np.diff(t)] * v / 3.6)
    est = estimate_ers(t, v, d, np.zeros(200), np.zeros(200), np.full(200, 40.0), P,
                       rpm=np.full(200, 9000.0), event_rules=RULES)
    assert est["diagnostics"]["envelope"]["override_cap_violations"] == 0


def test_the_twin_reads_the_curve_from_the_rule_engine_not_from_itself():
    """Swap in a different configured curve; the diagnostics must move (section 32)."""
    t, v, d, z, brake, thr, rpm = _violent_lap()
    base = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    generous = R.PowerEnvelope(
        breakpoints_kmh=(0.0, 400.0), max_power_kw=(5000.0, 5000.0),
        source="test", citation="test")
    loose = R.with_power_envelope(
        R.with_power_envelope(RULES, R.MODE_OVERRIDE, generous),
        R.MODE_NORMAL, generous)
    got = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=loose)
    assert base["diagnostics"]["envelope"]["override_cap_violations"] > 0
    assert got["diagnostics"]["envelope"]["override_cap_violations"] == 0
    assert np.allclose(got["ers_deploy_power_est_kw"], base["ers_deploy_power_est_kw"]), \
        "changing the cap must change only the diagnostic, never the estimate"


def test_no_envelope_constant_or_second_implementation_lives_in_the_twin():
    """Section 32 forbids both. The old TwinParams.ers_cap_kw and ers_envelope_kw are gone."""
    assert not hasattr(TwinParams(), "ers_cap_kw")
    assert not hasattr(TwinParams(), "max_brake_harvest_kw")
    assert not hasattr(twin_mod, "ers_envelope_kw")
    source = pathlib.Path(twin_mod.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    code = code.split('"""', 2)[-1]          # drop the module docstring
    for banned in ("7100", "1850", "355.0", "337.5", "350.0"):
        assert banned not in code, f"envelope constant {banned} must live only in rules.py"


def test_machine_limit_comes_from_the_rule_engine():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    assert est["rules"]["machine_peak_kw"] == \
        R.max_electrical_power_kw(0.0, R.MODE_OVERRIDE, RULES)


def test_override_evidence_only_above_the_separation_speed():
    """Section 20.2: below separation the discriminator carries no information."""
    sep = R.envelope_separation_speed_kmh(RULES)
    n = 60
    t = np.linspace(0, 6.0, n)
    v = np.full(n, sep - 50.0)
    d = np.cumsum(np.r_[0, np.diff(t)] * v / 3.6)
    deploy = np.full(n, 10_000.0)      # absurd, guaranteed above both caps
    sigma = np.zeros(n)
    env = envelope_diagnostics(v, deploy, sigma, np.gradient(t), RULES)
    assert env["envelope_separation_speed_kmh"] == pytest.approx(sep)
    assert env["override_evidence_samples"] == 0, "no evidence below separation"
    assert env["normal_cap_violations"] == n, "still a violation, just not evidence"


def test_override_evidence_needs_a_margin_larger_than_the_twins_own_sigma():
    sep = R.envelope_separation_speed_kmh(RULES)
    speed = sep + 20.0
    cap_n = R.max_electrical_power_kw(speed, R.MODE_NORMAL, RULES)
    cap_o = R.max_electrical_power_kw(speed, R.MODE_OVERRIDE, RULES)
    assert cap_o > cap_n
    deploy = np.array([0.5 * (cap_n + cap_o)])
    margin = deploy[0] - cap_n
    v = np.array([speed])
    dt = np.array([1.0])
    tiny = envelope_diagnostics(v, deploy, np.array([0.0]), dt, RULES)
    huge = envelope_diagnostics(
        v, deploy, np.array([margin / OVERRIDE_EVIDENCE_K_SIGMA * 2.0]), dt, RULES)
    assert tiny["override_evidence_samples"] == 1
    assert huge["override_evidence_samples"] == 0, \
        "a sigma larger than the margin must not claim evidence"


def test_unusable_speed_samples_are_counted_not_filled_in():
    v = np.array([200.0, np.nan, 250.0])
    env = envelope_diagnostics(v, np.array([500.0, 500.0, 500.0]),
                               np.zeros(3), np.ones(3), RULES)
    assert env["samples_with_unusable_speed"] == 1
    assert np.isnan(env["cap_normal_kw"][1])
    assert env["override_cap_violations"] == 2, "the NaN sample must not be counted"


# ===========================================================================
# 8. Rule provenance surfaced in the payload
# ===========================================================================
def test_payload_carries_the_unverified_compliance_statement():
    t, v, d, z, brake, thr, rpm = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, rpm=rpm, event_rules=RULES)
    comp = est["rules"]["compliance"]
    assert comp["legal_by_construction"] is False
    assert comp["models_speed_dependent_envelope"] is True
    assert len(comp["unverified_keys"]) > 0
    assert "UNVERIFIED" in est["provenance"]["envelope"]
