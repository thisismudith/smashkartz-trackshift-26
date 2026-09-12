"""Energy twin: the longitudinal power balance and ERS estimator (MODELS.md M14).

PURE FUNCTIONS ONLY. Every function here takes plain arrays/dicts and returns plain
arrays/dicts. Nothing reads a file, touches global state, or knows where its inputs came
from or where its outputs are going. That is deliberate: today `build_sim_data.py` calls
these and writes the results into a static artifact, and later an HTTP API will call the
exact same functions per request. Only the transport changes.

Provenance, per the project contract in AGENTS.md:
  - inputs (speed, distance, elevation, brake, throttle) are OBSERVED
  - the force split and wheel power are DERIVED (algebra on observed channels)
  - anything naming ERS, SOC or fuel is INFERRED or SIMULATED, because the public feed
    contains no battery, no MGU-K power and no fuel mass. These are never to be presented
    as measured, and the uncertainty field is not optional.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

SCHEMA_VERSION = 1

#: Half-width of the window used to differentiate speed, seconds.
ACCEL_WINDOW_S = 0.35


def _windowed_slope(t, y, half_width_s: float) -> np.ndarray:
    """d y / d t estimated over +-half_width_s, robust to irregular sampling."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    n = t.size
    out = np.zeros(n)
    lo = np.searchsorted(t, t - half_width_s, side="left")
    hi = np.searchsorted(t, t + half_width_s, side="right") - 1
    for i in range(n):
        a, b = lo[i], hi[i]
        if b <= a:
            a, b = max(0, i - 1), min(n - 1, i + 1)
        span = t[b] - t[a]
        out[i] = (y[b] - y[a]) / span if span > 1e-6 else 0.0
    return out


# ---------------------------------------------------------------------------
# Parameters. Ballpark values shared with the intro loader's constants.ts, which
# says plainly they are engineering estimates and not a validated vehicle model.
# They are therefore ASSUMPTIONS with ranges, carried in the output so any number
# derived from them can be traced back and re-run with different values.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TwinParams:
    mass_kg: float = 800.0              # car + driver + fuel, mid-race
    mass_uncertainty_kg: float = 40.0
    cda_m2: float = 1.2                 # drag area
    cda_uncertainty_m2: float = 0.15
    cla_m2: float = 3.5                 # downforce area
    crr: float = 0.015                  # rolling resistance
    rho_air: float = 1.2                # kg/m^3, refined per-sample from weather
    drivetrain_eff: float = 0.92        # wheel <- PU
    harvest_eff: float = 0.85           # braking energy -> store
    deploy_eff: float = 0.95            # store -> wheel
    ice_peak_kw: float = 400.0          # 2026 PU split: ~400 kW ICE
    ers_cap_kw: float = 350.0           # UNVERIFIED (AGENTS.md 19 lists this unresolved)
    max_brake_harvest_kw: float = 350.0
    gravity: float = 9.81


def air_density(air_temp_c: float | None, pressure_hpa: float | None,
                 fallback: float = 1.2) -> float:
    """Ideal-gas air density from the weather feed; falls back when either is missing."""
    if air_temp_c is None or pressure_hpa is None:
        return fallback
    return (pressure_hpa * 100.0) / (287.05 * (air_temp_c + 273.15))


def force_split(speed_mps, accel_mps2, grade_rad, params: TwinParams, rho: float | None = None):
    """The longitudinal balance of Math.md 11, as separate named forces (newtons).

    F_traction = M*a + F_drag + F_roll + F_gradient

    Returned separately rather than summed so the UI can show WHERE the power goes,
    and so each term can be argued with independently.
    """
    v = np.asarray(speed_mps, dtype=float)
    a = np.asarray(accel_mps2, dtype=float)
    th = np.asarray(grade_rad, dtype=float)
    r = params.rho_air if rho is None else rho

    f_inertia = params.mass_kg * a
    f_drag = 0.5 * r * params.cda_m2 * v ** 2
    f_downforce = 0.5 * r * params.cla_m2 * v ** 2
    # rolling resistance carries the aero load as well as the car's weight
    f_roll = params.crr * (params.mass_kg * params.gravity * np.cos(th) + f_downforce)
    f_grade = params.mass_kg * params.gravity * np.sin(th)

    return {
        "inertia_n": f_inertia,
        "drag_n": f_drag,
        "downforce_n": f_downforce,
        "rolling_n": f_roll,
        "gradient_n": f_grade,
        "traction_n": f_inertia + f_drag + f_roll + f_grade,
    }


