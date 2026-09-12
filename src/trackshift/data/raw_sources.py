"""Resolve which directory on this machine holds a given season's raw mirror.

AGENTS.md section 7 names the target raw layout as
``data/raw/tracinginsights/<season>/``, and says the migration into that layout
happens only *after* the local-data audit. Until that migration has happened on
a given machine, a season mirror can sit under a different parent, or -- worse,
because it is invisible -- in a directory whose name is not the season it holds.
A single ``--raw-root`` flag cannot express either case, so every consumer that
wants "the 2024 mirror" asks here instead of composing a path by hand.

Two rules keep this honest rather than clever:

**The mirror declares its own season; the directory name is only a hint.** A
TracingInsights mirror is a clone of ``TracingInsights/<season>``, so its git
remote states the season unambiguously, and its README states it in prose. Those
are read metadata, not inference. The directory name is the last resort and is
recorded as the weakest evidence when it is all there is.

**A name/season disagreement is reported, never repaired.** Raw data is
immutable (section 7) and missing metadata is never invented, so this module will
tell a caller that ``data/raw/tracinginsights/2027`` in fact declares season 2025
and let the caller refuse to build from it. Relabelling the partition would put a
guess into the lake where no guess is visible afterwards.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import yaml

__all__ = [
    "DEFAULT_CONFIG",
    "RawSourceConfigError",
    "SeasonMirror",
    "load_search_config",
    "declared_season",
    "resolve_season_mirrors",
    "select_season_mirror",
]

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "config" / "raw_sources.yaml"

_SEASON_DIR = re.compile(r"^(\d{4})$")
#: A TracingInsights season repository: ``.../TracingInsights/2024(.git)``.
_REMOTE_SEASON = re.compile(r"^\s*url\s*=\s*\S*?[/:](\d{4})(?:\.git)?\s*$", re.MULTILINE)
#: The season line every published mirror README carries.
_README_SEASON = re.compile(r"(\d{4})\s+Public F1 telemetry files")
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class RawSourceConfigError(ValueError):
    """The raw-source search configuration is missing or malformed."""


@dataclass(frozen=True)
class SeasonMirror:
    """One directory that holds -- or claims to hold -- a season of raw telemetry."""

    season: str
    """The season requested, not the season the directory is named after."""

    path: Path
    """The season directory itself, e.g. ``<repo>/data/raw/tracinginsights/2024``."""

    declared_season: str | None
    """What the mirror's own metadata says it contains. None when nothing says."""

    season_evidence: str
    """Human-readable provenance for ``declared_season``; goes into the manifest."""

    evidence_rank: int
    """0 git remote, 1 README, 2 directory name, 3 nothing. Lower is stronger."""

    search_order: int
    """Index of the search pattern that found it; ties break toward the earlier one."""

    in_scope_sessions: int = 0
    """Session directories under this mirror matching the caller's session scope."""

    sessions_by_name: dict[str, int] = field(default_factory=dict)
    """Per-session-name counts, so a thin mirror is visible and not merely ranked."""

    @property
    def raw_root(self) -> Path:
        """The parent to pass as ``--raw-root`` to the single-session builder."""
        return self.path.parent

    @property
    def year_dir(self) -> str:
        """The directory name the single-session builder appends to ``--raw-root``."""
        return self.path.name

    @property
    def directory_matches_season(self) -> bool:
        """True when ``--raw-root``/``--year`` can address this mirror directly.

        ``build_phase2_dataset.py`` composes ``raw_root / year / event / session``
        and writes a ``year=<year>`` partition from the same value, so a mirror
        whose directory name is not the season cannot be built through it without
        mislabelling the partition.
        """
        return self.year_dir == self.season

    def as_record(self, repo_root: Path | None = None) -> dict:
        """Manifest-safe view. Paths are repo-relative so manifests stay portable."""
        base = repo_root or ROOT
        try:
            shown = self.path.relative_to(base).as_posix()
        except ValueError:
            shown = self.path.as_posix()
        return {
            "season": self.season,
            "path": shown,
            "raw_root": (
                self.raw_root.relative_to(base).as_posix()
                if _within(self.raw_root, base) else self.raw_root.as_posix()
            ),
            "year_dir": self.year_dir,
            "declared_season": self.declared_season,
            "season_evidence": self.season_evidence,
            "directory_matches_season": self.directory_matches_season,
            "in_scope_sessions": self.in_scope_sessions,
            "sessions_by_name": dict(sorted(self.sessions_by_name.items())),
        }


def _within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def load_search_config(path: Path | None = None) -> tuple[list[str], set[str]]:
    """Read the search patterns and the never-a-mirror directory names."""
    source = path or DEFAULT_CONFIG
    if not source.exists():
        raise RawSourceConfigError(f"raw-source configuration is absent: {source}")
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - surfaced verbatim
        raise RawSourceConfigError(f"raw-source configuration is malformed: {source}: {exc}") from exc
    patterns = payload.get("search")
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise RawSourceConfigError(f"{source}: 'search' must be a list of strings")
    if not patterns:
        raise RawSourceConfigError(f"{source}: 'search' is empty; there is nowhere to look")
    ignored = payload.get("ignore_directories") or []
    if not isinstance(ignored, list):
        raise RawSourceConfigError(f"{source}: 'ignore_directories' must be a list")
    return patterns, {str(item) for item in ignored}


