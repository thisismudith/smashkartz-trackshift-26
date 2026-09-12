#!/usr/bin/env python3
"""Fit the physics calibration hierarchy (M15, CP-20).

All five rungs of section 29, fitted and compared on the same held-out split so
that "the model got better" is a claim with evidence behind it. A rung is kept
only if it improves held-out MAE (section 34), and a rung that improves MAE
while sitting on a physical bound or breaking a constraint is rejected whatever
its score.

Bounds are physical constraints, not tuning knobs. A fit that runs to a bound is
telling you the model is missing something -- downforce-induced drag, an aero
state -- and the fix is to add the term, never to widen the bound.

Training data is 2026 Practice 1 clean-air laps: PUSH, LONG_RUN and RACE_PACE
from CP-07, plus Qualifying for the high-performance envelope. The section 29
controls are joined from CP-06 weather, CP-08 tyre pace, CP-19 fuel, CP-10
overtake state and C7 race context; every control that is absent is reported
rather than silently defaulted.

Usage:
    python scripts/train/calibrate_physics.py --year 2026
    python scripts/train/calibrate_physics.py --year 2026 --jobs 8
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.twin.api import (  # noqa: E402
    MAE_TARGETS,
    PARAMETER_BOUNDS,
    PARAMETER_NAMES,
    PhysicsParameters,
    RungResult,
    compare_rungs,
    error_by_group,
    fit_parameters,
    mean_absolute_error,
    residual_share,
    root_mean_square_error,
)
from trackshift.twin.forward import (  # noqa: E402
    predict_segment_time_grip_or_power as predict_segment_time_s,
)

SEGMENTS = ROOT / "data" / "processed" / "segments"
WEATHER = ROOT / "data" / "processed" / "weather_overlay"
TWIN = ROOT / "data" / "processed" / "energy_twin"
FUEL = ROOT / "data" / "processed" / "fuel_curves"
CLASSES = ROOT / "data" / "processed" / "practice_lap_classes"
BASELINES = ROOT / "data" / "processed" / "field_segment_baselines"
OVERTAKE = ROOT / "data" / "processed" / "overtake_state"
RACE_CONTEXT = ROOT / "data" / "processed" / "race_context"
PRIORS = ROOT / "config" / "physics" / "priors.yaml"
OUT = ROOT / "artifacts" / "models" / "twin"
REPORT = ROOT / "artifacts" / "validation"

#: CP-07 labels that mark a clean-air lap usable for calibration (section 9).
CLEAN_CLASSES = ("PUSH", "LONG_RUN", "RACE_PACE")

#: Section 29: a (team, event) cell needs this many clean laps or rung 4 falls
#: back to the team rung and records the fallback.
MIN_CLEAN_LAPS_PER_CELL = 30

KEY = ["year", "event", "session", "driver", "lap"]


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def load_priors() -> dict[str, float]:
    import yaml

    raw = yaml.safe_load(PRIORS.read_text(encoding="utf-8"))

    def value(*path):
        node = raw
        for part in path:
            node = node[part]
        return float(node["value"])

    return {
        "mass_kg": value("mass", "chassis_minimum_kg"),
        "cda_m2": value("aerodynamics", "cda_m2"),
        "crr": value("rolling", "crr"),
        "eta_drivetrain": value("drivetrain", "eta_drivetrain"),
        "eta_deploy": value("drivetrain", "eta_deploy"),
        "eta_harvest": value("drivetrain", "eta_harvest"),
        "p_ice_max_kw": value("engine", "p_ice_max_kw"),
        "gravity_mps2": value("environment", "gravity_mps2"),
        "fallback_rho": value("environment", "air_density_fallback_kgm3"),
    }


def load_training_frame(year: str, sessions: tuple[str, ...]) -> tuple[Any, dict[str, Any]]:
    """Segments joined to every section 29 control that exists on disk."""
    import pandas as pd

    parts = []
    for path in sorted(SEGMENTS.glob("circuit=*/segments.parquet")):
        circuit = path.parent.name.removeprefix("circuit=")
        frame = pd.read_parquet(path)
        frame = frame[frame["year"].astype(str) == str(year)]
        frame = frame[frame["session"].isin(sessions)]
        if frame.empty:
            continue
        frame["circuit"] = circuit
        parts.append(frame)
    if not parts:
        raise SystemExit(f"no {year} segments for sessions {sessions}; run build_segments.py first")
    frame = pd.concat(parts, ignore_index=True)

    joined: dict[str, Any] = {"segments_rows": len(frame)}

    def join(frame, name: str, root: Path, filename: str, columns: list[str]):
        """Left-join one control set, recording whether it was actually there."""
        files = sorted(q for q in root.rglob(filename) if "circuit=" in str(q))
        if not files:
            joined[name] = "MISSING"
            return frame
        other = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        keys = [k for k in KEY + ["segment_id"] if k in frame.columns and k in other.columns]
        carry = [c for c in columns if c in other.columns]
        if not keys or not carry:
            joined[name] = "NO_SHARED_COLUMNS"
            return frame
        merged = frame.merge(other[keys + carry].drop_duplicates(keys), on=keys, how="left")
        coverage = float(merged[carry[0]].notna().mean())
        joined[name] = {"keys": keys, "columns": carry, "coverage": round(coverage, 4)}
        return merged

    frame = join(frame, "weather", WEATHER, "weather_overlay.parquet",
                 ["air_density_proxy", "wind_head_component_mps", "wind_cross_component_mps",
                  "track_temperature", "wet_track_flag"])
    frame = join(frame, "energy_twin", TWIN, "energy_twin.parquet",
                 ["ers_deploy_power_est_kw"])
    frame = join(frame, "fuel", FUEL, "fuel_curves.parquet", ["fuel_load_kg_est"])

    # C2 field baseline: the segment's speed ceiling as a property of the track.
    baseline_path = BASELINES / "field_segment_baselines.parquet"
    if baseline_path.exists():
        base = pd.read_parquet(baseline_path)
        base = base[["circuit", "segment_id", "max_speed_kmh_median",
                     "segment_time_s_median", "exit_speed_kmh_median"]].dropna()
        base = base.rename(columns={"max_speed_kmh_median": "segment_speed_ceiling_kmh",
                                    "segment_time_s_median": "segment_baseline_time_s",
                                    "exit_speed_kmh_median": "segment_exit_speed_ref_kmh"})
        base = base.drop_duplicates(["circuit", "segment_id"])
        frame = frame.merge(base, on=["circuit", "segment_id"], how="left")
        joined["c2_speed_ceiling"] = {
            "coverage": round(float(frame["segment_speed_ceiling_kmh"].notna().mean()), 4)}
    else:
        joined["c2_speed_ceiling"] = "MISSING"

    # Section 29 lists aero/Overtake state and normal_race_model_eligible among
    # the required controls. Both lakes are built for Race only, so neither
    # exists for the Practice 1 + Qualifying sessions this rung calibrates on.
    # Report that rather than omitting the columns quietly: a control that is
    # absent is a known hole in the fit, and the manifest is where it belongs.
    def sessions_covered(root: Path, filename: str) -> set[str]:
        covered: set[str] = set()
        for path in root.rglob(filename):
            part = next((x for x in path.parts if x.startswith("session=")), None)
            if part:
                covered.add(part.removeprefix("session=").replace("_", " "))
                continue
            try:
                other = pd.read_parquet(path, columns=["session"])
            except (OSError, ValueError, KeyError):
                continue
            covered.update(other["session"].astype(str).unique().tolist())
        return covered

    for name, root, filename, note in (
        ("aero_overtake_state", OVERTAKE, "overtake_state.parquet",
         "Section 29 requires an aero/Overtake-state control. Without it the fit "
         "cannot tell a DRS-open straight from a closed one, which is the leading "
         "candidate for the missing physics driving CdA to its lower bound."),
        ("normal_race_model_eligible", RACE_CONTEXT, "segments.parquet",
         "Section 12 requires this filter. It is a race concept and the lake is "
         "built for Race only, so it neither exists nor strictly applies here; "
         "recorded so the gap is visible rather than assumed away."),
    ):
        covered = sessions_covered(root, filename)
        missing_for = sorted(set(sessions) - covered)
        joined[name] = "PRESENT" if not missing_for else {
            "status": "MISSING",
            "sessions_without_it": missing_for,
            "sessions_available": sorted(covered),
            "note": note,
        }

    # CP-07 clean-air labels are lap-level, not segment-level.
    class_files = sorted(CLASSES.glob("**/*.parquet"))
    if class_files:
        classes = pd.concat([pd.read_parquet(f) for f in class_files], ignore_index=True)
        keys = [k for k in KEY if k in frame.columns and k in classes.columns]
        frame = frame.merge(classes[keys + ["practice_lap_class"]].drop_duplicates(keys),
                            on=keys, how="left")
        joined["practice_lap_classes"] = f"joined on {keys}"
    else:
        joined["practice_lap_classes"] = "MISSING"
    return frame, joined


def select_clean(frame, sessions: tuple[str, ...]) -> tuple[Any, dict[str, Any]]:
    """Clean-air rows only, with the reason for every exclusion recorded."""
    import pandas as pd

    before = len(frame)
    reasons: dict[str, int] = {}

    target = pd.to_numeric(frame.get("segment_time_s_offline"), errors="coerce")
    entry = pd.to_numeric(frame.get("entry_speed_kmh"), errors="coerce")
    length = pd.to_numeric(frame.get("segment_length_m"), errors="coerce")
    usable = target.notna() & (target > 0) & entry.notna() & (entry > 0) & length.notna() & (length > 0)
    reasons["MISSING_TARGET_OR_GEOMETRY"] = int((~usable).sum())
    frame = frame[usable].copy()

    # Practice rows must carry a clean-air label; Qualifying is admitted for the
    # high-performance envelope and has no practice label by definition.
    if "practice_lap_class" in frame.columns:
        is_practice = frame["session"].astype(str).str.startswith("Practice")
        keep = (~is_practice) | frame["practice_lap_class"].isin(CLEAN_CLASSES)
        reasons["PRACTICE_LAP_NOT_CLEAN_AIR"] = int((~keep).sum())
        frame = frame[keep].copy()

    if "is_accurate" in frame.columns:
        bad = frame["is_accurate"].astype(str).str.lower().isin(["false", "0"])
        reasons["INACCURATE_TIMING"] = int(bad.sum())
        frame = frame[~bad].copy()

    # British GP is the frozen final test and never enters calibration.
    held_out = frame["event"].astype(str).str.contains("British", case=False, na=False)
    reasons["BRITISH_GP_HELD_OUT"] = int(held_out.sum())
    frame = frame[~held_out].copy()

    return frame, {"rows_before": before, "rows_after": len(frame), "excluded_by_reason": reasons}


def _residual_fn(rows: list[dict[str, Any]], priors: dict[str, float], observed):
    """Residual closure over one cell's rows for scipy.least_squares."""
    import numpy as np

    def residual(x):
        parameters = PhysicsParameters(
            mass_kg=priors["mass_kg"], cda_m2=x[0], crr=x[1],
            eta_drivetrain=x[2], p_ice_max_kw=x[3],
            eta_deploy=priors["eta_deploy"], eta_harvest=priors["eta_harvest"],
            gravity_mps2=priors["gravity_mps2"],
        )
        predicted = np.array([
            predict_segment_time_s(row, parameters, fallback_rho_kgm3=priors["fallback_rho"])
            for row in rows
        ])
        return predicted - observed

    return residual


