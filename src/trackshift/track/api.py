"""Public C1/C2 track boundary.

Consumers import C2 construction and residual semantics here, never from the
implementation module directly.
"""
from .baselines import (
    BASELINE_LEVELS,
    BASELINE_METRICS,
    BASELINE_SCHEMA_VERSION,
    BaselineBuildResult,
    build_segment_baselines,
    residual_at_use_time,
)

__all__ = [
    "BASELINE_LEVELS",
    "BASELINE_METRICS",
    "BASELINE_SCHEMA_VERSION",
    "BaselineBuildResult",
    "build_segment_baselines",
    "residual_at_use_time",
]
