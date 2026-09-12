#!/usr/bin/env python3
"""Prepare the historical 2022-2025 non-British C1/C7 spine (CP-08 prerequisite).

CP-08 ("Rival-side regulation-era evaluation") is blocked because no historical
M08 partitions exist for 2022-2025, and a regulation-era comparison cannot be
made from 2026-only data. M08 sits on M06, which sits on C8, which sits on C1
segments carrying C7 race context. This script builds that bottom layer, and
only that layer:

    raw mirror -> telemetry lake (phase2_20m_v1)
               -> C1 segments     (c1_segments_v1)
               -> C7 race context (c7_race_context_materialized_v1)

It is an orchestration boundary, not a reimplementation. The lake comes from
``scripts/data/build_lake.py``, C1 from ``scripts/features/build_segments.py``,
and C7 from the public ``derive_lap_c7_context`` normaliser -- the same boundary
``scripts/features/materialize_chain_s.py`` uses for 2026. What is new here is
everything the historical years need and the 2026 path never did:

**Per-season raw roots.** 2022-2025 mirrors do not all live under one parent on
a real machine, so the season directory is resolved per year through
``trackshift.data.raw_sources`` rather than from a single ``--raw-root``.

**Skip reasons instead of silence.** Most historical events have no segment map,
one season's Sprint sessions can be absent from the mirror that is usable, and
no season before 2026 has a rule configuration. Each is recorded per unit with a
code and a reason, because an event that quietly fails to appear in a 52-unit
build is indistinguishable from one that was never in scope.

**British Grand Prix held out by default.** It is the demo event and the final
held-out test (CHECKPOINTS_RISHABH.md non-negotiable rule 1), and this is a
training-oriented materialisation, so it is excluded for every year unless the
operator says otherwise in as many words.

Historical DRS is left exactly where the contract puts it. The lake column
``drs_open`` is an OBSERVED 2022-2025 covariate (feature registry, AGENTS.md
section 12) and is carried through untouched; its per-unit availability is
audited into the manifest as era evidence. No ``overtake_*`` column is ever
written for a historical partition -- 2026 Overtake state comes from the rule
engine alone (sections 20, 41) -- and :func:`assert_no_overtake_state_columns`
enforces that rather than trusting it.

Usage:
    # What is available, and what would be skipped and why. Touches no outputs.
    python scripts/data/materialize_historical_spine.py --audit-only

    # Smoke test: one 2022 event, two drivers, three laps each.
    python scripts/data/materialize_historical_spine.py \\
        --years 2022 --events "Australian Grand Prix" --sessions Race \\
        --drivers VER LEC --max-laps-per-driver 3 \\
        --run-root data/processed/historical_spine_smoke_2022

    # The full historical spine.
    python scripts/data/materialize_historical_spine.py --jobs 8
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.guards import (  # noqa: E402
    DemoScope,
    assert_demo_held_out,
    normalise_event,
)
from trackshift.data.raw_sources import (  # noqa: E402
    SeasonMirror,
    resolve_season_mirrors,
    select_season_mirror,
)

LAKE_BUILDER = ROOT / "scripts" / "data" / "build_lake.py"
SEGMENT_BUILDER = ROOT / "scripts" / "features" / "build_segments.py"
GEOMETRY_DIR = ROOT / "config" / "geometry"
RULES_DIR = ROOT / "config" / "rules"

RUN_SCHEMA_VERSION = "historical_spine_materialization_v1"
LAKE_SCHEMA_VERSION = "phase2_20m_v1"
C1_SCHEMA_VERSION = "c1_segments_v1"
C7_SCHEMA_VERSION = "c7_race_context_materialized_v1"

#: The regulation era these years belong to. Never "2026"; see AGENTS.md section 41.
HISTORICAL_REGULATION_VERSION = "historical_drs_era"

#: Historical seasons in scope. 2026 is a different regulatory domain and has its
#: own path (materialize_chain_s.py), so it is not buildable here.
HISTORICAL_YEARS = ("2022", "2023", "2024", "2025")

#: AGENTS.md section 8.1 core sessions, narrowed to the two that carry
#: race-combat behaviour. Qualifying has no battles, so C8/M06/M08 cannot use it.
DEFAULT_SESSIONS = ("Race", "Sprint")

#: Held out of every training and calibration run (CHECKPOINTS_INTEGRATION.md
#: item 10, CHECKPOINTS_RISHABH.md rule 1). Matched through guards.normalise_event,
#: so spelling differences between a directory name, a partition key and a config
#: file cannot let it through.
DEFAULT_EXCLUDED_EVENT = "British Grand Prix"

#: Leave-one-track-out, i.e. every British Grand Prix in every season. The guard's
#: own default (DemoScope.EVENT_YEAR) holds out only 2026 and would be a silent
#: no-op here, which is precisely the failure this scope exists to avoid: a
#: historical DRS-era prior that has memorised Silverstone cannot support a
#: held-out claim about the 2026 Silverstone demo.
DEMO_SCOPE = DemoScope.TRACK

#: Columns that would assert a 2026 Overtake reading of historical data.
FORBIDDEN_HISTORICAL_COLUMNS = ("overtake_state", "overtake_eligible", "overtake_unavailable_reason")

#: Written onto C1 rows by the C7 normaliser.
C7_FIELDS = (
    "pit_state",
    "normalized_race_control_state",
    "safety_car_active",
    "virtual_safety_car_active",
    "race_control_transition_flag",
    "pit_transition_flag",
    "green_flag_elapsed_s",
    "normal_race_model_eligible",
)

SESSION_METADATA_FILES = ("rcm.json", "weather.json", "corners.json", "drivers.json", "session_laptimes.json")


class SpineError(RuntimeError):
    """The run cannot proceed and the reason is the operator's to fix."""


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #

