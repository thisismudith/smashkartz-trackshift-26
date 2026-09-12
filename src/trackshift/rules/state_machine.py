"""Deterministic 2026 Overtake eligibility state machine (M20 / CP-10).

The machine deliberately knows nothing about telemetry storage, pandas, or
race-control files.  Its only temporal input is the immediately preceding
position and gap supplied in ``race_control`` by a caller.  That makes line
crossings explicit and prevents an implementation from looking ahead to a
future telemetry sample.

``race_control`` is a small, JSON-compatible transition context.  The control
flags must come from observed race-control messages; ``previous_position_m``
and ``previous_gap_s`` are causal row history maintained by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

NOT_ARMED = "NOT_ARMED"
ARMED = "ARMED"
ACTIVE = "ACTIVE"
DISABLED = "DISABLED"
STATES = frozenset({NOT_ARMED, ARMED, ACTIVE, DISABLED})


@dataclass(frozen=True)
class Transition:
    """One inspectable transition rule.

    ``event`` is emitted solely from the current call's inputs.  Keeping this
    table data-only lets tests assert exactly the same legal transitions as the
    production evaluator.
    """

    name: str
    from_states: frozenset[str]
    event: str
    to_state: str


TRANSITION_TABLE = (
    Transition("race_control_disabled", frozenset(STATES), "RACE_CONTROL_DISABLED", DISABLED),
    Transition("race_control_enabled", frozenset({DISABLED}), "RACE_CONTROL_ENABLED", NOT_ARMED),
    Transition("detection_qualifies", frozenset({NOT_ARMED}), "DETECTION_QUALIFIES", ARMED),
    Transition("activation_after_arm", frozenset({ARMED}), "ACTIVATION_CROSSED", ACTIVE),
    Transition("zone_exit", frozenset({ACTIVE}), "ZONE_EXIT", NOT_ARMED),
    Transition("lift", frozenset({ACTIVE}), "LIFT", NOT_ARMED),
    Transition("session_end", frozenset({ACTIVE, ARMED}), "SESSION_END", NOT_ARMED),
)

_BY_EVENT = {rule.event: rule for rule in TRANSITION_TABLE}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _value(block: Any) -> float | None:
    """Read a config value block without ever substituting a default."""
    if isinstance(block, Mapping):
        return _number(block.get("value"))
    return _number(block)


def resolved_zones(event_rules: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Zones with the lap's Detection Line filled in where a zone lacks its own.

    The 2026 Race Director's Competition Notes describe **one** detection line
    per circuit -- "the detection line referred to in Article B 7.2.1 is at the
    same location of the Safety Car Line 1" -- not one per zone as DRS had. So
    ``overtake.detection_line_m`` is an event-level value that every zone shares,
    and a zone-level entry, if one is ever supplied, still wins over it.

    Keeping the zone structure matters: activation lines remain genuinely
    per-zone, and a future event note that does place a detection line per zone
    needs no schema change to express it.
    """
    overtake = event_rules.get("overtake")
    if not isinstance(overtake, Mapping) or overtake.get("enabled") is not True:
        return []
    zones = overtake.get("zones")
    if not isinstance(zones, list):
        return []

    lap_detection = overtake.get("detection_line_m")
    resolved: list[dict[str, Any]] = []
    for zone in zones:
        entry = dict(zone)
        own = entry.get("detection_line_m") or {}
        if _value(own) is None and isinstance(lap_detection, Mapping):
            entry["detection_line_m"] = dict(lap_detection)
        resolved.append(entry)
    return resolved


def _zones(event_rules: Mapping[str, Any]) -> list[dict[str, Any]]:
    return resolved_zones(event_rules)


