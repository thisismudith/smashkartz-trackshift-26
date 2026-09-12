"""Where the build reads raw telemetry from, pinned.

These tests build fake layouts under tmp_path rather than reading the real mirror, so
they pass on a machine that has downloaded nothing. The one thing they cannot fake is
process spawning, which is covered explicitly in test_year_survives_a_spawned_process --
that mechanism is the reason the active year lives in the environment at all.
"""
from __future__ import annotations

import concurrent.futures as futures
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simdata import paths


@pytest.fixture(autouse=True)
def clean_env():
    """No test may inherit OR LEAK the year: it is global to the whole pytest process.

    Save and restore by hand rather than with monkeypatch.delenv(raising=False), which
    records no undo entry when the variable was not set to begin with -- so a test that
    then calls set_year() leaves it set for every test that follows. That is not
    hypothetical: it made test_replay.py build the 2022 mirror and fail on a circuit
    whose geometry that season cannot supply.
    """
    keys = (paths.YEAR_ENV, paths.ROOT_ENV)
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, value in saved.items():
        if value is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = value


@pytest.fixture
def layouts(tmp_path, monkeypatch):
    """The three accepted layouts, rooted in tmp_path, none of them populated yet."""
    canonical = tmp_path / "data" / "raw" / "tracinginsights"
    legacy_raw = tmp_path / "data" / "raw"
    legacy_flat = tmp_path / "data"
    monkeypatch.setattr(paths, "CANONICAL_RAW", canonical)
    monkeypatch.setattr(paths, "LEGACY_RAW", legacy_raw)
    monkeypatch.setattr(paths, "LEGACY_FLAT", legacy_flat)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return {"canonical": canonical, "legacy_raw": legacy_raw, "legacy_flat": legacy_flat}


def make_mirror(base: Path, year: str, event="British Grand Prix", session="Race") -> Path:
    """A directory that looks like a mirror: <year>/<event>/<session>/."""
    root = base / year / event / session
    root.mkdir(parents=True, exist_ok=True)
    return base / year


# ---------------------------------------------------------------- resolution order

def test_canonical_wins_when_every_layout_exists(layouts):
    """The migrated copy is authoritative even while the pre-migration ones survive.

    This is the state of the machine the migration was done on: data/2026 was left in
    place after the copy, and reading it instead would silently build from whichever
    mirror happened to be more complete.
    """
    for key in ("canonical", "legacy_raw", "legacy_flat"):
        make_mirror(layouts[key], "2026")
    assert paths.data_root("2026") == layouts["canonical"] / "2026"


def test_falls_back_to_data_raw_year(layouts):
    make_mirror(layouts["legacy_raw"], "2024")
    make_mirror(layouts["legacy_flat"], "2024")
    assert paths.data_root("2024") == layouts["legacy_raw"] / "2024"


def test_falls_back_to_data_year(layouts):
    make_mirror(layouts["legacy_flat"], "2025")
    assert paths.data_root("2025") == layouts["legacy_flat"] / "2025"


def test_missing_year_resolves_to_the_canonical_path(layouts):
    """A year nobody has downloaded must fail against where it SHOULD go.

    Returning a legacy candidate here would send the reader looking in the wrong place.
    """
    root = paths.data_root("2023")
    assert root == layouts["canonical"] / "2023"
    assert not root.is_dir()


# ---------------------------------------------------------------- the active year

def test_default_year_when_unset(layouts):
    assert paths.active_year() == paths.DEFAULT_YEAR


def test_set_year_changes_what_data_root_resolves(layouts):
    make_mirror(layouts["canonical"], "2022")
    make_mirror(layouts["canonical"], "2026")
    paths.set_year("2022")
    assert paths.active_year() == "2022"
    assert paths.data_root() == layouts["canonical"] / "2022"


@pytest.mark.parametrize("bad", ["26", "20266", "twenty", "", "202x", "-999"])
def test_set_year_rejects_anything_but_four_digits(bad):
    with pytest.raises(ValueError):
        paths.set_year(bad)


def test_set_year_accepts_an_int(layouts):
    paths.set_year(2024)
    assert paths.active_year() == "2024"


# ---------------------------------------------------------------- the pin

def test_root_env_pins_the_directory_and_ignores_the_year(layouts, monkeypatch, tmp_path):
    """The escape hatch is a SESSION root, not a parent of years: an external mirror
    will not be laid out the way this repo lays one out."""
    external = tmp_path / "elsewhere" / "mirror"
    external.mkdir(parents=True)
    make_mirror(layouts["canonical"], "2026")
    monkeypatch.setenv(paths.ROOT_ENV, str(external))
    assert paths.data_root() == external
    assert paths.data_root("2022") == external


# ---------------------------------------------------------------- discovery

def test_available_years_finds_every_layout(layouts):
    make_mirror(layouts["canonical"], "2026")
    make_mirror(layouts["legacy_raw"], "2024")
    make_mirror(layouts["legacy_flat"], "2025")
    assert paths.available_years() == ["2024", "2025", "2026"]


def test_available_years_skips_a_cloned_but_unchecked_out_mirror(layouts):
    """The exact state a half-finished download leaves behind: a year directory holding
    only .git. Advertising it would offer a year the build cannot read one lap from."""
    (layouts["canonical"] / "2024" / ".git").mkdir(parents=True)
    make_mirror(layouts["canonical"], "2026")
    assert paths.available_years() == ["2026"]


def test_available_years_skips_a_year_of_bare_cache_directories(layouts):
    for junk in ("cache", "fastf1_cache", ".github"):
        (layouts["legacy_flat"] / "2025" / junk).mkdir(parents=True)
    assert paths.available_years() == []


def test_available_years_ignores_non_year_directories(layouts):
    make_mirror(layouts["legacy_flat"], "processed")
    make_mirror(layouts["legacy_flat"], "tracks")
    make_mirror(layouts["canonical"], "2026")
    assert paths.available_years() == ["2026"]


def test_available_years_deduplicates_a_year_present_in_two_layouts(layouts):
    make_mirror(layouts["canonical"], "2026")
    make_mirror(layouts["legacy_flat"], "2026")
    assert paths.available_years() == ["2026"]


# ---------------------------------------------------------------- reporting

def test_layout_note_names_the_layout_that_answered(layouts):
    make_mirror(layouts["legacy_flat"], "2025")
    note = paths.layout_note("2025")
    assert "data/2025" in note and "legacy" in note


def test_layout_note_says_when_the_mirror_is_absent(layouts):
    assert "MISSING" in paths.layout_note("2023")


def test_relative_root_is_posix_and_repo_relative(layouts):
    make_mirror(layouts["canonical"], "2026")
    assert paths.relative_root("2026") == "data/raw/tracinginsights/2026"


# ---------------------------------------------------------------- the spawn contract

def _worker_year(_):
    """Runs in a spawned child. Re-imports paths from scratch, as a real worker does."""
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from simdata import paths as child_paths
    return child_paths.active_year()


def test_year_survives_a_spawned_process(monkeypatch):
    """build_sim_data.py --jobs N builds in worker PROCESSES.

    On Windows those are spawned, not forked: the child re-imports every module and any
    module-global the parent set is gone. Holding the year in the environment is the
    whole reason a --jobs 8 build stays on the year the caller asked for instead of
    silently reverting to DEFAULT_YEAR in every worker. If this test fails, a parallel
    build is reading the wrong season.
    """
    monkeypatch.setenv(paths.YEAR_ENV, "2022")
    with futures.ProcessPoolExecutor(max_workers=2) as pool:
        seen = list(pool.map(_worker_year, range(2)))
    assert seen == ["2022", "2022"]
