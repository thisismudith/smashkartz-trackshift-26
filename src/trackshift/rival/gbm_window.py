"""CPU rolling-window gradient-boosted stump candidate (M09)."""
from __future__ import annotations

from typing import Any, Iterable

from .model import RivalModel, fit_model


def fit_rolling_window_gbm(rows: Iterable[dict[str, Any]], *, split_version: str) -> RivalModel:
    return fit_model(rows, kind="gbm", split_version=split_version)


__all__ = ["RivalModel", "fit_rolling_window_gbm"]
