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

from trackshift.features.battle_join import (  # noqa: E402
    BattleIndex,
    build_number_to_code,
    load_episodes,
)
from trackshift.features.opportunity_context import (  # noqa: E402
    build_lap_context,
    build_segment_geometry,
    coerce_context_dtypes,
    pair_context,
)
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
BATTLES = ROOT / "data" / "processed" / "battle_episodes"
WEATHER = ROOT / "data" / "processed" / "weather_overlay"
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


def _at_offset(journey, target: float):
    """The row at or immediately before ``target`` metres since the Detection Line."""
    eligible = journey[journey["offset_m"] <= target]
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
        "distance_detection_to_activation": zone["activation_offset_m"],
    }
    if zone.get("zone_end_offset_m") is not None:
        features["distance_remaining_in_zone"] = float(zone["zone_end_offset_m"]) - cutoff

    unavailable: dict[str, dict[str, Any]] = {}
    if gap_s is not None:
        speed_mps = (_number_or_none(last.get("speed_kmh")) or 0.0) / 3.6
        # Distance still to run to the Detection Line. Zero at and after it,
        # which is the causally honest horizon for a projection made there.
        remaining = max(0.0, -float(last.get("offset_m", 0.0)))
        horizon = remaining / speed_mps if speed_mps > 0 else 0.0
        projection = project_gap_at_line(
            gap_s, trailing[-1] if trailing else 0.0, horizon, trailing, threshold_s=threshold_s
        )
        features.update({
            "p_eligible": projection.p_eligible,
            "projected_gap_at_detection_s": projection.mu_s,
            "eligibility_margin": projection.eligibility_margin_s,
        })
        # The historical floor-only value is not a decision-point-varying C6
        # uncertainty estimate. Keep the public field unavailable until a real
        # causal uncertainty source is materialised; never train on 0.05.
        unavailable["projected_gap_sigma_s"] = {
            "value": None,
            "unit": "s",
            "provenance": "INFERRED",
            "reason": "UNAVAILABLE_C6: decision-point-varying gap uncertainty is not materialised",
        }
        features["projected_gap_sigma_s"] = None

    if checkpoint in ("ACTIVATION", "BRAKING"):
        row = _at_offset(view, zone["activation_offset_m"])
        if row is not None:
            features["gap_at_activation_s"] = _gap_s(row.get("gap_ahead_m"), row.get("speed_kmh"))
            features["speed_at_activation_kmh"] = _number_or_none(row.get("speed_kmh"))
        if zone.get("brake_offset_m") is not None:
            features["distance_activation_to_brake"] = float(zone["brake_offset_m"]) - zone["activation_offset_m"]
    if checkpoint == "BRAKING":
        features["speed_at_braking_kmh"] = _number_or_none(last.get("speed_kmh"))

    if unavailable:
        features["unavailable_quantities"] = unavailable
    return features


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


def _circuit_key(event: str) -> str:
    """C1 partitions by circuit; the rules config keys by event."""
    return event.removesuffix("_grand_prix")


