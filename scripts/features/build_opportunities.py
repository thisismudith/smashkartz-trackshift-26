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
OUT = ROOT / "data" / "processed" / "overtake_opportunities"


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def zone_lines(rules: dict) -> list[dict[str, Any]]:
    """Detection, activation and zone-end per configured zone, with provenance."""
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

    # Reached only once Detection Lines are configured. The per-opportunity
    # feature builders are assembled here, one truncated view per checkpoint,
    # so that no row is ever built from data past its own cutoff.
    raise NotImplementedError(
        f"{event}: detection lines are configured but the opportunity extraction "
        "is not wired yet. Build it against the checkpoint-scoped views in "
        "trackshift.features.opportunities.build_opportunity_rows."
    )


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
