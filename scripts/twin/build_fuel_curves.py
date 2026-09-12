#!/usr/bin/env python3
"""Build causal per-lap fuel curves (M34, CP-19).

Fuel is not in the public feed. This is an estimator, tagged ``INFERRED`` and
labelled a proxy wherever it surfaces (sections 11, 38).

The twin supplies the **shape** and a per-circuit constant supplies the
**level**. That split is deliberate and is what CP-19 prescribes: the twin's
uncalibrated ICE work gives roughly 0.32 kg/lap at Monaco against a real figure
nearer 1.8, because the priors have not been fitted yet. Using that level
directly would have every car finish with most of its fuel still aboard. So the
lap-to-lap *variation* comes from ``ice_work_est_mj`` -- which knows that a
qualifying lap burns more than a cool-down lap -- and the level is scaled per
circuit so a green-flag race ends near the reserve.

The scale factor is recorded per circuit. It is a calibration, not a
measurement, and CP-20 is expected to move the priors underneath it.

Usage:
    python scripts/twin/build_fuel_curves.py --year 2026
    python scripts/twin/build_fuel_curves.py --year 2026 --jobs 8
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
from trackshift.twin.api import (  # noqa: E402
    FuelError,
    circuits_available,
    consumption_from_ice_work,
    estimate_fuel_curve,
    read_partition,
    start_fuel_kg,
)

TWIN = ROOT / "data" / "processed" / "energy_twin"
OUT = ROOT / "data" / "processed" / "fuel_curves"

#: Scrutineering sample the car must still carry at the flag. A curve that ends
#: on exactly zero has not been modelled, it has been wished.
RESERVE_KG = 1.0

#: FIA 2026 race fuel allowance. UNVERIFIED pending a citation in CP-03; used
#: only as an upper bound, never as the value itself.
REGULATORY_MAXIMUM_KG = 100.0

#: Sprint is out of scope for this project.
RACE_SESSIONS = ("Race",)

#: Sessions CP-20 calibrates on. Fuel there is not race-calibrated -- there is
#: no race distance to anchor against -- so the level comes from a documented
#: per-session prior and only the within-stint burn is measured.
CALIBRATION_SESSIONS = ("Practice 1", "Qualifying")

#: Stint-start fuel priors, kg. These are priors, not measurements, and the
#: uncertainty below says so.
#:
#: Qualifying is run on minimum fuel -- that is the whole point of the session,
#: and it is the most reliable fuel statement available anywhere in the feed.
#: Practice 1 splits by CP-07 lap class: a PUSH stint is a qualifying
#: simulation on light fuel, while LONG_RUN and RACE_PACE stints are
#: race-representative and heavy. The class is therefore doing real work here,
#: not decoration.
STINT_START_PRIOR_KG = {
    "Qualifying": 20.0,
    "Practice 1:PUSH": 30.0,
    "Practice 1:RACE": 90.0,
    "Practice 1": 60.0,
}

#: Wide on purpose. The level is a prior and consumers must be able to see that
#: it is far less certain than a race-calibrated curve.
PRIOR_UNCERTAINTY_KG = 15.0


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None



def _stint_prior_kg(session: str, lap_classes: set[str]) -> tuple[float, str]:
    """Stint-start fuel prior for a non-race session, and why it was chosen."""
    if session == "Qualifying":
        return STINT_START_PRIOR_KG["Qualifying"], "qualifying is run on minimum fuel"
    if lap_classes & {"LONG_RUN", "RACE_PACE"}:
        return (STINT_START_PRIOR_KG["Practice 1:RACE"],
                "stint contains LONG_RUN/RACE_PACE laps: race-representative load")
    if "PUSH" in lap_classes:
        return (STINT_START_PRIOR_KG["Practice 1:PUSH"],
                "stint is a PUSH qualifying simulation: light fuel")
    return STINT_START_PRIOR_KG.get(session, 60.0), "no CP-07 class evidence for this stint"


def _calibration_session_curve(event, session, block, year, classes):
    """Per-lap fuel for a Practice or Qualifying session.

    The *burn* is measured -- it comes from the twin's ICE work, scaled by the
    event's race calibration. The *level* is a prior, because a practice stint
    has no race distance to anchor against. Both facts travel with every row:
    ``fuel_level_is_calibrated`` is false here and the uncertainty is wide.

    What actually matters to CP-20 is that mass now varies lap to lap within a
    stint, which it could not before. With mass pinned at 800 kg for every row
    the physics had no lever that moved, which is why the fit kept trying to
    switch its own power correction off.
    """
    rows = []
    scale = getattr(_calibration_session_curve, "_scale", 1.0)
    for driver, laps in block.groupby("driver", sort=True):
        laps = laps.sort_values("lap")
        lap_classes = set()
        if classes is not None:
            match = classes[(classes["event"] == event) & (classes["driver"] == driver)]
            lap_classes = set(match["practice_lap_class"].dropna().astype(str))
        start, reason = _stint_prior_kg(str(session), lap_classes)
        remaining = start
        for lap_number, burned in zip(laps["lap"], laps["raw_kg"]):
            remaining = max(0.0, remaining - float(burned) * scale)
            rows.append({
                "year": year, "event": event, "session": session, "driver": driver,
                "lap": int(lap_number),
                "fuel_load_kg_est": remaining,
                "fuel_load_uncertainty_kg": PRIOR_UNCERTAINTY_KG,
                "fuel_consumption_kg_est": float(burned) * scale,
                "fuel_clipped_at_zero": remaining <= 0.0,
                "fuel_level_is_calibrated": False,
                "fuel_level_prior_reason": reason,
                "provenance": "INFERRED",
            })
    return rows


def build_circuit(circuit: str, year: str, output_root: Path) -> dict[str, Any]:
    """Build one circuit's fuel curves. Module-level so it pickles."""
    import pandas as pd

    frame = read_partition(TWIN, "energy_twin.parquet", year=year, circuit=circuit,
                           columns=["year", "event", "session", "driver", "lap",
                                    "ice_work_est_mj"])
    if frame.empty:
        return {"circuit": circuit, "skipped": "no energy twin output"}
    frame = frame[frame["session"].isin(RACE_SESSIONS + CALIBRATION_SESSIONS)]
    if frame.empty:
        return {"circuit": circuit, "skipped": f"no {year} race-like rows"}

    # Per-lap ICE work: the shape the twin actually knows about.
    per_lap = (frame.groupby(["event", "session", "driver", "lap"], as_index=False)
               ["ice_work_est_mj"].sum())
    per_lap["raw_kg"] = per_lap["ice_work_est_mj"].map(consumption_from_ice_work)

    classes = None
    class_files = sorted((ROOT / "data" / "processed" / "practice_lap_classes").glob("**/*.parquet"))
    if class_files:
        classes = pd.concat([pd.read_parquet(f) for f in class_files], ignore_index=True)
        classes = classes[["event", "driver", "practice_lap_class"]].drop_duplicates()

    rows: list[dict[str, Any]] = []
    calibrations: list[dict[str, Any]] = []

    ordered = sorted(per_lap.groupby(["event", "session"], sort=True),
                     key=lambda item: (item[0][0], item[0][1] not in RACE_SESSIONS))
    for (event, session), block in ordered:
        if session not in RACE_SESSIONS:
            rows.extend(_calibration_session_curve(event, session, block, year, classes))
            continue
        race_laps = int(block["lap"].max())
        if race_laps < 2:
            continue
        # Level. Two separate quantities, and conflating them is what made the
        # first two attempts wrong.
        #
        #   scale       - a per-event correction to the twin's ICE work, which
        #                 runs about five times low until CP-20 moves the
        #                 priors. One number for the whole event.
        #   start fuel  - how much each car was actually fuelled with. In F1
        #                 that is decided per car before the race, not shared
        #                 across the grid.
        #
        # A grid-wide start fuel forces a choice between half the field running
        # dry (calibrate to the median) and the field being over-fuelled
        # (calibrate to the maximum). Per-car fuelling is both the physical
        # truth and free of that trade-off.
        #
        # Start fuel is calibrated from the completed race, so it is a
        # calibration rather than a live estimate -- but it is a single pre-race
        # constant, not lap-level lookahead. The curve itself stays causal: fuel
        # at lap k subtracts only laps up to k.
        laps_by_driver = block.groupby("driver")["lap"].max()
        finishers = laps_by_driver[laps_by_driver >= race_laps - 1].index
        totals = block[block["driver"].isin(finishers)].groupby("driver")["raw_kg"].sum()
        if totals.empty:
            totals = block.groupby("driver")["raw_kg"].sum()
        reference_total = float(totals.median())
        if reference_total <= 0:
            continue
        target_burn = max(0.0, REGULATORY_MAXIMUM_KG - RESERVE_KG)
        scale = target_burn / reference_total
        calibrated_rate = reference_total * scale / race_laps
        # Non-race sessions reuse the event's burn scale: the same car burns at
        # the same rate whatever session it is, only the starting load differs.
        _calibration_session_curve._scale = scale

        driver_totals = block.groupby("driver")["raw_kg"].sum()
        driver_laps = laps_by_driver

        for driver, laps in block.groupby("driver", sort=True):
            laps = laps.sort_values("lap")
            consumption = [float(v) * scale for v in laps["raw_kg"]]
            # Per-car start fuel: what this car burned, plus the reserve. A car
            # that retired is fuelled for the full distance at its own observed
            # rate, so its curve does not end artificially low.
            burned = float(driver_totals.get(driver, 0.0)) * scale
            ran = int(driver_laps.get(driver, race_laps)) or race_laps
            full_distance_burn = burned * race_laps / max(1, ran)
            start = min(REGULATORY_MAXIMUM_KG, full_distance_burn + RESERVE_KG)
            try:
                curve = estimate_fuel_curve(consumption, start)
            except FuelError:
                continue
            for lap_number, estimate in zip(laps["lap"], curve):
                rows.append({
                    "year": year, "event": event, "session": session, "driver": driver,
                    "lap": int(lap_number),
                    "fuel_load_kg_est": estimate.fuel_load_kg_est,
                    "fuel_load_uncertainty_kg": estimate.fuel_load_uncertainty_kg,
                    "fuel_consumption_kg_est": estimate.consumption_kg,
                    "fuel_clipped_at_zero": estimate.clipped_at_zero,
                    "fuel_level_is_calibrated": True,
                    "provenance": estimate.provenance,
                })

    written = None
    if rows:
        target = output_root / f"circuit={circuit}"
        target.mkdir(parents=True, exist_ok=True)
        destination = target / "fuel_curves.parquet"
        pd.DataFrame(rows).to_parquet(destination, index=False)
        written = str(destination.relative_to(ROOT))

    # The 0-3 kg gate applies to cars that went the distance. A car that retired
    # on lap 20 correctly still has most of its fuel; scoring it as a failure
    # would push the calibration to starve every finisher.
    final_laps: dict[tuple, tuple[int, float]] = {}
    session_max: dict[tuple, int] = {}
    for r in rows:
        # Only a race has a distance to go. Scoring a practice stint against the
        # 0-3 kg gate would push the calibration to starve every finisher.
        if r["session"] not in RACE_SESSIONS:
            continue
        key = (r["event"], r["session"], r["driver"])
        session_key = (r["event"], r["session"])
        session_max[session_key] = max(session_max.get(session_key, 0), r["lap"])
        if key not in final_laps or r["lap"] > final_laps[key][0]:
            final_laps[key] = (r["lap"], r["fuel_load_kg_est"])
    finishers = [
        value for (event, session, _), value in final_laps.items()
        if value[0] >= session_max[(event, session)] - 1
    ]
    finals = [fuel for _, fuel in finishers]
    all_finals = [fuel for _, fuel in final_laps.values()]

    return {
        "circuit": circuit,
        "rows": len(rows),
        "drivers": len(final_laps),
        "finishers": len(finishers),
        "median_final_fuel_all_drivers_kg": (sorted(all_finals)[len(all_finals) // 2]
                                             if all_finals else None),
        "median_final_fuel_kg": (sorted(finals)[len(finals) // 2] if finals else None),
        "finishers_within_0_to_3_kg": (sum(1 for f in finals if 0.0 <= f <= 3.0) / len(finals)
                                       if finals else None),
        "clipped_rows": sum(1 for r in rows if r["fuel_clipped_at_zero"]),
        "calibrations": calibrations,
        "written": written,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", default="2026")
    parser.add_argument("--circuit", action="append")
    parser.add_argument("--twin-dir", type=Path, default=TWIN)
    parser.add_argument("--output-root", type=Path, default=OUT)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    circuits = args.circuit or circuits_available(args.twin_dir, "energy_twin.parquet")
    if not circuits:
        parser.error(f"no energy twin output under {args.twin_dir}; run build_energy_twin.py first")

    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    prog = Progress(len(circuits), enabled=not args.no_progress)

    if args.jobs > 1 and len(circuits) > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(build_circuit, c, args.year, args.output_root): c for c in circuits}
            pending = set(futures)
            while pending:
                finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in finished:
                    results.append(future.result())
                    prog.set_label(str(results[-1].get("circuit")))
                    prog.tick()
                if pending:
                    prog.set_label(f"{min(len(pending), args.jobs)} building")
                    prog.heartbeat()
    else:
        for circuit in circuits:
            prog.set_label(circuit)
            results.append(build_circuit(circuit, args.year, args.output_root))
            prog.tick()
    prog.close()

    results.sort(key=lambda row: str(row.get("circuit")))
    manifest = {
        "schema_version": "m34_fuel_curves_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "year": args.year,
        "provenance": "INFERRED",
        "reserve_kg": RESERVE_KG,
        "regulatory_maximum_kg": REGULATORY_MAXIMUM_KG,
        "level_policy": (
            "Shape from the twin's ice_work_est_mj; level calibrated per event so a "
            "green-flag race ends near the reserve. The scale factor is recorded per "
            "event because it is a calibration, not a measurement -- the uncalibrated "
            "twin runs about five times low until CP-20 moves the priors."
        ),
        "rows": sum(int(r.get("rows") or 0) for r in results),
        "circuits": results,
    }
    (args.output_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "circuits"}, indent=2))
    for result in results:
        if result.get("rows") and result.get("median_final_fuel_kg") is not None:
            print(f"  {result['circuit']:<12} rows={result['rows']:>6}  "
                  f"final_fuel_median={result['median_final_fuel_kg']:.2f} kg  "
                  f"finishers_within_0_3kg={result['finishers_within_0_to_3_kg']:.0%} "
                  f"({result['finishers']}/{result['drivers']} raced the distance)")
        elif result.get("rows"):
            print(f"  {result['circuit']:<12} rows={result['rows']:>6}  "
                  "no race session; practice/qualifying fuel is prior-based only")
        else:
            print(f"  {result['circuit']:<12} {result.get('skipped', 'no rows')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
