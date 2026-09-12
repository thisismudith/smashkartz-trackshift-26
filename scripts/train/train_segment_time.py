#!/usr/bin/env python3
"""Fit the segment-time transition, dE -> dt (M16, CP-21).

The causal transition the planner consumes (section 30)::

    t_k(dE, L) = t_base,k - a_k * dE + c_k * L

``a_k`` is segment *k*'s energy sensitivity -- the local slope that becomes the
shadow price. Without it the DP has nothing to price energy against.

**Physical plausibility outranks MAE** (section 30). A segment whose fit says
deploying energy makes it slower has not found a real effect, it has found
confounding: on a lap where a driver deployed hard they were usually defending
or stuck in traffic, and both cost time. Such a segment is refused rather than
published, and the refusal is counted.

Usage:
    python scripts/train/train_segment_time.py --year 2026
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
    MAE_TARGET_S,
    SegmentResponse,
    SegmentTimeError,
    check_monotonic_in_energy,
    check_sensitivity_by_segment_type,
    extrapolation_sanity,
)

SEGMENTS = ROOT / "data" / "processed" / "segments"
TWIN = ROOT / "data" / "processed" / "energy_twin"
OUT = ROOT / "artifacts" / "models" / "segment_time"

#: A segment needs this many clean observations before a slope means anything.
MIN_OBSERVATIONS = 40


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    import numpy as np
    import pandas as pd

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", default="2026")
    parser.add_argument("--sessions", default="Race")
    parser.add_argument("--min-observations", type=int, default=MIN_OBSERVATIONS)
    parser.add_argument("--output-root", type=Path, default=OUT)
    args = parser.parse_args()

    sessions = tuple(s.strip() for s in args.sessions.split(",") if s.strip())

    parts = []
    for path in sorted(SEGMENTS.glob("circuit=*/segments.parquet")):
        circuit = path.parent.name.removeprefix("circuit=")
        frame = pd.read_parquet(path)
        frame = frame[(frame["year"].astype(str) == str(args.year))
                      & (frame["session"].isin(sessions))]
        if frame.empty:
            continue
        frame["circuit"] = circuit
        twin_path = TWIN / f"circuit={circuit}" / "energy_twin.parquet"
        if twin_path.exists():
            twin = pd.read_parquet(twin_path,
                                   columns=["year", "event", "session", "driver", "lap",
                                            "segment_id", "ers_energy_used_est_mj"])
            keys = ["year", "event", "session", "driver", "lap", "segment_id"]
            twin = twin.sort_values(keys)
            # Per-segment deployment, not the running total.
            twin["delta_e_mj"] = twin.groupby(
                ["year", "event", "session", "driver", "lap"])["ers_energy_used_est_mj"].diff()
            frame = frame.merge(twin[keys + ["delta_e_mj"]].drop_duplicates(keys),
                                on=keys, how="left")
        parts.append(frame)
    if not parts:
        raise SystemExit(f"no {args.year} segments for sessions {sessions}")
    frame = pd.concat(parts, ignore_index=True)

    frame["t"] = pd.to_numeric(frame["segment_time_s_offline"], errors="coerce")
    frame["dE"] = pd.to_numeric(frame.get("delta_e_mj"), errors="coerce")
    frame["life"] = pd.to_numeric(frame.get("tyre_life_laps"), errors="coerce")
    # Green flag only. A Safety Car lap's segment time says nothing about energy.
    if "track_status" in frame.columns:
        frame = frame[frame["track_status"].astype(str).str.strip() == "1"]
    frame = frame[frame["t"].notna() & (frame["t"] > 0) & frame["dE"].notna()]

    responses: list[SegmentResponse] = []
    refused: dict[str, int] = {}
    diagnostics: list[dict[str, Any]] = []

    for (circuit, segment_id), block in frame.groupby(["circuit", "segment_id"], sort=True):
        if len(block) < args.min_observations:
            refused["TOO_FEW_OBSERVATIONS"] = refused.get("TOO_FEW_OBSERVATIONS", 0) + 1
            continue
        energy = block["dE"].to_numpy(dtype=float)
        life = block["life"].fillna(0.0).to_numpy(dtype=float)
        time = block["t"].to_numpy(dtype=float)
        if np.ptp(energy) <= 0:
            refused["NO_ENERGY_VARIATION"] = refused.get("NO_ENERGY_VARIATION", 0) + 1
            continue

        design = np.column_stack([np.ones_like(energy), energy, life])
        try:
            beta, *_ = np.linalg.lstsq(design, time, rcond=None)
        except np.linalg.LinAlgError:
            refused["SINGULAR_DESIGN"] = refused.get("SINGULAR_DESIGN", 0) + 1
            continue
        t_base, slope_energy, slope_life = (float(b) for b in beta)

        # t = t_base - a_k * dE, so a_k is the negated slope.
        a_k = -slope_energy
        c_k = max(0.0, slope_life)
        try:
            response = SegmentResponse(
                segment_id=f"{circuit}:{segment_id}", t_base_s=t_base,
                a_k_s_per_mj=a_k, c_k_s_per_unit_lift=c_k,
                kind=str(block["kind"].iloc[0]) if "kind" in block.columns else None,
                n_observations=len(block))
        except SegmentTimeError:
            # Section 30: a non-positive a_k is confounding, not a finding.
            # Refuse it rather than publish a transition that would tell the DP
            # deploying energy costs time.
            refused["NON_POSITIVE_ENERGY_SENSITIVITY"] = \
                refused.get("NON_POSITIVE_ENERGY_SENSITIVITY", 0) + 1
            continue

        responses.append(response)
        predicted = design @ beta
        diagnostics.append({
            "segment": response.segment_id, "kind": response.kind,
            "n": len(block), "a_k_s_per_mj": a_k, "c_k": c_k, "t_base_s": t_base,
            "mae_s": float(np.abs(time - predicted).mean()),
            "monotonic": check_monotonic_in_energy(
                response, energy_range_mj=(0.0, max(0.1, float(energy.max()))))["monotonic"],
            "extrapolation_safe": not extrapolation_sanity(
                response, observed_max_mj=float(max(0.01, energy.max())))["goes_negative"],
        })

    if not responses:
        raise SystemExit("no segment produced a physically plausible transition; "
                         f"refusals: {refused}")

    by_type = check_sensitivity_by_segment_type(responses)
    mae = float(np.mean([d["mae_s"] for d in diagnostics]))

    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.output_root / version
    target.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(diagnostics).to_parquet(target / "segment_responses.parquet", index=False)

    manifest = {
        "schema_version": "m16_segment_time_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "year": args.year,
        "sessions": list(sessions),
        "form": "t_k(dE, L) = t_base,k - a_k * dE + c_k * L",
        "segments_fitted": len(responses),
        "segments_refused": refused,
        "refusal_note": ("A non-positive a_k is refused, not published. Section 30 puts "
                         "physical plausibility above MAE: a transition saying energy "
                         "costs time would send the planner the wrong way, and the usual "
                         "cause is that hard deployment coincides with defending or "
                         "traffic."),
        "mean_in_sample_mae_s": mae,
        "mae_target_s": MAE_TARGET_S,
        "mae_target_met": mae <= MAE_TARGET_S,
        "energy_sensitivity_by_segment_kind": by_type,
        "all_monotonic": all(d["monotonic"] for d in diagnostics),
        "all_extrapolation_safe": all(d["extrapolation_safe"] for d in diagnostics),
        "gates": {
            "every_a_k_positive": True,  # enforced by SegmentResponse construction
            "monotone_in_energy": all(d["monotonic"] for d in diagnostics),
            "straight_sensitivity_exceeds_corner": by_type.get("straight_exceeds_corner"),
            "mae_below_target": mae <= MAE_TARGET_S,
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (args.output_root / "latest.txt").write_text(version, encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
