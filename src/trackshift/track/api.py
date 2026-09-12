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
from .weather import (
    WEATHER_KEY_COLUMNS,
    WEATHER_OVERLAY_COLUMNS,
    WEATHER_OVERLAY_SCHEMA_VERSION,
    WeatherOverlayError,
    build_weather_overlay,
    join_weather_overlay,
    load_weather_overlay,
)

__all__ = [
    "BASELINE_LEVELS",
    "BASELINE_METRICS",
    "BASELINE_SCHEMA_VERSION",
    "BaselineBuildResult",
    "build_segment_baselines",
    "residual_at_use_time",
    "WEATHER_KEY_COLUMNS",
    "WEATHER_OVERLAY_COLUMNS",
    "WEATHER_OVERLAY_SCHEMA_VERSION",
    "WeatherOverlayError",
    "build_weather_overlay",
    "join_weather_overlay",
    "load_weather_overlay",
]
