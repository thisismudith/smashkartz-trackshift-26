#!/usr/bin/env python3
"""Build the energy twin over the segment tables (M14, CP-18).

Evaluates the longitudinal power balance at every segment, integrates deployment
and recovery, and advances the Energy Store per lap. Output goes to
``data/processed/energy_twin/``, every column tagged SIMULATED.

Nothing is clamped to the power envelope (section 28.1). The cap from the CP-11
evaluator is compared against the raw estimate and the violation recorded beside
it, because the violation rate is a calibration metric in CP-20 and clipping
would delete exactly the signal that metric reads.

Weather is optional but wanted: without CP-06's overlay the balance falls back to
ISA standard density, every affected row is flagged `air_density_is_fallback`,
and the manifest reports the share. Drag goes as the cube of air speed, so that
fallback is not free.

Usage:
    python scripts/twin/build_energy_twin.py --year 2026
    python scripts/twin/build_energy_twin.py --year 2026 --jobs 8
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.progress import Progress  # noqa: E402
from trackshift.rules.api import load_event_rules, max_electrical_power_kw  # noqa: E402
from trackshift.twin.api import (  # noqa: E402
    EnergyState,
    PhysicsParameters,
    advance_energy_state,
    integrate_segment,
    segment_power,
    trailing_mean,
)

SEGMENTS = ROOT / "data" / "processed" / "segments"
WEATHER = ROOT / "data" / "processed" / "weather_overlay"
PRIORS = ROOT / "config" / "physics" / "priors.yaml"
OUT = ROOT / "data" / "processed" / "energy_twin"

#: Trailing window for acc_x, which is noisy at 20 m resolution. Never centred.
ACC_WINDOW = 3


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _prior(priors: dict, *path: str) -> Any:
    node: Any = priors
    for part in path:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"priors.yaml has no {'.'.join(path)}")
        node = node[part]
    return node.get("value") if isinstance(node, dict) else node


def load_priors(path: Path = PRIORS) -> dict[str, Any]:
    import yaml

    priors = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        "mass_kg": float(_prior(priors, "mass", "chassis_minimum_kg")),
        "cda_m2": float(_prior(priors, "aerodynamics", "cda_m2")),
        "crr": float(_prior(priors, "rolling", "crr")),
        "eta_drivetrain": float(_prior(priors, "drivetrain", "eta_drivetrain")),
        "eta_deploy": float(_prior(priors, "drivetrain", "eta_deploy")),
        "eta_harvest": float(_prior(priors, "drivetrain", "eta_harvest")),
        "p_ice_max_kw": float(_prior(priors, "engine", "p_ice_max_kw")),
        "gravity_mps2": float(_prior(priors, "environment", "gravity_mps2")),
        "fallback_rho": float(_prior(priors, "environment", "air_density_fallback_kgm3")),
    }


def build_circuit(circuit: str, year: str, output_root: Path, fuel_kg: float) -> dict[str, Any]:
    """Build one circuit. Module-level so it pickles into the process pool."""
    import pandas as pd

    priors = load_priors()
    parameters = PhysicsParameters(
        mass_kg=priors["mass_kg"] + fuel_kg,
        cda_m2=priors["cda_m2"],
        crr=priors["crr"],
        eta_drivetrain=priors["eta_drivetrain"],
        p_ice_max_kw=priors["p_ice_max_kw"],
        eta_deploy=priors["eta_deploy"],
        eta_harvest=priors["eta_harvest"],
        gravity_mps2=priors["gravity_mps2"],
    )

    path = SEGMENTS / f"circuit={circuit}" / "segments.parquet"
    if not path.exists():
        return {"circuit": circuit, "skipped": "no C1 segments"}
    frame = pd.read_parquet(path)
    frame = frame[frame["year"].astype(str) == str(year)]
    if frame.empty:
        return {"circuit": circuit, "skipped": f"no {year} rows in C1"}

    # CP-06's overlay is optional. Joining it when present is what makes the
    # drag term honest; its absence is recorded per row, not assumed away.
    weather = WEATHER / f"circuit={circuit}" / "weather_overlay.parquet"
    if weather.exists():
        overlay = pd.read_parquet(weather)
        keys = [k for k in ("year", "event", "session", "driver", "lap", "segment_id")
                if k in frame.columns and k in overlay.columns]
        carry = [c for c in ("air_density_proxy", "wind_head_component_mps") if c in overlay.columns]
        if keys and carry:
            frame = frame.merge(overlay[keys + carry], on=keys, how="left")

    rules = None
    try:
        rules = load_event_rules(f"{circuit}_grand_prix", str(year))
    except Exception:
        rules = None

    frame = frame.sort_values(["session", "driver", "lap", "segment_id"], kind="stable")
    rows: list[dict[str, Any]] = []
    violations = fallbacks = clipped = 0

    for (session, driver), stint in frame.groupby(["session", "driver"], sort=True):
        state = EnergyState(ers_soc_est_mj=0.0)
        history: list[float] = []
        previous_lap: Any = None
        for row in stint.itertuples():
            speed = getattr(row, "entry_speed_kmh", None)
            duration = getattr(row, "segment_time_s_offline", None)
            if speed is None or duration is None or pd.isna(speed) or pd.isna(duration):
                continue
            exit_speed = getattr(row, "exit_speed_kmh_offline", None)
            # Acceleration from the speed trace across the segment, smoothed on a
            # trailing window only.
            accel = None
            if exit_speed is not None and not pd.isna(exit_speed) and float(duration) > 0:
                accel = (float(exit_speed) - float(speed)) / 3.6 / float(duration)
            history.append(accel if accel is not None else 0.0)
            smoothed = trailing_mean(history, ACC_WINDOW)

            rho = getattr(row, "air_density_proxy", None)
            rho = None if rho is None or pd.isna(rho) else float(rho)
            wind = getattr(row, "wind_head_component_mps", None)
            wind = 0.0 if wind is None or pd.isna(wind) else float(wind)

            cap = None
            if rules is not None:
                try:
                    cap = max_electrical_power_kw(float(speed), "override", rules)
                except Exception:
                    cap = None

            power = segment_power(
                speed, smoothed, parameters,
                rho_kgm3=rho, fallback_rho_kgm3=priors["fallback_rho"],
                wind_head_component_mps=wind, envelope_cap_kw=cap,
            )
            violations += int(power.envelope_violation)
            fallbacks += int(power.air_density_is_fallback)

            if previous_lap is not None and row.lap != previous_lap:
                history.clear()
            previous_lap = row.lap

            deploy_mj = integrate_segment(power.ers_deploy_power_est_kw, duration)
            harvest_mj = integrate_segment(power.ers_harvest_power_est_kw, duration)
            state = advance_energy_state(state, deploy_mj, harvest_mj, parameters)
            clipped += int(state.soc_clipped)

            rows.append({
                "year": row.year, "event": row.event, "session": session, "driver": driver,
                "lap": row.lap, "segment_id": row.segment_id,
                "ers_deploy_power_est_kw": power.ers_deploy_power_est_kw,
                "ers_harvest_power_est_kw": power.ers_harvest_power_est_kw,
                "p_wheel_kw": power.p_wheel_kw, "p_drag_kw": power.p_drag_kw,
                "p_rolling_kw": power.p_rolling_kw, "p_gradient_kw": power.p_gradient_kw,
                "p_inertial_kw": power.p_inertial_kw,
                "ers_energy_used_est_mj": state.ers_energy_used_est_mj,
                "ers_energy_harvested_est_mj": state.ers_energy_harvested_est_mj,
                "ers_soc_est_mj": state.ers_soc_est_mj,
                "envelope_cap_kw": power.envelope_cap_kw,
                "envelope_violation": power.envelope_violation,
                "violation_margin_kw": power.violation_margin_kw,
                "air_density_is_fallback": power.air_density_is_fallback,
                "provenance": power.provenance,
            })

    written = None
    if rows:
        target = output_root / f"circuit={circuit}"
        target.mkdir(parents=True, exist_ok=True)
        destination = target / "energy_twin.parquet"
        pd.DataFrame(rows).to_parquet(destination, index=False)
        written = str(destination.relative_to(ROOT))

    total = max(1, len(rows))
    return {
        "circuit": circuit,
        "rows": len(rows),
        "envelope_violations": violations,
        "envelope_violation_rate": violations / total,
        "air_density_fallback_rate": fallbacks / total,
        "soc_clipped_rows": clipped,
        "written": written,
        "rules_available": rules is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", default="2026")
    parser.add_argument("--circuit", action="append", help="C1 circuit key; repeat to select several")
    parser.add_argument("--segments-dir", type=Path, default=SEGMENTS)
    parser.add_argument("--output-root", type=Path, default=OUT)
    parser.add_argument("--fuel-kg", type=float, default=50.0,
                        help="Mean fuel load proxy until CP-19 supplies a curve (section 38)")
    parser.add_argument("--jobs", type=int, default=1, help="Circuits built in parallel")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    circuits = args.circuit or sorted(
        path.parent.name.removeprefix("circuit=")
        for path in args.segments_dir.glob("circuit=*/segments.parquet")
    )
    if not circuits:
        parser.error(f"no C1 segments under {args.segments_dir}; run build_segments.py first")

    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    prog = Progress(len(circuits), enabled=not args.no_progress)

    if args.jobs > 1 and len(circuits) > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(build_circuit, c, args.year, args.output_root, args.fuel_kg): c
                       for c in circuits}
            pending = set(futures)
            while pending:
                finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in finished:
                    results.append(future.result())
                    prog.set_label(str(results[-1].get("circuit")))
                    prog.tick()
                if pending:
                    running = min(len(pending), args.jobs)
                    prog.set_label(f"{running} building, {len(pending) - running} queued")
                    prog.heartbeat()
    else:
        for circuit in circuits:
            prog.set_label(circuit)
            results.append(build_circuit(circuit, args.year, args.output_root, args.fuel_kg))
            prog.tick()
    prog.close()

    results.sort(key=lambda row: str(row.get("circuit")))
    rows = sum(int(r.get("rows") or 0) for r in results)
    violations = sum(int(r.get("envelope_violations") or 0) for r in results)
    manifest = {
        "schema_version": "m14_energy_twin_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "year": args.year,
        "provenance": "SIMULATED",
        "priors": str(PRIORS.relative_to(ROOT)),
        "fuel_proxy_kg": args.fuel_kg,
        "fuel_proxy_note": ("Constant mean load until CP-19 supplies a per-lap curve. "
                            "Labelled a proxy per section 38; it biases mass and therefore "
                            "every term that carries mass."),
        "rows": rows,
        "envelope_violations": violations,
        "envelope_violation_rate": (violations / rows) if rows else None,
        "envelope_policy": ("Violations are recorded, never clamped (section 28.1). The rate "
                            "is a calibration metric in CP-20: a calibration that lowers RMSE "
                            "while raising this has not improved."),
        "circuits": results,
    }
    (args.output_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "circuits"}, indent=2))
    for result in results:
        if result.get("rows"):
            print(f"  {result['circuit']:<12} rows={result['rows']:>7}  "
                  f"violation={result['envelope_violation_rate']:.1%}  "
                  f"rho_fallback={result['air_density_fallback_rate']:.0%}")
        else:
            print(f"  {result['circuit']:<12} {result.get('skipped', 'no rows')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
