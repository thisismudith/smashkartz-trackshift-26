"""Public C3 rule boundary.

Consumers import the action space, the envelope evaluator and the Overtake
state machine from here, never from the implementation modules directly. In
particular the planner and the energy twin must reach
:func:`max_electrical_power_kw` through this boundary: it is the only
implementation of the speed-dependent cap in the system (AGENTS.md section 32).
"""
from .config import (
    ResolvedValue,
    RuleConfigError,
    UnsourcedValue,
    available_events,
    load_common,
    load_event_rules,
    resolve,
    unsourced_keys,
)
from .engine import (
    COAST,
    DEPLOY_LEVELS,
    ENGINE_SCHEMA_VERSION,
    LIFT_AMOUNTS,
    MODES,
    Action,
    Excluded,
    RuleEngineError,
    RuleKeyMissing,
    UnknownEvent,
    applicable_mode_for,
    envelope_curve,
    envelope_table,
    legal_actions,
    max_electrical_power_kw,
    separation_speed_kmh,
    stub_action_set,
    verify_envelope_table,
)
from .eligibility import (
    SIGMA_FLOOR_S,
    TRAILING_WINDOW,
    EligibilityError,
    GapProjection,
    derive_checkpoint_geometry,
    eligibility_margin,
    normal_cdf,
    project_gap_at_line,
)
from .state_machine import Transition, configured_line_provenance, eligible, step

__all__ = [
    # configuration
    "ResolvedValue",
    "RuleConfigError",
    "UnsourcedValue",
    "available_events",
    "load_common",
    "load_event_rules",
    "resolve",
    "unsourced_keys",
    # engine (C3)
    "COAST",
    "DEPLOY_LEVELS",
    "ENGINE_SCHEMA_VERSION",
    "LIFT_AMOUNTS",
    "MODES",
    "Action",
    "Excluded",
    "RuleEngineError",
    "RuleKeyMissing",
    "UnknownEvent",
    "applicable_mode_for",
    "envelope_curve",
    "envelope_table",
    "legal_actions",
    "max_electrical_power_kw",
    "separation_speed_kmh",
    "stub_action_set",
    "verify_envelope_table",
    # eligibility probability (M21)
    "SIGMA_FLOOR_S",
    "TRAILING_WINDOW",
    "EligibilityError",
    "GapProjection",
    "derive_checkpoint_geometry",
    "eligibility_margin",
    "normal_cdf",
    "project_gap_at_line",
    # state machine (M20)
    "Transition",
    "configured_line_provenance",
    "eligible",
    "step",
]
