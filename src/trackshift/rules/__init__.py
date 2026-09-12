"""2026 rule configuration, Overtake state machine, and the rule engine.

Chain R (Owner B). Everything regulatory enters TrackShift through this package:
event configuration (M18, CP-03), the Overtake state machine (M20, CP-10), the
deterministic rule engine (M19, CP-11) and eligibility probability (M21, CP-12).

The engine is the sole owner of `max_electrical_power_kw(speed_kmh, mode,
event_rules)`. No envelope constant may appear anywhere else in the system
(AGENTS.md sections 20.1, 32).
"""