def wheel_power_kw(forces: dict, speed_mps) -> np.ndarray:
    """P = F * v. Negative means the car is shedding energy (braking/coasting)."""
    return forces["traction_n"] * np.asarray(speed_mps, dtype=float) / 1000.0


def kinematics(time_s, speed_kph, distance_m, elevation_m):
    """Acceleration and road gradient from the observed channels.

    Speed is differentiated here rather than using the feed's acc_x: DATA_REFERENCE.md
    states acc_* are computed by the extraction pipeline with outlier replacement and
    smoothing, and the audit measured values beyond +-40 m/s^2 in them. Differentiating
    speed against its own timebase is reproducible and its noise is understood.
    """
    t = np.asarray(time_s, dtype=float)
    v = np.asarray(speed_kph, dtype=float) / 3.6
    d = np.asarray(distance_m, dtype=float)
    z = np.asarray(elevation_m, dtype=float)

    dt = np.gradient(t)
    dt[dt <= 0] = np.nan

    # Acceleration over a TIME WINDOW, not between adjacent samples. The feed is
    # irregular (audit: mean step 0.13 s but a minimum of 0.007 s), and dividing a
    # speed change by a 7 ms gap manufactures huge spurious acceleration -- measured,
    # it put peak wheel power at 1400-2400 kW when a real car makes 750-800 kW.
    # A centred window over +-ACCEL_WINDOW_S is stable regardless of sample spacing.
    accel = _windowed_slope(t, v, ACCEL_WINDOW_S)

    ds = np.gradient(d)
    ds[ds <= 0] = np.nan
    grade = np.gradient(z) / ds
    # beyond this is a data artefact, not a road: F1 gradients top out around 20 %
    grade = np.clip(np.nan_to_num(grade), -0.2, 0.2)

    return {"speed_mps": v, "accel_mps2": accel, "grade_rad": np.arctan(grade), "dt_s": dt}


def ers_envelope_kw(speed_kph, mode: str, params: TwinParams) -> np.ndarray:
    """Speed-dependent electrical power ceiling (Math.md 7).

    UNVERIFIED. AGENTS.md 19 lists "resolve exact 350 kW semantics" and the taper as
    open questions for the rules workstream, and MODELS.md M19 says the rule engine is
    the sole owner of this curve. This implementation exists so the twin can flag cap
    violations as a FIT DIAGNOSTIC (M14: never a silent clamp) and must be replaced by
    the rule engine's version once that lands.
    """
    v = np.asarray(speed_kph, dtype=float)
    if mode == "override":
        taper = 7100.0 - 20.0 * v
    else:
        taper = 1850.0 - 5.0 * v
    return np.clip(np.minimum(taper, params.ers_cap_kw), 0.0, params.ers_cap_kw)


