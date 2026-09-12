#!/usr/bin/env python3
"""Apply the override / ERS-mode discriminator (M35, CP-18b).

Above the separation speed, a car deploying more than the normal envelope allows
cannot be in normal mode. That is rare in this project: public telemetry
constraining a rival's energy decision rather than hinting at it.

Three things keep it honest, and all three are in the module rather than here:
the discriminability gate fires first, the margin scales with the twin's own
uncertainty rather than a fixed kW figure, and the output is a probability with
an ``INFERRED`` label attached.

**Never use this as a training label** (section 24). It is a feature and a prior
for the rival-state model, never ground truth. The temptation is strong
precisely because it feels observational.

The false-positive control comes free: run it on 2022-2025, where no override
mechanism existed, and every detection is wrong by construction.

Usage:
    python scripts/twin/apply_override_discriminator.py --year 2026
    python scripts/twin/apply_override_discriminator.py --year 2024   # control
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

from trackshift.rules.api import (  # noqa: E402
    load_event_rules,
    max_electrical_power_kw,
    separation_speed_kmh,
)
from trackshift.twin.api import (  # noqa: E402
    DEFAULT_K_SIGMA,
    discriminate,
    historical_false_positive_rate,
)

TWIN = ROOT / "data" / "processed" / "energy_twin"
SEGMENTS = ROOT / "data" / "processed" / "segments"
UNCERTAINTY = ROOT / "data" / "processed" / "twin_uncertainty"
OUT = ROOT / "data" / "processed" / "override_state"


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def twin_sigma_kw(default: float = 25.0) -> tuple[float, str]:
    """Deployment-power uncertainty, from CP-22 where it exists.

    Falling back to a fixed figure is exactly what CP-18b warns against, so the
    fallback is reported rather than silently applied.
    """
    manifest = UNCERTAINTY / "run_manifest.json"
    if not manifest.exists():
        return default, f"CP-22 uncertainty not built; using a {default} kW default"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    spread = (data.get("coverage") or {}).get("mean_spread")
    if not spread:
        return default, f"CP-22 manifest has no spread; using a {default} kW default"
    # An 80% interval spans about 2.56 sigma. The twin's time spread is converted
    # to a power scale by the ratio the balance itself implies.
    sigma_time_s = float(spread) / 2.5631
    return max(5.0, sigma_time_s * 100.0), (
        f"from CP-22: 80% interval mean spread {spread:.3f} s -> sigma {sigma_time_s:.3f} s")


def main() -> int:
    import pandas as pd

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", default="2026")
    parser.add_argument("--circuit", action="append")
    parser.add_argument("--k-sigma", type=float, default=DEFAULT_K_SIGMA)
    parser.add_argument("--sigma-kw", type=float, help="Override the CP-22 sigma")
    parser.add_argument("--output-root", type=Path, default=OUT)
    args = parser.parse_args()

    sigma, sigma_source = (args.sigma_kw, "supplied on the command line") \
        if args.sigma_kw else twin_sigma_kw()

    circuits = args.circuit or sorted(
        p.parent.name.removeprefix("circuit=") for p in TWIN.glob("circuit=*/energy_twin.parquet"))
    if not circuits:
        raise SystemExit("no energy twin output; run build_energy_twin.py first")

    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    all_inferences = []

    for circuit in circuits:
        twin = pd.read_parquet(TWIN / f"circuit={circuit}" / "energy_twin.parquet")
        twin = twin[twin["year"].astype(str) == str(args.year)]
        if twin.empty:
            results.append({"circuit": circuit, "skipped": f"no {args.year} rows"})
            continue

        segments = pd.read_parquet(SEGMENTS / f"circuit={circuit}" / "segments.parquet",
                                   columns=["year", "event", "session", "driver", "lap",
                                            "segment_id", "entry_speed_kmh"])
        keys = ["year", "event", "session", "driver", "lap", "segment_id"]
        frame = twin.merge(segments.drop_duplicates(keys), on=keys, how="left")

        try:
            rules = load_event_rules(f"{circuit}_grand_prix", str(args.year))
        except Exception:
            results.append({"circuit": circuit, "skipped": "no rule configuration"})
            continue
        separation = separation_speed_kmh(rules)
        if separation is None:
            results.append({"circuit": circuit, "skipped": "no separation speed configured"})
            continue

        rows = []
        for row in frame.itertuples():
            speed = getattr(row, "entry_speed_kmh", None)
            deploy = getattr(row, "ers_deploy_power_est_kw", None)
            if speed is None or deploy is None or pd.isna(speed) or pd.isna(deploy):
                continue
            cap = max_electrical_power_kw(float(speed), "normal", rules)
            inference = discriminate(float(speed), float(deploy), cap,
                                     separation_speed_kmh=separation,
                                     sigma_kw=sigma, k_sigma=args.k_sigma)
            all_inferences.append(inference)
            rows.append({
                "year": row.year, "event": row.event, "session": row.session,
                "driver": row.driver, "lap": row.lap, "segment_id": row.segment_id,
                "entry_speed_kmh": float(speed),
                "discriminable": inference.discriminable,
                "ers_mode_inferred": inference.ers_mode_inferred,
                "override_active_inferred": inference.override_active_inferred,
                "excess_kw": inference.excess_kw,
                "normal_cap_kw": inference.normal_cap_kw,
                "override_unavailable_reason": inference.reason,
                "provenance": inference.provenance,
            })

        written = None
        if rows:
            target = args.output_root / f"circuit={circuit}"
            target.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_parquet(target / "override_state.parquet", index=False)
            written = str((target / "override_state.parquet").relative_to(ROOT))

        discriminable = sum(1 for r in rows if r["discriminable"])
        override = sum(1 for r in rows if r["ers_mode_inferred"] == "OVERRIDE")
        results.append({
            "circuit": circuit, "rows": len(rows),
            "discriminable": discriminable,
            "discriminable_share": (discriminable / len(rows)) if rows else None,
            "override_detections": override,
            "override_share_of_discriminable": (override / discriminable) if discriminable else None,
            "written": written,
        })

    control = historical_false_positive_rate(all_inferences)
    is_control_season = str(args.year) != "2026"
    manifest = {
        "schema_version": "m35_override_state_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "year": args.year,
        "provenance": "INFERRED",
        "never_a_label": ("Section 24: this is a feature and a prior for the rival-state "
                          "model, never ground truth and never a training label."),
        "sigma_kw": sigma,
        "sigma_source": sigma_source,
        "k_sigma": args.k_sigma,
        "is_control_season": is_control_season,
        "control_note": ("Every OVERRIDE in a pre-2026 season is a false positive by "
                         "construction: the mechanism did not exist. A non-trivial rate "
                         "means the twin over-estimates power -- fix the twin in CP-20, "
                         "not the margin."),
        "detection_summary": control,
        "circuits": results,
    }
    (args.output_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "circuits"}, indent=2))
    for r in results:
        if r.get("rows"):
            print(f"  {r['circuit']:<12} rows={r['rows']:>7}  "
                  f"discriminable={r['discriminable_share']:.1%}  "
                  f"override={r['override_detections']:>6}")
        else:
            print(f"  {r['circuit']:<12} {r.get('skipped')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
