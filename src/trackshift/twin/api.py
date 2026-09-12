"""Public C5 twin boundary.

Consumers import the power balance, fuel estimate, mode inference, calibration
and uncertainty from here, never from the implementation modules. The planner in
particular must reach the envelope through `trackshift.rules.api`, not through
this package: the twin *reports* violations of the cap, it never enforces it.
"""
from .calibration import (
    MAE_TARGETS,
    PARAMETER_BOUNDS,
    PARAMETER_NAMES,
    RUNGS,
    CalibrationError,
    RungResult,
    check_physical_constraints,
    compare_rungs,
    error_by_group,
    fit_parameters,
    mean_absolute_error,
    residual_share,
    root_mean_square_error,
)
from .fuel import (
    LAP_TIME_EFFECT_S_PER_KG,
    FuelError,
    FuelEstimate,
    consumption_from_ice_work,
    estimate_fuel_curve,
    start_fuel_kg,
)
from .override import (
    NORMAL,
    OVERRIDE,
    UNKNOWN,
    OverrideError,
    OverrideInference,
    discriminate,
    historical_false_positive_rate,
)
from .power_balance import (
    PROVENANCE,
    EnergyState,
    PhysicsParameters,
    SegmentPower,
    TwinError,
    advance_energy_state,
    air_speed_mps,
    drag_power_kw,
    gradient_power_kw,
    inertial_power_kw,
    integrate_segment,
    kmh_to_mps,
    rolling_power_kw,
    segment_power,
    trailing_mean,
    wheel_power_kw,
)
from .segment_time import (
    MAE_TARGET_S,
    SegmentResponse,
    SegmentTimeError,
    check_monotonic_in_energy,
    check_sensitivity_by_segment_type,
    energy_sensitivity,
    extrapolation_sanity,
    segment_time_s,
)
from .uncertainty import (
    DRAWS,
    ParameterUncertainty,
    UncertaintyError,
    coverage,
    draw_parameters,
    interval,
    parameter_covariance,
    propagate,
)

__all__ = [
    "PROVENANCE",
    "EnergyState", "PhysicsParameters", "SegmentPower", "TwinError",
    "advance_energy_state", "air_speed_mps", "drag_power_kw", "gradient_power_kw",
    "inertial_power_kw", "integrate_segment", "kmh_to_mps", "rolling_power_kw",
    "segment_power", "trailing_mean", "wheel_power_kw",
    "LAP_TIME_EFFECT_S_PER_KG", "FuelError", "FuelEstimate",
    "consumption_from_ice_work", "estimate_fuel_curve", "start_fuel_kg",
    "NORMAL", "OVERRIDE", "UNKNOWN", "OverrideError", "OverrideInference",
    "discriminate", "historical_false_positive_rate",
    "MAE_TARGETS", "PARAMETER_BOUNDS", "PARAMETER_NAMES", "RUNGS",
    "CalibrationError", "RungResult", "check_physical_constraints", "compare_rungs",
    "error_by_group", "fit_parameters", "mean_absolute_error", "residual_share",
    "root_mean_square_error",
    "MAE_TARGET_S", "SegmentResponse", "SegmentTimeError",
    "check_monotonic_in_energy", "check_sensitivity_by_segment_type",
    "energy_sensitivity", "extrapolation_sanity", "segment_time_s",
    "DRAWS", "ParameterUncertainty", "UncertaintyError", "coverage",
    "draw_parameters", "interval", "parameter_covariance", "propagate",
]
