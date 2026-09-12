"""Dwell-aware HSMM-equivalent candidate (M09).

The benchmark uses a sticky transition prior as the documented equivalent of
an explicit duration lattice for these short causal windows.
"""
from __future__ import annotations

from typing import Any, Iterable

from .model import RivalModel, fit_model


def fit_hsmm(rows: Iterable[dict[str, Any]], *, split_version: str) -> RivalModel:
    return fit_model(rows, kind="hsmm", split_version=split_version)


__all__ = ["RivalModel", "fit_hsmm"]
