"""Energy twin: the longitudinal power balance and ERS estimator (MODELS.md M14).

PURE FUNCTIONS ONLY. Every function here takes plain arrays/dicts and returns plain
arrays/dicts. Nothing reads a file, touches global state, or knows where its inputs came
from or where its outputs are going. That is deliberate: today `build_sim_data.py` calls
these and writes the results into a static artifact, and later an HTTP API will call the
exact same functions per request. Only the transport changes.

Provenance, per the project contract in AGENTS.md section 10:
  - inputs (time, speed, distance, elevation, brake, throttle, rpm) are OBSERVED
  - the force split and wheel power are DERIVED (algebra on observed channels)
  - anything naming ERS, SOC or the ICE split is INFERRED or SIMULATED, because the public
    feed contains no battery, no MGU-K power and no fuel mass. These are never to be
    presented as measured, and the uncertainty field is not optional.
  - every power/energy *limit* is RULE, and comes from `simdata.rules`. Fuel mass is NOT
    produced here; that is M34's job.

WHAT THIS MODULE OWNS AND WHAT IT DOES NOT
------------------------------------------
Section 32: the rule engine owns `max_electrical_power_kw(speed_kmh, mode, event_rules)`
and there may be no second implementation of the envelope curve and no numeric envelope
constant in any other Python module. An earlier version of this file held both
(`TwinParams.ers_cap_kw = 350.0` and a local `ers_envelope_kw()`). Both are gone. Every
cap in this module is obtained by calling `simdata.rules`.

THE THREE SEPARATE ENERGY QUANTITIES (section 28)
-------------------------------------------------
Section 28 is emphatic that these are three distinct constraints and that collapsing them
"produces a twin that is right about total energy and wrong about what the car may legally
do with it next". They are tracked separately here, from three separate rule values with
three separate accounting windows, each with its own uncertainty series:

    ers_soc_est_mj                      stored energy, bounded by ers_store_capacity (RULE)
    ers_deploy_budget_remaining_est_mj  how much may still be deployed this window
    ers_harvest_budget_remaining_est_mj how much may still be recovered this window

THE ENVELOPE IS A DIAGNOSTIC, NEVER A CLAMP (section 28.1)
----------------------------------------------------------
`ers_deploy_power_est_kw` is the RAW residual estimate `P_wheel/eta - P_ICE`. It is never
truncated to the regulatory cap. Where it exceeds the cap, the excess is counted and its
energy is integrated, and those counts are the calibration metric. Truncating would hide
the mis-set mass / CdA / ICE map that produced the excess and would manufacture a
plausible-looking series.

There is one deliberate asymmetry, and it is not a loophole:

  - DEPLOY is an *estimate of an unobserved quantity* derived as a residual. A residual
    that lands above the cap is evidence of model error, so it is recorded raw.
  - BRAKING HARVEST is a *forward model of a device*. The recovered power is modelled as
    min(power available at the MGU-K shaft, the machine's rating). The machine rating is a
    property of the hardware, not a regulatory truncation of an estimate, and a model that
    claims the MGU-K absorbed 660 kW is simply wrong rather than honest. The energy the
    machine could not take is reported as `harvest_spill_mj` so the limiting is visible.

THE ICE EFFECTIVE POWER MAP
---------------------------
Section 28 lists "ICE effective power map" among the model parameters. A single peak-power
constant is too crude: it makes the ICE contribute its full output at 5000 rpm at 10 %
throttle, which throws the entire residual into the electrical term. The map here is:

    P_ICE_available(rpm) = P_rated * clip(rpm / rpm_rated, 0, 1),  zero below idle
    P_ICE_effective      = P_ICE_available * throttle_fraction,    zero under braking

ASSUMPTIONS, stated so they can be argued with:

  1. Constant torque below rated speed. A turbocharged engine held at its boost/flow limit
     produces roughly constant torque through the mid range, so power rises linearly with
     engine speed up to the rated point and is flat above it. This is the standard ideal
     traction envelope; it is an approximation, not a measured map.
  2. `ice_rated_power_kw = 400`. The reported 2026 PU split is ~400 kW ICE + ~350 kW
     electrical. UNVERIFIED, carried with an uncertainty of +-60 kW.
  3. `ice_rated_speed_rpm = 12000`. Chosen from the OBSERVED rpm distribution of the feed
     itself, not from the energy balance: over HAM's British GP race laps 10-29 the rpm
     channel has p95 = 11808, p99 = 12065 and max = 12625. The engine spends its full-load
     life just below 12000, so that is where rated power is placed. Uncertainty +-800 rpm.
  4. Throttle maps linearly to delivered power. Real part-load behaviour is not linear;
     linear is the simplest defensible choice and is flagged here rather than hidden.
  5. Engine overrun (motoring) braking is NOT modelled. There is no defensible public
     figure for it. Including it would *reduce* the estimated braking harvest, so its
     omission is optimistic for the energy balance, not pessimistic. Said plainly because
     the residual imbalance below is the headline number.

WHEN rpm IS NOT SUPPLIED the map degrades to `P_ICE_available = P_rated` at all engine
speeds. That is a worse model (it over-credits the ICE at low rpm and therefore
under-estimates deploy). It is not a hidden fallback: `status.ice_map_basis` reports
`"throttle_only"` and a warning is emitted in the returned payload.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from simdata import rules as rules_mod
from simdata.rules import (
    MODE_NORMAL,
    MODE_OVERRIDE,
    EventRules,
    default_event_rules,
    envelope_separation_speed_kmh,
    max_electrical_power_kw,
    max_electrical_power_kw_series,
)

SCHEMA_VERSION = 2

#: Half-width of the window used to differentiate speed, seconds.
ACCEL_WINDOW_S = 0.35

#: Sigma multiple used by the section 20.2 override discriminator. Evidence only.
OVERRIDE_EVIDENCE_K_SIGMA = 2.0


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
# Parameters. Every one is an ASSUMPTION with a range, carried in the output so any
# number derived from them can be traced back and re-run with different values.
# NOTE: no electrical power CAP lives here. Caps come from simdata.rules (section 32).
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
    harvest_eff: float = 0.85           # eta_h, MGU-K shaft -> store (Math.md 9)
    deploy_eff: float = 0.95            # eta_d, store -> MGU-K shaft (Math.md 9)
    gravity: float = 9.81

    # --- ICE effective power map (see module docstring for the derivation) ---
    ice_rated_power_kw: float = 400.0
    ice_rated_power_uncertainty_kw: float = 60.0
    ice_rated_speed_rpm: float = 12000.0
    ice_rated_speed_uncertainty_rpm: float = 800.0
    ice_idle_rpm: float = 3000.0        # below this the engine is not driving the car

    # --- braking recovery path ---
    #: Share of total braking power that passes through the REAR axle, which is the only
    #: axle the MGU-K is coupled to. F1 brake bias sits front-biased (~55-58 % front), so
    #: 0.45 is the rear share. UNVERIFIED per-car and per-corner; carried with a range.
    rear_brake_share: float = 0.45
    rear_brake_share_uncertainty: float = 0.08

    # --- uncertainty bookkeeping ---
    initial_soc_uncertainty_mj: float = 1.0     # the starting charge is simply unknown
    energy_uncertainty_fraction: float = 0.25   # fractional error on integrated energy


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


# ---------------------------------------------------------------------------
# ICE effective power map (section 28: "ICE effective power map")
# ---------------------------------------------------------------------------
def ice_availability_fraction(rpm, params: TwinParams) -> np.ndarray:
    """Fraction of rated ICE power available at this engine speed. Pure.

    Constant torque below the rated speed => power proportional to rpm; flat above it.
    Zero below idle, where the engine is not driving the car. See the module docstring
    for why the rated speed is 12000 rpm and where that number came from.
    """
    r = np.asarray(rpm, dtype=float)
    frac = np.clip(r / params.ice_rated_speed_rpm, 0.0, 1.0)
    frac = np.where(np.isfinite(r) & (r >= params.ice_idle_rpm), frac, 0.0)
    return frac


def ice_power_kw(throttle, brake, params: TwinParams, rpm=None):
    """Effective ICE power delivered to the driveline, kW. INFERRED, never observed.

    Returns ``(power_kw, availability_fraction, basis)`` where ``basis`` is ``"rpm"`` when
    the engine-speed map was used and ``"throttle_only"`` when no rpm channel was supplied
    and the map degraded to flat rated power. The basis is returned rather than logged so
    the caller must carry it into the payload.
    """
    thr = np.clip(np.nan_to_num(np.asarray(throttle, dtype=float)) / 100.0, 0.0, 1.0)
    braking = np.nan_to_num(np.asarray(brake, dtype=float)) > 0.5
    if rpm is None:
        frac = np.ones_like(thr)
        basis = "throttle_only"
    else:
        frac = ice_availability_fraction(rpm, params)
        basis = "rpm"
    # fuel cut on the brakes: the ICE is not driving the car while the driver is braking
    delivered = params.ice_rated_power_kw * frac * thr
    delivered = np.where(braking, 0.0, delivered)
    return delivered, frac, basis


# ---------------------------------------------------------------------------
# The electrical split (Math.md 11: P_K ~= P_wheel/eta - P_ICE)
# ---------------------------------------------------------------------------
def electrical_split_kw(p_wheel_kw, p_ice_kw, brake, params: TwinParams,
                        machine_limit_kw: float):
    """Split the power balance residual into deploy and harvest. Pure.

    Math.md section 11 gives, with no max() anywhere in it:

        P_K ~= P_wheel / eta_drivetrain - P_ICE_effective

    Taking BOTH signs of that residual is the substantive change from the previous
    implementation, which took only the positive branch above a throttle threshold. The
    negative branch is not a rounding artefact: with no MGU-H, a 2026 car that deploys
    ~8 MJ per lap cannot recover all of it under braking alone, so it spends part of every
    straight running the ICE above the wheel demand and charging the store through the
    MGU-K. Suppressing that branch is what made the old twin deploy 6.3 MJ against a
    2.9 MJ harvest and drain the store to zero on every lap.

    Three streams come out:

      deploy_kw            residual > 0 while on power. RAW: never capped (section 28.1).
      harvest_ice_kw       residual < 0 while on power: ICE surplus charging the store.
                           Also raw -- it is the same residual estimate with the other
                           sign, so truncating it would hide the same calibration error.
      harvest_brake_kw     modelled forward from the braking power actually available at
                           the MGU-K shaft, then limited by the machine rating (see the
                           module docstring for why this one limit is a device model and
                           not a clamp on an estimate). `harvest_brake_spill_kw` is the
                           part the machine could not absorb.
    """
    pw = np.asarray(p_wheel_kw, dtype=float)
    pice = np.asarray(p_ice_kw, dtype=float)
    braking = np.nan_to_num(np.asarray(brake, dtype=float)) > 0.5

    on_power = ~braking
    residual = pw / params.drivetrain_eff - pice

    deploy_kw = np.where(on_power, np.maximum(residual, 0.0), 0.0)
    harvest_ice_kw = np.where(on_power, np.maximum(-residual, 0.0), 0.0)

    # Braking: energy flows wheels -> driveline -> MGU-K shaft, so the driveline
    # efficiency multiplies rather than divides. Only the rear axle is coupled to the
    # MGU-K, hence rear_brake_share.
    brake_power_kw = np.where(braking, np.maximum(-pw, 0.0), 0.0)
    available_kw = brake_power_kw * params.rear_brake_share * params.drivetrain_eff
    harvest_brake_kw = np.minimum(available_kw, machine_limit_kw)
    spill_kw = np.maximum(available_kw - machine_limit_kw, 0.0)

    return {
        "deploy_kw": deploy_kw,
        "harvest_brake_kw": harvest_brake_kw,
        "harvest_ice_kw": harvest_ice_kw,
        "harvest_kw": harvest_brake_kw + harvest_ice_kw,
        "harvest_brake_available_kw": available_kw,
        "harvest_brake_spill_kw": spill_kw,
        "residual_kw": residual,
    }


def deploy_power_uncertainty_kw(speed_mps, accel_mps2, grade_rad, ice_fraction,
                                throttle, params: TwinParams, rho: float):
    """First-order propagation of parameter uncertainty into the deploy estimate, kW.

    P_deploy = (M*a + F_drag + F_roll + F_grade) * v / eta - P_ICE, so

        dP/dM     = v * (a + g*(sin th + crr*cos th)) / eta
        dP/dCdA   = 0.5 * rho * v^3 / eta
        dP/dP_ICE = -1                      (scaled by availability * throttle)

    combined in quadrature. This is the sigma the section 20.2 override discriminator
    scales its margin by; a twin that does not know its own error cannot claim evidence
    about a rival's mode.
    """
    v = np.asarray(speed_mps, dtype=float)
    a = np.asarray(accel_mps2, dtype=float)
    th = np.asarray(grade_rad, dtype=float)
    frac = np.asarray(ice_fraction, dtype=float)
    thr = np.clip(np.nan_to_num(np.asarray(throttle, dtype=float)) / 100.0, 0.0, 1.0)
    eta = params.drivetrain_eff

    d_mass = np.abs(v * (a + params.gravity * (np.sin(th) + params.crr * np.cos(th)))) / eta / 1000.0
    d_cda = 0.5 * rho * v ** 3 / eta / 1000.0
    s_mass = d_mass * params.mass_uncertainty_kg
    s_cda = d_cda * params.cda_uncertainty_m2
    s_ice = params.ice_rated_power_uncertainty_kw * frac * thr
    return np.sqrt(s_mass ** 2 + s_cda ** 2 + s_ice ** 2)


# ---------------------------------------------------------------------------
# Energy store dynamics (Math.md 9 / AGENTS.md 28)
# ---------------------------------------------------------------------------
def integrate_store(deploy_kw, harvest_kw, dt_s, params: TwinParams,
                    initial_soc_mj: float, capacity_mj: float):
    """E_{k+1} = E_k + eta_h * P_harvest * dt - P_deploy * dt / eta_d, bounded.

    Section 28 requires the store to be "subject to the encoded Energy Store bounds", so
    the bound IS applied here -- but every joule the bound had to invent or throw away is
    recorded, because that quantity is the calibration metric for the energy balance:

        store_deficit_mj  energy the bound had to SUPPLY because the model deployed more
                          than it ever harvested. A non-zero value means the twin does not
                          balance and the SOC trace below it is not meaningful.
        store_spill_mj    energy thrown away at a full store.

    The unbounded integration is returned alongside as `soc_raw_mj` so nothing is lost.
    """
    d = np.asarray(deploy_kw, dtype=float)
    h = np.asarray(harvest_kw, dtype=float)
    dt = np.nan_to_num(np.asarray(dt_s, dtype=float))

    d_store_mj = (params.harvest_eff * h - d / params.deploy_eff) * dt / 1000.0
    soc_raw = initial_soc_mj + np.cumsum(d_store_mj)

    n = d_store_mj.size
    soc = np.empty(n)
    deficit = 0.0
    spill = 0.0
    empty_samples = 0
    full_samples = 0
    e = float(initial_soc_mj)
    for i in range(n):
        raw = e + d_store_mj[i]
        clipped = min(max(raw, 0.0), capacity_mj)
        correction = clipped - raw
        if correction > 0.0:
            deficit += correction
            empty_samples += 1
        elif correction < 0.0:
            spill += -correction
            full_samples += 1
        e = clipped
        soc[i] = e

    throughput_mj = np.cumsum(np.abs(params.harvest_eff * h - d / params.deploy_eff) * dt) / 1000.0
    sigma = np.sqrt(params.initial_soc_uncertainty_mj ** 2
                    + (params.energy_uncertainty_fraction * throughput_mj) ** 2)

    return {
        "soc_mj": soc,
        "soc_raw_mj": soc_raw,
        "soc_uncertainty_mj": sigma,
        "store_deficit_mj": float(deficit),
        "store_spill_mj": float(spill),
        "store_empty_samples": int(empty_samples),
        "store_full_samples": int(full_samples),
        "throughput_mj": throughput_mj,
    }


# ---------------------------------------------------------------------------
# Envelope diagnostics (section 28.1, section 20.2) -- calls the rule engine only
# ---------------------------------------------------------------------------
def _cap_series(speed_kph, mode: str, event_rules: EventRules) -> np.ndarray:
    """Regulatory cap at every sample, NaN where the speed channel is unusable.

    The rule engine refuses to guess a cap for a NaN speed (it raises), which is correct.
    Those samples are excluded from the diagnostics and counted, never filled in.
    """
    v = np.asarray(speed_kph, dtype=float)
    ok = np.isfinite(v)
    caps = np.full(v.shape, np.nan)
    if ok.any():
        caps[ok] = np.asarray(max_electrical_power_kw_series(v[ok], mode, event_rules))
    return caps


def envelope_diagnostics(speed_kph, deploy_kw, deploy_sigma_kw, dt_s,
                         event_rules: EventRules) -> dict:
    """Count, size and locate envelope violations. NEVER modifies the estimate.

    Section 28.1: "An estimate that exceeds the override cap at the observed speed is not
    a discovery about the car; it is a defect in the twin." Violations of the OVERRIDE cap
    (the most permissive curve the rule engine will produce) are unambiguous defects and
    are the headline metric. Violations of the NORMAL cap are reported too, because above
    the separation speed they are the section 20.2 override evidence rather than
    necessarily an error.
    """
    v = np.asarray(speed_kph, dtype=float)
    d = np.asarray(deploy_kw, dtype=float)
    sig = np.asarray(deploy_sigma_kw, dtype=float)
    dt = np.nan_to_num(np.asarray(dt_s, dtype=float))
    usable = np.isfinite(v) & np.isfinite(d)

    cap_normal = _cap_series(v, MODE_NORMAL, event_rules)
    cap_override = _cap_series(v, MODE_OVERRIDE, event_rules)

    def _excess(cap):
        ex = np.where(usable & np.isfinite(cap), d - cap, 0.0)
        ex = np.maximum(ex, 0.0)
        hits = usable & np.isfinite(cap) & (d > cap + 1e-6)
        return ex, hits

    ex_o, hit_o = _excess(cap_override)
    ex_n, hit_n = _excess(cap_normal)

    sep = envelope_separation_speed_kmh(event_rules)
    if sep is None:
        above_sep = np.zeros_like(v, dtype=bool)
    else:
        above_sep = usable & (v > sep)
    # Section 20.2: evidence of override, only where the two curves actually separate and
    # only by a margin larger than the twin's own uncertainty. INFERRED, never a label.
    evidence = (above_sep
                & np.isfinite(cap_normal) & np.isfinite(cap_override)
                & (d > cap_normal + OVERRIDE_EVIDENCE_K_SIGMA * sig)
                & (d <= cap_override + 1e-6))

    return {
        "cap_normal_kw": cap_normal,
        "cap_override_kw": cap_override,
        "override_cap_violations": int(hit_o.sum()),
        "override_cap_excess_mj": float(np.sum(ex_o * dt) / 1000.0),
        "override_cap_max_excess_kw": float(ex_o.max()) if ex_o.size else 0.0,
        "normal_cap_violations": int(hit_n.sum()),
        "normal_cap_excess_mj": float(np.sum(ex_n * dt) / 1000.0),
        "override_evidence_samples": int(evidence.sum()),
        "envelope_separation_speed_kmh": sep,
        "samples_with_unusable_speed": int((~usable).sum()),
    }


# ---------------------------------------------------------------------------
# The twin's single entry point
# ---------------------------------------------------------------------------
def estimate_ers(time_s, speed_kph, distance_m, elevation_m, brake, throttle,
                  params: TwinParams | None = None,
                  air_temp_c: float | None = None, pressure_hpa: float | None = None,
                  initial_soc_mj: float = 2.0, soc_capacity_mj: float | None = None,
                  rpm=None,
                  event_rules: EventRules | None = None,
                  initial_deploy_budget_remaining_mj: float | None = None,
                  initial_harvest_budget_remaining_mj: float | None = None):
    """Per-sample ERS estimate for one accounting window (one lap). Pure.

    Returns arrays the caller may serialise, display or hand to an API response. Every ERS
    field is INFERRED or SIMULATED and carries an uncertainty; none is ever OBSERVED.

    `soc_capacity_mj` defaults to the rule engine's `ers_store_capacity`, which is RULE and
    UNVERIFIED. It is accepted as an argument only so a caller can explore a different
    encoded bound; it is NOT a physics parameter and does not live in `TwinParams`.

    The three section 28 quantities are tracked separately and are never collapsed:
    the store integrates Math.md 9 and is bounded by the store capacity; the deploy budget
    counts down from `deploy_budget` in its own accounting window; the harvest budget
    counts down from `harvest_budget` in its own. The budgets are NOT clamped at zero --
    a negative remaining budget means the twin says the car did something it may not do,
    which is information, and `..._budget_overrun_mj` states the size of it.
    """
    p = params or TwinParams()
    rl = event_rules or default_event_rules()
    rho = air_density(air_temp_c, pressure_hpa, p.rho_air)

    kin = kinematics(time_s, speed_kph, distance_m, elevation_m)
    forces = force_split(kin["speed_mps"], kin["accel_mps2"], kin["grade_rad"], p, rho)
    p_wheel = wheel_power_kw(forces, kin["speed_mps"])
    dt = np.nan_to_num(kin["dt_s"])

    warnings: list[str] = []

    # The MGU-K's own rating, taken FROM THE RULE ENGINE rather than written here: the
    # highest cap the configured envelope will ever return is the machine's peak. There is
    # no envelope constant in this module (section 32).
    machine_limit_kw = max_electrical_power_kw(0.0, MODE_OVERRIDE, rl)

    p_ice, ice_frac, ice_basis = ice_power_kw(throttle, brake, p, rpm=rpm)
    if ice_basis == "throttle_only":
        warnings.append(
            "no rpm channel supplied: the ICE map degraded to flat rated power at all "
            "engine speeds, which over-credits the ICE at low rpm and therefore "
            "under-estimates deployment"
        )

    split = electrical_split_kw(p_wheel, p_ice, brake, p, machine_limit_kw)
    deploy_kw = split["deploy_kw"]                 # RAW. Not capped. Section 28.1.
    harvest_kw = split["harvest_kw"]

    sigma_deploy = deploy_power_uncertainty_kw(
        kin["speed_mps"], kin["accel_mps2"], kin["grade_rad"], ice_frac, throttle, p, rho)

    env = envelope_diagnostics(speed_kph, deploy_kw, sigma_deploy, dt, rl)

    # --- 1. the energy store -------------------------------------------------
    capacity_mj = float(soc_capacity_mj if soc_capacity_mj is not None
                        else rl.ers_store_capacity.value_mj)
    store = integrate_store(deploy_kw, harvest_kw, dt, p, initial_soc_mj, capacity_mj)

    # --- 2. the deploy budget, its own quantity with its own window ----------
    deploy_mj_cum = np.cumsum(deploy_kw * dt) / 1000.0
    deploy_budget_mj = float(rl.deploy_budget.value_mj)
    deploy_start = (deploy_budget_mj if initial_deploy_budget_remaining_mj is None
                    else float(initial_deploy_budget_remaining_mj))
    deploy_remaining = deploy_start - deploy_mj_cum

    # --- 3. the harvest budget, likewise ------------------------------------
    harvest_mj_cum = np.cumsum(harvest_kw * dt) / 1000.0
    harvest_budget_mj = float(rl.harvest_budget.value_mj)
    harvest_start = (harvest_budget_mj if initial_harvest_budget_remaining_mj is None
                     else float(initial_harvest_budget_remaining_mj))
    harvest_remaining = harvest_start - harvest_mj_cum

    frac = p.energy_uncertainty_fraction
    deploy_used_mj = float(deploy_mj_cum[-1]) if deploy_mj_cum.size else 0.0
    harvest_used_mj = float(harvest_mj_cum[-1]) if harvest_mj_cum.size else 0.0
    ice_energy_mj = float(np.sum(p_ice * dt) / 1000.0)

    if rl.deploy_budget.accounting_window != "lap":
        warnings.append(
            "deploy budget accounting window is "
            f"{rl.deploy_budget.accounting_window!r}, but this function integrates one lap"
        )
    if rl.harvest_budget.accounting_window != "lap":
        warnings.append(
            "harvest budget accounting window is "
            f"{rl.harvest_budget.accounting_window!r}, but this function integrates one lap"
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        # --- DERIVED: algebra on observed channels ---
        "wheel_power_kw": p_wheel,
        "drag_kw": forces["drag_n"] * kin["speed_mps"] / 1000.0,
        "rolling_kw": forces["rolling_n"] * kin["speed_mps"] / 1000.0,
        "gradient_kw": forces["gradient_n"] * kin["speed_mps"] / 1000.0,
        # --- INFERRED: the unobservable split ---
        "ice_power_est_kw": p_ice,
        "ice_energy_est_mj": ice_energy_mj,
        "ers_deploy_power_est_kw": deploy_kw,
        "ers_deploy_power_uncertainty_kw": sigma_deploy,
        "ers_harvest_power_est_kw": harvest_kw,
        "ers_harvest_brake_power_est_kw": split["harvest_brake_kw"],
        "ers_harvest_ice_power_est_kw": split["harvest_ice_kw"],
        "harvest_spill_mj": float(np.sum(split["harvest_brake_spill_kw"] * dt) / 1000.0),
        # --- SIMULATED: three separate quantities, never collapsed (section 28) ---
        "ers_soc_est_mj": store["soc_mj"],
        "ers_soc_raw_est_mj": store["soc_raw_mj"],
        "ers_soc_uncertainty_mj": store["soc_uncertainty_mj"],
        "ers_deploy_budget_remaining_est_mj": deploy_remaining,
        "ers_deploy_budget_uncertainty_mj": frac * deploy_mj_cum,
        "ers_harvest_budget_remaining_est_mj": harvest_remaining,
        "ers_harvest_budget_uncertainty_mj": frac * harvest_mj_cum,
        "ers_energy_used_est_mj": deploy_used_mj,
        "ers_energy_harvested_est_mj": harvest_used_mj,
        "ers_energy_harvested_braking_est_mj":
            float(np.sum(split["harvest_brake_kw"] * dt) / 1000.0),
        "ers_energy_harvested_ice_est_mj":
            float(np.sum(split["harvest_ice_kw"] * dt) / 1000.0),
        # --- calibration diagnostics (section 28.1, section 29) ---
        "diagnostics": {
            "envelope": {k: v for k, v in env.items() if not isinstance(v, np.ndarray)},
            "store_deficit_mj": store["store_deficit_mj"],
            "store_spill_mj": store["store_spill_mj"],
            "store_empty_samples": store["store_empty_samples"],
            "store_full_samples": store["store_full_samples"],
            "energy_balance_mj": harvest_used_mj - deploy_used_mj,
            "deploy_budget_overrun_mj": max(0.0, -float(deploy_remaining[-1])
                                            if deploy_remaining.size else 0.0),
            "harvest_budget_overrun_mj": max(0.0, -float(harvest_remaining[-1])
                                             if harvest_remaining.size else 0.0),
        },
        "cap_normal_kw": env["cap_normal_kw"],
        "cap_override_kw": env["cap_override_kw"],
        # back-compatible alias: the headline violation count is against the most
        # permissive (override) curve, where an exceedance is unambiguously a twin defect
        "envelope_cap_violations": env["override_cap_violations"],
        "status": {
            # False as soon as the twin had to run in a degraded configuration. It is not
            # a quality score for the estimate -- an estimate can be `ok` and still fail
            # the energy balance, which is what `diagnostics` is for.
            "ok": not warnings,
            "ice_map_basis": ice_basis,
            "envelope_applied_as": "diagnostic_only",
            "deploy_estimate_is_raw": True,
            "warnings": warnings,
        },
        "provenance": {
            "wheel_power_kw": "DERIVED",
            "drag_kw": "DERIVED",
            "rolling_kw": "DERIVED",
            "gradient_kw": "DERIVED",
            "ice_power_est_kw": "INFERRED",
            "ers_deploy_power_est_kw": "INFERRED",
            "ers_harvest_power_est_kw": "INFERRED",
            "ers_soc_est_mj": "SIMULATED",
            "ers_deploy_budget_remaining_est_mj": "SIMULATED",
            "ers_harvest_budget_remaining_est_mj": "SIMULATED",
            "cap_normal_kw": "RULE",
            "cap_override_kw": "RULE",
            "envelope": f"RULE (UNVERIFIED: {', '.join(rules_mod.unverified_keys(rl))})",
        },
        "rules": {
            "configuration_version": rl.configuration_version,
            "ers_store_capacity_mj": capacity_mj,
            "ers_store_capacity_window": rl.ers_store_capacity.accounting_window,
            "deploy_budget_mj": deploy_budget_mj,
            "deploy_budget_window": rl.deploy_budget.accounting_window,
            "harvest_budget_mj": harvest_budget_mj,
            "harvest_budget_window": rl.harvest_budget.accounting_window,
            "machine_peak_kw": machine_limit_kw,
            "compliance": rules_mod.describe_compliance(rl),
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
    looks agreeable. The residual imbalance is reported WITH ITS MAGNITUDE and with what
    would be needed to close it, rather than being made to disappear.
    """
    pw = np.asarray(estimate["wheel_power_kw"], dtype=float)
    used = estimate["ers_energy_used_est_mj"]
    harvested = estimate["ers_energy_harvested_est_mj"]
    diag = estimate["diagnostics"]
    env = diag["envelope"]
    soc = np.asarray(estimate["ers_soc_est_mj"], dtype=float)
    dep_rem = np.asarray(estimate["ers_deploy_budget_remaining_est_mj"], dtype=float)
    har_rem = np.asarray(estimate["ers_harvest_budget_remaining_est_mj"], dtype=float)

    warnings: list[str] = list(estimate["status"]["warnings"])

    balance = harvested - used
    if diag["store_deficit_mj"] > 0.05:
        warnings.append(
            f"ENERGY BALANCE NOT CLOSED: the store bound had to supply "
            f"{diag['store_deficit_mj']:.2f} MJ that the model never harvested "
            f"(deploy {used:.2f} MJ vs harvest {harvested:.2f} MJ, residual "
            f"{balance:+.2f} MJ over the lap). The SOC trace below the empty bound is "
            "not meaningful. Closing this needs a calibrated ICE effective power map "
            "fitted per team (MODELS.md M15/M34) -- the deploy/ICE split is the only "
            "free quantity large enough to account for a residual of this size -- and/or "
            "a measured rear brake share. It is NOT to be closed by tuning until the "
            "plot looks agreeable (AGENTS.md 34.6)."
        )
    elif abs(balance) > 0.5 * max(used, 1e-6):
        warnings.append(
            f"energy balance residual {balance:+.2f} MJ over the lap against "
            f"{used:.2f} MJ deployed; the ICE/electrical split is approximate and the "
            "SOC trace should be read as indicative only"
        )
    if env["override_cap_violations"] > 0:
        warnings.append(
            f"{env['override_cap_violations']} samples ({env['override_cap_excess_mj']:.2f} "
            f"MJ, peak excess {env['override_cap_max_excess_kw']:.0f} kW) exceed the "
            "most permissive (override) regulation envelope. Per AGENTS.md 28.1 this is a "
            "defect in the twin -- mass, CdA, rolling resistance, drivetrain efficiency, "
            "the ICE map or the gradient -- and the estimate has deliberately NOT been "
            "truncated to the cap."
        )
    if diag["deploy_budget_overrun_mj"] > 0:
        warnings.append(
            f"deploy budget exceeded by {diag['deploy_budget_overrun_mj']:.2f} MJ in this "
            "accounting window (budget value is RULE and UNVERIFIED)"
        )
    if env["samples_with_unusable_speed"] > 0:
        warnings.append(
            f"{env['samples_with_unusable_speed']} samples had a non-finite speed and were "
            "excluded from the envelope diagnostics rather than filled in"
        )

    return {
        "peakWheelPowerKw": round(float(np.nanpercentile(pw, 99)), 1),
        "peakBrakingKw": round(float(np.nanpercentile(pw, 1)), 1),
        "rawMaxWheelPowerKw": round(float(np.nanmax(pw)), 1),
        "iceEnergyMj": round(estimate["ice_energy_est_mj"], 2),
        "ersEnergyUsedMj": round(used, 2),
        "ersEnergyHarvestedMj": round(harvested, 2),
        "ersEnergyHarvestedBrakingMj": round(estimate["ers_energy_harvested_braking_est_mj"], 2),
        "ersEnergyHarvestedIceMj": round(estimate["ers_energy_harvested_ice_est_mj"], 2),
        "energyBalanceMj": round(balance, 2),
        "socEndMj": round(float(soc[-1]), 2) if soc.size else None,
        "socMinMj": round(float(np.min(soc)), 2) if soc.size else None,
        "socMaxMj": round(float(np.max(soc)), 2) if soc.size else None,
        "socUncertaintyMj": round(float(estimate["ers_soc_uncertainty_mj"][-1]), 2),
        "storeDeficitMj": round(diag["store_deficit_mj"], 2),
        "storeSpillMj": round(diag["store_spill_mj"], 2),
        "deployBudgetRemainingMj": round(float(dep_rem[-1]), 2) if dep_rem.size else None,
        "harvestBudgetRemainingMj": round(float(har_rem[-1]), 2) if har_rem.size else None,
        "envelopeCapViolations": env["override_cap_violations"],
        "envelopeCapExcessMj": round(env["override_cap_excess_mj"], 3),
        "envelopeNormalCapViolations": env["normal_cap_violations"],
        "overrideEvidenceSamples": env["override_evidence_samples"],
        "iceMapBasis": estimate["status"]["ice_map_basis"],
        "warnings": warnings,
    }
