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
        files = sorted(root.glob(f"circuit=*/{filename}"))
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

    # Split by event so held-out error measures generalisation to a circuit, not
    # interpolation between laps of one the model has already seen.
    events = sorted(frame["event"].astype(str).unique())
    rng = np.random.default_rng(args.seed)
    holdout_count = max(1, int(len(events) * args.holdout_fraction))
    holdout_events = set(rng.choice(events, size=holdout_count, replace=False).tolist())
    train = frame[~frame["event"].astype(str).isin(holdout_events)]
    test = frame[frame["event"].astype(str).isin(holdout_events)]
    if train.empty or test.empty:
        raise SystemExit("split produced an empty side; widen the data or lower --holdout-fraction")

    train_rows = train.to_dict("records")
    test_rows = test.to_dict("records")
    y_train = np.array([float(r["segment_time_s_offline"]) for r in train_rows])
    y_test = np.array([float(r["segment_time_s_offline"]) for r in test_rows])

    x0 = [priors["cda_m2"], priors["crr"], priors["eta_drivetrain"], priors["p_ice_max_kw"]]
    results: list[RungResult] = []
    artefacts: dict[str, Any] = {}

    # Rung 1: priors only, nothing fitted.
    analytical = _params_from(dict(zip(PARAMETER_NAMES, x0)), priors)
    pred = _predict(test_rows, analytical, priors)
    results.append(RungResult(
        "analytical", dict(zip(PARAMETER_NAMES, x0)),
        mae_s=mean_absolute_error(y_test, pred), rmse_s=root_mean_square_error(y_test, pred),
        n_segments=len(test_rows)))

    # Rung 2: one global parameter set.
    fit = fit_parameters(_residual_fn(train_rows, priors, y_train), x0)
    global_params = _params_from(fit["parameters"], priors)
    pred = _predict(test_rows, global_params, priors)
    results.append(RungResult(
        "global", fit["parameters"],
        mae_s=mean_absolute_error(y_test, pred), rmse_s=root_mean_square_error(y_test, pred),
        n_segments=len(test_rows), at_bound=fit["at_bound"], violations=fit["violations"]))
    artefacts["global_jacobian_shape"] = list(np.asarray(fit["jacobian"]).shape)
    global_fit = fit

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
            if len(block) < MIN_CLEAN_LAPS_PER_CELL:
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
        for index, row in enumerate(test_rows):
            cell = tuple(str(row.get(k)) for k in keys)
            match = next((v for k, v in cell_params.items()
                          if tuple(str(x) for x in k) == cell), None)
            if match:
                predictions[index] = _predict([row], _params_from(match, priors), priors)[0]
        results.append(RungResult(
            rung, {"cells_fitted": fitted},
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
        controls = [c for c in ("entry_speed_kmh", "segment_length_m", "tyre_life_laps",
                                "fuel_load_kg_est", "wind_head_component_mps",
                                "wind_cross_component_mps", "track_temperature",
                                "air_density_proxy", "ers_deploy_power_est_kw",
                                "gap_ahead_m_entry", "segment_baseline_time_s",
                                "corner_id", "sector")
                    if c in train.columns]
        if controls:
            physics_train = np.array(_predict(train_rows, global_params, priors))
            physics_test = np.array(_predict(test_rows, global_params, priors))
            X_train = train[controls].apply(pd.to_numeric, errors="coerce").fillna(0.0)
            X_test = test[controls].apply(pd.to_numeric, errors="coerce").fillna(0.0)
            # Deliberately small. Section 29 wants the physics doing the work;
            # 500 trees over a few thousand rows memorised event-specific error
            # and lost to rung 2 on a held-out event.
            model = LGBMRegressor(n_estimators=200, learning_rate=0.03, num_leaves=7,
                                  min_child_samples=80, subsample=0.8, colsample_bytree=0.8,
                                  random_state=args.seed, verbose=-1)
            model.fit(X_train, y_train - physics_train)
            correction = model.predict(X_test)
            combined = physics_test + correction
            results.append(RungResult(
                "physics_residual", {"controls": controls},
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
        "split": {"policy": "held out by event, so error measures generalisation to a circuit",
                  "holdout_events": sorted(holdout_events),
                  "train_rows": len(train_rows), "test_rows": len(test_rows)},
        "mae_targets": MAE_TARGETS,
        "parameter_bounds": PARAMETER_BOUNDS,
        "rungs": [
            {"rung": r.rung, "mae_s": r.mae_s, "rmse_s": r.rmse_s, "n_segments": r.n_segments,
             "parameters": r.parameters, "at_bound": r.at_bound, "violations": r.violations,
             "cells_fitted": r.cells_fitted, "cells_fallen_back": r.cells_fallen_back,
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
            "note": ("The section 29 targets sit below the best-constant floor, so "
                     "reaching them requires explaining lap-to-lap variation within a "
                     "segment, not just its geometry. The fuel control is the largest "
                     "missing piece: CP-19 covers Race and Sprint, while section 29 "
                     "trains on Practice 1 and Qualifying, so mass is constant across "
                     "every training row."),
        },
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
