"""Where the raw TracingInsights feed lives. One resolver, used by every simdata module.

The canonical layout is `data/raw/tracinginsights/<year>` -- the root every script under
scripts/data/ already defaults to, and what scripts/data/download_initial_dataset.ps1
writes. Two earlier layouts still exist on developer machines:

    data/raw/tracinginsights/<year>   canonical
    data/raw/<year>                   the layout AGENTS.md section 6.1 documents
    data/<year>                       where the 2025 and 2026 mirrors were first cloned

All three are ACCEPTED for reading, in that order. A mirror is 7-9 GB, so moving a folder
must never be the thing that triggers a re-download; the resolved path is printed by the
build rather than left implicit. Nothing here writes, and nothing here is a fallback for
missing data: a year with no mirror at all resolves to the CANONICAL path, so the error
the caller raises names where the data is supposed to go.

The active year is process state rather than an argument threaded through forty call
sites, because a build is always ONE year: artifact filenames are keyed by circuit slug
alone (`british-grand-prix-race.<hash>.bin`), so two years written into the same
frontend/public/sim would collide. build_sim_data.py --year picks the year and its index
records which one it built.

That state lives in the ENVIRONMENT, not a module global, on purpose: --jobs N spawns
worker processes, and a spawned worker on Windows re-imports this module from scratch.
A global would silently revert to DEFAULT_YEAR in every worker; the environment is
inherited across the spawn.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: The layout everything should converge on.
CANONICAL_RAW = ROOT / "data" / "raw" / "tracinginsights"
#: Read-only compatibility with the two earlier layouts, in preference order.
LEGACY_RAW = ROOT / "data" / "raw"
LEGACY_FLAT = ROOT / "data"

DEFAULT_YEAR = "2026"

#: Set by build_sim_data.py --year; read by every module in this package.
YEAR_ENV = "TRACKSHIFT_YEAR"
#: Escape hatch for a mirror kept outside the repo (an external drive, a shared mount).
#: Pins the session root directly and skips year resolution entirely.
ROOT_ENV = "TRACKSHIFT_DATA_ROOT"

#: Directory names that appear beside the events in a mirror and are not events.
NON_EVENT_DIRS = frozenset({".git", ".github", "schemas", "cache", "cache_preseason",
                            "cache_joblib", "fastf1_cache"})


def _validated(year) -> str:
    text = str(year)
    if len(text) != 4 or not text.isdigit():
        raise ValueError(f"year must be four digits, got {year!r}")
    return text


def active_year() -> str:
    """The year every DATA_ROOT-style lookup in this package currently resolves to."""
    return os.environ.get(YEAR_ENV) or DEFAULT_YEAR


def set_year(year) -> str:
    """Point the whole package -- this process and any it spawns -- at `year`."""
    text = _validated(year)
    os.environ[YEAR_ENV] = text
    return text


def candidates(year=None) -> list[Path]:
    """Every layout searched for `year`, most canonical first."""
    text = _validated(year if year is not None else active_year())
    return [CANONICAL_RAW / text, LEGACY_RAW / text, LEGACY_FLAT / text]


def data_root(year=None) -> Path:
    """The session-bearing directory for `year`: <root>/<event>/<session>/<driver>/...

    Returns the canonical path when no layout holds the year, so a missing mirror fails
    against the path it should be downloaded to rather than against a legacy one.
    """
    pinned = os.environ.get(ROOT_ENV)
    if pinned:
        return Path(pinned)
    options = candidates(year)
    for option in options:
        if option.is_dir():
            return option
    return options[0]


def _holds_events(year_dir: Path) -> bool:
    """True when `year_dir` looks like a mirror rather than an empty or bare checkout.

    A cloned-but-never-checked-out mirror is a directory containing only .git. Counting
    it as available would advertise a year the build cannot read a single lap from.
    """
    try:
        for child in year_dir.iterdir():
            if not child.is_dir() or child.name in NON_EVENT_DIRS or child.name.startswith("."):
                continue
            if any(sub.is_dir() for sub in child.iterdir()):
                return True
    except OSError:
        return False
    return False


def available_years() -> list[str]:
    """Years with an actual mirror on disk, across all three layouts."""
    found = set()
    for base in (CANONICAL_RAW, LEGACY_RAW, LEGACY_FLAT):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and len(child.name) == 4 and child.name.isdigit():
                if _holds_events(child):
                    found.add(child.name)
    return sorted(found)


def relative_root(year=None) -> str:
    """The resolved root as a repo-relative posix path, for provenance strings."""
    root = data_root(year)
    try:
        return root.relative_to(ROOT).as_posix()
    except ValueError:
        return root.as_posix()


def layout_note(year=None) -> str:
    """One line naming the resolved root AND which layout answered, for build logs."""
    root = data_root(year)
    if os.environ.get(ROOT_ENV):
        kind = f"pinned by ${ROOT_ENV}"
    elif root.parent == CANONICAL_RAW:
        kind = "canonical"
    elif root.parent == LEGACY_RAW:
        kind = "legacy data/raw/<year>"
    elif root.parent == LEGACY_FLAT:
        kind = "legacy data/<year>"
    else:
        kind = "unrecognised layout"
    if not root.is_dir():
        kind += "; MISSING"
    return f"{relative_root(year)} ({kind})"


__all__ = ["CANONICAL_RAW", "DEFAULT_YEAR", "LEGACY_FLAT", "LEGACY_RAW", "ROOT",
           "ROOT_ENV", "YEAR_ENV", "active_year", "available_years", "candidates",
           "data_root", "layout_note", "relative_root", "set_year"]
