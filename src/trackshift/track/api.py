"""Public C1/C2 track boundary.

Consumers import C2 construction and residual semantics here, never from the
implementation module directly.
"""
from .baselines import (
    BASELINE_LEVELS,
    BASELINE_METRICS,
    BASELINE_SCHEMA_VERSION,
    BaselineBuildResult,
    assign_year_group,
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
from .lap_classifier import (
    KEY_COLUMNS as LAP_CLASS_KEY_COLUMNS,
    LAP_CLASSES,
    LAP_CLASSIFIER_SCHEMA_VERSION,
    LAP_CLASS_PROVENANCE,
    LapClassificationConfig,
    LapClassificationError,
    classify_practice_laps,
    load_lap_classification_config,
)

__all__ = [
    "BASELINE_LEVELS",
    "BASELINE_METRICS",
    "BASELINE_SCHEMA_VERSION",
    "BaselineBuildResult",
    "assign_year_group",
    "build_segment_baselines",
    "residual_at_use_time",
    "WEATHER_KEY_COLUMNS",
    "WEATHER_OVERLAY_COLUMNS",
    "WEATHER_OVERLAY_SCHEMA_VERSION",
    "WeatherOverlayError",
    "build_weather_overlay",
    "join_weather_overlay",
    "load_weather_overlay",
    "LAP_CLASS_KEY_COLUMNS",
    "LAP_CLASSES",
    "LAP_CLASSIFIER_SCHEMA_VERSION",
    "LAP_CLASS_PROVENANCE",
    "LapClassificationConfig",
    "LapClassificationError",
    "classify_practice_laps",
    "load_lap_classification_config",
]