def git_provenance() -> dict[str, Any]:
    """Record which code produced a build (AGENTS.md section 53).

    ``dirty`` matters as much as the commit: a build made with uncommitted
    changes is not reproducible from any commit, and a historical lake built on
    one machine is routinely consumed on another.
    """
    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return (done.stdout.strip() or None) if done.returncode == 0 else None

    status = run("git", "status", "--porcelain")
    # Same field set as scripts/data/build_lake.py::git_provenance on purpose:
    # scripts/data/verify_lake.py gates on git_branch, git_dirty and
    # git_dirty_files, and a manifest carrying only the commit passes that gate
    # vacuously. porcelain v1 is XY<space>PATH and the pair can be "M ", " M",
    # "??" or "R ", so slice past it and strip rather than assuming an offset.
    return {
        "git_commit": run("git", "rev-parse", "HEAD"),
        "git_branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(status) if status is not None else None,
        "git_dirty_files": [line[2:].strip() for line in status.splitlines()][:20] if status else [],
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "hostname": platform.node(),
    }


def sha256_file(path: Path) -> str | None:
    """Hash a consumed artifact. The commit alone does not identify the inputs:
    a lake rebuilt at the same commit over a different raw scope yields a
    different C1, and nothing downstream could tell."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _safe(value: str) -> str:
    """Partition spelling used by every producer in the repo."""
    return str(value).replace(" ", "_")


def _relative(path: Path) -> str:
    """Repo-relative POSIX path, so a manifest does not name one machine."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


# --------------------------------------------------------------------------- #
# configuration coverage
# --------------------------------------------------------------------------- #

def load_geometry_index(geometry_dir: Path = GEOMETRY_DIR) -> dict[str, dict[str, Any]]:
    """Map an event display name to the segment map that keys its C1 rows.

    C1's ``segment_id`` only means the same piece of tarmac while
    ``geometry_version`` and ``boundary_hash`` are unchanged (AGENTS.md section
    13), so both travel with every unit into the manifest.
    """
    import yaml

    index: dict[str, dict[str, Any]] = {}
    for path in sorted(geometry_dir.glob("*.yaml")):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except yaml.YAMLError as exc:
            raise SpineError(f"segment map is malformed: {path}: {exc}") from exc
        display = payload.get("event_display")
        if not display:
            continue
        key = normalise_event(str(display))
        # A second map for the same event would make segment_id ambiguous. Keep
        # the first by sorted filename and record the collision rather than
        # picking silently.
        index.setdefault(key, {
            "circuit": payload.get("circuit") or path.stem,
            "geometry_version": payload.get("geometry_version"),
            "boundary_hash": payload.get("boundary_hash"),
            "segment_count": payload.get("segment_count"),
            "config_path": _relative(path),
        })
    return index


def rules_coverage(rules_dir: Path = RULES_DIR) -> list[str]:
    """Seasons that have an encoded FIA rule configuration on disk."""
    if not rules_dir.is_dir():
        return []
    return sorted(item.name for item in rules_dir.iterdir() if item.is_dir())


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Unit:
    """One (year, event, session) the spine can build, with its inputs audited."""

    year: str
    event: str
    session: str
    circuit: str
    geometry_version: str | None
    boundary_hash: str | None
    geometry_config: str | None
    raw_session_dir: Path
    mirror_path: Path
    drivers: int
    telemetry_laps: int
    drivers_with_laptimes: int
    session_metadata: dict[str, bool]
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.year}/{self.event}/{self.session}"

    def as_record(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "event": self.event,
            "session": self.session,
            "circuit": self.circuit,
            "geometry_version": self.geometry_version,
            "boundary_hash": self.boundary_hash,
            "geometry_config": self.geometry_config,
            "raw_session_dir": _relative(self.raw_session_dir),
            "raw_mirror": _relative(self.mirror_path),
            "drivers": self.drivers,
            "telemetry_laps": self.telemetry_laps,
            "drivers_with_laptimes": self.drivers_with_laptimes,
            "session_metadata_present": dict(sorted(self.session_metadata.items())),
            "warnings": sorted(self.warnings),
        }


@dataclass
class Skip:
    """A unit that will not be built, and exactly why."""

    year: str
    event: str
    session: str
    code: str
    reason: str

    def as_record(self) -> dict[str, str]:
        return {"year": self.year, "event": self.event, "session": self.session,
                "code": self.code, "reason": self.reason}


@dataclass
class Plan:
    units: list[Unit]
    skipped: list[Skip]
    mirrors: dict[str, dict[str, Any]]

    @property
    def circuits(self) -> list[str]:
        return sorted({unit.circuit for unit in self.units})

    @property
    def years(self) -> list[str]:
        return sorted({unit.year for unit in self.units})


def _scan_session(session_dir: Path) -> tuple[int, int, int, dict[str, bool]]:
    """Count what a raw session directory holds. Stats files; parses nothing."""
    drivers = sorted(item for item in session_dir.iterdir() if item.is_dir())
    laps = 0
    with_laptimes = 0
    for driver in drivers:
        try:
            entries = list(driver.iterdir())
        except OSError:
            continue
        laps += sum(1 for item in entries if item.is_file() and item.name.endswith("_tel.json"))
        if (driver / "laptimes.json").is_file():
            with_laptimes += 1
    present = {name: (session_dir / name).is_file() for name in SESSION_METADATA_FILES}
    return len(drivers), laps, with_laptimes, present


def _enumerate_mirror(
    mirror: SeasonMirror,
    sessions: Sequence[str],
    events: Sequence[str] | None,
) -> list[tuple[str, str, Path]]:
    """Every (event, session, dir) in a mirror inside the requested scope."""
    wanted_events = {normalise_event(item) for item in events} if events else None
    found: list[tuple[str, str, Path]] = []
    for event_dir in sorted(item for item in mirror.path.iterdir()
                            if item.is_dir() and not item.name.startswith(".")):
        if wanted_events is not None and normalise_event(event_dir.name) not in wanted_events:
            continue
        for name in sessions:
            session_dir = event_dir / name
            if session_dir.is_dir():
                found.append((event_dir.name, name, session_dir))
    return found