def configured_line_provenance(event_rules: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return line value/provenance for manifests without resolving defaults."""
    result: list[dict[str, Any]] = []
    for zone in _zones(event_rules):
        result.append({
            "zone": zone.get("zone"),
            "detection_line_m": dict(zone.get("detection_line_m") or {}),
            "activation_line_m": dict(zone.get("activation_line_m") or {}),
            # CP-03 configs do not currently supply this optional boundary.
            "zone_end_m": dict(zone.get("zone_end_m") or zone.get("end_line_m") or {}),
        })
    return result


def _crossed(previous_m: float | None, current_m: float | None, line_m: float | None) -> bool:
    """Whether a non-wrapping causal interval crosses ``line_m``.

    A new lap is handled by the lake applicator as a fresh interval.  We never
    treat a decreasing distance as a wrap-around crossing because that would
    falsely cross every line between the end and start of a lap.
    """
    return (
        previous_m is not None
        and current_m is not None
        and line_m is not None
        and current_m >= previous_m
        and previous_m < line_m <= current_m
    )


def _gap_at_line(
    previous_m: float | None,
    current_m: float | None,
    previous_gap_s: float | None,
    current_gap_s: float | None,
    line_m: float,
) -> float | None:
    """Causally interpolate the gap at a crossed line when both endpoints exist."""
    if current_gap_s is None:
        return None
    if previous_gap_s is None or previous_m is None or current_m is None or current_m == previous_m:
        return current_gap_s
    fraction = (line_m - previous_m) / (current_m - previous_m)
    return previous_gap_s + fraction * (current_gap_s - previous_gap_s)


def _line_events(
    position_m: float | None,
    gap_s: float | None,
    race_control: Mapping[str, Any],
    event_rules: Mapping[str, Any],
) -> list[tuple[float, int, str]]:
    """Emit ordered line events from this row's causal interval.

    Detection wins an exact-position tie with Activation, which is the only
    ordering that can allow a same-20m-bin Detection/Activation sequence while
    still making ACTIVE impossible without a prior ARMED state.
    """
    previous_m = _number(race_control.get("previous_position_m"))
    previous_gap_s = _number(race_control.get("previous_gap_s"))
    threshold = _value((event_rules.get("overtake") or {}).get("detection_gap_s"))
    events: list[tuple[float, int, str]] = []
    for zone in _zones(event_rules):
        detection = _value(zone.get("detection_line_m"))
        activation = _value(zone.get("activation_line_m"))
        zone_end = _value(zone.get("zone_end_m"))
        if _crossed(previous_m, position_m, detection):
            line_gap = _gap_at_line(previous_m, position_m, previous_gap_s, gap_s, detection)
            if threshold is not None and line_gap is not None and line_gap < threshold:
                events.append((detection, 0, "DETECTION_QUALIFIES"))
        if _crossed(previous_m, position_m, activation):
            events.append((activation, 1, "ACTIVATION_CROSSED"))
        if _crossed(previous_m, position_m, zone_end):
            events.append((zone_end, 2, "ZONE_EXIT"))
    return sorted(events)


def _transition(state: str, event: str) -> str:
    rule = _BY_EVENT[event]
    return rule.to_state if state in rule.from_states else state


def step(
    state: str,
    position_m: float | None,
    gap_s: float | None,
    race_control: Mapping[str, Any] | None,
    event_rules: Mapping[str, Any],
) -> str:
    """Advance one causal Overtake state-machine step.

    ``race_control`` accepts only observed Overtake control events plus causal
    transition context: ``overtake_disabled``, ``overtake_enabled``, optional
    ``lifted``/``session_ended``, and the preceding row's position/gap.  It
    intentionally has no DRS input; 2026 DRS cannot influence this machine.
    """
    if state not in STATES:
        raise ValueError(f"unknown Overtake state {state!r}; expected one of {sorted(STATES)}")
    if not isinstance(event_rules, Mapping):
        raise TypeError("event_rules must be a mapping")
    control = dict(race_control or {})

    # An observed disable is dominant even if an enable or line crossing appears
    # in the same resampled row.
    if control.get("overtake_disabled") is True:
        return _transition(state, "RACE_CONTROL_DISABLED")
    if control.get("overtake_enabled") is True:
        state = _transition(state, "RACE_CONTROL_ENABLED")

    for _, _, event in _line_events(_number(position_m), _number(gap_s), control, event_rules):
        state = _transition(state, event)

    if control.get("lifted") is True:
        state = _transition(state, "LIFT")
    if control.get("session_ended") is True:
        state = _transition(state, "SESSION_END")
    return state


def eligible(state: str) -> bool:
    """The only state in which the 2026 Overtake facility is active."""
    if state not in STATES:
        raise ValueError(f"unknown Overtake state {state!r}")
    return state == ACTIVE


__all__ = [
    "ACTIVE", "ARMED", "DISABLED", "NOT_ARMED", "STATES", "TRANSITION_TABLE",
    "Transition", "configured_line_provenance", "eligible", "step",
]