def _predict(rows: list[dict[str, Any]], parameters: PhysicsParameters, priors) -> list[float]:
    return [predict_segment_time_s(r, parameters, fallback_rho_kgm3=priors["fallback_rho"])
            for r in rows]


def _params_from(values: dict[str, float], priors: dict[str, float]) -> PhysicsParameters:
    return PhysicsParameters(
        mass_kg=priors["mass_kg"] + 0.0,
        cda_m2=values["cda_m2"], crr=values["crr"],
        eta_drivetrain=values["eta_drivetrain"], p_ice_max_kw=values["p_ice_max_kw"],
        eta_deploy=priors["eta_deploy"], eta_harvest=priors["eta_harvest"],
        gravity_mps2=priors["gravity_mps2"],
    )



def envelope_violation_rate(rows, parameters, priors, rules_cache) -> float | None:
    """Share of rows whose implied deployment exceeds the override cap.

    Section 55 makes this a first-class calibration metric, not a diagnostic.
    The 2024 control measured a 10.9% false-positive rate for the override
    discriminator, which is the same over-estimate seen from the other side: a
    fit that reduces segment-time error by lowering CdA is buying that reduction
    by attributing missing drag to electrical power the car is not allowed to
    use.
    """
    from trackshift.rules.api import max_electrical_power_kw
    from trackshift.twin.api import segment_power

    violations = counted = 0
    for row in rows:
        event = str(row.get("event") or "")
        circuit = str(row.get("circuit") or "")
        rules = rules_cache.get(circuit)
        if rules is None:
            continue
        speed = row.get("entry_speed_kmh")
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            continue
        if speed <= 0:
            continue
        rho = row.get("air_density_proxy")
        try:
            rho = float(rho)
            if rho <= 0 or rho != rho:
                rho = None
        except (TypeError, ValueError):
            rho = None
        try:
            cap = max_electrical_power_kw(speed, "override", rules)
        except Exception:
            continue
        # Real acceleration, not zero. At zero the ICE covers the demand and
        # nothing deploys, so the whole diagnostic reads 0% and says nothing.
        accel = 0.0
        try:
            exit_speed = float(row.get("exit_speed_kmh_offline"))
            duration = float(row.get("segment_time_s_offline"))
            if duration > 0:
                accel = (exit_speed - speed) / 3.6 / duration
        except (TypeError, ValueError):
            accel = 0.0
        power = segment_power(speed, accel, parameters, rho_kgm3=rho,
                              fallback_rho_kgm3=priors["fallback_rho"],
                              envelope_cap_kw=cap)
        counted += 1
        violations += int(power.envelope_violation)
    return (violations / counted) if counted else None