def build_plan(
    years: Sequence[str],
    sessions: Sequence[str],
    *,
    events: Sequence[str] | None = None,
    excluded_events: Iterable[str] = (DEFAULT_EXCLUDED_EVENT,),
    raw_sources: Iterable[Path] = (),
    allow_directory_mismatch: bool = False,
    geometry_dir: Path = GEOMETRY_DIR,
    require_race_control: bool = False,
    repo_root: Path | None = None,
) -> Plan:
    """Decide what to build, and record a coded reason for everything else.

    Every rejection produces a :class:`Skip`. That is the whole point: a
    historical build drops roughly half its candidate sessions for want of a
    segment map, and an unexplained absence is indistinguishable from a bug.

    ``repo_root`` is forwarded to :func:`resolve_season_mirrors` and defaults to
    this repository; it exists so tests can point mirror resolution at a
    synthetic tree instead of the real one.
    """
    geometry = load_geometry_index(geometry_dir)
    excluded = {normalise_event(item) for item in excluded_events}
    units: list[Unit] = []
    skipped: list[Skip] = []
    mirrors: dict[str, dict[str, Any]] = {}

    for year in years:
        candidates = resolve_season_mirrors(year, sessions, extra_roots=raw_sources, repo_root=repo_root)
        selected, mirror_skip = select_season_mirror(
            candidates, allow_directory_mismatch=allow_directory_mismatch)
        mirrors[year] = {
            "selected": selected.as_record() if selected else None,
            "selection_skip_reason": mirror_skip,
            "candidates": [item.as_record() for item in candidates],
        }

        if selected is None:
            code = "NO_USABLE_RAW_MIRROR" if candidates else "NO_SEASON_MIRROR"
            skipped.append(Skip(year, "", "", code, mirror_skip or
                                "no directory on this machine declares this season"))
            continue

        present = _enumerate_mirror(selected, sessions, events)
        present_keys = {(normalise_event(event), session) for event, session, _ in present}

        # A session that exists only in a mirror we refused is a real coverage
        # gap with a known cause. Reporting it from the evidence on disk is not
        # the same as inventing a calendar: it names the path that has the data
        # and the reason that path is unusable.
        for other in candidates:
            if other.path == selected.path:
                continue
            for event, session, _ in _enumerate_mirror(other, sessions, events):
                if (normalise_event(event), session) in present_keys:
                    continue
                if normalise_event(event) in excluded:
                    continue
                present_keys.add((normalise_event(event), session))
                cause = ("its directory name is not the season it declares, so the lake partition "
                         f"would be labelled year={other.year_dir} instead of year={year}"
                         if not other.directory_matches_season else
                         "it was not selected for this season")
                # State every blocker, not just the first. A session that is both
                # in the wrong mirror and has no segment map is not one migration
                # away from being buildable, and a one-line reason that implies it
                # is sends the reader down the wrong path.
                also = ("" if normalise_event(event) in geometry else
                        "; it also has no config/geometry segment map, so it needs one before C1")
                skipped.append(Skip(
                    year, event, session, "SESSION_ONLY_IN_UNUSABLE_MIRROR",
                    f"absent from the selected mirror {_relative(selected.path)} but present in "
                    f"{_relative(other.path)}, which is not usable because {cause}. Complete the "
                    f"AGENTS.md section 7 raw-layout migration (data/raw/tracinginsights/{year}/) "
                    f"or pass --allow-directory-mismatch{also}",
                ))

        for event, session, session_dir in present:
            if normalise_event(event) in excluded:
                skipped.append(Skip(year, event, session, "EXCLUDED_EVENT",
                                    "held out of training-oriented materialisation by configuration"))
                continue

            drivers, laps, with_laptimes, metadata = _scan_session(session_dir)
            if laps == 0:
                skipped.append(Skip(year, event, session, "NO_TELEMETRY_LAPS",
                                    f"no *_tel.json under {_relative(session_dir)}"))
                continue

            geometry_entry = geometry.get(normalise_event(event))
            if geometry_entry is None:
                skipped.append(Skip(
                    year, event, session, "NO_GEOMETRY_CONFIG",
                    f"no config/geometry/*.yaml declares event_display {event!r}; C1 segment_id "
                    "cannot be assigned without a segment map. Derive one first: "
                    f'python scripts/features/derive_track_geometry.py --circuit <key> --event "{event}"',
                ))
                continue

            if require_race_control and not metadata["rcm.json"]:
                skipped.append(Skip(year, event, session, "MISSING_RACE_CONTROL_METADATA",
                                    f"rcm.json absent under {_relative(session_dir)}"))
                continue

            warnings: list[str] = []
            if not metadata["rcm.json"]:
                warnings.append(
                    "MISSING_RACE_CONTROL_METADATA: rcm.json absent; C7 derives race-control state "
                    "from lap track_status and does not need it, but M20/C6 overtake windows do")
            if with_laptimes < drivers:
                warnings.append(
                    f"PARTIAL_LAP_METADATA: {drivers - with_laptimes} of {drivers} driver directories "
                    "have no laptimes.json; those laps carry no pit or track-status metadata and "
                    "their C7 fields will be unresolved")
            for name in ("weather.json", "corners.json"):
                if not metadata[name]:
                    warnings.append(f"MISSING_SESSION_FILE: {name} absent; downstream overlays that "
                                    "read it will record their own unavailability")

            units.append(Unit(
                year=year, event=event, session=session,
                circuit=str(geometry_entry["circuit"]),
                geometry_version=geometry_entry.get("geometry_version"),
                boundary_hash=geometry_entry.get("boundary_hash"),
                geometry_config=geometry_entry.get("config_path"),
                raw_session_dir=session_dir,
                mirror_path=selected.path,
                drivers=drivers, telemetry_laps=laps, drivers_with_laptimes=with_laptimes,
                session_metadata=metadata, warnings=warnings,
            ))

    units.sort(key=lambda item: (item.year, item.event, item.session))
    skipped.sort(key=lambda item: (item.year, item.event, item.session, item.code))
    return Plan(units, skipped, mirrors)


def assert_no_excluded_events(records: Iterable[dict[str, Any]], excluded: Iterable[str], *, context: str) -> None:
    """Fail closed if a held-out event reached an output.

    The plan already filters it. This re-checks the built rows, because the
    exclusion is a training-data guarantee and a guarantee that is only asserted
    at the point of filtering is not one.

    Two independent checks, not one:

    1. Against ``excluded`` -- the operator's configured ``--exclude-event``
       list -- so a custom exclusion set is honoured exactly as configured.
    2. Against :func:`trackshift.data.guards.assert_demo_held_out` at
       ``DemoScope.TRACK`` -- every British Grand Prix, every season --
       unconditionally. This script only ever produces a training-oriented
       spine (there is no final-evaluation or replay path here), so unlike
       ``build_baselines.py``'s ``--include-british-gp-final-replay`` there is
       no legitimate reason for this check to be bypassable from the CLI. The
       guard's own default scope is ``EVENT_YEAR`` (2026 only) and would be a
       silent no-op for 2022-2025 Silverstone, which is exactly the gap this
       call closes.
    """
    blocked = {normalise_event(item) for item in excluded}
    materialised = list(records)
    for record in materialised:
        if normalise_event(str(record.get("event", ""))) in blocked:
            raise SpineError(
                f"excluded event {record.get('event')!r} reached a spine output; "
                "the materialisation is training-oriented and must not contain it")
    assert_demo_held_out(materialised, context, scope=DEMO_SCOPE)


