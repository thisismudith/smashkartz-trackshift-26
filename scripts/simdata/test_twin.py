"""Physics sanity tests for the energy twin. Pure functions, so no fixtures needed."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
from simdata.twin import (TwinParams, air_density, ers_envelope_kw, estimate_ers,
                           force_split, kinematics, lap_summary, wheel_power_kw)

P = TwinParams()

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

def test_envelope_never_exceeds_cap_and_reaches_zero():
    v = np.linspace(0, 400, 50)
    e = ers_envelope_kw(v, "override", P)
    assert e.max() <= P.ers_cap_kw + 1e-9
    assert e[-1] == 0.0, "no electrical power at the top of the taper"
    assert (e >= 0).all()

def _synthetic_lap(n=400, lap_s=90.0):
    t = np.linspace(0, lap_s, n)
    v = 200 + 100 * np.sin(2 * np.pi * t / lap_s)      # km/h, accelerating and braking
    d = np.cumsum(np.r_[0, np.diff(t)] * v / 3.6)
    z = np.zeros(n)
    accel = np.gradient(v / 3.6) / np.gradient(t)
    brake = (accel < -2).astype(float)
    thr = np.where(accel > 0, 100.0, 0.0)
    return t, v, d, z, brake, thr

def test_estimate_is_energy_consistent_and_bounded():
    t, v, d, z, brake, thr = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P, air_temp_c=25, pressure_hpa=1010)
    soc = est["ers_soc_est_mj"]
    assert (soc >= 0).all() and (soc <= 4.0).all(), "store must stay within capacity"
    assert est["ers_energy_harvested_est_mj"] >= 0
    assert est["ers_energy_used_est_mj"] >= 0
    assert (est["ers_deploy_power_est_kw"] <= P.ers_cap_kw + 1e-6).all()
    assert (est["ers_harvest_power_est_kw"] >= 0).all()

def test_harvest_only_happens_under_braking():
    t, v, d, z, brake, thr = _synthetic_lap()
    est = estimate_ers(t, v, d, z, brake, thr, P)
    assert not np.any(est["ers_harvest_power_est_kw"][brake < 0.5] > 0)

def test_every_ers_field_is_tagged_not_observed():
    t, v, d, z, brake, thr = _synthetic_lap()
    prov = estimate_ers(t, v, d, z, brake, thr, P)["provenance"]
    for k, tag in prov.items():
        if k.startswith("ers_"):
            assert tag in ("INFERRED", "SIMULATED"), f"{k} must never claim to be measured"

def test_lap_summary_shape():
    t, v, d, z, brake, thr = _synthetic_lap()
    s = lap_summary(estimate_ers(t, v, d, z, brake, thr, P))
    assert s["peakWheelPowerKw"] > 0 > s["peakBrakingKw"]
    assert "socUncertaintyMj" in s

def test_pure_no_input_mutation():
    t, v, d, z, brake, thr = _synthetic_lap()
    before = v.copy()
    estimate_ers(t, v, d, z, brake, thr, P)
    assert np.array_equal(v, before), "inputs must not be mutated"
