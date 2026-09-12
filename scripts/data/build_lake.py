#!/usr/bin/env python3
"""Batch-build the 20 m telemetry lake across many sessions (CP-04).

Thin driver around `build_phase2_dataset.py`, which stays unchanged: it works,
it is tested, and AGENTS.md section 3 says not to rewrite working Phase 2
components. This only decides *what* to build and collects the results.

Two problems it exists to solve:

**Manifest collision.** The single-session builder partitions its Parquet by
year/event/session but writes lap_manifest.csv, rejected_laps.csv,
quality_summary.csv and run_manifest.json to the *shared* output root. Building
279 sessions into one root would leave only the last session's manifests, losing
every rejection reason from the other 278. So each session is built into its own
staging directory; the Parquet is then moved into the partitioned path and the
manifests are appended to aggregates.

**Wall clock.** 177k JSON files single-threaded takes hours. Sessions are
independent, so `--jobs` runs them in parallel. Parallelism stops at the session
boundary: within a session the build stays sequential, because MODELS.md section
6.3 has both owners building locally and comparing results, which requires the
output to be deterministic.

Usage:
    # 4a smoke test
    python scripts/data/build_lake.py --years 2026 --events "British Grand Prix" \\
        --sessions Race --drivers HAM ANT --dry-run

    # 4c: the 2026 domain
    python scripts/data/build_lake.py --years 2026 --jobs 8

    # 4d: DRS-era priors
    python scripts/data/build_lake.py --years 2022,2023,2024,2025 \\
        --sessions Qualifying,Race --jobs 8
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.progress import Progress, format_duration  # noqa: E402

BUILDER = ROOT / "scripts" / "build_phase2_dataset.py"
YEARS = ("2022", "2023", "2024", "2025", "2026")

#: Session scope from AGENTS.md section 8. Practice 1 is 2026-only by policy.
CORE_SESSIONS_HISTORIC = ("Qualifying", "Sprint Qualifying", "Sprint Shootout", "Sprint", "Race")
CORE_SESSIONS_2026 = ("Practice 1",) + CORE_SESSIONS_HISTORIC

MANIFEST_CSVS = ("lap_manifest.csv", "rejected_laps.csv", "quality_summary.csv")


def git_provenance() -> dict:
    """Record which code produced a build.

    Required by AGENTS.md section 53, and load-bearing when a lake is built on
    one machine and used on another: without the commit there is no way to tell
    that the data predates a change to the resampling or metadata code, and the
    mismatch is silent. `dirty` matters just as much -- a build made with
    uncommitted changes is not reproducible from any commit.
    """
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=10)
            return out.stdout.strip() or None if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    status = run("git", "status", "--porcelain")
    return {
        "git_commit": run("git", "rev-parse", "HEAD"),
        "git_branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(status) if status is not None else None,
        "git_dirty_files": [line[2:].strip() for line in status.splitlines()][:20] if status else [],
        # porcelain v1 is XY<space>PATH, but the two status characters can be
        # "M ", " M", "??" or "R ", so slice past the pair and strip rather
        # than assuming a fixed offset.
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "hostname": platform.node(),
    }


def safe_name(value: str) -> str:
    """Match the partition naming used by build_phase2_dataset.py."""
    return value.replace(" ", "_")


@dataclass
class SessionJob:
    year: str
    event: str
    session: str
    laps: int = 0

    @property
    def label(self) -> str:
        return f"{self.year} {self.event} / {self.session}"

    @property
    def partition(self) -> Path:
        return Path(f"year={self.year}") / f"event={safe_name(self.event)}" / f"session={safe_name(self.session)}"


@dataclass
class SessionResult:
    job: SessionJob
    ok: bool
    discovered: int = 0
    accepted: int = 0
    rejected: int = 0
    rows: int = 0
    seconds: float = 0.0
    error: str = ""
    lap_rows: list = field(default_factory=list)
    rejected_rows: list = field(default_factory=list)
    quality_rows: list = field(default_factory=list)


def discover_sessions(raw_root: Path, years, events, sessions, exclude_events) -> list[SessionJob]:
    """Walk the mirror for buildable sessions, applying the scope filters."""
    found: list[SessionJob] = []
    wanted_events = {e.lower() for e in events} if events else None
    excluded = {e.lower() for e in exclude_events} if exclude_events else set()

    for year in years:
        year_dir = raw_root / year
        if not year_dir.is_dir():
            continue
        default_sessions = CORE_SESSIONS_2026 if year == "2026" else CORE_SESSIONS_HISTORIC
        wanted_sessions = set(sessions) if sessions else set(default_sessions)

        for event_dir in sorted(p for p in year_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            name = event_dir.name
            if wanted_events is not None and name.lower() not in wanted_events:
                continue
            if name.lower() in excluded:
                continue
            for session_dir in sorted(p for p in event_dir.iterdir() if p.is_dir()):
                if session_dir.name not in wanted_sessions:
                    continue
                # A session with no driver directories has nothing to build.
                drivers_present = [p for p in session_dir.iterdir() if p.is_dir()]
                if not drivers_present:
                    continue
                # Pre-count laps so progress is weighted by work, not by session
                # count: sessions differ by an order of magnitude, so ticking one
                # per session makes the bar jump unpredictably.
                laps = sum(1 for d in drivers_present for f in d.iterdir()
                           if f.is_file() and f.name.endswith("_tel.json"))
                found.append(SessionJob(year, name, session_dir.name, laps))
    return found


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_one(job: SessionJob, raw_root: Path, output_root: Path, spacing_m: float,
              drivers: list[str] | None, max_laps: int | None, python: str) -> SessionResult:
    """Build one session into staging, then move its Parquet into place.

    Runs the existing builder as a subprocess so it stays unmodified and so a
    crash in one session cannot take the whole batch down.
    """
    started = time.monotonic()
    staging = Path(tempfile.mkdtemp(prefix="trackshift_lake_"))
    try:
        cmd = [
            python, str(BUILDER),
            "--raw-root", str(raw_root),
            "--output-root", str(staging),
            "--year", job.year,
            "--event", job.event,
            "--session", job.session,
            "--spacing-m", str(spacing_m),
        ]
        if drivers:
            cmd += ["--drivers", *drivers]
        if max_laps is not None:
            cmd += ["--max-laps-per-driver", str(max_laps)]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return SessionResult(job, False, seconds=time.monotonic() - started,
                                 error=tail[-1] if tail else f"exit {proc.returncode}")

        try:
            summary = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return SessionResult(job, False, seconds=time.monotonic() - started,
                                 error="builder produced unparseable summary")

        result = SessionResult(
            job, True,
            discovered=summary.get("discovered_laps", 0),
            accepted=summary.get("accepted_laps", 0),
            rejected=summary.get("rejected_laps", 0),
            seconds=time.monotonic() - started,
            lap_rows=_read_csv(staging / "lap_manifest.csv"),
            rejected_rows=_read_csv(staging / "rejected_laps.csv"),
            quality_rows=_read_csv(staging / "quality_summary.csv"),
        )
        result.rows = summary.get("resampled_rows", 0)

        produced = summary.get("output")
        if produced:
            destination = output_root / job.partition / "telemetry_20m.parquet"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(Path(produced)), str(destination))
        return result
    except Exception as exc:  # noqa: BLE001 - one bad session must not stop the batch
        return SessionResult(job, False, seconds=time.monotonic() - started, error=f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _write_aggregate(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "data" / "raw" / "tracinginsights")
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "processed" / "telemetry_20m")
    parser.add_argument("--years", default="2026", help="CSV, e.g. 2026 or 2022,2023,2024,2025")
    parser.add_argument("--events", default=None, help="CSV of exact event names; default every event")
    parser.add_argument("--sessions", default=None, help="CSV of session names; default the core scope for each year")
    parser.add_argument("--exclude-event", action="append", default=[], help="Repeatable")
    parser.add_argument("--drivers", nargs="+", default=None, help="Limit to these drivers (smoke tests)")
    parser.add_argument("--max-laps-per-driver", type=int, default=None)
    parser.add_argument("--spacing-m", type=float, default=20.0)
    parser.add_argument("--jobs", type=int, default=1, help="Sessions built in parallel. Never parallel within a session.")
    parser.add_argument("--dry-run", action="store_true", help="List the sessions that would be built")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    years = tuple(y.strip() for y in args.years.split(",") if y.strip())
    unknown = [y for y in years if y not in YEARS]
    if unknown:
        parser.error(f"unsupported year(s): {','.join(unknown)}")
    events = [e.strip() for e in args.events.split(",")] if args.events else None
    sessions = [s.strip() for s in args.sessions.split(",")] if args.sessions else None

    if not args.raw_root.is_dir():
        parser.error(f"raw root does not exist: {args.raw_root}")

    jobs = discover_sessions(args.raw_root, years, events, sessions, args.exclude_event)
    if not jobs:
        print(json.dumps({"sessions": 0, "note": "no sessions matched the given scope"}, indent=2))
        return 0

    if args.dry_run:
        print(json.dumps({
            "sessions": len(jobs),
            "laps": sum(j.laps for j in jobs),
            "by_year": {y: sum(1 for j in jobs if j.year == y) for y in years},
            "plan": [j.label for j in jobs],
        }, indent=2))
        return 0

    args.output_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    total_laps = sum(j.laps for j in jobs) or len(jobs)
    prog = Progress(total_laps, enabled=not args.no_progress)
    print(f"building {len(jobs)} sessions ({total_laps:,} laps) with {args.jobs} job(s)", file=sys.stderr)

    results: list[SessionResult] = []
    worker = (args.raw_root, args.output_root, args.spacing_m, args.drivers,
              args.max_laps_per_driver, sys.executable)

    if args.jobs > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(build_one, job, *worker): job for job in jobs}
            pending = set(futures)
            prog.set_label(f"{len(pending)} session(s) queued")
            prog.heartbeat()
            while pending:
                # Wait with a timeout rather than blocking on completion, so the
                # line keeps updating between finishes. A stage of five sessions
                # would otherwise print nothing for a minute.
                finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in finished:
                    result = future.result()
                    results.append(result)
                    prog.set_label(result.job.label)
                    prog.tick(result.job.laps or 1)
                if pending:
                    running = min(len(pending), args.jobs)
                    queued = len(pending) - running
                    prog.set_label(
                        f"{running} building" + (f", {queued} queued" if queued else "")
                        + f", {len(results)}/{len(jobs)} done"
                    )
                    prog.heartbeat()
    else:
        for job in jobs:
            prog.set_label(job.label)
            results.append(build_one(job, *worker))
            prog.tick(job.laps or 1)
    prog.close()

    # Deterministic ordering regardless of completion order, so two runs of the
    # same scope produce byte-identical manifests.
    results.sort(key=lambda r: (r.job.year, r.job.event, r.job.session))

    lap_rows = [row for r in results for row in r.lap_rows]
    rejected_rows = [row for r in results for row in r.rejected_rows]
    quality_rows = [row for r in results for row in r.quality_rows]
    _write_aggregate(args.output_root / "lap_manifest.csv", lap_rows)
    _write_aggregate(args.output_root / "rejected_laps.csv", rejected_rows)
    _write_aggregate(args.output_root / "quality_summary.csv", quality_rows)

    failures = [r for r in results if not r.ok]
    discovered = sum(r.discovered for r in results)
    accepted = sum(r.accepted for r in results)
    elapsed = time.monotonic() - started

    rejection_codes: dict[str, int] = {}
    for row in rejected_rows:
        code = row.get("rejection_code") or "UNKNOWN"
        rejection_codes[code] = rejection_codes.get(code, 0) + 1

    manifest = {
        "schema_version": "phase2_20m_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        **git_provenance(),
        "command_arguments": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "raw_root": str(args.raw_root),
        "sessions_requested": len(jobs),
        "sessions_built": len(results) - len(failures),
        "sessions_failed": len(failures),
        "failures": [{"session": r.job.label, "error": r.error} for r in failures],
        "discovered_laps": discovered,
        "accepted_laps": accepted,
        "rejected_laps": sum(r.rejected for r in results),
        "acceptance_rate_pct": round(100.0 * accepted / discovered, 2) if discovered else 0.0,
        "resampled_rows": sum(r.rows for r in results),
        "rejection_codes": dict(sorted(rejection_codes.items(), key=lambda kv: -kv[1])),
        "elapsed_seconds": round(elapsed, 1),
        "by_session": [
            {"year": r.job.year, "event": r.job.event, "session": r.job.session,
             "discovered": r.discovered, "accepted": r.accepted, "rejected": r.rejected,
             "rows": r.rows, "seconds": round(r.seconds, 1), "ok": r.ok}
            for r in results
        ],
    }
    (args.output_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps({
        "sessions_built": manifest["sessions_built"],
        "sessions_failed": manifest["sessions_failed"],
        "discovered_laps": discovered,
        "accepted_laps": accepted,
        "acceptance_rate_pct": manifest["acceptance_rate_pct"],
        "resampled_rows": manifest["resampled_rows"],
        "rejection_codes": manifest["rejection_codes"],
        "elapsed": format_duration(elapsed),
        "output_root": str(args.output_root),
    }, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