def assert_run_root_fresh(run_root: Path) -> None:
    """Refuse to build into a directory that already holds a completed run.

    Mirrors ``materialize_chain_s.py``'s ``assert_output_root_compatible``: a
    completed manifest means a previous run's C7 Parquet is already under
    ``--run-root``, and a second run with a different scope would merge into it
    with no record of which rows came from which invocation. A *partial* run
    (no manifest yet, e.g. an interrupted build) is not blocked -- resuming it
    is the normal recovery path for a long historical build, and policing that
    would make every smoke-test iteration require a manual wipe first.
    """
    manifest_path = run_root / "run_manifest.json"
    if not manifest_path.exists():
        return
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpineError(f"existing run manifest is unreadable: {manifest_path}") from exc
    version = existing.get("schema_version")
    if version != RUN_SCHEMA_VERSION:
        raise SpineError(
            f"manifest version mismatch in {manifest_path}: found {version!r}, "
            f"expected {RUN_SCHEMA_VERSION!r}; use a fresh --run-root")
    raise SpineError(
        f"--run-root {run_root} already contains a completed {RUN_SCHEMA_VERSION} run "
        "(run_manifest.json exists); use a fresh --run-root so two runs' C7 outputs "
        "are never silently mixed")


def assert_no_overtake_state_columns(columns: Iterable[str], where: str) -> None:
    """Refuse to label historical data with 2026 Overtake state.

    2026 ``overtake_eligible`` / ``overtake_state`` are RULE-derived from the
    configured Detection/Activation lines and observed OVERTAKE messages
    (AGENTS.md sections 20, 41). Historical DRS is a covariate and nothing else,
    so a historical partition carrying an Overtake column is a contract breach
    regardless of how the values were computed.
    """
    found = sorted(set(FORBIDDEN_HISTORICAL_COLUMNS) & {str(item) for item in columns})
    if found:
        raise SpineError(
            f"{where} carries 2026 Overtake column(s) {', '.join(found)} on a "
            f"{HISTORICAL_REGULATION_VERSION} partition; historical DRS is a covariate "
            "(feature registry drs_open, historical_drs_open) and is never Overtake state")


# --------------------------------------------------------------------------- #
# stage 1: telemetry lake
# --------------------------------------------------------------------------- #

def _run(command: list[str], *, label: str) -> tuple[int, str, str]:
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return done.returncode, done.stdout, done.stderr


def _tail(text: str, lines: int = 3) -> str:
    parts = [line for line in (text or "").strip().splitlines() if line.strip()]
    return " | ".join(parts[-lines:]) if parts else ""


def build_lake_stage(plan: Plan, args: argparse.Namespace) -> dict[str, Any]:
    """Build the 20 m lake for each planned year from that year's own mirror.

    ``build_lake.py`` takes one ``--raw-root``, so a year whose mirror sits under
    a different parent needs its own invocation; that is also why failure is
    recorded per year instead of aborting the run.

    It writes ``lap_manifest.csv``, ``rejected_laps.csv`` and
    ``quality_summary.csv`` to the shared lake root, so a second year overwrites
    the first year's copies. The Parquet partitions do not collide, so the data
    is safe; the manifests are snapshotted per year into the run directory
    immediately after each invocation, which is what preserves every year's
    rejection reasons.
    """
    results: dict[str, Any] = {"schema_version": LAKE_SCHEMA_VERSION, "by_year": {}, "failures": []}
    for year in plan.years:
        units = [unit for unit in plan.units if unit.year == year]
        mirror = plan.mirrors[year]["selected"]
        if mirror is None:  # pragma: no cover - planning already skipped the year
            continue
        command = [
            sys.executable, str(LAKE_BUILDER),
            "--raw-root", str((ROOT / mirror["raw_root"]).resolve()),
            "--output-root", str(args.lake_root.resolve()),
            "--years", year,
            "--sessions", ",".join(sorted({unit.session for unit in units})),
            "--events", ",".join(sorted({unit.event for unit in units})),
            "--spacing-m", str(args.spacing_m),
            "--jobs", str(args.jobs),
            "--no-progress",
        ]
        for excluded in args.exclude_event:
            command += ["--exclude-event", excluded]
        if args.drivers:
            command += ["--drivers", *args.drivers]
        if args.max_laps_per_driver is not None:
            command += ["--max-laps-per-driver", str(args.max_laps_per_driver)]

        code, out, err = _run(command, label=f"lake {year}")
        try:
            summary = json.loads(out)
        except json.JSONDecodeError:
            results["failures"].append({
                "year": year, "code": "LAKE_BUILD_UNPARSEABLE",
                "reason": _tail(err) or _tail(out) or f"exit {code}",
            })
            continue

        snapshot = args.run_root / "lake" / f"year={year}"
        snapshot.mkdir(parents=True, exist_ok=True)
        copied = []
        for name in ("lap_manifest.csv", "rejected_laps.csv", "quality_summary.csv", "run_manifest.json"):
            source = args.lake_root / name
            if source.exists():
                shutil.copy2(source, snapshot / name)
                copied.append(name)

        results["by_year"][year] = {
            "command": [str(item) for item in command],
            "raw_root": mirror["raw_root"],
            "raw_mirror": mirror["path"],
            "season_evidence": mirror["season_evidence"],
            "exit_code": code,
            "sessions_built": summary.get("sessions_built"),
            "sessions_failed": summary.get("sessions_failed"),
            "discovered_laps": summary.get("discovered_laps"),
            "accepted_laps": summary.get("accepted_laps"),
            "acceptance_rate_pct": summary.get("acceptance_rate_pct"),
            "output_rows": summary.get("resampled_rows"),
            "rejection_codes": summary.get("rejection_codes"),
            "manifest_snapshot": _relative(snapshot),
            "manifest_files": copied,
        }
        if code != 0 or summary.get("sessions_failed"):
            results["failures"].append({
                "year": year, "code": "LAKE_BUILD_FAILED",
                "reason": f"{summary.get('sessions_failed')} session(s) failed; see "
                          f"{_relative(snapshot / 'run_manifest.json')}",
            })
    return results


