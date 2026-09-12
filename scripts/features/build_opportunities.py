#!/usr/bin/env python3
"""Build the overtake-opportunity dataset (M07, CP-13).

One row per opportunity per decision checkpoint, written to
``data/processed/overtake_opportunities/``. An opportunity is an
attacker-defender pair approaching a Detection Line with the gap plausibly
close enough to matter, defined once per (battle, zone, lap) -- not per
segment, which is the documented way this table ends up an order of magnitude
too large.

**This produces zero rows until the FIA Detection Lines are sourced.** Every
2026 event currently configures ``detection_line_m: null``, and section 19
defines the opportunity at that line. The builder reports the shortfall per
event rather than substituting the Activation Line, because an opportunity
anchored on the wrong line would train a model that is confidently wrong about
when a driver may act.

Usage:
    python scripts/features/build_opportunities.py --year 2026
    python scripts/features/build_opportunities.py --year 2026 --jobs 8
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

from trackshift.features.opportunities import (  # noqa: E402
    CHECKPOINTS,
    LABEL_DEFINITION,
    OPPORTUNITY_SCHEMA_VERSION,
)
from trackshift.progress import Progress  # noqa: E402
from trackshift.rules.config import available_events, load_event_rules, resolve  # noqa: E402
from trackshift.rules.config import RuleConfigError  # noqa: E402

SEGMENTS = ROOT / "data" / "processed" / "segments"
LAKE = ROOT / "data" / "processed" / "telemetry_20m"
OUT = ROOT / "data" / "processed" / "overtake_opportunities"

LAKE_COLUMNS = [
    "year", "event", "session", "driver", "driver_number", "lap", "distance_m",
    "speed_kmh", "gap_ahead_m", "driver_ahead_number", "race_position", "team",
    "brake_on", "track_status",
]


def _number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _gap_s(gap_m: Any, speed_kmh: Any) -> float | None:
    """DistanceToDriverAhead is metres. Converting is not optional."""
    distance, speed = _number_or_none(gap_m), _number_or_none(speed_kmh)
    if distance is None or speed is None or speed <= 0:
        return None
    return distance / (speed / 3.6)


def _at_distance(frame, target: float):
    """The row at or immediately before ``target``; None before the first sample."""
    eligible = frame[frame["distance_m"] <= target]
    return None if eligible.empty else eligible.iloc[-1]


def _checkpoint_features(view, zone: dict, checkpoint: str, cutoff: float,
                         threshold_s: float, trailing: list[float]) -> dict[str, Any]:
    """Features knowable at ``checkpoint``, from a view already truncated at the cutoff.

    The truncation happens in the caller, so nothing here can reach past the
    cutoff even by accident; the registry check in build_opportunity_rows then
    proves it for every row rather than trusting this function.
    """
    from trackshift.rules.eligibility import project_gap_at_line

    if view.empty:
        return {}
    last = view.iloc[-1]
    gap_s = _gap_s(last.get("gap_ahead_m"), last.get("speed_kmh"))

    features: dict[str, Any] = {
        "gap_at_checkpoint": gap_s,
        "closing_rate_s_per_s": trailing[-1] if trailing else None,
        "distance_detection_to_activation": zone["activation_line_m"] - zone["detection_line_m"],
    }
    if zone.get("zone_end_m") is not None:
        features["distance_remaining_in_zone"] = float(zone["zone_end_m"]) - cutoff

    if gap_s is not None:
        speed_mps = (_number_or_none(last.get("speed_kmh")) or 0.0) / 3.6
        remaining = max(0.0, zone["detection_line_m"] - cutoff)
        horizon = remaining / speed_mps if speed_mps > 0 else 0.0
        projection = project_gap_at_line(
            gap_s, trailing[-1] if trailing else 0.0, horizon, trailing, threshold_s=threshold_s
        )
        features.update({
            "p_eligible": projection.p_eligible,
            "projected_gap_at_detection_s": projection.mu_s,
            "projected_gap_sigma_s": projection.sigma_s,
            "eligibility_margin": projection.eligibility_margin_s,
        })

    if checkpoint in ("ACTIVATION", "BRAKING"):
        row = _at_distance(view, zone["activation_line_m"])
        if row is not None:
            features["gap_at_activation_s"] = _gap_s(row.get("gap_ahead_m"), row.get("speed_kmh"))
            features["speed_at_activation_kmh"] = _number_or_none(row.get("speed_kmh"))
        if zone.get("brake_onset_m") is not None:
            features["distance_activation_to_brake"] = float(zone["brake_onset_m"]) - zone["activation_line_m"]
    if checkpoint == "BRAKING":
        features["speed_at_braking_kmh"] = _number_or_none(last.get("speed_kmh"))

    return {key: value for key, value in features.items() if value is not None}


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def zone_lines(rules: dict) -> list[dict[str, Any]]:
    """Detection, activation and zone-end per configured zone, with provenance.

    Reads through ``resolved_zones`` so the lap's single Detection Line counts
    for every zone; reading the raw config here would report an event as
    blocked even with the event-level line configured.
    """
    from trackshift.rules.state_machine import _value, resolved_zones

    zones = resolved_zones(rules)
    out = []
    for index, resolved in enumerate(zones):
        entry: dict[str, Any] = {"zone": resolved.get("zone", index + 1)}
        for field in ("detection_line_m", "activation_line_m", "zone_end_m"):
            node = resolved.get(field) or {}
            entry[field] = _value(node)
            entry[f"{field}_source"] = node.get("value_source")
        out.append(entry)
    return out


def _legacy_zone_lines(rules: dict) -> list[dict[str, Any]]:
    zones = (rules.get("overtake") or {}).get("zones") or []
    out = []
    for index, _ in enumerate(zones):
        entry: dict[str, Any] = {"zone": index + 1}
        for field in ("detection_line_m", "activation_line_m", "zone_end_m"):
            key = f"overtake.zones.{index}.{field}"
            try:
                resolved = resolve(rules, key)
                entry[field] = resolved.value
                entry[f"{field}_source"] = resolved.value_source
            except RuleConfigError:
                entry[field] = None
                entry[f"{field}_source"] = None
        out.append(entry)
    return out


def plan_event(event: str, year: str) -> dict[str, Any]:
    """What this event can contribute, without building anything yet.

    Split out so the buildable and unbuildable events are distinguishable
    before any work happens, and so the manifest can say which is which.
    """
    rules = load_event_rules(event, year)
    zones = zone_lines(rules)
    usable = [z for z in zones if z.get("detection_line_m") is not None]
    return {
        "event": event,
        "zones_configured": len(zones),
        "zones_with_detection_line": len(usable),
        "buildable": bool(usable),
        "blocked_reason": None if usable else (
            "no zone configures detection_line_m; CP-13 defines an opportunity at the "
            "Detection Line and will not anchor on the Activation Line instead"
        ),
        "zones": zones,
    }


def build_event(event: str, year: str, output_root: Path) -> dict[str, Any]:
    """Build one event. Module-level so it pickles into the process pool."""
    plan = plan_event(event, year)
    if not plan["buildable"]:
        return {**plan, "opportunities": 0, "rows": 0, "written": None}

    import pandas as pd

    from trackshift.features.opportunities import (
        OpportunityContext,
        build_opportunity_rows,
        label_zone_exit_v1,
        opportunity_id,
    )
    from trackshift.rules.state_machine import _value, resolved_zones

    rules = load_event_rules(event, year)
    threshold = _value((rules.get("overtake") or {}).get("detection_gap_s")) or 1.0

    zones = []
    for zone in resolved_zones(rules):
        detection = _value(zone.get("detection_line_m"))
        activation = _value(zone.get("activation_line_m"))
        if detection is None or activation is None or not detection < activation:
            continue
        zones.append({
            "zone": zone.get("zone"),
            "detection_line_m": float(detection),
            "activation_line_m": float(activation),
            "zone_end_m": _value(zone.get("zone_end_m")),
        })
    if not zones:
        return {**plan, "buildable": False, "opportunities": 0, "rows": 0, "written": None,
                "blocked_reason": "no zone has both a detection and an activation line in order"}

    display = str(rules.get("event_display") or event.replace("_", " ").title()).replace(" ", "_")
    files = [
        path for path in sorted(LAKE.glob(f"year={year}/event={display}/session=*/telemetry_20m.parquet"))
        if path.parent.name in ("session=Race", "session=Sprint")
    ]
    if not files:
        return {**plan, "opportunities": 0, "rows": 0, "written": None,
                "blocked_reason": "no Race or Sprint data in the lake for this event"}

    frame = pd.concat([pd.read_parquet(path, columns=LAKE_COLUMNS) for path in files], ignore_index=True)
    frame = frame.sort_values(["session", "driver", "lap", "distance_m"], kind="stable")

    # Defender position at the zone exit, looked up by car number rather than
    # inferred from the attacker's own position: zone_exit_v1 is about this pair.
    by_number = {
        (session, lap, str(number)): group
        for (session, lap, number), group in frame.groupby(["session", "lap", "driver_number"], sort=False)
    }

    rows_out: list[dict[str, Any]] = []
    opportunities = 0

    for (session, driver, lap), block in frame.groupby(["session", "driver", "lap"], sort=True):
        block = block.reset_index(drop=True)
        # Green flag only. Section 12 makes race-control transitions hard
        # boundaries, and an approach under Safety Car is not an opportunity.
        status = str(block.iloc[0].get("track_status") or "")
        if status and set(status) != {"1"}:
            continue

        for zone in zones:
            detection = zone["detection_line_m"]
            activation = zone["activation_line_m"]
            entry = _at_distance(block, detection)
            if entry is None:
                continue
            gap_s = _gap_s(entry.get("gap_ahead_m"), entry.get("speed_kmh"))
            if gap_s is None or gap_s >= threshold * 2:
                continue
            defender = _number_or_none(entry.get("driver_ahead_number"))
            if defender is None:
                continue

            lap_end = float(block["distance_m"].max())
            zone_end = float(zone["zone_end_m"]) if zone["zone_end_m"] is not None else lap_end
            zone_end = min(zone_end, lap_end)

            in_zone = block[(block["distance_m"] > activation) & (block["distance_m"] <= zone_end)]
            braking = in_zone[in_zone["brake_on"].fillna(False).astype(bool)]
            brake_m = float(braking.iloc[0]["distance_m"]) if len(braking) else zone_end
            if not detection < activation < brake_m:
                continue

            before = block[block["distance_m"] <= detection].tail(6)
            gaps = [_gap_s(row.gap_ahead_m, row.speed_kmh) for row in before.itertuples()]
            gaps = [g for g in gaps if g is not None]
            trailing = [a - b for a, b in zip(gaps, gaps[1:])] or [0.0]

            exit_row = _at_distance(block, zone_end)
            attacker_exit = _number_or_none(exit_row.get("race_position")) if exit_row is not None else None
            defender_block = by_number.get((session, lap, str(int(defender))))
            defender_exit = None
            if defender_block is not None:
                defender_row = _at_distance(defender_block.sort_values("distance_m"), zone_end)
                if defender_row is not None:
                    defender_exit = _number_or_none(defender_row.get("race_position"))

            geometry = {**zone, "brake_onset_m": brake_m}
            context = OpportunityContext(
                opportunity_id=opportunity_id(year, event, session, lap, zone["zone"], driver, int(defender)),
                year=year, event=event, session=session, lap=int(lap), zone=zone["zone"],
                attacker=driver, defender=str(int(defender)),
                attacker_team=entry.get("team"), regulation_era="2026",
            )
            rows_out.extend(build_opportunity_rows(
                context,
                {"DETECTION": detection, "ACTIVATION": activation, "BRAKING": brake_m},
                lambda checkpoint, cutoff, _block=block, _zone=geometry, _trailing=trailing:
                    _checkpoint_features(
                        _block[_block["distance_m"] <= cutoff], _zone, checkpoint, cutoff,
                        threshold, _trailing,
                    ),
                label=label_zone_exit_v1(attacker_exit, defender_exit),
                outcome_distance_m=zone_end,
            ))
            opportunities += 1

    written = None
    if rows_out:
        target = output_root / f"event={event}"
        target.mkdir(parents=True, exist_ok=True)
        destination = target / "opportunities.parquet"
        pd.DataFrame(rows_out).to_parquet(destination, index=False)
        written = str(destination.relative_to(ROOT))

    return {**plan, "opportunities": opportunities, "rows": len(rows_out), "written": written}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", default="2026")
    parser.add_argument("--event", action="append", help="Event config key; repeat to select several")
    parser.add_argument("--segments-dir", type=Path, default=SEGMENTS)
    parser.add_argument("--output-root", type=Path, default=OUT)
    parser.add_argument("--jobs", type=int, default=1, help="Events built in parallel")
    parser.add_argument("--dry-run", action="store_true", help="Report what each event can contribute")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    events = args.event or available_events(args.year)
    if not events:
        parser.error(f"no rule configuration found for {args.year}")

    plans = [plan_event(event, args.year) for event in events]
    buildable = [p["event"] for p in plans if p["buildable"]]
    blocked = [p for p in plans if not p["buildable"]]

    print(f"events: {len(plans)}  buildable: {len(buildable)}  blocked: {len(blocked)}")
    for plan in blocked:
        print(f"  [BLOCKED] {plan['event']}: {plan['blocked_reason']}")

    results: list[dict[str, Any]] = []
    if buildable and not args.dry_run:
        args.output_root.mkdir(parents=True, exist_ok=True)
        prog = Progress(len(buildable), enabled=not args.no_progress)
        if args.jobs > 1 and len(buildable) > 1:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                futures = {pool.submit(build_event, e, args.year, args.output_root): e for e in buildable}
                pending = set(futures)
                while pending:
                    finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                    for future in finished:
                        result = future.result()
                        results.append(result)
                        prog.set_label(str(result.get("event")))
                        prog.tick()
                    if pending:
                        running = min(len(pending), args.jobs)
                        prog.set_label(f"{running} building, {len(pending) - running} queued")
                        prog.heartbeat()
        else:
            for event in buildable:
                prog.set_label(event)
                results.append(build_event(event, args.year, args.output_root))
                prog.tick()
        prog.close()

    # Sorted before writing: parallel completion order is not deterministic and
    # a manifest that reorders between runs cannot be diffed.
    results.sort(key=lambda row: str(row.get("event")))
    manifest = {
        "schema_version": OPPORTUNITY_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "year": args.year,
        "label_definition": LABEL_DEFINITION,
        "decision_checkpoints": list(CHECKPOINTS),
        "events_total": len(plans),
        "events_buildable": len(buildable),
        "events_blocked": len(blocked),
        "blocked": [{"event": p["event"], "reason": p["blocked_reason"],
                     "zones_configured": p["zones_configured"]} for p in blocked],
        "built": results,
        "opportunities": sum(int(r.get("opportunities") or 0) for r in results),
        "rows": sum(int(r.get("rows") or 0) for r in results),
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "built"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