def estimate_ers(time_s, speed_kph, distance_m, elevation_m, brake, throttle,
                  params: TwinParams | None = None,
                  air_temp_c: float | None = None, pressure_hpa: float | None = None,
                  initial_soc_mj: float = 2.0, soc_capacity_mj: float = 4.0):
    """Per-sample ERS estimate for one lap. The twin's single entry point.

    Returns arrays the caller may serialise, display or hand to an API response. Every
    ERS field is INFERRED/SIMULATED; `soc_uncertainty_mj` grows with integration time
    because the starting charge is unknown and the error compounds.
    """
    p = params or TwinParams()
    rho = air_density(air_temp_c, pressure_hpa, p.rho_air)

    kin = kinematics(time_s, speed_kph, distance_m, elevation_m)
    forces = force_split(kin["speed_mps"], kin["accel_mps2"], kin["grade_rad"], p, rho)
    p_wheel = wheel_power_kw(forces, kin["speed_mps"])

    dt = np.nan_to_num(kin["dt_s"])
    braking = np.asarray(brake, dtype=float) > 0.5
    thr = np.asarray(throttle, dtype=float)

    # HARVEST: only while braking, only what the regulation ceiling allows.
    harvest_kw = np.where(braking & (p_wheel < 0),
                           np.minimum(-p_wheel * p.harvest_eff, p.max_brake_harvest_kw), 0.0)

    # DEPLOY: the share of demanded power beyond what the ICE alone could provide.
    # Crude by construction -- the real split is private -- so it is capped by the
    # envelope and reported with an explicit uncertainty rather than dressed up.
    demand_kw = np.maximum(p_wheel, 0.0) / p.drivetrain_eff
    cap_kw = ers_envelope_kw(speed_kph, "normal", p)
    deploy_raw = np.where(thr > 80.0, np.maximum(demand_kw - p.ice_peak_kw, 0.0), 0.0)
    deploy_kw = np.minimum(deploy_raw, cap_kw)
    # a diagnostic, not a clamp: how often the raw estimate exceeded the ceiling
    cap_violations = int(np.sum(deploy_raw > cap_kw + 1e-6))

    # STORE: Math.md 9 energy-store dynamics, integrated along the lap.
    d_store_mj = (harvest_kw - deploy_kw / p.deploy_eff) * dt / 1000.0
    soc_mj = np.clip(initial_soc_mj + np.cumsum(d_store_mj), 0.0, soc_capacity_mj)

    elapsed = np.nan_to_num(np.asarray(time_s, dtype=float))
    soc_uncertainty = 0.5 + 0.004 * elapsed  # unknown start + compounding integration

    return {
        "schemaVersion": SCHEMA_VERSION,
        "wheel_power_kw": p_wheel,
        "drag_kw": forces["drag_n"] * kin["speed_mps"] / 1000.0,
        "rolling_kw": forces["rolling_n"] * kin["speed_mps"] / 1000.0,
        "gradient_kw": forces["gradient_n"] * kin["speed_mps"] / 1000.0,
        "ers_deploy_power_est_kw": deploy_kw,
        "ers_harvest_power_est_kw": harvest_kw,
        "ers_soc_est_mj": soc_mj,
        "ers_soc_uncertainty_mj": soc_uncertainty,
        "ers_energy_used_est_mj": float(np.sum(deploy_kw * dt) / 1000.0),
        "ers_energy_harvested_est_mj": float(np.sum(harvest_kw * dt) / 1000.0),
        "envelope_cap_violations": cap_violations,
        "provenance": {
            "wheel_power_kw": "DERIVED",
            "drag_kw": "DERIVED",
            "rolling_kw": "DERIVED",
            "gradient_kw": "DERIVED",
            "ers_deploy_power_est_kw": "INFERRED",
            "ers_harvest_power_est_kw": "INFERRED",
            "ers_soc_est_mj": "SIMULATED",
            "envelope": "RULE (UNVERIFIED - see AGENTS.md 19)",
        },
        "params": asdict(p),
    }


def lap_summary(estimate: dict) -> dict:
    """Scalar per-lap roll-up, the shape a UI panel or an API response wants.

    Peaks are reported at the 99th/1st percentile, not the absolute extreme. The speed
    channel stalls and jumps (the audit found multi-second freezes), and a single
    recovered sample lands as a spike: absolute max read 1200-2400 kW against a real
    car's 750-800 kW, while p99 sits in the right band. The raw extreme is kept too,
    so the distortion stays visible rather than being quietly discarded.

    `warnings` is the honesty valve required by AGENTS.md 34.6: when the twin cannot
    satisfy a basic energy check it says so, instead of being tuned until the plot
    looks agreeable.
    """
    pw = np.asarray(estimate["wheel_power_kw"], dtype=float)
    used = estimate["ers_energy_used_est_mj"]
    harvested = estimate["ers_energy_harvested_est_mj"]

    warnings: list[str] = []
    # Over a full lap a real car must roughly balance what it deploys against what it
    # recovers. A large imbalance means the deploy/ICE split is mis-attributing power,
    # not that the car found free energy.
    if used > 0 and harvested >= 0 and used > 2.0 * max(harvested, 1e-6):
        warnings.append(
            f"deploy estimate ({used:.1f} MJ) exceeds harvest ({harvested:.1f} MJ) by more "
            "than 2x over the lap; the ICE/electrical split is approximate and the SOC "
            "trace should be read as indicative only"
        )
    if estimate["envelope_cap_violations"] > 0:
        warnings.append(
            f"{estimate['envelope_cap_violations']} samples wanted more electrical power "
            "than the (unverified) regulation envelope allows"
        )

    return {
        "peakWheelPowerKw": round(float(np.nanpercentile(pw, 99)), 1),
        "peakBrakingKw": round(float(np.nanpercentile(pw, 1)), 1),
        "rawMaxWheelPowerKw": round(float(np.nanmax(pw)), 1),
        "ersEnergyUsedMj": round(used, 2),
        "ersEnergyHarvestedMj": round(harvested, 2),
        "energyBalanceMj": round(harvested - used, 2),
        "socEndMj": round(float(estimate["ers_soc_est_mj"][-1]), 2),
        "socUncertaintyMj": round(float(estimate["ers_soc_uncertainty_mj"][-1]), 2),
        "envelopeCapViolations": estimate["envelope_cap_violations"],
        "warnings": warnings,
    }