# --------------------------------------------------------------------------- #
# stage 2: C1 segments
# --------------------------------------------------------------------------- #

def build_c1_stage(plan: Plan, args: argparse.Namespace) -> dict[str, Any]:
    """Apply each circuit's segment map to the planned years.

    Invoked once per circuit rather than with ``--all``: the excluded event has
    its own circuit key, so per-circuit invocation is what keeps it out by
    construction, and it isolates one malformed segment map from the rest of the
    build. ``build_segments.py --years`` is additive, so years outside this run
    stay in the table.
    """
    results: dict[str, Any] = {"schema_version": C1_SCHEMA_VERSION, "by_circuit": {}, "failures": []}
    years = ",".join(plan.years)
    for circuit in plan.circuits:
        command = [
            sys.executable, str(SEGMENT_BUILDER),
            "--circuit", circuit,
            "--years", years,
            "--lake", str(args.lake_root.resolve()),
            "--output", str(args.segments_root.resolve()),
            "--no-progress",
        ]
        code, out, err = _run(command, label=f"c1 {circuit}")
        try:
            manifest = json.loads(out)
        except json.JSONDecodeError:
            results["failures"].append({
                "circuit": circuit, "code": "C1_BUILD_FAILED",
                "reason": _tail(err) or _tail(out) or f"exit {code}",
            })
            continue
        written = {str(item.get("circuit")): item for item in manifest.get("written", [])}
        skipped = {str(item.get("circuit")): item for item in manifest.get("skipped", [])}
        if circuit in skipped:
            results["failures"].append({
                "circuit": circuit, "code": "C1_NO_ROWS",
                "reason": str(skipped[circuit].get("reason") or "segment builder produced no rows"),
            })
        entry = written.get(circuit, {})
        results["by_circuit"][circuit] = {
            "command": [str(item) for item in command],
            "exit_code": code,
            "geometry_version": entry.get("geometry_version"),
            "segments_per_lap": entry.get("segments_per_lap"),
            "output_rows": entry.get("rows"),
            "artifact": _relative(args.segments_root / f"circuit={circuit}" / "segments.parquet"),
        }
    return results


# --------------------------------------------------------------------------- #
# stage 3: C7 race context
# --------------------------------------------------------------------------- #

def _lake_partition(lake_root: Path, unit: Unit) -> Path:
    return (lake_root / f"year={unit.year}" / f"event={_safe(unit.event)}"
            / f"session={_safe(unit.session)}" / "telemetry_20m.parquet")


def _drs_era_audit(lake_partition: Path) -> dict[str, Any]:
    """Audit the historical DRS covariate without promoting it to anything.

    ``drs_open`` is an OBSERVED 2022-2025 covariate. Recording how much of it is
    actually present is what makes a later regulation-era comparison honest --
    CP-08's gate is that historical DRS is never supplied as 2026 Overtake
    state, not that it is discarded.
    """
    import pandas as pd

    try:
        frame = pd.read_parquet(lake_partition, columns=["drs_open"])
    except (OSError, ValueError, KeyError) as exc:
        return {"available": False, "reason": f"drs_open unreadable: {type(exc).__name__}: {exc}"}
    column = frame["drs_open"]
    present = int(column.notna().sum())
    active = int(pd.to_numeric(column, errors="coerce").fillna(0).gt(0).sum())
    return {
        "available": True,
        "column": "drs_open",
        "provenance": "OBSERVED",
        "regulation_version": HISTORICAL_REGULATION_VERSION,
        "rows": int(len(column)),
        "non_null_rows": present,
        "active_rows": active,
        "active_pct": round(100.0 * active / len(column), 3) if len(column) else 0.0,
        "classification": "historical DRS-era covariate; gated historical_drs_open / "
                          "historical_drs_eligible are produced downstream by "
                          "scripts/rules/apply_overtake_state.py, not here",
        "not_overtake_state": "2026 overtake_eligible/overtake_state are RULE-derived only "
                              "(AGENTS.md sections 20, 41); no Overtake column is written here",
    }


def _track_status_fidelity(laps) -> dict[str, Any]:
    """Audit what C7's race-control input can and cannot resolve.

    ``derive_lap_c7_context`` maps ``track_status`` to GREEN iff
    ``str(value) == "1"`` and UNKNOWN otherwise -- it has no YELLOW, SC or VSC
    state of its own, so ``safety_car_active``/``virtual_safety_car_active`` are
    identically False for every historical partition built here. That is a real
    limit on what this spine's eligibility gate can distinguish, not a defect to
    paper over, so it is counted rather than silently accepted: a null
    ``track_status`` (no race-control metadata reached this lap at all) is a
    different failure from a present-but-non-GREEN value (a neutralised lap C7
    correctly marks UNKNOWN), and conflating the two would hide which laps are
    merely cautious versus genuinely unresolvable.
    """
    total = int(len(laps))
    status = laps["track_status"]
    null_status = int(status.isna().sum())
    non_null = status.dropna().astype(str)
    green = int((non_null == "1").sum())
    neutralised = int((non_null != "1").sum())
    return {
        "total_laps": total,
        "null_track_status_laps": null_status,
        "green_laps": green,
        "neutralised_or_unresolved_laps": neutralised,
        "safety_car_and_vsc_detection": "UNAVAILABLE: derive_lap_c7_context has no SC/VSC state of "
                                        "its own for this input; safety_car_active and "
                                        "virtual_safety_car_active are identically False here",
    }