def declared_season(path: Path) -> tuple[str | None, str, int]:
    """Read the season a mirror declares about itself.

    Returns ``(season, evidence, rank)``. Strongest first: the git remote names
    the upstream season repository, the README states the season in prose, and
    the directory name is accepted last because it is the one signal nobody had
    to keep correct.
    """
    config = path / ".git" / "config"
    if config.is_file():
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = _REMOTE_SEASON.search(text)
        if match:
            return match.group(1), f"git remote season repository {match.group(1)}", 0

    readme = path / "README.md"
    if readme.is_file():
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = _README_SEASON.search(text)
        if match:
            return match.group(1), f"README.md declares season {match.group(1)}", 1

    match = _SEASON_DIR.match(path.name)
    if match:
        return match.group(1), "directory name only (no git remote or README season line)", 2
    return None, "no declared season (no git remote, README season line, or four-digit name)", 3


def _expand(pattern: str, season: str, repo_root: Path) -> Path | None:
    """Substitute ``<season>`` and ``${ENV}``; None when an env var is unset."""
    text = pattern.replace("<season>", season)
    missing = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal missing
        value = os.environ.get(match.group(1))
        if not value:
            missing = True
            return ""
        return value

    text = _ENV_REF.sub(substitute, text)
    if missing:
        return None
    candidate = Path(text)
    return candidate if candidate.is_absolute() else repo_root / candidate


def _count_sessions(mirror: Path, sessions: Sequence[str]) -> tuple[int, dict[str, int]]:
    """Count in-scope session directories. Stats directories only; never parses."""
    wanted = list(sessions)
    counts = {name: 0 for name in wanted}
    try:
        events = [item for item in mirror.iterdir() if item.is_dir() and not item.name.startswith(".")]
    except OSError:
        return 0, counts
    for event in events:
        for name in wanted:
            if (event / name).is_dir():
                counts[name] += 1
    return sum(counts.values()), counts


def resolve_season_mirrors(
    season: str,
    sessions: Sequence[str],
    *,
    repo_root: Path | None = None,
    config_path: Path | None = None,
    extra_roots: Iterable[Path] = (),
) -> list[SeasonMirror]:
    """Every directory on this machine that declares it holds ``season``.

    Ranked best-first. ``extra_roots`` are ``--raw-source`` overrides and are
    searched before the configured patterns, so a caller can always name a path
    the configuration does not know about -- the section 5 escape hatch.

    A directory is a candidate only if its own metadata declares this season, so
    a wildcard search pattern cannot drag an unrelated season into scope. That is
    also what makes a misnamed mirror visible: it is returned, ranked below any
    correctly named one, with ``directory_matches_season`` false.
    """
    base = (repo_root or ROOT).resolve()
    patterns, ignored = load_search_config(config_path)

    expanded: list[tuple[int, Path]] = []
    for index, root in enumerate(extra_roots):
        path = Path(root)
        expanded.append((index, path if path.is_absolute() else base / path))
    offset = len(expanded)
    for index, pattern in enumerate(patterns):
        target = _expand(pattern, season, base)
        if target is not None:
            expanded.append((offset + index, target))

    found: dict[Path, SeasonMirror] = {}
    for order, target in expanded:
        if target.name == "*":
            parent = target.parent
            if not parent.is_dir():
                continue
            try:
                candidates = sorted(item for item in parent.iterdir() if item.is_dir())
            except OSError:
                continue
            candidates = [item for item in candidates if item.name not in ignored
                          and not item.name.startswith(".")]
        else:
            candidates = [target] if target.is_dir() else []

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in found:
                continue
            found_season, evidence, rank = declared_season(candidate)
            if found_season != str(season):
                continue
            total, by_name = _count_sessions(candidate, sessions)
            found[resolved] = SeasonMirror(
                season=str(season),
                path=candidate,
                declared_season=found_season,
                season_evidence=evidence,
                evidence_rank=rank,
                search_order=order,
                in_scope_sessions=total,
                sessions_by_name=by_name,
            )

    # A correctly named mirror always outranks a misnamed one, because only the
    # former can be addressed by --raw-root/--year without mislabelling the
    # partition. Within that, prefer the mirror that actually holds more of the
    # requested scope: picking a thinner mirror that happens to sort first is how
    # a season silently loses its Sprint sessions.
    return sorted(
        found.values(),
        key=lambda item: (
            not item.directory_matches_season,
            -item.in_scope_sessions,
            item.evidence_rank,
            item.search_order,
            item.path.as_posix(),
        ),
    )


def select_season_mirror(
    mirrors: Sequence[SeasonMirror],
    *,
    allow_directory_mismatch: bool = False,
) -> tuple[SeasonMirror | None, str | None]:
    """Pick the mirror to build from, or say why none is usable.

    Returns ``(mirror, skip_reason)``; exactly one is set. Refusing a misnamed
    mirror by default is the point: building through it would write a partition
    labelled with the directory's year rather than the season the data is from,
    and nothing downstream could detect that afterwards.
    """
    if not mirrors:
        return None, "no directory on this machine declares this season"
    best = mirrors[0]
    if best.directory_matches_season:
        return best, None
    if allow_directory_mismatch:
        return best, None
    return None, (
        f"mirror {best.path.name!r} declares season {best.declared_season} "
        f"({best.season_evidence}) but its directory name is not the season; "
        "the lake partition is named from the directory, so building here would "
        "mislabel it. Complete the AGENTS.md section 7 raw-layout migration "
        f"(move it to data/raw/tracinginsights/{best.season}/), or pass "
        "--allow-directory-mismatch to accept the directory name as the partition."
    )