def main() -> int:
    import numpy as np
    import pandas as pd

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", default="2026")
    parser.add_argument("--sessions", default="Practice 1,Qualifying")
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument("--max-rows", type=int, default=40000,
                        help="Cap fitted rows; the forward model is iterative and CPU-bound")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-root", type=Path, default=OUT)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()

    sessions = tuple(s.strip() for s in args.sessions.split(",") if s.strip())
    priors = load_priors()
    frame, join_report = load_training_frame(args.year, sessions)
    frame, selection = select_clean(frame, sessions)
    if frame.empty:
        raise SystemExit("no clean-air rows to calibrate on; check CP-07 output")

    if len(frame) > args.max_rows:
        frame = frame.sample(args.max_rows, random_state=args.seed)

    # Two splits, because one cannot answer both questions.
    #
    # Holding out whole events measures generalisation to an unseen circuit,
    # which is the right question for rungs 1 and 2. It is the *wrong* question
    # for rungs 3 and 4: those fit per-(team, circuit) cells, and circuit maps
    # one-to-one onto event here, so no held-out row's cell was ever fitted.
    # Under that split rung 4 silently returned the global prediction and
    # reported a bit-identical MAE while claiming 110 cells fitted.
    #
    # So the ladder is scored on a lap-held-out split stratified by circuit,
    # where every rung is actually applicable and the rung-to-rung comparison
    # section 29 asks for is like-for-like. The event-held-out split is still
    # computed and reported beside it as a generalisation check.
    rng = np.random.default_rng(args.seed)

    events = sorted(frame["event"].astype(str).unique())
    holdout_count = max(1, int(len(events) * args.holdout_fraction))
    holdout_events = set(rng.choice(events, size=holdout_count, replace=False).tolist())
    unseen_train = frame[~frame["event"].astype(str).isin(holdout_events)]
    unseen_test = frame[frame["event"].astype(str).isin(holdout_events)]

    # Lap-held-out: whole laps, never individual segments, or a lap's other
    # segments leak its conditions into training. Stratified by circuit so every
    # circuit is represented on both sides.
    lap_key = frame[["circuit", "event", "session", "driver", "lap"]].astype(str).agg("|".join, axis=1)
    frame = frame.assign(_lap_key=lap_key)
    held_laps: set[str] = set()
    for _, block in frame.groupby("circuit", sort=True):
        laps = sorted(block["_lap_key"].unique())
        if len(laps) < 2:
            continue
        take = max(1, int(len(laps) * args.holdout_fraction))
        held_laps.update(rng.choice(laps, size=take, replace=False).tolist())
    train = frame[~frame["_lap_key"].isin(held_laps)]
    test = frame[frame["_lap_key"].isin(held_laps)]
    if train.empty or test.empty:
        raise SystemExit("split produced an empty side; widen the data or lower --holdout-fraction")
    if unseen_train.empty or unseen_test.empty:
        raise SystemExit("event split produced an empty side; widen the data or lower --holdout-fraction")

    train_rows = train.to_dict("records")
    test_rows = test.to_dict("records")
    y_train = np.array([float(r["segment_time_s_offline"]) for r in train_rows])
    y_test = np.array([float(r["segment_time_s_offline"]) for r in test_rows])

    from trackshift.rules.api import load_event_rules

    rules_cache = {}
    for circuit in sorted({str(r.get("circuit")) for r in test_rows}):
        try:
            rules_cache[circuit] = load_event_rules(f"{circuit}_grand_prix", str(args.year))
        except Exception:
            pass

    x0 = [priors["cda_m2"], priors["crr"], priors["eta_drivetrain"], priors["p_ice_max_kw"]]
    results: list[RungResult] = []
    artefacts: dict[str, Any] = {}

    # Rung 1: priors only, nothing fitted.
    analytical = _params_from(dict(zip(PARAMETER_NAMES, x0)), priors)
    pred = _predict(test_rows, analytical, priors)
    results.append(RungResult(
        "analytical", dict(zip(PARAMETER_NAMES, x0)),
        mae_s=mean_absolute_error(y_test, pred), rmse_s=root_mean_square_error(y_test, pred),
        n_segments=len(test_rows),
        envelope_violation_rate=envelope_violation_rate(test_rows, analytical, priors, rules_cache)))

    # Rung 2: one global parameter set.
    fit = fit_parameters(_residual_fn(train_rows, priors, y_train), x0)
    global_params = _params_from(fit["parameters"], priors)
    pred = _predict(test_rows, global_params, priors)
    results.append(RungResult(
        "global", fit["parameters"],
        mae_s=mean_absolute_error(y_test, pred), rmse_s=root_mean_square_error(y_test, pred),
        n_segments=len(test_rows), at_bound=fit["at_bound"], violations=fit["violations"],
        envelope_violation_rate=envelope_violation_rate(test_rows, global_params, priors, rules_cache)))
    artefacts["global_jacobian_shape"] = list(np.asarray(fit["jacobian"]).shape)
    global_fit = fit

    # Generalisation to a circuit the fit has never seen. Reported beside the
    # ladder rather than as part of it: only rungs 1 and 2 can answer this
    # question at all, since 3 and 4 are per-circuit by construction. This is
    # the harder number and the one to quote when asked whether the twin
    # transfers to a new track.
    unseen_train_rows = unseen_train.to_dict("records")
    unseen_test_rows = unseen_test.to_dict("records")
    y_unseen = np.array([float(r["segment_time_s_offline"]) for r in unseen_test_rows])
    unseen_fit = fit_parameters(
        _residual_fn(unseen_train_rows, priors,
                     np.array([float(r["segment_time_s_offline"]) for r in unseen_train_rows])), x0)
    unseen_params = _params_from(unseen_fit["parameters"], priors)
    unseen_report = {
        "policy": "whole events held out, so error measures transfer to an unseen circuit",
        "holdout_events": sorted(holdout_events),
        "train_rows": len(unseen_train_rows), "test_rows": len(unseen_test_rows),
        "applicable_rungs": ["analytical", "global"],
        "not_applicable_rungs": {
            "team/event": ("per-circuit cells cannot be evaluated on a circuit that was "
                           "held out; they are scored on the lap split instead"),
        },
        "analytical_mae_s": mean_absolute_error(
            y_unseen, _predict(unseen_test_rows, analytical, priors)),
        "global_mae_s": mean_absolute_error(
            y_unseen, _predict(unseen_test_rows, unseen_params, priors)),
        "global_parameters": unseen_fit["parameters"],
        "global_at_bound": unseen_fit["at_bound"],
    }

    # Rungs 3 and 4: per team, then per (team, event), falling back where a cell
    # is too thin to fit honestly.
    for rung, keys in (("team", ["team"]), ("event", ["team", "circuit"])):
        if not all(k in train.columns for k in keys):
            continue
        predictions = np.array(_predict(test_rows, global_params, priors))
        cells = fitted = fell_back = 0
        cell_params: dict[Any, dict[str, float]] = {}
        for cell, block in train.groupby(keys, sort=True):
            cells += 1
            # Section 29 says >=30 clean *laps* per cell. `block` is segment
            # rows, and a lap is ~30 segments, so testing len(block) made the
            # guard about thirty times too lenient -- every cell passed and
            # nothing ever fell back.
            if block["_lap_key"].nunique() < MIN_CLEAN_LAPS_PER_CELL:
                fell_back += 1
                continue
            rows = block.to_dict("records")
            y = np.array([float(r["segment_time_s_offline"]) for r in rows])
            try:
                cell_fit = fit_parameters(_residual_fn(rows, priors, y), x0)
            except Exception:
                fell_back += 1
                continue
            cell_params[cell if isinstance(cell, tuple) else (cell,)] = cell_fit["parameters"]
            fitted += 1
        # Apply each cell's parameters to its own held-out rows.
        by_cell = {tuple(str(x) for x in k): v for k, v in cell_params.items()}
        applied = 0
        for index, row in enumerate(test_rows):
            match = by_cell.get(tuple(str(row.get(k)) for k in keys))
            if match:
                predictions[index] = _predict([row], _params_from(match, priors), priors)[0]
                applied += 1
        # A rung that touched no held-out row is not a result, it is the
        # previous rung's numbers wearing this rung's name. Say so rather than
        # reporting a MAE that was never this rung's to report.
        if applied == 0:
            results.append(RungResult(
                rung, {"cells_fitted": fitted}, mae_s=float("nan"), rmse_s=float("nan"),
                n_segments=0, cells_fitted=fitted, cells_fallen_back=fell_back,
                applicable=False,
                rejection_reason=(
                    f"NOT_APPLICABLE: none of the {fitted} fitted {'/'.join(keys)} cells "
                    f"appears in the held-out set, so this rung never predicted anything. "
                    f"Its parameters are untested, not equivalent to the previous rung's.")))
            continue
        results.append(RungResult(
            rung, {"cells_fitted": fitted, "test_rows_covered": applied},
            mae_s=mean_absolute_error(y_test, predictions),
            rmse_s=root_mean_square_error(y_test, predictions),
            n_segments=len(test_rows), cells_fitted=fitted, cells_fallen_back=fell_back))

    # Rung 5: rung 4 plus a deliberately small learned residual. Small on
    # purpose -- a large residual model memorises the physics error instead of
    # correcting it, which is the failure section 29 guards against.
    try:
        from lightgbm import LGBMRegressor

        # gap_ahead_m_entry is the traffic control. Dirty air is the largest
        # source of within-segment variation that physics cannot see, and a
        # clean-air lap label does not remove it -- a lap can be "clean" by
        # CP-07's definition and still spend one corner behind a car.
        # tyre_compound and wet_track_flag are section 29 controls that were
        # joined and then dropped here. Compound is categorical, which is why it
        # needed the encoding below rather than being passed through to_numeric.
        controls = [c for c in ("entry_speed_kmh", "segment_length_m", "tyre_life_laps",
                                "tyre_compound", "fuel_load_kg_est",
                                "wind_head_component_mps",
                                "wind_cross_component_mps", "track_temperature",
                                "wet_track_flag",
                                "air_density_proxy", "ers_deploy_power_est_kw",
                                "gap_ahead_m_entry", "segment_baseline_time_s",
                                "corner_id", "sector")
                    if c in train.columns]
        if controls:
            physics_train = np.array(_predict(train_rows, global_params, priors))
            physics_test = np.array(_predict(test_rows, global_params, priors))

            def is_categorical(column) -> bool:
                return column.dtype == object or str(column.dtype) in ("string", "category")

            # One level map per categorical control, built over both sides so a
            # compound that appears only in the held-out set still encodes to the
            # same integer it would have in training.
            levels = {
                name: {value: index for index, value in enumerate(sorted(
                    set(train[name].astype("string").fillna("UNKNOWN").unique())
                    | set(test[name].astype("string").fillna("UNKNOWN").unique())))}
                for name in controls if is_categorical(train[name])
            }

            def design(block):
                """Numeric design matrix, with missing left missing.

                LightGBM handles NaN natively. Filling with 0.0 asserted things
                that were never measured -- with the twin absent it claimed every
                segment ran zero ERS deployment, which is a fabricated
                observation, not a neutral default.
                """
                out = {}
                for name in controls:
                    column = block[name]
                    if name in levels:
                        out[name] = (column.astype("string").fillna("UNKNOWN")
                                     .map(levels[name]).astype("float64"))
                    elif column.dtype == bool or str(column.dtype) == "boolean":
                        out[name] = column.astype("float64")
                    else:
                        out[name] = pd.to_numeric(column, errors="coerce")
                return pd.DataFrame(out, index=block.index)

            X_train = design(train)
            X_test = design(test)
            # Deliberately small. Section 29 wants the physics doing the work;
            # 500 trees over a few thousand rows memorised event-specific error
            # and lost to rung 2 on a held-out event.
            model = LGBMRegressor(n_estimators=200, learning_rate=0.03, num_leaves=7,
                                  min_child_samples=80, subsample=0.8, colsample_bytree=0.8,
                                  random_state=args.seed, verbose=-1)
            model.fit(X_train, y_train - physics_train,
                      categorical_feature=[c for c in levels if c in X_train.columns])
            correction = model.predict(X_test)
            combined = physics_test + correction
            results.append(RungResult(
                "physics_residual",
                {"controls": controls, "categorical_controls": sorted(levels)},
                mae_s=mean_absolute_error(y_test, combined),
                rmse_s=root_mean_square_error(y_test, combined),
                n_segments=len(test_rows),
                residual_share=residual_share(physics_test, correction)))
    except ImportError:
        artefacts["physics_residual"] = "lightgbm unavailable"

    compared = compare_rungs(results)

    by_kind = {}
    if "kind" in test.columns:
        by_kind = error_by_group(y_test, _predict(test_rows, global_params, priors),
                                 test["kind"].astype(str).tolist())

    # Naive floors on the held-out rows, for context on the MAE above.
    baseline_pred = np.array([float(r.get("segment_baseline_time_s") or np.nan) for r in test_rows])
    mask = ~np.isnan(baseline_pred)
    naive_baseline_mae = (float(np.abs(y_test[mask] - baseline_pred[mask]).mean())
                          if mask.any() else None)
    seg_keys = [f"{r.get('circuit')}|{r.get('segment_id')}" for r in test_rows]
    by_segment: dict[str, list[float]] = {}
    for key, value in zip(seg_keys, y_test):
        by_segment.setdefault(key, []).append(float(value))
    medians = {k: float(np.median(v)) for k, v in by_segment.items()}
    naive_median_mae = float(np.mean([abs(v - medians[k]) for k, v in zip(seg_keys, y_test)]))

    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.output_root / version
    target.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "m15_physics_calibration_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "year": args.year,
        "sessions": list(sessions),
        "clean_classes": list(CLEAN_CLASSES),
        "controls_joined": join_report,
        "selection": selection,
        "split": {
            "policy": ("Ladder scored on held-out LAPS, stratified by circuit, because "
                       "that is the only split on which all five rungs are applicable. "
                       "Rungs 3 and 4 fit per-(team, circuit) cells, and circuit maps "
                       "one-to-one onto event, so an event-held-out split leaves their "
                       "cells with nothing to predict -- rung 4 silently returned rung "
                       "2's numbers under it."),
            "holdout_unit": "lap",
            "train_rows": len(train_rows), "test_rows": len(test_rows),
            "train_laps": int(train["_lap_key"].nunique()),
            "test_laps": int(test["_lap_key"].nunique()),
            "circuits_in_both_sides": sorted(
                set(train["circuit"].astype(str)) & set(test["circuit"].astype(str))),
        },
        "generalisation_to_unseen_circuit": unseen_report,
        "mae_targets": MAE_TARGETS,
        "parameter_bounds": PARAMETER_BOUNDS,
        "rungs": [
            {"rung": r.rung, "mae_s": r.mae_s, "rmse_s": r.rmse_s, "n_segments": r.n_segments,
             "parameters": r.parameters, "at_bound": r.at_bound, "violations": r.violations,
             "cells_fitted": r.cells_fitted, "cells_fallen_back": r.cells_fallen_back,
             "applicable": r.applicable,
             "residual_share": r.residual_share, "accepted": r.accepted,
             "rejection_reason": r.rejection_reason}
            for r in compared
        ],
        # "accepted" means a rung improved on the previous one. It does not mean
        # the calibration is usable, and a rung with no MAE target in section 29
        # can be accepted at an error that is nowhere near fit for the DP. This
        # is the flag that says whether the calibration is actually good enough.
        # A bare MAE means nothing without knowing what is achievable. The two
        # naive predictors below bound the problem: the C2 baseline is a constant
        # per segment, and the held-out median is the best any constant-per-
        # segment model could do. Beating them is what says the physics is
        # contributing rather than the geometry carrying the whole score.
        "naive_baselines": {
            "c2_baseline_mae_s": naive_baseline_mae,
            "best_constant_per_segment_mae_s": naive_median_mae,
            "note": ("The section 29 targets sit below the best-constant-per-segment "
                     "floor, so reaching them requires explaining lap-to-lap variation "
                     "within a segment, not just its geometry. No model predicting from "
                     "segment identity plus physics can cross that floor, which makes "
                     "the targets a question for section 29 rather than for the fit. "
                     "Fuel is no longer the gap it once was: CP-19 now covers Practice 1 "
                     "and Qualifying, and mass varies across training rows. The "
                     "remaining named gap is the aero/Overtake state, absent for these "
                     "sessions -- see controls_joined."),
        },
        "envelope_violation_by_rung": {
            r.rung: r.envelope_violation_rate for r in compared
            if r.envelope_violation_rate is not None
        },
        "violation_note": (
            "Section 55. The 2024 control measured a 10.9% false-positive rate for the "
            "override discriminator, which is this same over-estimate seen from the other "
            "side. A fit that lowers segment-time error by lowering CdA is attributing "
            "missing drag to electrical power the car may not legally use, so the rate is "
            "a rejection criterion here rather than a footnote."
        ),
        "calibration_meets_targets": all(
            r.mae_s <= MAE_TARGETS[r.rung] for r in compared if r.rung in MAE_TARGETS
        ),
        "best_mae_s": min(r.mae_s for r in compared),
        "error_by_segment_kind": by_kind,
        "artefacts": artefacts,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # CP-22 draws parameter uncertainty from this Jacobian. Saving it beside the
    # manifest is what makes the uncertainty reproducible rather than something
    # recomputed from a fit nobody kept.
    np.savez(target / "global_fit.npz",
             jacobian=np.asarray(global_fit["jacobian"], dtype=float),
             residuals=np.asarray(global_fit["residuals"], dtype=float),
             parameters=np.array([global_fit["parameters"][n] for n in PARAMETER_NAMES]),
             names=np.array(list(PARAMETER_NAMES)))
    (args.output_root / "latest.txt").write_text(version, encoding="utf-8")
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "twin_report.md").write_text(
        "# CP-20 physics calibration\n\n"
        f"Version `{version}`, commit `{manifest['git_commit']}`.\n\n"
        f"Train {len(train_rows)} rows, held out {len(test_rows)} on "
        f"{len(holdout_events)} event(s).\n\n"
        "| Rung | MAE (s) | RMSE (s) | Accepted | Reason |\n|---|---|---|---|---|\n"
        + "".join(f"| {r.rung} | {r.mae_s:.4f} | {r.rmse_s:.4f} | "
                 f"{'yes' if r.accepted else 'no'} | {r.rejection_reason or ''} |\n"
                 for r in compared),
        encoding="utf-8")

    print(json.dumps({k: v for k, v in manifest.items()
                      if k not in ("parameter_bounds", "error_by_segment_kind")}, indent=2))
    print()
    for r in compared:
        print(f"  {r.rung:<18} MAE {r.mae_s:7.4f} s  RMSE {r.rmse_s:7.4f} s  "
              f"{'ACCEPTED' if r.accepted else 'rejected'}"
              + (f"  ({r.rejection_reason})" if r.rejection_reason else ""))
    print()
    meets = manifest["calibration_meets_targets"]
    print(f"  CALIBRATION MEETS SECTION 29 TARGETS: {'YES' if meets else 'NO'}"
          f"   best MAE {manifest['best_mae_s']:.4f} s")
    if not meets:
        print("  Every fitted parameter sitting on a bound means the forward model is")
        print("  missing physics, not that the bounds are wrong (section 29).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
