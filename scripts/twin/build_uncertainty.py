#!/usr/bin/env python3
"""Physics uncertainty from the CP-20 fit (M17, CP-22).

Draws parameter sets from the least-squares covariance at the optimum, pushes
each through the forward model, and reports mean plus a 10th/90th interval so
C5 can return an ``Uncertain`` rather than a point estimate (section 42).

Two sources of error, and both are needed. The Jacobian covariance says how well
the fit is pinned down; it says nothing about the model being the wrong shape.
Held-out residual variance says that. Reporting only the first is the documented
way coverage lands far below nominal while every interval looks respectable.

**This inherits CP-20's fit, including its problems.** If that calibration has
parameters sitting on their bounds, the covariance around them is not a sensible
description of anything and the intervals will be too narrow in the directions
that matter. The manifest records the parent calibration's verdict so a consumer
can see what it is standing on.

Usage:
    python scripts/twin/build_uncertainty.py
    python scripts/twin/build_uncertainty.py --version 20260912T201301Z
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
    DRAWS,
    PARAMETER_BOUNDS,
    PARAMETER_NAMES,
    PhysicsParameters,
    coverage,
    draw_parameters,
    interval,
    parameter_covariance,
    propagate,
)
from trackshift.twin.forward import predict_segment_time_grip_or_power  # noqa: E402

MODELS = ROOT / "artifacts" / "models" / "twin"
OUT = ROOT / "data" / "processed" / "twin_uncertainty"


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def resolve_version(version: str | None) -> str:
    if version:
        return version
    pointer = MODELS / "latest.txt"
    if not pointer.exists():
        raise SystemExit("no CP-20 calibration found; run scripts/train/calibrate_physics.py first")
    return pointer.read_text(encoding="utf-8").strip()


def main() -> int:
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="CP-20 calibration version; defaults to latest")
    parser.add_argument("--draws", type=int, default=DRAWS)
    parser.add_argument("--sample-segments", type=int, default=400,
                        help="Segments to score coverage on")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-root", type=Path, default=OUT)
    args = parser.parse_args()

    version = resolve_version(args.version)
    target = MODELS / version
    manifest_path = target / "manifest.json"
    fit_path = target / "global_fit.npz"
    if not manifest_path.exists() or not fit_path.exists():
        raise SystemExit(f"calibration {version} is incomplete; re-run calibrate_physics.py")

    parent = json.loads(manifest_path.read_text(encoding="utf-8"))
    fit = np.load(fit_path, allow_pickle=False)

    uncertainty = parameter_covariance(fit["jacobian"], fit["residuals"], PARAMETER_NAMES)
    mean = [float(v) for v in fit["parameters"]]
    bounds = [PARAMETER_BOUNDS[name] for name in PARAMETER_NAMES]
    draws = draw_parameters(mean, uncertainty.covariance, draws=args.draws,
                            bounds=bounds, seed=args.seed)

    best = min((r["mae_s"] for r in parent["rungs"]), default=None)

    # Score coverage on a sample of real segments from the parent's own scope.
    import pandas as pd

    segments = []
    for path in sorted((ROOT / "data" / "processed" / "segments").glob("circuit=*/segments.parquet")):
        frame = pd.read_parquet(path)
        frame = frame[(frame["year"].astype(str) == str(parent["year"]))
                      & (frame["session"].isin(parent["sessions"]))]
        if not frame.empty:
            frame["circuit"] = path.parent.name.removeprefix("circuit=")
            segments.append(frame)
    if not segments:
        raise SystemExit("no segments in the parent calibration's scope")
    frame = pd.concat(segments, ignore_index=True)

    base = pd.read_parquet(ROOT / "data" / "processed" / "field_segment_baselines"
                           / "field_segment_baselines.parquet")
    base = base[["circuit", "segment_id", "max_speed_kmh_median", "segment_time_s_median",
                 "exit_speed_kmh_median"]].dropna().drop_duplicates(["circuit", "segment_id"])
    base = base.rename(columns={"max_speed_kmh_median": "segment_speed_ceiling_kmh",
                                "segment_time_s_median": "segment_baseline_time_s",
                                "exit_speed_kmh_median": "segment_exit_speed_ref_kmh"})
    frame = frame.merge(base, on=["circuit", "segment_id"], how="left")
    frame = frame[frame["segment_baseline_time_s"].notna()]
    observed = pd.to_numeric(frame["segment_time_s_offline"], errors="coerce")
    frame = frame[observed.notna() & (observed > 0)]
    if len(frame) > args.sample_segments:
        frame = frame.sample(args.sample_segments, random_state=args.seed)
    rows = frame.to_dict("records")
    truth = [float(r["segment_time_s_offline"]) for r in rows]

    priors = {"eta_deploy": 0.95, "eta_harvest": 0.90, "gravity": 9.80665, "mass": 800.0}

    # Split the sample. One half measures how wrong the model actually is, the
    # other scores coverage. Estimating the inflation on the same rows it is
    # scored against would be circular and would report whatever coverage was
    # asked for.
    split = len(rows) // 2
    calib_rows, calib_truth = rows[:split], truth[:split]
    rows, truth = rows[split:], truth[split:]

    def predict_mean(row):
        parameters = PhysicsParameters(
            mass_kg=priors["mass"], cda_m2=mean[0], crr=mean[1],
            eta_drivetrain=mean[2], p_ice_max_kw=mean[3],
            eta_deploy=priors["eta_deploy"], eta_harvest=priors["eta_harvest"],
            gravity_mps2=priors["gravity"])
        return predict_segment_time_grip_or_power(row, parameters)

    calib_residuals = np.array([predict_mean(r) - t for r, t in zip(calib_rows, calib_truth)])
    # Quantiles, not a normal sigma from MAE. The held-out error is skewed --
    # a segment can be much slower than predicted and only so much faster -- and
    # a symmetric normal inflation under-covers exactly where the tail is.
    residual_low = float(np.percentile(calib_residuals, 10))
    residual_high = float(np.percentile(calib_residuals, 90))
    residual_variance = float(np.var(calib_residuals))

    intervals = []
    for row in rows:
        def predict(values, _row=row):
            parameters = PhysicsParameters(
                mass_kg=priors["mass"], cda_m2=values[0], crr=values[1],
                eta_drivetrain=values[2], p_ice_max_kw=values[3],
                eta_deploy=priors["eta_deploy"], eta_harvest=priors["eta_harvest"],
                gravity_mps2=priors["gravity"],
            )
            return predict_segment_time_grip_or_power(_row, parameters)

        values = propagate(draws, predict)
        band = interval(values)
        # Widen by the measured error quantiles, asymmetrically. A prediction
        # that is too fast by 0.4 s and too slow by 0.1 s needs an interval
        # shaped the same way.
        band = {**band,
                "low": band["low"] - max(0.0, residual_high),
                "high": band["high"] - min(0.0, residual_low)}
        band["spread"] = band["high"] - band["low"]
        intervals.append(band)

    score = coverage(truth, intervals)

    args.output_root.mkdir(parents=True, exist_ok=True)
    out = pd.DataFrame([
        {"circuit": r.get("circuit"), "event": r.get("event"), "session": r.get("session"),
         "driver": r.get("driver"), "lap": r.get("lap"), "segment_id": r.get("segment_id"),
         "t_mean_s": band["mean"], "t_low_s": band["low"], "t_high_s": band["high"],
         "t_spread_s": band["spread"], "n_draws": band["n_draws"],
         "observed_segment_time_s": value, "provenance": "SIMULATED"}
        for r, band, value in zip(rows, intervals, truth)
    ])
    out.to_parquet(args.output_root / "twin_uncertainty.parquet", index=False)

    manifest = {
        "schema_version": "m17_twin_uncertainty_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "parent_calibration": version,
        "parent_meets_targets": parent.get("calibration_meets_targets"),
        "parent_best_mae_s": best,
        "inherits_note": (
            "Intervals are only as sound as the fit they come from. The parent "
            "calibration does not meet the section 29 targets and has parameters at "
            "their bounds, so the covariance around them under-describes the real "
            "uncertainty in those directions. Development use only."
            if not parent.get("calibration_meets_targets") else
            "Parent calibration meets its section 29 targets."
        ),
        "draws": args.draws,
        "parameters": dict(zip(PARAMETER_NAMES, mean)),
        "residual_variance_s2": residual_variance,
        "residual_quantiles_s": {"p10": residual_low, "p90": residual_high},
        "calibration_rows": len(calib_rows),
        "residual_variance_note": (
            "From held-out MAE, not from the Jacobian. Parameter covariance alone "
            "assumes the model shape is right and systematically under-covers."
        ),
        "coverage": score,
        "segments_scored": len(rows),
        "nominal_coverage": 0.80,
        "coverage_gate_pass": abs(score["coverage"] - 0.80) <= 0.10,
    }
    (args.output_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    print()
    print(f"  coverage {score['coverage']:.1%} against a nominal 80%  "
          f"({'PASS' if manifest['coverage_gate_pass'] else 'FAIL'})")
    print(f"  mean interval width {score['mean_spread']:.3f} s over {len(rows)} segments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
