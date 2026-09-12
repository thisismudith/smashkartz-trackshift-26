"""Focused CP-08-prerequisite orchestration guards; all fixtures are local and tiny.

Covers the four areas the historical spine task calls out explicitly:
portability (the raw mirror is resolved, never hard-coded), year filtering
(only 2022-2025 is buildable here), manifest skip reasons (every dropped unit
carries a code and a reason), and British GP exclusion (absolute, by
construction, independent of what --exclude-event is given).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SPEC = importlib.util.spec_from_file_location(
    "materialize_historical_spine", ROOT / "scripts" / "data" / "materialize_historical_spine.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

import trackshift.data.raw_sources as raw_sources  # noqa: E402
from trackshift.data.guards import DemoEventLeak  # noqa: E402


def _write_geometry(geometry_dir: Path, circuit: str, event_display: str) -> None:
    geometry_dir.mkdir(parents=True, exist_ok=True)
    (geometry_dir / f"{circuit}.yaml").write_text(
        f"circuit: {circuit}\n"
        f"event_display: {event_display}\n"
        f"geometry_version: {circuit}-geom-v1\n"
        f"boundary_hash: deadbeef\n"
        f"segment_count: 2\n",
        encoding="utf-8",
    )


def _write_session(mirror: Path, event: str, session: str, drivers: dict[str, int],
                   with_laptimes: bool = True, with_rcm: bool = True) -> None:
    """A raw session directory: N laps per driver, optional rcm/laptimes."""
    session_dir = mirror / event / session
    for driver, laps in drivers.items():
        driver_dir = session_dir / driver
        driver_dir.mkdir(parents=True, exist_ok=True)
        for lap in range(1, laps + 1):
            (driver_dir / f"{lap}_tel.json").write_text("{}", encoding="utf-8")
        if with_laptimes:
            (driver_dir / "laptimes.json").write_text("{}", encoding="utf-8")
    if with_rcm:
        (session_dir / "rcm.json").write_text("{}", encoding="utf-8")


def _write_mirror(root: Path, season: str, *, remote: bool = True) -> Path:
    """A season mirror whose directory name matches its declared season."""
    mirror = root / season
    mirror.mkdir(parents=True, exist_ok=True)
    if remote:
        git_dir = mirror / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            f'[remote "origin"]\n\turl = https://github.com/TracingInsights/{season}.git\n',
            encoding="utf-8",
        )
    return mirror


def _raw_sources_config(tmp_path: Path, raw_parent: Path | None = None) -> Path:
    """A raw_sources.yaml with one ABSOLUTE search pattern.

    Absolute patterns bypass repo_root resolution entirely (see
    ``trackshift.data.raw_sources._expand``), so these fixtures are independent
    of whether a test also asks build_plan for a synthetic ``repo_root``.
    """
    parent = raw_parent if raw_parent is not None else (tmp_path / "raw")
    config = tmp_path / "raw_sources.yaml"
    pattern = (parent / "<season>").as_posix()
    config.write_text(f'schema_version: 1\nsearch:\n  - "{pattern}"\nignore_directories: []\n',
                      encoding="utf-8")
    return config


@pytest.fixture(autouse=True)
def _isolate_raw_sources_config(monkeypatch):
    """Every test in this file supplies its own raw_sources.yaml; never let a
    test accidentally resolve against the real project configuration."""
    monkeypatch.setattr(raw_sources, "DEFAULT_CONFIG", Path("/nonexistent/raw_sources.yaml"))


def _plan(tmp_path, config, years, sessions, geometry_dir, monkeypatch, **kwargs):
    monkeypatch.setattr(raw_sources, "DEFAULT_CONFIG", config)
    return MODULE.build_plan(years, sessions, geometry_dir=geometry_dir, **kwargs)


# --------------------------------------------------------------------- portability

def test_build_plan_resolves_the_mirror_through_raw_sources_not_a_hardcoded_path(tmp_path, monkeypatch):
    """The plan must find a mirror via its declared season, the same seam
    tests/test_raw_sources.py exercises directly -- not by assuming a fixed
    --raw-root. The mirror lives under an arbitrary parent directory; nothing
    in this test or in build_plan names it specifically."""
    raw_parent = tmp_path / "somewhere_else_entirely"
    mirror = _write_mirror(raw_parent, "2022")
    _write_session(mirror, "Australian Grand Prix", "Race", {"VER": 3})
    geometry_dir = tmp_path / "geometry"
    _write_geometry(geometry_dir, "australian", "Australian Grand Prix")
    config = _raw_sources_config(tmp_path, raw_parent)

    plan = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch)

    assert [u.key for u in plan.units] == ["2022/Australian Grand Prix/Race"]
    assert plan.mirrors["2022"]["selected"] is not None
    assert plan.units[0].mirror_path == mirror


def test_build_plan_records_no_usable_mirror_when_the_season_is_absent(tmp_path, monkeypatch):
    geometry_dir = tmp_path / "geometry"
    _write_geometry(geometry_dir, "australian", "Australian Grand Prix")
    config = _raw_sources_config(tmp_path)  # nothing lives under tmp_path/raw

    plan = _plan(tmp_path, config, ["2023"], ["Race"], geometry_dir, monkeypatch)

    assert plan.units == []
    codes = {s.code for s in plan.skipped}
    assert "NO_SEASON_MIRROR" in codes


def test_build_plan_refuses_a_misnamed_mirror_by_default(tmp_path, monkeypatch):
    """A season resolvable only through a directory whose name disagrees with
    its declared season must not be silently built -- the lake partition is
    named from the directory, which would mislabel the data. The resolver-level
    behaviour is covered exhaustively in tests/test_raw_sources.py; this checks
    that build_plan surfaces the refusal as a coded skip rather than swallowing
    it or building anyway."""
    raw_parent = tmp_path / "raw"
    mirror = raw_parent / "2027"  # directory name disagrees with the declared season
    mirror.mkdir(parents=True)
    git_dir = mirror / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/TracingInsights/2025.git\n', encoding="utf-8")
    _write_session(mirror, "Australian Grand Prix", "Race", {"VER": 3})
    geometry_dir = tmp_path / "geometry"
    _write_geometry(geometry_dir, "australian", "Australian Grand Prix")
    # A wildcard pattern is what lets a misnamed mirror be found at all.
    config = tmp_path / "raw_sources.yaml"
    config.write_text(
        f'schema_version: 1\nsearch:\n  - "{(raw_parent / "*").as_posix()}"\nignore_directories: []\n',
        encoding="utf-8")

    plan = _plan(tmp_path, config, ["2025"], ["Race"], geometry_dir, monkeypatch)

    assert plan.units == []
    skip = next(s for s in plan.skipped if s.year == "2025")
    assert skip.code == "NO_USABLE_RAW_MIRROR"
    assert "directory name" in skip.reason


# --------------------------------------------------------------------- year filtering

def test_cli_rejects_2026_as_a_historical_year():
    """2026 is a different regulatory domain with its own materialiser
    (materialize_chain_s.py); this orchestrator must refuse to touch it."""
    built = MODULE.parser()
    args = built.parse_args(["--years", "2022,2026"])
    with pytest.raises(SystemExit):
        MODULE._normalise(args, built.error)


def test_cli_accepts_a_csv_subset_of_historical_years():
    built = MODULE.parser()
    args = MODULE._normalise(built.parse_args(["--years", "2023,2025"]), built.error)
    assert args.years == ("2023", "2025")


def test_build_plan_only_plans_the_requested_years(tmp_path, monkeypatch):
    raw_parent = tmp_path / "raw"
    mirror_2022 = _write_mirror(raw_parent, "2022")
    _write_session(mirror_2022, "Australian Grand Prix", "Race", {"VER": 3})
    mirror_2023 = _write_mirror(raw_parent, "2023")
    _write_session(mirror_2023, "Australian Grand Prix", "Race", {"VER": 3})
    geometry_dir = tmp_path / "geometry"
    _write_geometry(geometry_dir, "australian", "Australian Grand Prix")
    config = _raw_sources_config(tmp_path, raw_parent)

    plan = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch)

    assert {u.year for u in plan.units} == {"2022"}
    assert "2023" not in plan.mirrors


# --------------------------------------------------------------------- manifest skip reasons

@pytest.fixture
def two_event_mirror(tmp_path):
    raw_parent = tmp_path / "raw"
    mirror = _write_mirror(raw_parent, "2022")
    _write_session(mirror, "Australian Grand Prix", "Race", {"VER": 3})
    # Abu Dhabi has telemetry but no geometry map -- a real, common gap.
    _write_session(mirror, "Abu Dhabi Grand Prix", "Race", {"VER": 3})
    # British Grand Prix must be excluded regardless of having everything.
    _write_session(mirror, "British Grand Prix", "Race", {"VER": 3})
    # A session directory that exists but has no telemetry at all.
    (mirror / "Monaco Grand Prix" / "Race" / "empty_placeholder").mkdir(parents=True)
    geometry_dir = tmp_path / "geometry"
    _write_geometry(geometry_dir, "australian", "Australian Grand Prix")
    _write_geometry(geometry_dir, "monaco", "Monaco Grand Prix")
    config = _raw_sources_config(tmp_path, raw_parent)
    return tmp_path, geometry_dir, config


def test_every_skip_carries_a_code_and_a_reason(two_event_mirror, monkeypatch):
    tmp_path, geometry_dir, config = two_event_mirror
    plan = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch)
    assert plan.skipped, "fixture is designed to produce skips"
    for skip in plan.skipped:
        assert skip.code and skip.code == skip.code.upper(), "codes are UPPER_SNAKE"
        assert skip.reason, f"skip {skip.code} has no human reason"


def test_no_geometry_config_is_recorded_by_code(two_event_mirror, monkeypatch):
    tmp_path, geometry_dir, config = two_event_mirror
    plan = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch)
    abu_dhabi_skips = [s for s in plan.skipped if s.event == "Abu Dhabi Grand Prix"]
    assert len(abu_dhabi_skips) == 1
    assert abu_dhabi_skips[0].code == "NO_GEOMETRY_CONFIG"
    assert "segment map" in abu_dhabi_skips[0].reason


def test_no_telemetry_laps_is_recorded_by_code(two_event_mirror, monkeypatch):
    tmp_path, geometry_dir, config = two_event_mirror
    plan = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch)
    monaco_skips = [s for s in plan.skipped if s.event == "Monaco Grand Prix"]
    assert len(monaco_skips) == 1
    assert monaco_skips[0].code == "NO_TELEMETRY_LAPS"


def test_skips_are_deterministically_sorted(two_event_mirror, monkeypatch):
    tmp_path, geometry_dir, config = two_event_mirror
    plan_a = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch)
    plan_b = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch)
    assert [s.as_record() for s in plan_a.skipped] == [s.as_record() for s in plan_b.skipped]


def test_manifest_run_records_every_configured_field(two_event_mirror, monkeypatch):
    """The run manifest (via run()) must carry source year, event, schema,
    geometry version, git commit, and skip/failure reasons -- not just the
    built units."""
    tmp_path, geometry_dir, config = two_event_mirror
    monkeypatch.setattr(raw_sources, "DEFAULT_CONFIG", config)
    built = MODULE.parser()
    args = MODULE._normalise(built.parse_args([
        "--years", "2022", "--sessions", "Race", "--audit-only",
        "--geometry-dir", str(geometry_dir),
    ]), built.error)
    manifest = MODULE.run(args)

    assert manifest["status"] == "AUDIT_ONLY"
    assert manifest["schema_version"] == MODULE.RUN_SCHEMA_VERSION
    assert "git_commit" in manifest
    unit = next(u for u in manifest["units"] if u["event"] == "Australian Grand Prix")
    assert unit["year"] == "2022"
    assert unit["geometry_version"] == "australian-geom-v1"
    assert any(s["code"] == "NO_GEOMETRY_CONFIG" for s in manifest["skipped_units"])
    assert any(s["code"] == "EXCLUDED_EVENT" for s in manifest["skipped_units"])


# --------------------------------------------------------------------- British exclusion

def test_british_gp_is_excluded_by_default(two_event_mirror, monkeypatch):
    tmp_path, geometry_dir, config = two_event_mirror
    plan = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch)
    assert "British Grand Prix" not in {u.event for u in plan.units}
    british_skips = [s for s in plan.skipped if s.event == "British Grand Prix"]
    assert len(british_skips) == 1
    assert british_skips[0].code == "EXCLUDED_EVENT"


def test_british_gp_exclusion_is_spelling_insensitive(two_event_mirror, monkeypatch):
    """Matching goes through guards.normalise_event, not a bare string compare,
    so a differently-spaced or cased configured exclusion still catches it."""
    tmp_path, geometry_dir, config = two_event_mirror
    plan = _plan(tmp_path, config, ["2022"], ["Race"], geometry_dir, monkeypatch,
                excluded_events=["british_grand_prix"])
    assert "British Grand Prix" not in {u.event for u in plan.units}


def test_exclude_event_flag_is_additive_not_a_replacement():
    """Passing --exclude-event for something else must not let the held-out
    demo event back in: this script has no final-evaluation/replay path, so
    there is no legitimate reason for the CLI to be able to remove it."""
    built = MODULE.parser()
    args = MODULE._normalise(built.parse_args(["--exclude-event", "Monaco Grand Prix"]), built.error)
    assert "British Grand Prix" in args.exclude_event
    assert "Monaco Grand Prix" in args.exclude_event


def test_assert_no_excluded_events_raises_on_historical_british_gp_regardless_of_configured_list():
    """Independent backstop: even if the configured exclusion list were empty,
    a historical British GP row must still be rejected, because every row this
    script can produce is training-oriented and DemoScope.TRACK is checked
    unconditionally."""
    rows = [{"year": "2023", "event": "British Grand Prix"}]
    with pytest.raises(DemoEventLeak):
        MODULE.assert_no_excluded_events(rows, excluded=[], context="test")


def test_assert_no_excluded_events_passes_clean_rows():
    rows = [{"year": "2023", "event": "Monaco Grand Prix"}]
    MODULE.assert_no_excluded_events(rows, excluded=["British Grand Prix"], context="test")


def test_assert_no_excluded_events_raises_on_configured_exclusion_too():
    rows = [{"year": "2023", "event": "Monaco Grand Prix"}]
    with pytest.raises(MODULE.SpineError):
        MODULE.assert_no_excluded_events(rows, excluded=["Monaco Grand Prix"], context="test")


def test_no_overtake_state_columns_on_a_historical_partition():
    """Historical DRS must never be asserted as 2026 Overtake state."""
    with pytest.raises(MODULE.SpineError, match="Overtake"):
        MODULE.assert_no_overtake_state_columns(["driver", "overtake_eligible"], "test C7 output")
    MODULE.assert_no_overtake_state_columns(["driver", "historical_drs_open"], "test C7 output")