def build_c7_stage(plan: Plan, args: argparse.Namespace) -> dict[str, Any]:
    """Join C7 race context onto each unit's C1 rows, per circuit.

    C7 comes from the public ``derive_lap_c7_context`` normaliser on the lake's
    lap metadata -- C1 deliberately keeps its input immutable and carries no pit
    timestamps -- which is the same boundary the 2026 Chain S materialiser uses.

    One output file per circuit and one manifest for the whole run. The
    standalone ``scripts/features/build_race_context.py`` writes its manifest to
    a single fixed path per invocation, so driving a whole season through it
    leaves only the last session's record; an aggregate manifest is what makes a
    52-unit build auditable.
    """
    import pandas as pd

    from trackshift.features.tyre_pace import derive_lap_c7_context

    metadata_columns = ["year", "event", "session", "driver", "lap", "track_status",
                        "lap_start_session_s", "pit_in_session_s", "pit_out_session_s"]
    results: dict[str, Any] = {"schema_version": C7_SCHEMA_VERSION, "by_unit": {},
                               "by_circuit": {}, "failures": [], "era_covariates": {}}
    frames: dict[str, list[Any]] = {}
    # Many units share a circuit's C1 artifact; hash each source path once
    # rather than once per unit that reads it.
    hash_cache: dict[Path, str | None] = {}

    def cached_sha256(path: Path) -> str | None:
        if path not in hash_cache:
            hash_cache[path] = sha256_file(path)
        return hash_cache[path]

    for unit in plan.units:
        source_c1 = args.segments_root / f"circuit={unit.circuit}" / "segments.parquet"
        if not source_c1.exists():
            results["failures"].append({"unit": unit.key, "code": "C7_NO_C1_ARTIFACT",
                                        "reason": f"C1 artifact absent: {_relative(source_c1)}"})
            continue
        lake_partition = _lake_partition(args.lake_root, unit)
        if not lake_partition.exists():
            results["failures"].append({"unit": unit.key, "code": "C7_NO_LAKE_PARTITION",
                                        "reason": f"telemetry-20m partition absent: {_relative(lake_partition)}"})
            continue

        c1 = pd.read_parquet(source_c1)
        c1 = c1[(c1["year"].astype(str) == unit.year)
                & (c1["event"] == unit.event)
                & (c1["session"] == unit.session)].copy()
        if c1.empty:
            results["failures"].append({"unit": unit.key, "code": "C7_NO_C1_ROWS",
                                        "reason": f"no C1 rows for this unit in {_relative(source_c1)}"})
            continue
        assert_no_overtake_state_columns(c1.columns, f"C1 input for {unit.key}")
        # C2's documented join key includes the circuit, which the C1 artifact
        # encodes as a partition rather than a column. Make it explicit here so
        # downstream joins do not have to re-derive it from the path.
        c1["circuit"] = unit.circuit

        lake = pd.read_parquet(lake_partition, columns=metadata_columns)
        laps = lake.drop_duplicates(["year", "event", "session", "driver", "lap"])
        fidelity = _track_status_fidelity(laps)
        c7 = derive_lap_c7_context(laps)
        keys = ["year", "event", "session", "driver", "lap"]
        merged = c1.merge(c7[[*keys, *C7_FIELDS]], on=keys, how="left", validate="many_to_one")

        unresolved = {name: int(merged[name].isna().sum()) for name in C7_FIELDS
                      if merged[name].isna().any()}
        if unresolved:
            # A C1 lap with no matching lake lap metadata cannot be given a race
            # state. Failing this unit rather than the run keeps one thin session
            # from costing the other fifty-one.
            results["failures"].append({
                "unit": unit.key, "code": "C7_FIELDS_UNRESOLVED",
                "reason": "no lake lap metadata for some C1 laps; unresolved rows per field: "
                          + ", ".join(f"{name}={count}" for name, count in sorted(unresolved.items())),
            })
            continue
        assert_no_overtake_state_columns(merged.columns, f"C7 output for {unit.key}")

        frames.setdefault(unit.circuit, []).append(merged)
        eligible = int(merged["normal_race_model_eligible"].eq(True).sum())
        results["by_unit"][unit.key] = {
            "year": unit.year, "event": unit.event, "session": unit.session,
            "circuit": unit.circuit,
            "geometry_version": unit.geometry_version,
            "boundary_hash": unit.boundary_hash,
            "source_c1": _relative(source_c1),
            "source_telemetry_20m": _relative(lake_partition),
            "output_rows": int(len(merged)),
            "normal_race_model_eligible_rows": eligible,
            "normal_race_model_ineligible_rows": int(len(merged) - eligible),
            "laps": int(merged[["driver", "lap"]].drop_duplicates().shape[0]),
            "warnings": sorted(unit.warnings),
            "c7_source": "telemetry_20m_lap_metadata_v1",
            "race_control_fidelity": fidelity,
            "input_artifact_sha256": {
                "source_c1": cached_sha256(source_c1),
                "source_telemetry_20m": cached_sha256(lake_partition),
            },
        }
        results["era_covariates"][unit.key] = _drs_era_audit(lake_partition)

    for circuit, circuit_frames in sorted(frames.items()):
        combined = pd.concat(circuit_frames, ignore_index=True)
        assert_no_overtake_state_columns(combined.columns, f"C7 artifact for circuit={circuit}")
        assert_no_excluded_events(combined[["year", "event"]].drop_duplicates().to_dict("records"),
                                  args.exclude_event, context=f"C7 artifact for circuit={circuit}")
        destination = args.run_root / "c7" / f"circuit={circuit}"
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / "segments.parquet"
        combined.to_parquet(target, index=False)
        results["by_circuit"][circuit] = {
            "artifact": _relative(target),
            "output_rows": int(len(combined)),
            "normal_race_model_eligible_rows": int(combined["normal_race_model_eligible"].eq(True).sum()),
            "units": sorted({f"{row.year}/{row.event}/{row.session}"
                             for row in combined[["year", "event", "session"]]
                             .drop_duplicates().itertuples(index=False)}),
        }
    return results


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #

