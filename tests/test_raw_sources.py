"""Tests for resolving a season's raw mirror across machines (CP-08 prerequisite).

AGENTS.md section 5 forbids hard-coding one machine's layout; section 7 says the
migration into ``data/raw/tracinginsights/<season>/`` happens only after the
local audit, so a mirror can live elsewhere or under a directory whose name is
not its season. These tests concentrate on exactly that seam: a mirror must be
found by what it *declares itself to be*, never by its directory name alone, and
a name/season disagreement must be reported rather than silently accepted.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.raw_sources import (  # noqa: E402
    RawSourceConfigError,
    declared_season,
    load_search_config,
    resolve_season_mirrors,
    select_season_mirror,
)


def _make_mirror(path: Path, *, remote_season: str | None = None,
                 readme_season: str | None = None, sessions: dict[str, list[str]] | None = None) -> None:
    """Build a miniature season mirror: optional git remote, README, sessions."""
    path.mkdir(parents=True, exist_ok=True)
    if remote_season is not None:
        git_dir = path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(textwrap.dedent(f"""\
            [remote "origin"]
            \turl = https://github.com/TracingInsights/{remote_season}.git
            \tfetch = +refs/heads/*:refs/remotes/origin/*
        """), encoding="utf-8")
    if readme_season is not None:
        (path / "README.md").write_text(
            f"# {readme_season}\n{readme_season} Public F1 telemetry files\n", encoding="utf-8")
    for event, session_names in (sessions or {}).items():
        for session in session_names:
            (path / event / session / "HAM").mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------- declared_season

def test_git_remote_is_the_strongest_evidence(tmp_path):
    mirror = tmp_path / "2027"  # directory name disagrees with the remote on purpose
    _make_mirror(mirror, remote_season="2025", readme_season="2027")
    season, evidence, rank = declared_season(mirror)
    assert season == "2025"
    assert "git remote" in evidence
    assert rank == 0


def test_readme_is_evidence_when_there_is_no_git_remote(tmp_path):
    mirror = tmp_path / "mystery"
    _make_mirror(mirror, readme_season="2023")
    season, evidence, rank = declared_season(mirror)
    assert season == "2023"
    assert "README" in evidence
    assert rank == 1


def test_bare_directory_name_is_the_weakest_evidence(tmp_path):
    mirror = tmp_path / "2022"
    mirror.mkdir()
    season, evidence, rank = declared_season(mirror)
    assert season == "2022"
    assert rank == 2


def test_no_evidence_at_all_declares_no_season(tmp_path):
    mirror = tmp_path / "some_folder"
    mirror.mkdir()
    season, _evidence, rank = declared_season(mirror)
    assert season is None
    assert rank == 3


# --------------------------------------------------------------------- resolve_season_mirrors

def test_resolves_a_correctly_named_mirror(tmp_path):
    raw_root = tmp_path / "data" / "raw" / "tracinginsights"
    _make_mirror(raw_root / "2022", remote_season="2022",
                sessions={"Australian Grand Prix": ["Race"]})
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - data/raw/tracinginsights/<season>\nignore_directories: []\n",
        encoding="utf-8")

    mirrors = resolve_season_mirrors("2022", ("Race", "Sprint"), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml")
    assert len(mirrors) == 1
    assert mirrors[0].directory_matches_season is True
    assert mirrors[0].in_scope_sessions == 1


def test_a_wildcard_search_only_matches_directories_that_declare_the_season(tmp_path):
    """The wildcard must not drag an unrelated directory into scope."""
    raw_root = tmp_path / "data" / "raw" / "tracinginsights"
    _make_mirror(raw_root / "2022", remote_season="2022")
    _make_mirror(raw_root / "2023", remote_season="2023")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - data/raw/tracinginsights/*\nignore_directories: []\n",
        encoding="utf-8")

    mirrors = resolve_season_mirrors("2022", ("Race",), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml")
    assert [m.path.name for m in mirrors] == ["2022"]


def test_a_misnamed_mirror_is_found_and_flagged_not_matching(tmp_path):
    """A mirror whose directory name is not its declared season must still be
    visible -- the point is that it is reported, never silently skipped."""
    raw_root = tmp_path / "data" / "raw" / "tracinginsights"
    _make_mirror(raw_root / "2027", remote_season="2025",
                sessions={"Belgian Grand Prix": ["Sprint"]})
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - data/raw/tracinginsights/*\nignore_directories: []\n",
        encoding="utf-8")

    mirrors = resolve_season_mirrors("2025", ("Race", "Sprint"), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml")
    assert len(mirrors) == 1
    assert mirrors[0].declared_season == "2025"
    assert mirrors[0].directory_matches_season is False
    assert mirrors[0].year_dir == "2027"


def test_a_correctly_named_mirror_outranks_a_misnamed_one_even_with_more_sessions(tmp_path):
    """Session count is the tie-breaker among equally addressable mirrors, not a
    reason to prefer a mirror that cannot be built through --raw-root/--year
    without mislabelling the partition."""
    raw_root = tmp_path / "data" / "raw" / "tracinginsights"
    _make_mirror(raw_root / "2025", readme_season="2025",
                sessions={"Australian Grand Prix": ["Race"]})
    _make_mirror(raw_root / "2027", remote_season="2025",
                sessions={"Australian Grand Prix": ["Race"], "Belgian Grand Prix": ["Sprint"]})
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - data/raw/tracinginsights/*\nignore_directories: []\n",
        encoding="utf-8")

    mirrors = resolve_season_mirrors("2025", ("Race", "Sprint"), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml")
    assert [m.path.name for m in mirrors] == ["2025", "2027"]


def test_extra_raw_source_override_is_searched(tmp_path):
    other = tmp_path / "elsewhere" / "my_2024_copy"
    _make_mirror(other, remote_season="2024", sessions={"Monaco Grand Prix": ["Race"]})
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - data/raw/tracinginsights/<season>\nignore_directories: []\n",
        encoding="utf-8")

    mirrors = resolve_season_mirrors("2024", ("Race",), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml",
                                     extra_roots=[other])
    assert any(m.path == other for m in mirrors)


def test_env_var_substitution_is_skipped_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACKSHIFT_RAW_ROOT", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - ${TRACKSHIFT_RAW_ROOT}/<season>\nignore_directories: []\n",
        encoding="utf-8")
    # Must not raise despite the unresolved env var; it should just find nothing.
    mirrors = resolve_season_mirrors("2024", ("Race",), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml")
    assert mirrors == []


def test_missing_config_raises_a_clear_error(tmp_path):
    with pytest.raises(RawSourceConfigError):
        load_search_config(tmp_path / "does_not_exist.yaml")


# --------------------------------------------------------------------- select_season_mirror

def test_select_prefers_the_directory_matching_mirror(tmp_path):
    raw_root = tmp_path / "data" / "raw" / "tracinginsights"
    _make_mirror(raw_root / "2022", remote_season="2022", sessions={"Monaco Grand Prix": ["Race"]})
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - data/raw/tracinginsights/<season>\nignore_directories: []\n",
        encoding="utf-8")
    mirrors = resolve_season_mirrors("2022", ("Race",), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml")
    selected, reason = select_season_mirror(mirrors)
    assert selected is not None and reason is None
    assert selected.directory_matches_season is True


def test_select_refuses_a_misnamed_mirror_by_default(tmp_path):
    raw_root = tmp_path / "data" / "raw" / "tracinginsights"
    _make_mirror(raw_root / "2027", remote_season="2025", sessions={"Monaco Grand Prix": ["Race"]})
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - data/raw/tracinginsights/*\nignore_directories: []\n",
        encoding="utf-8")
    mirrors = resolve_season_mirrors("2025", ("Race",), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml")
    selected, reason = select_season_mirror(mirrors)
    assert selected is None
    assert reason is not None and "directory name" in reason


def test_select_accepts_a_misnamed_mirror_when_explicitly_allowed(tmp_path):
    raw_root = tmp_path / "data" / "raw" / "tracinginsights"
    _make_mirror(raw_root / "2027", remote_season="2025", sessions={"Monaco Grand Prix": ["Race"]})
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raw_sources.yaml").write_text(
        "schema_version: 1\nsearch:\n  - data/raw/tracinginsights/*\nignore_directories: []\n",
        encoding="utf-8")
    mirrors = resolve_season_mirrors("2025", ("Race",), repo_root=tmp_path,
                                     config_path=tmp_path / "config" / "raw_sources.yaml")
    selected, reason = select_season_mirror(mirrors, allow_directory_mismatch=True)
    assert selected is not None
    assert reason is None


def test_select_with_no_candidates_names_the_reason(tmp_path):
    selected, reason = select_season_mirror([])
    assert selected is None
    assert "no directory" in reason
