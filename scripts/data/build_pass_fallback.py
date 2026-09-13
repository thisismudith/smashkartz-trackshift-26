"""Build the season-scoped empirical pass-rate table served while M10 has no artifact.

Scans the raw telemetry mirror for attacker/defender approaches to an activation
zone, counts how many ended with the attacker ahead, and writes a bucketed table
with Wilson intervals to artifacts/pass_fallback/.

This is a contingency table, not a model. See
src/trackshift/serve/pass_fallback.py for what it is and, more importantly, what
it is NOT -- it exists so the /pass/predict route answers with a measured
frequency instead of a flat 0.5 placeholder, and is dropped the moment
trackshift.pass_model writes a real artifact.

Usage:
    python scripts/data/build_pass_fallback.py                     # 2026, all events
    python scripts/data/build_pass_fallback.py --event "British Grand Prix"
    python scripts/data/build_pass_fallback.py --season 2026 --out artifacts/...
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from trackshift.serve.pass_fallback import (  # noqa: E402
    ARTIFACT_PATH,
    build_rate_table,
    collect_opportunities,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="2026")
    parser.add_argument("--raw-root", default=str(REPO_ROOT / "data"),
                        help="directory holding <season>/<event>/<session>/<driver>/")
    parser.add_argument("--config-dir", default=str(REPO_ROOT / "config" / "rules" / "2026"))
    parser.add_argument("--event", action="append", default=None,
                        help="restrict to this event; repeatable")
    parser.add_argument("--out", default=None)
    parser.add_argument("--rows-out", default=None,
                        help="optional JSONL of every opportunity, for inspection")
    args = parser.parse_args(argv)

    started = time.time()
    print(f"scanning {args.raw_root}/{args.season} ...", flush=True)
    opportunities, used, skipped = collect_opportunities(
        raw_root=Path(args.raw_root),
        config_dir=Path(args.config_dir),
        season=args.season,
        events=args.event,
    )
    elapsed = time.time() - started
    print(f"  {len(opportunities)} opportunities from {len(used)} event(s) in {elapsed:.0f}s")
    for event in used:
        n = sum(1 for o in opportunities if o.event == event)
        p = sum(1 for o in opportunities if o.event == event and o.passed)
        print(f"    {event:32s} n={n:5d} passes={p:4d}")
    for event, reason in sorted(skipped.items()):
        print(f"    SKIPPED {event:24s} {reason}")

    if not opportunities:
        print("no opportunities found; nothing written", flush=True)
        return 1

    table = build_rate_table(opportunities, season=args.season, events=used)
    out_path = Path(args.out) if args.out else (REPO_ROOT / ARTIFACT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(table.to_json(), indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")

    print(f"\n{'gap at detection':>20} {'n':>6} {'passes':>7} {'rate':>7}  95% interval")
    for bucket in table.buckets:
        if bucket.n == 0:
            print(f"{bucket.low_s:>8.1f}-{bucket.high_s:<8.1f} {0:>6}       -       -  (no observations)")
            continue
        print(f"{bucket.low_s:>8.1f}-{bucket.high_s:<8.1f} {bucket.n:>6} {bucket.passes:>7} "
              f"{bucket.rate:>7.3f}  [{bucket.ci_low:.3f}, {bucket.ci_high:.3f}]")

    if args.rows_out:
        rows_path = Path(args.rows_out)
        rows_path.parent.mkdir(parents=True, exist_ok=True)
        with rows_path.open("w", encoding="utf-8") as handle:
            for o in opportunities:
                handle.write(json.dumps(o.__dict__) + "\n")
        print(f"wrote {rows_path} ({len(opportunities)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