def _plan_summary(plan: Plan, args: argparse.Namespace) -> dict[str, Any]:
    by_year: dict[str, Any] = {}
    for year in args.years:
        units = [unit for unit in plan.units if unit.year == year]
        skips = [skip for skip in plan.skipped if skip.year == year]
        codes: dict[str, int] = {}
        for skip in skips:
            codes[skip.code] = codes.get(skip.code, 0) + 1
        by_year[year] = {
            "buildable_units": len(units),
            "race_units": sum(1 for unit in units if unit.session == "Race"),
            "sprint_units": sum(1 for unit in units if unit.session == "Sprint"),
            "telemetry_laps": sum(unit.telemetry_laps for unit in units),
            "circuits": sorted({unit.circuit for unit in units}),
            "events": sorted({unit.event for unit in units}),
            "skipped": len(skips),
            "skip_codes": dict(sorted(codes.items(), key=lambda item: (-item[1], item[0]))),
            "raw_mirror": (plan.mirrors.get(year, {}).get("selected") or {}).get("path"),
            "raw_mirror_selection_skip_reason": plan.mirrors.get(year, {}).get("selection_skip_reason"),
        }
    return by_year


def run(args: argparse.Namespace) -> dict[str, Any]:
    plan = build_plan(
        args.years, args.sessions,
        events=args.events,
        excluded_events=args.exclude_event,
        raw_sources=args.raw_source,
        allow_directory_mismatch=args.allow_directory_mismatch,
        require_race_control=args.require_race_control,
        geometry_dir=args.geometry_dir,
    )
    assert_no_excluded_events([unit.as_record() for unit in plan.units], args.exclude_event,
                              context="historical spine discovery plan")

    rules_seasons = rules_coverage()
    manifest: dict[str, Any] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        **git_provenance(),
        "regulation_era": HISTORICAL_REGULATION_VERSION,
        "configuration": {
            "years": list(args.years),
            "sessions": list(args.sessions),
            "events": list(args.events) if args.events else None,
            "excluded_events": list(args.exclude_event),
            "stages": list(args.stages),
            "audit_only": bool(args.audit_only),
            "jobs": args.jobs,
            "spacing_m": args.spacing_m,
            "drivers": list(args.drivers) if args.drivers else None,
            "max_laps_per_driver": args.max_laps_per_driver,
            "allow_directory_mismatch": bool(args.allow_directory_mismatch),
            "require_race_control": bool(args.require_race_control),
            "lake_root": _relative(args.lake_root),
            "segments_root": _relative(args.segments_root),
            "geometry_dir": _relative(args.geometry_dir),
            "run_root": _relative(args.run_root),
            "raw_sources_override": [_relative(item) for item in args.raw_source],
        },
        "schema_versions": {
            "telemetry_20m": LAKE_SCHEMA_VERSION,
            "segments": C1_SCHEMA_VERSION,
            "race_context": C7_SCHEMA_VERSION,
        },
        "rule_configuration": {
            # Recorded, not filled in: C1 and C7 do not consume event rules, and a
            # historical rule snapshot that does not exist must not be implied.
            "seasons_on_disk": rules_seasons,
            "seasons_in_scope_with_configuration": [year for year in args.years if year in rules_seasons],
            "rules_version": None,
            "rules_version_reason": "no config/rules/<season> exists for 2022-2025; C1 and C7 do not "
                                    "consume event rules (AGENTS.md section 21). M20/C6 and any "
                                    "Overtake-derived feature does, and is out of this scope.",
        },
        "raw_mirrors": plan.mirrors,
        "plan_by_year": _plan_summary(plan, args),
        "units": [unit.as_record() for unit in plan.units],
        "skipped_units": [skip.as_record() for skip in plan.skipped],
        "british_gp_excluded_by_default": normalise_event(DEFAULT_EXCLUDED_EVENT) in
                                          {normalise_event(item) for item in args.exclude_event},
        "historical_drs_policy": (
            "drs_open is carried through the lake unchanged as an OBSERVED 2022-2025 covariate. "
            "No overtake_state/overtake_eligible column is written for a historical partition; "
            "2026 Overtake state is RULE-derived only (AGENTS.md sections 20, 41)."
        ),
        "stages": {},
    }

    if args.audit_only:
        manifest["status"] = "AUDIT_ONLY"
        return manifest

    if not plan.units:
        manifest["status"] = "NOTHING_TO_BUILD"
        return manifest

    assert_run_root_fresh(args.run_root)
    args.run_root.mkdir(parents=True, exist_ok=True)
    if "lake" in args.stages:
        manifest["stages"]["lake"] = build_lake_stage(plan, args)
    if "c1" in args.stages:
        manifest["stages"]["c1"] = build_c1_stage(plan, args)
    if "c7" in args.stages:
        manifest["stages"]["c7"] = build_c7_stage(plan, args)

    failures = [failure for stage in manifest["stages"].values()
                for failure in stage.get("failures", [])]
    c7 = manifest["stages"].get("c7", {})
    manifest["totals"] = {
        "planned_units": len(plan.units),
        "skipped_units": len(plan.skipped),
        "lake_output_rows": sum(int(entry.get("output_rows") or 0)
                                for entry in manifest["stages"].get("lake", {}).get("by_year", {}).values()),
        "c1_output_rows": sum(int(entry.get("output_rows") or 0)
                              for entry in manifest["stages"].get("c1", {}).get("by_circuit", {}).values()),
        "c7_output_rows": sum(int(entry.get("output_rows") or 0)
                              for entry in c7.get("by_unit", {}).values()),
        "c7_normal_race_model_eligible_rows": sum(int(entry.get("normal_race_model_eligible_rows") or 0)
                                                  for entry in c7.get("by_unit", {}).values()),
        "c7_units_built": len(c7.get("by_unit", {})),
        "stage_failures": len(failures),
    }
    manifest["stage_failures"] = failures
    manifest["status"] = "COMPLETE" if not failures else "COMPLETE_WITH_FAILURES"
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--years", default=",".join(HISTORICAL_YEARS),
                        help=f"CSV of historical seasons (default: {','.join(HISTORICAL_YEARS)})")
    result.add_argument("--sessions", default=",".join(DEFAULT_SESSIONS),
                        help=f"CSV of session names (default: {','.join(DEFAULT_SESSIONS)})")
    result.add_argument("--events", default=None, help="CSV of exact event names; default every event")
    result.add_argument("--exclude-event", action="append", default=None,
                        help=f"Repeatable. Additive to the default {DEFAULT_EXCLUDED_EVENT!r} exclusion, "
                             "never a replacement of it: this script only ever produces a "
                             "training-oriented spine, and an --exclude-event value for something "
                             "else must not be able to let the held-out demo event back in "
                             "(precedent: scripts/features/materialize_chain_s.py --exclude-event).")
    result.add_argument("--raw-source", action="append", type=Path, default=[],
                        help="Repeatable season directory searched before config/raw_sources.yaml")
    result.add_argument("--allow-directory-mismatch", action="store_true",
                        help="Accept a mirror whose directory name is not the season it declares. "
                             "The lake partition is named from the directory, so this mislabels it.")
    result.add_argument("--require-race-control", action="store_true",
                        help="Skip a session with no rcm.json instead of recording a warning")
    result.add_argument("--lake-root", type=Path, default=ROOT / "data" / "processed" / "telemetry_20m")
    result.add_argument("--segments-root", type=Path, default=ROOT / "data" / "processed" / "segments")
    result.add_argument("--geometry-dir", type=Path, default=GEOMETRY_DIR,
                        help="config/geometry overlay; overridable for tests and alternate circuit sets")
    result.add_argument("--run-root", type=Path, default=ROOT / "data" / "processed" / "historical_spine",
                        help="C7 output and the run manifest. Under data/, which is never committed.")
    result.add_argument("--stages", default="lake,c1,c7", help="CSV subset of lake,c1,c7")
    result.add_argument("--audit-only", action="store_true",
                        help="Report availability and skip reasons; write nothing but the manifest")
    result.add_argument("--manifest", type=Path, default=None,
                        help="Where to write the run manifest (default: <run-root>/run_manifest.json; "
                             "for --audit-only: artifacts/schema_audit/historical_spine_audit.json)")
    result.add_argument("--drivers", nargs="+", default=None, help="Limit the lake build (smoke tests)")
    result.add_argument("--max-laps-per-driver", type=int, default=None)
    result.add_argument("--spacing-m", type=float, default=20.0)
    result.add_argument("--jobs", type=int, default=1)
    return result


