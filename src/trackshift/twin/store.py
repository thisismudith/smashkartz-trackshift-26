"""Reading the twin's partitioned outputs without caring about the layout.

The twin and the override state are written under ``circuit=<c>/year=<y>/``.
They used to be written under ``circuit=<c>/`` alone, and that was a data-loss
bug rather than a cosmetic one: building a second season overwrote the first,
and nothing complained because every reader joins on ``year`` and so merely saw
no rows where a season used to be.

These helpers read either layout, so an un-migrated tree still resolves while
the year filter stays authoritative. Everything is filtered by year on the way
out, never by path alone -- a file's location is a hint, its ``year`` column is
the fact.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

__all__ = ["circuit_of", "partition_files", "circuits_available", "read_partition"]


def circuit_of(path: Path) -> str:
    """The circuit key from any depth of partition directory under ``circuit=``."""
    for part in path.parts:
        if part.startswith("circuit="):
            return part.removeprefix("circuit=")
    raise ValueError(f"no circuit= partition in {path}")


def partition_files(root: Path, filename: str, circuit: str | None = None) -> list[Path]:
    """Every ``filename`` under ``root``, in either the flat or year-partitioned layout."""
    if not root.exists():
        return []
    found = (p for p in root.rglob(filename) if "circuit=" in str(p))
    if circuit is not None:
        found = (p for p in found if circuit_of(p) == circuit)
    return sorted(found)


def circuits_available(root: Path, filename: str) -> list[str]:
    """Circuit keys that have at least one partition of ``filename``."""
    return sorted({circuit_of(p) for p in partition_files(root, filename)})


def read_partition(root: Path, filename: str, year: str | int | None = None,
                   circuit: str | None = None, columns: list[str] | None = None) -> "pd.DataFrame":
    """Concatenate matching partitions, filtered to ``year`` by column, not by path.

    Returns an empty frame rather than raising when nothing matches, so a caller
    can report the gap as a coverage number instead of dying on it.
    """
    import pandas as pd

    files = partition_files(root, filename, circuit)
    if not files:
        return pd.DataFrame(columns=columns or [])

    frames = []
    for path in files:
        frame = pd.read_parquet(path, columns=columns)
        if year is not None and "year" in frame.columns:
            frame = frame[frame["year"].astype(str) == str(year)]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=columns or [])
    return pd.concat(frames, ignore_index=True)
