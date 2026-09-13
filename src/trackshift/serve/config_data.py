"""
Live reader for config/*.yaml, exposed by app.py under /internal/config/*.

Not part of API.md's public contract — API.md names no route for listing
system configuration. This exists to back the frontend's /config explorer
(frontend/src/config-explorer/) with real values instead of a hand-transcribed
snapshot. Labels, units, and "affects" annotations are curated here (the same
ones frontend/src/config-explorer/data.ts carries); every `value` is read live
off disk on each request — nothing here is a cached snapshot.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


# Cached per-process; config/*.yaml changes require a server restart to pick up,
# same tradeoff every other loader in this codebase makes (rules/config.py,
# data/registry.py both @lru_cache too).
@functools.lru_cache(maxsize=None)
def _common() -> dict:
    return _read_yaml(CONFIG_DIR / "rules" / "2026" / "common.yaml")


@functools.lru_cache(maxsize=None)
def _priors() -> dict:
    return _read_yaml(CONFIG_DIR / "physics" / "priors.yaml")


@functools.lru_cache(maxsize=None)
def _dp_development() -> dict:
    return _read_yaml(CONFIG_DIR / "value" / "dp_development.yaml")


@functools.lru_cache(maxsize=None)
def _state_discretization() -> dict:
    return _read_yaml(CONFIG_DIR / "state_discretization.yaml")


@functools.lru_cache(maxsize=None)
def _baselines() -> dict:
    return _read_yaml(CONFIG_DIR / "baselines.yaml")


@functools.lru_cache(maxsize=None)
def _lap_classification() -> dict:
    return _read_yaml(CONFIG_DIR / "lap_classification.yaml")


@functools.lru_cache(maxsize=None)
def _tyre_pace() -> dict:
    return _read_yaml(CONFIG_DIR / "tyre_pace.yaml")


def _leaf(node: Any, default_source: str) -> dict:
    """A leaf that carries its own {value, value_source, source, note}."""
    if isinstance(node, dict) and "value" in node:
        return {
            "value": node.get("value"),
            "valueSource": node.get("value_source", "UNVERIFIED"),
            "source": node.get("source", default_source),
            "note": node.get("note"),
        }
    return {"value": node, "valueSource": "UNVERIFIED", "source": default_source, "note": None}


def _curve_leaf(node: Any, key: str, default_source: str) -> dict:
    """A curve dict (breakpoints_kmh/max_power_kw living beside value_source/source/note)."""
    if not isinstance(node, dict):
        return {"value": None, "valueSource": "UNVERIFIED", "source": default_source, "note": None}
    return {
        "value": node.get(key),
        "valueSource": node.get("value_source", "UNVERIFIED"),
        "source": node.get("source", default_source),
        "note": node.get("note"),
    }


def _var(id_: str, label: str, file: str, path: str, leaf: dict, *, unit: str | None = None,
         affects: list[str], input_kind: str, min_: float | None = None, max_: float | None = None,
         step: float | None = None) -> dict:
    return {
        "id": id_,
        "label": label,
        "file": file,
        "path": path,
        "value": leaf["value"],
        "unit": unit,
        "valueSource": leaf["valueSource"],
        "source": leaf["source"],
        "note": leaf["note"],
        "affects": affects,
        "inputKind": input_kind,
        "min": min_,
        "max": max_,
        "step": step,
    }


def build_variables() -> list[dict]:
    """One category per config/*.yaml family, values read live from disk."""
    common = _common()
    priors = _priors()
    dp = _dp_development()
    disc = _state_discretization()
    baselines = _baselines()
    lapcls = _lap_classification()
    tyre = _tyre_pace()

    RULES_FILE = "config/rules/2026/common.yaml"
    PRIORS_FILE = "config/physics/priors.yaml"
    DP_FILE = "config/value/dp_development.yaml"
    DISC_FILE = "config/state_discretization.yaml"
    BASE_FILE = "config/baselines.yaml"
    LAP_FILE = "config/lap_classification.yaml"
    TYRE_FILE = "config/tyre_pace.yaml"

    overtake = common.get("overtake", {}) or {}
    envelope = overtake.get("power_envelope", {}) or {}
    energy = common.get("energy", {}) or {}
    race_control = common.get("race_control", {}) or {}

    categories = [
        {
            "id": "overtake-regulation",
            "title": "Overtake & regulation thresholds",
            "description": "The 2026 Overtake state machine and legal action set (M18 -> M20 -> M19 -> M21). External regulation, never a learned parameter — the rule engine refuses to start against an UNVERIFIED value once strict_mode is true.",
            "variables": [
                _var("detection_gap_s", "Detection Line gap threshold", RULES_FILE, "overtake.detection_gap_s",
                     _leaf(overtake.get("detection_gap_s"), "common.yaml"), unit="s",
                     affects=["M20 Overtake state machine", "M07 opportunity dataset", "POST /rules/eligibility"],
                     input_kind="number", min_=0, max_=3, step=0.05),
                _var("power_envelope_normal_breakpoints", "Normal-mode power envelope — breakpoints", RULES_FILE,
                     "overtake.power_envelope.normal.breakpoints_kmh",
                     _curve_leaf(envelope.get("normal"), "breakpoints_kmh", "common.yaml"), unit="km/h",
                     affects=["M19 max_electrical_power_kw", "M22 DP action space", "GET /rules/{event}/power_envelope"],
                     input_kind="list-number"),
                _var("power_envelope_normal_power", "Normal-mode power envelope — max power", RULES_FILE,
                     "overtake.power_envelope.normal.max_power_kw",
                     _curve_leaf(envelope.get("normal"), "max_power_kw", "common.yaml"), unit="kW",
                     affects=["M19 max_electrical_power_kw", "M22 DP action space"], input_kind="list-number"),
                _var("power_envelope_override_breakpoints", "Override-mode power envelope — breakpoints", RULES_FILE,
                     "overtake.power_envelope.override.breakpoints_kmh",
                     _curve_leaf(envelope.get("override"), "breakpoints_kmh", "common.yaml"), unit="km/h",
                     affects=["M19 max_electrical_power_kw", "M35 override discriminator"], input_kind="list-number"),
                _var("power_envelope_override_power", "Override-mode power envelope — max power", RULES_FILE,
                     "overtake.power_envelope.override.max_power_kw",
                     _curve_leaf(envelope.get("override"), "max_power_kw", "common.yaml"), unit="kW",
                     affects=["M19 max_electrical_power_kw", "M35 override discriminator"], input_kind="list-number"),
                _var("separation_speed_kmh", "Envelope separation speed", RULES_FILE,
                     "overtake.power_envelope.separation_speed_kmh",
                     _leaf(envelope.get("separation_speed_kmh"), "common.yaml"), unit="km/h",
                     affects=["M35 override / ERS-mode discriminator", "ErsState.discriminable"],
                     input_kind="number", min_=0, max_=400, step=5),
                _var("energy_deploy_limit_per_lap_mj", "Deploy limit per lap", RULES_FILE,
                     "energy.deploy_limit_per_lap_mj", _leaf(energy.get("deploy_limit_per_lap_mj"), "common.yaml"),
                     unit="MJ", affects=["M19 legal_actions", "C5 energy twin accounting"], input_kind="number",
                     min_=0, max_=10, step=0.1),
                _var("energy_harvest_limit_per_lap_mj", "Harvest limit per lap", RULES_FILE,
                     "energy.harvest_limit_per_lap_mj", _leaf(energy.get("harvest_limit_per_lap_mj"), "common.yaml"),
                     unit="MJ", affects=["M19 legal_actions", "C5 energy twin accounting"], input_kind="number",
                     min_=0, max_=10, step=0.1),
                _var("energy_store_capacity_mj", "Energy Store capacity", RULES_FILE, "energy.store_capacity_mj",
                     _leaf(energy.get("store_capacity_mj"), "common.yaml"), unit="MJ",
                     affects=["C5 twin (ers_store_capacity_mj)", "ErsState.ers_soc_est_mj bound"],
                     input_kind="number", min_=0, max_=10, step=0.1),
                _var("overtake_disabled_conditions", "Conditions that disable Overtake", RULES_FILE,
                     "race_control.overtake_disabled_conditions",
                     _leaf(race_control.get("overtake_disabled_conditions"), "common.yaml"),
                     affects=["M02/C7 normal_race_model_eligible gate", "race_control.overtake_disabled"],
                     input_kind="list-string"),
                _var("strict_mode", "Strict mode", RULES_FILE, "strict_mode",
                     {"value": common.get("strict_mode", False), "valueSource": "OBSERVED",
                      "source": "operational switch, set by whoever runs the build",
                      "note": "Set true before any demo claim. The rule engine then refuses to run against UNVERIFIED or PROXY_* values."},
                     affects=["CP-11 rule engine start guard", "every §57/§58 legality claim"], input_kind="boolean"),
            ],
        },
        {
            "id": "physics-priors",
            "title": "Physics priors (energy twin)",
            "description": "Starting values for the longitudinal power-balance model (M14). CP-20 fits all five calibration rungs against these.",
            "variables": [
                _var("chassis_minimum_kg", "Chassis minimum mass", PRIORS_FILE, "mass.chassis_minimum_kg",
                     _leaf(priors.get("mass", {}).get("chassis_minimum_kg"), "priors.yaml"), unit="kg",
                     affects=["M14 energy twin", "M15 physics calibration hierarchy"], input_kind="number",
                     min_=600, max_=1000, step=5),
                _var("cda_m2", "Aerodynamic drag area (CdA), default", PRIORS_FILE, "aerodynamics.cda_m2",
                     _leaf(priors.get("aerodynamics", {}).get("cda_m2"), "priors.yaml"), unit="m²",
                     affects=["M14 energy twin", "M35 override discriminator false-positive rate"],
                     input_kind="number", min_=0.8, max_=1.6, step=0.01),
                _var("cda_low_drag_m2", "CdA — low-drag configuration", PRIORS_FILE, "aerodynamics.cda_low_drag_m2",
                     _leaf(priors.get("aerodynamics", {}).get("cda_low_drag_m2"), "priors.yaml"), unit="m²",
                     affects=["M14 energy twin, per-circuit calibration"], input_kind="number", min_=0.8, max_=1.4,
                     step=0.01),
                _var("cda_high_downforce_m2", "CdA — high-downforce configuration", PRIORS_FILE,
                     "aerodynamics.cda_high_downforce_m2",
                     _leaf(priors.get("aerodynamics", {}).get("cda_high_downforce_m2"), "priors.yaml"), unit="m²",
                     affects=["M14 energy twin, per-circuit calibration"], input_kind="number", min_=1.0, max_=1.8,
                     step=0.01),
                _var("crr", "Rolling resistance coefficient", PRIORS_FILE, "rolling.crr",
                     _leaf(priors.get("rolling", {}).get("crr"), "priors.yaml"), affects=["M14 energy twin"],
                     input_kind="number", min_=0.005, max_=0.03, step=0.001),
                _var("eta_drivetrain", "Drivetrain efficiency", PRIORS_FILE, "drivetrain.eta_drivetrain",
                     _leaf(priors.get("drivetrain", {}).get("eta_drivetrain"), "priors.yaml"),
                     affects=["M14 energy twin"], input_kind="number", min_=0.7, max_=1.0, step=0.01),
                _var("eta_deploy", "Deploy efficiency (Energy Store -> wheel)", PRIORS_FILE, "drivetrain.eta_deploy",
                     _leaf(priors.get("drivetrain", {}).get("eta_deploy"), "priors.yaml"),
                     affects=["C5 ers_deploy_power_est_kw"], input_kind="number", min_=0.7, max_=1.0, step=0.01),
                _var("eta_harvest", "Harvest efficiency (wheel -> Energy Store)", PRIORS_FILE,
                     "drivetrain.eta_harvest", _leaf(priors.get("drivetrain", {}).get("eta_harvest"), "priors.yaml"),
                     affects=["C5 ers_harvest_power_est_kw"], input_kind="number", min_=0.7, max_=1.0, step=0.01),
                _var("p_ice_max_kw", "ICE maximum power", PRIORS_FILE, "engine.p_ice_max_kw",
                     _leaf(priors.get("engine", {}).get("p_ice_max_kw"), "priors.yaml"), unit="kW",
                     affects=["M14 energy twin power balance"], input_kind="number", min_=300, max_=550, step=5),
                _var("gravity_mps2", "Gravitational acceleration", PRIORS_FILE, "environment.gravity_mps2",
                     _leaf(priors.get("environment", {}).get("gravity_mps2"), "priors.yaml"), unit="m/s²",
                     affects=["M14 energy twin"], input_kind="number", min_=9.7, max_=9.9, step=0.00001),
                _var("air_density_fallback_kgm3", "Air density fallback", PRIORS_FILE,
                     "environment.air_density_fallback_kgm3",
                     _leaf(priors.get("environment", {}).get("air_density_fallback_kgm3"), "priors.yaml"),
                     unit="kg/m³", affects=["M14 energy twin", "M33 weather-adjusted physics"], input_kind="number",
                     min_=1.0, max_=1.3, step=0.005),
            ],
        },
        {
            "id": "planner-dp",
            "title": "Planner / dynamic-programming value function",
            "description": "The development-only abstract-utility grid the DP (M22) and beam planner (M24) search over. Not a calibrated time objective.",
            "variables": [
                _var("energy_grid_mj_range", "Energy grid range", DP_FILE, "energy_grid_mj",
                     {"value": [min(dp.get("energy_grid_mj", [0])), max(dp.get("energy_grid_mj", [0]))],
                      "valueSource": "DEVELOPMENT_ONLY", "source": "dp_development.yaml", "note": None},
                     unit=f"MJ ({len(dp.get('energy_grid_mj', []))} points)",
                     affects=["M22 DP state grid", "GET /value/{event}/shadow_price grid.energy_kj"],
                     input_kind="list-number"),
                _var("gap_grid_s_range", "Gap grid range", DP_FILE, "gap_grid_s",
                     {"value": [min(dp.get("gap_grid_s", [0])), max(dp.get("gap_grid_s", [0]))],
                      "valueSource": "DEVELOPMENT_ONLY", "source": "dp_development.yaml", "note": None},
                     unit=f"s ({len(dp.get('gap_grid_s', []))} points)",
                     affects=["M22 DP state grid", "GET /value/{event}/shadow_price grid.gap_s"],
                     input_kind="list-number"),
                _var("eligibility_states", "Eligibility states", DP_FILE, "eligibility_states",
                     {"value": dp.get("eligibility_states"), "valueSource": "DEVELOPMENT_ONLY",
                      "source": "dp_development.yaml", "note": None}, affects=["M22 DP state grid"],
                     input_kind="list-string"),
                _var("terminal_ahead_value", "Terminal utility — ahead value", DP_FILE,
                     "terminal_utility.ahead_value",
                     {"value": dp.get("terminal_utility", {}).get("ahead_value"), "valueSource": "DEVELOPMENT_ONLY",
                      "source": "dp_development.yaml", "note": None}, unit="abstract utility",
                     affects=["M22 terminal value function", "M24 planner objective"], input_kind="number", min_=0,
                     max_=5, step=0.1),
                _var("terminal_residual_energy_value", "Terminal utility — residual energy value", DP_FILE,
                     "terminal_utility.residual_energy_value_per_mj",
                     {"value": dp.get("terminal_utility", {}).get("residual_energy_value_per_mj"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "dp_development.yaml", "note": None},
                     unit="utility / MJ", affects=["M22 terminal value function"], input_kind="number", min_=0, max_=1,
                     step=0.005),
                _var("shadow_finite_difference_delta_mj", "Shadow-price finite-difference step", DP_FILE,
                     "shadow_finite_difference_delta_mj",
                     {"value": dp.get("shadow_finite_difference_delta_mj"), "valueSource": "DEVELOPMENT_ONLY",
                      "source": "dp_development.yaml",
                      "note": "The step M22 uses to numerically differentiate V(state) into lambda_E."},
                     unit="MJ", affects=["M22 shadow price λ_E"], input_kind="number", min_=0.01, max_=0.5,
                     step=0.01),
            ],
        },
        {
            "id": "state-discretization",
            "title": "Strategic-state discretization",
            "description": "The shared bin edges every consumer of StrategicState (rule engine, DP, planner, simulator) must agree on.",
            "variables": [
                _var(f"disc_{dim}", label, DISC_FILE, f"dimensions.{dim}.edges",
                     {"value": disc.get("dimensions", {}).get(dim, {}).get("edges"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "state_discretization.yaml", "note": None},
                     unit=disc.get("dimensions", {}).get(dim, {}).get("unit"), affects=affects,
                     input_kind="list-number")
                for dim, label, affects in [
                    ("energy_state_mj", "Energy state bin edges", ["C3-C6 StrategicState binning", "M22 DP grid"]),
                    ("tyre_state", "Tyre state bin edges", ["C3-C6 StrategicState binning"]),
                    ("time_gap_s", "Time gap bin edges", ["C3-C6 StrategicState binning", "M22 DP grid"]),
                    ("distance_gap_m", "Distance gap bin edges", ["C3-C6 StrategicState binning"]),
                    ("relative_speed_mps", "Relative speed bin edges", ["C3-C6 StrategicState binning"]),
                    ("gap_rate_s_per_s", "Gap-rate bin edges", ["C3-C6 StrategicState binning"]),
                    ("horizon", "Horizon bin edges", ["M24 planner beam depth"]),
                ]
            ] + [
                _var(f"disc_{dim}_categories", label, DISC_FILE, f"dimensions.{dim}.categories",
                     {"value": disc.get("dimensions", {}).get(dim, {}).get("categories"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "state_discretization.yaml", "note": None},
                     affects=affects, input_kind="list-string")
                for dim, label, affects in [
                    ("overtake_state", "Overtake-state categories", ["M20 Overtake state machine"]),
                    ("rival_belief", "Rival-belief categories", ["M09 rival-state model", "POST /rival/state"]),
                ]
            ],
        },
        {
            "id": "baselines",
            "title": "Driver / team / field baselines",
            "description": "Minimum sample thresholds before a segment baseline is trusted (M04). Below the threshold, baseline_valid is false.",
            "variables": [
                _var(f"baseline_min_samples_{k}", f"Minimum samples — {k} baseline", BASE_FILE,
                     f"minimum_samples.{k}",
                     {"value": baselines.get("minimum_samples", {}).get(k), "valueSource": "DEVELOPMENT_ONLY",
                      "source": "baselines.yaml", "note": None}, unit="laps",
                     affects=["M04 baseline_valid"] + (["timeline.baseline_residuals.attacker"] if k == "driver" else []),
                     input_kind="number", min_=1, max_=1000, step=1)
                for k in ("driver", "team", "field")
            ],
        },
        {
            "id": "lap-classification",
            "title": "Practice-lap classification",
            "description": "M01's causal thresholds for labelling a Practice 1 lap PUSH / LONG_RUN / RACE_PACE / COOLDOWN.",
            "variables": [
                _var("push_session_best_ratio_max", "Push — session-best ratio ceiling", LAP_FILE,
                     "thresholds.push_session_best_ratio_max",
                     {"value": lapcls.get("thresholds", {}).get("push_session_best_ratio_max"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "lap_classification.yaml",
                      "note": "CP-07 permits this one-percent relaxation after diagnosing UNKNOWN > 30%."},
                     affects=["M01 practice lap classifier", "M15 physics calibration (Practice-only laps)"],
                     input_kind="number", min_=1.0, max_=1.3, step=0.01),
                _var("push_tyre_life_laps_max", "Push — max tyre life", LAP_FILE,
                     "thresholds.push_tyre_life_laps_max",
                     {"value": lapcls.get("thresholds", {}).get("push_tyre_life_laps_max"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "lap_classification.yaml", "note": None},
                     unit="laps", affects=["M01 practice lap classifier"], input_kind="number", min_=1, max_=15,
                     step=1),
                _var("cooldown_session_best_ratio_min_exclusive", "Cooldown — session-best ratio floor (exclusive)",
                     LAP_FILE, "thresholds.cooldown_session_best_ratio_min_exclusive",
                     {"value": lapcls.get("thresholds", {}).get("cooldown_session_best_ratio_min_exclusive"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "lap_classification.yaml", "note": None},
                     affects=["M01 practice lap classifier"], input_kind="number", min_=1.0, max_=1.5, step=0.01),
                _var("long_run_min_consecutive_laps", "Long-run — minimum consecutive laps", LAP_FILE,
                     "thresholds.long_run_min_consecutive_laps",
                     {"value": lapcls.get("thresholds", {}).get("long_run_min_consecutive_laps"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "lap_classification.yaml", "note": None},
                     unit="laps", affects=["M01 practice lap classifier"], input_kind="number", min_=2, max_=10,
                     step=1),
                _var("long_run_lap_time_spread_ratio_max_exclusive", "Long-run — lap-time spread ceiling (exclusive)",
                     LAP_FILE, "thresholds.long_run_lap_time_spread_ratio_max_exclusive",
                     {"value": lapcls.get("thresholds", {}).get("long_run_lap_time_spread_ratio_max_exclusive"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "lap_classification.yaml", "note": None},
                     affects=["M01 practice lap classifier"], input_kind="number", min_=0.005, max_=0.1, step=0.005),
                _var("max_unknown_bridge_laps", "Max UNKNOWN bridge laps", LAP_FILE,
                     "long_run.max_unknown_bridge_laps",
                     {"value": lapcls.get("long_run", {}).get("max_unknown_bridge_laps"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "lap_classification.yaml",
                      "note": "The one permitted UNKNOWN bridge inside a candidate long run."},
                     unit="laps", affects=["M01 practice lap classifier"], input_kind="number", min_=0, max_=3,
                     step=1),
            ],
        },
        {
            "id": "tyre-pace",
            "title": "Tyre-pace context",
            "description": "M30's causal degradation proxy and normalised pace windows — a comparative context, not a physical tyre sensor.",
            "variables": [
                _var("initial_baseline_retained_laps", "Initial baseline — retained laps", TYRE_FILE,
                     "proxy.initial_baseline_retained_laps",
                     {"value": tyre.get("proxy", {}).get("initial_baseline_retained_laps"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "tyre_pace.yaml", "note": None}, unit="laps",
                     affects=["M30 tyre_degradation_proxy"], input_kind="number", min_=1, max_=10, step=1),
                _var("rolling_retained_laps", "Rolling baseline — retained laps", TYRE_FILE,
                     "proxy.rolling_retained_laps",
                     {"value": tyre.get("proxy", {}).get("rolling_retained_laps"), "valueSource": "DEVELOPMENT_ONLY",
                      "source": "tyre_pace.yaml", "note": None}, unit="laps",
                     affects=["M30 tyre_degradation_proxy"], input_kind="number", min_=1, max_=15, step=1),
                _var("normalised_pace_min_prior_samples", "Normalised pace — minimum prior samples", TYRE_FILE,
                     "normalised_pace.minimum_prior_samples",
                     {"value": tyre.get("normalised_pace", {}).get("minimum_prior_samples"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "tyre_pace.yaml", "note": None}, unit="laps",
                     affects=["M30 normalised tyre pace", "M06 pairwise tyre features"], input_kind="number", min_=1,
                     max_=10, step=1),
                _var("similar_tyre_life_laps", "Similar tyre-life window", TYRE_FILE,
                     "normalised_pace.similar_tyre_life_laps",
                     {"value": tyre.get("normalised_pace", {}).get("similar_tyre_life_laps"),
                      "valueSource": "DEVELOPMENT_ONLY", "source": "tyre_pace.yaml", "note": None}, unit="laps",
                     affects=["M30 normalised tyre pace"], input_kind="number", min_=1, max_=10, step=1),
            ],
        },
    ]
    return categories


def build_events() -> list[dict]:
    """One row per config/rules/2026/<event>.yaml, read live."""
    events_dir = CONFIG_DIR / "rules" / "2026"
    rows = []
    for path in sorted(events_dir.glob("*.yaml")):
        if path.stem == "common":
            continue
        data = _read_yaml(path)
        lap_length = data.get("lap_length_m", {}) or {}
        geometry = data.get("geometry", {}) or {}
        overtake = data.get("overtake", {}) or {}
        detection = overtake.get("detection_line_m", {}) or {}
        rc = data.get("race_control", {}) or {}
        rows.append({
            "circuit": data.get("circuit"),
            "event": data.get("event"),
            "eventDisplay": data.get("event_display"),
            "completeInMirror": bool(data.get("complete_in_mirror", False)),
            "lapLengthM": lap_length.get("value"),
            "publishedLengthM": lap_length.get("published_circuit_length_m"),
            "deltaVsPublishedPct": lap_length.get("delta_vs_published_pct"),
            "cornerCount": geometry.get("corner_count"),
            "rotationDeg": geometry.get("rotation_deg"),
            "detectionLineM": detection.get("value"),
            "overtakeEnabledMessages": rc.get("overtake_enabled_messages"),
            "overtakeDisabledMessages": rc.get("overtake_disabled_messages"),
        })
    return rows