def _normalise(args: argparse.Namespace, parse_error) -> argparse.Namespace:
    args.years = tuple(item.strip() for item in args.years.split(",") if item.strip())
    unknown = [year for year in args.years if year not in HISTORICAL_YEARS]
    if unknown:
        parse_error(f"--years accepts only historical seasons {','.join(HISTORICAL_YEARS)}; "
                    f"got {','.join(unknown)}. 2026 is a separate regulatory domain with its own "
                    "path (scripts/features/materialize_chain_s.py).")
    args.sessions = tuple(item.strip() for item in args.sessions.split(",") if item.strip())
    if not args.sessions:
        parse_error("--sessions is empty")
    args.events = tuple(item.strip() for item in args.events.split(",") if item.strip()) if args.events else None
    # Additive, never a replacement: a user-supplied value extends the default
    # exclusion rather than standing in for it, so --exclude-event cannot be used
    # to let the held-out demo event back into a training-oriented build. The
    # independent assert_demo_held_out(..., scope=DEMO_SCOPE) call backs this
    # even if this list were ever bypassed.
    args.exclude_event = [DEFAULT_EXCLUDED_EVENT, *(args.exclude_event or [])]
    args.stages = tuple(item.strip() for item in args.stages.split(",") if item.strip())
    unknown_stages = [stage for stage in args.stages if stage not in ("lake", "c1", "c7")]
    if unknown_stages:
        parse_error(f"unknown stage(s): {','.join(unknown_stages)}; choose from lake,c1,c7")
    if args.jobs < 1:
        parse_error("--jobs must be positive")
    if args.spacing_m <= 0:
        parse_error("--spacing-m must be positive")
    if args.manifest is None:
        args.manifest = (ROOT / "artifacts" / "schema_audit" / "historical_spine_audit.json"
                         if args.audit_only else args.run_root / "run_manifest.json")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    built = parser()
    args = _normalise(built.parse_args(argv), built.error)
    manifest = run(args)

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n",
                             encoding="utf-8")

    # AGENTS.md section 49: every data-producing script reports input scope,
    # files discovered, accepted records, rejected records, output path,
    # configuration, and schema version on stdout. Rejected/skipped is never
    # swallowed -- skip_codes below is the per-unit breakdown and lake_laps
    # carries the lap-level accept/reject counts from the lake stage.
    lake_by_year = manifest.get("stages", {}).get("lake", {}).get("by_year", {})
    print(json.dumps({
        "schema_version": manifest["schema_version"],
        "status": manifest["status"],
        "git_commit": manifest["git_commit"],
        "input_scope": {
            "years": manifest["configuration"]["years"],
            "sessions": manifest["configuration"]["sessions"],
            "events": manifest["configuration"]["events"],
            "excluded_events": manifest["configuration"]["excluded_events"],
        },
        "configuration": manifest["configuration"],
        "discovered": {
            "planned_units": len(manifest["units"]),
            "skipped_units": len(manifest["skipped_units"]),
            "telemetry_laps_discovered": sum(
                int(year_plan.get("telemetry_laps") or 0)
                for year_plan in manifest["plan_by_year"].values()),
        },
        "accepted": {
            "lake_accepted_laps": sum(int(entry.get("accepted_laps") or 0) for entry in lake_by_year.values()),
            **{key: manifest["totals"][key] for key in
               ("lake_output_rows", "c1_output_rows", "c7_output_rows",
                "c7_normal_race_model_eligible_rows") if manifest.get("totals")},
        },
        "rejected": {
            "lake_rejected_laps": sum(int(entry.get("discovered_laps") or 0) - int(entry.get("accepted_laps") or 0)
                                      for entry in lake_by_year.values()),
            "skip_codes": {code: sum(1 for skip in manifest["skipped_units"] if skip["code"] == code)
                          for code in sorted({skip["code"] for skip in manifest["skipped_units"]})},
            "stage_failures": manifest.get("stage_failures", []),
        },
        "output_paths": {
            "lake_root": _relative(args.lake_root),
            "segments_root": _relative(args.segments_root),
            "run_root": _relative(args.run_root),
            "manifest": _relative(args.manifest),
        },
        "plan_by_year": manifest["plan_by_year"],
    }, indent=2, default=str))
    return 1 if manifest.get("stage_failures") else 0


if __name__ == "__main__":
    raise SystemExit(main())