def _event_overlay(root: Path, filename: str, event: str, year: str, sessions: set[str],
                   columns: list[str] | None = None):
    """One circuit partition, filtered to this year and the sessions in play.

    Returns ``None`` rather than raising when the overlay has not been built:
    a missing CP-06 partition costs the weather features for that event and
    nothing else, and the coverage is reported per event so the hole is visible.
    """
    import pandas as pd

    path = root / f"circuit={_circuit_key(event)}" / filename
    if not path.exists():
        return None
    if columns:
        # Overlay partitions do not all carry the same columns -- an event whose
        # weather join found no samples writes a narrower file. Ask only for
        # what this partition actually has, so one thin circuit costs its own
        # features rather than failing the whole build.
        import pyarrow.parquet as pq

        available = set(pq.ParquetFile(path).schema.names)
        columns = [c for c in columns if c in available]
        if "year" not in columns:
            return None
    frame = pd.read_parquet(path, columns=columns or None)
    frame = frame[frame["year"].astype(str) == str(year)]
    if "session" in frame.columns and sessions:
        frame = frame[frame["session"].astype(str).isin(sessions)]
    return frame if len(frame) else None


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
        # No in-lap ordering requirement: the Detection Line sits near the lap
        # end, so an activation zone earlier in lap distance is on the following
        # lap, not out of order. Ordering is checked per opportunity in distance
        # travelled since detection.
        if detection is None or activation is None or detection == activation:
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

    # A2: the causal C8 join. C8 names both cars by code; the telemetry knows the
    # defender only by number, so the map comes from the lake, which has both.
    # Built per session because car numbers are reused between seasons.
    # Segment rows carry each episode's within-lap extent. Without them a pair
    # with two episodes on one lap is unresolvable, which is 28% of 2026.
    battle_index = BattleIndex(load_episodes(
        BATTLES / "battle_episodes.jsonl",
        segment_rows=BATTLES / "battle_segment_rows.jsonl"))
    number_to_code = build_number_to_code(
        frame[["year", "event", "session", "driver", "driver_number"]]
        .drop_duplicates().to_dict("records"))
    join_counts: dict[str, int] = {}

    # CP-13 feature groups. The data was never missing -- it is keyed on exactly
    # the (year, event, session, driver, lap) this builder already has, and was
    # simply never joined, which left CP-14 fitting on three features.
    sessions_in_play = {str(v) for v in frame["session"].unique()}
    segments_frame = _event_overlay(
        SEGMENTS, "segments.parquet", event, year, sessions_in_play,
        columns=["year", "event", "session", "driver", "lap", "segment_id",
                 "start_distance_m", "end_distance_m", "corner_type", "sector",
                 "tyre_compound", "tyre_life_laps", "team"])
    weather_frame = _event_overlay(
        WEATHER, "weather_overlay.parquet", event, year, sessions_in_play,
        columns=["year", "event", "session", "driver", "lap",
                 "wind_head_component_mps", "wind_cross_component_mps",
                 "track_temperature_c", "wet_track_flag"])
    lap_context = build_lap_context(segments_frame, weather_frame)
    segment_geometry = build_segment_geometry(segments_frame)

    rows_out: list[dict[str, Any]] = []
    opportunities = 0

    for (session, driver), stint in frame.groupby(["session", "driver"], sort=True):
        # A continuous distance axis for this driver's whole session. The
        # Detection Line sits near the lap end, so an opportunity usually runs
        # into the following lap; a per-lap frame cannot express that at all.
        stint = stint.sort_values(["lap", "distance_m"], kind="stable").reset_index(drop=True)
        lap_end = stint.groupby("lap")["distance_m"].max()
        lap_start = lap_end.cumsum().shift(fill_value=0.0)
        stint["cum_m"] = stint["distance_m"] + stint["lap"].map(lap_start).astype(float)

        for lap, block in stint.groupby("lap", sort=True):
            status = str(block.iloc[0].get("track_status") or "")
            # Green flag only. Section 12 makes race-control transitions hard
            # boundaries, and an approach under Safety Car is not an opportunity.
            if status and set(status) != {"1"}:
                continue
            lap_length = float(lap_end.get(lap, 0.0) or 0.0)
            if lap_length <= 0:
                continue

            for zone in zones:
                detection, activation = zone["detection_line_m"], zone["activation_line_m"]
                entry = _at_distance(block, detection)
                if entry is None:
                    continue
                gap_s = _gap_s(entry.get("gap_ahead_m"), entry.get("speed_kmh"))
                if gap_s is None or gap_s >= threshold * 2:
                    continue
                defender = _number_or_none(entry.get("driver_ahead_number"))
                if defender is None:
                    continue

                zone_end = float(zone["zone_end_m"]) if zone["zone_end_m"] is not None else activation
                activation_offset = (activation - detection) % lap_length
                zone_end_offset = (zone_end - detection) % lap_length
                if activation_offset <= 0 or zone_end_offset <= activation_offset:
                    continue

                origin = float(entry["cum_m"])
                journey = stint[(stint["cum_m"] >= origin - 300.0)
                                & (stint["cum_m"] <= origin + zone_end_offset + 20.0)].copy()
                journey["offset_m"] = journey["cum_m"] - origin
                # Samples sit on a 20 m grid, so a row exactly at the zone end
                # is the exception, not the rule. Require coverage to within one
                # sample of it: anything less means the session or the car's data
                # stopped inside the zone and there is no outcome to label.
                if journey["offset_m"].max() < zone_end_offset - 20.0:
                    continue

                in_zone = journey[(journey["offset_m"] > activation_offset)
                                  & (journey["offset_m"] <= zone_end_offset)]
                braking = in_zone[in_zone["brake_on"].fillna(False).astype(bool)]
                brake_offset = (float(braking.iloc[0]["offset_m"]) if len(braking) else zone_end_offset)
                if not 0 < activation_offset < brake_offset:
                    continue
                brake_m = float((detection + brake_offset) % lap_length)

                before = journey[journey["offset_m"] <= 0].tail(6)
                gaps = [_gap_s(row.gap_ahead_m, row.speed_kmh) for row in before.itertuples()]
                gaps = [g for g in gaps if g is not None]
                trailing = [a - b for a, b in zip(gaps, gaps[1:])] or [0.0]

                exit_row = _at_offset(journey, zone_end_offset)
                attacker_exit = _number_or_none(exit_row.get("race_position")) if exit_row is not None else None
                defender_exit = None
                exit_lap = int(exit_row["lap"]) if exit_row is not None else int(lap)
                defender_block = by_number.get((session, exit_lap, str(int(defender))))
                if defender_block is not None:
                    defender_row = _at_distance(
                        defender_block.sort_values("distance_m"), float(exit_row["distance_m"])
                    )
                    if defender_row is not None:
                        defender_exit = _number_or_none(defender_row.get("race_position"))

                geometry = {**zone, "brake_onset_m": brake_m,
                            "activation_offset_m": activation_offset,
                            "brake_offset_m": brake_offset,
                            "zone_end_offset_m": zone_end_offset}
                # Resolve the C8 episode this opportunity belongs to. A miss
                # leaves battle_id null and records why; it is never invented,
                # because a fabricated id would put unrelated opportunities into
                # one split group and silently defeat CP-14's leakage guarantee.
                defender_code = number_to_code.get(
                    (str(entry.get("year") or year), str(entry.get("event") or event),
                     str(session), str(int(defender))))
                battle_id, join_status = battle_index.resolve(
                    year=entry.get("year") or year, event=entry.get("event") or event,
                    session=session, attacker=driver,
                    defender_code=defender_code, lap=int(lap),
                    # Anchored at the Detection Line, where the opportunity is
                    # defined. The opportunity runs on into the next lap, but the
                    # episode it belongs to is the one it starts inside.
                    distance_m=detection,
                )
                join_counts[join_status] = join_counts.get(join_status, 0) + 1

                context_row = pair_context(
                    lap_context, session=session, lap=int(lap),
                    attacker=driver, defender_code=defender_code)
                # Geometry belongs at the checkpoint's own distance, not the
                # lap's: resolving it per lap would attach a straight's geometry
                # to a corner's opportunity.
                context_row.update(segment_geometry.at(session, driver, int(lap), detection))

                context = OpportunityContext(
                    opportunity_id=opportunity_id(year, event, session, lap, zone["zone"], driver, int(defender)),
                    year=year, event=event, session=session, lap=int(lap), zone=zone["zone"],
                    attacker=driver, defender=str(int(defender)),
                    battle_id=battle_id, battle_join_status=join_status,
                    attacker_team=entry.get("team"),
                    defender_team=context_row.pop("defender_team", None),
                    regulation_era="2026",
                    context_features=context_row,
                )
                offsets = {"DETECTION": 0.0, "ACTIVATION": activation_offset, "BRAKING": brake_offset}
                rows_out.extend(build_opportunity_rows(
                    context,
                    {"DETECTION": detection, "ACTIVATION": activation, "BRAKING": brake_m},
                    lambda checkpoint, cutoff, _j=journey, _z=geometry, _t=trailing, _o=offsets:
                        _checkpoint_features(
                            _j[_j["offset_m"] <= _o[checkpoint]], _z, checkpoint,
                            _o[checkpoint], threshold, _t,
                        ),
                    label=label_zone_exit_v1(attacker_exit, defender_exit),
                    outcome_distance_m=zone_end,
                    lap_length_m=lap_length,
                ))
                opportunities += 1

    written = None
    if rows_out:
        target = output_root / f"event={event}"
        target.mkdir(parents=True, exist_ok=True)
        destination = target / "opportunities.parquet"
        coerce_context_dtypes(pd.DataFrame(rows_out)).to_parquet(destination, index=False)
        written = str(destination.resolve().relative_to(ROOT.resolve()))

    joined = join_counts.get("JOINED", 0)
    return {**plan, "opportunities": opportunities, "rows": len(rows_out),
            "written": written,
            "battle_join": {
                "counts": dict(sorted(join_counts.items())),
                "joined": joined,
                "coverage": round(joined / opportunities, 6) if opportunities else None,
            }}


def _battle_join_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the per-event A2 join counts into one coverage figure."""
    counts: dict[str, int] = {}
    for result in results:
        for status, value in (result.get("battle_join") or {}).get("counts", {}).items():
            counts[status] = counts.get(status, 0) + int(value)
    total = sum(counts.values())
    joined = counts.get("JOINED", 0)
    return {
        "counts": dict(sorted(counts.items())),
        "opportunities_with_battle_id": joined,
        "opportunities_total": total,
        "coverage": round(joined / total, 6) if total else None,
        "note": (
            "Unjoined opportunities keep a null battle_id and a battle_join_status "
            "naming the reason. None is invented: a fabricated id would place "
            "unrelated opportunities in one C9 split group. CP-14 excludes them."
        ),
    }


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
        # A2 join coverage belongs in the artifact, not only in the table. CP-14
        # refuses a partially populated split unit, so the share of rows without
        # a battle_id is the number that decides whether the benchmark can run.
        "battle_join": _battle_join_summary(results),
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "built"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
