"""Segment-time model, the dE -> dt transition (M16, CP-21).

The causal transition the planner consumes (section 30)::

    t_k(dE, L) = t_base,k - a_k * dE + c_k * L

``a_k`` is segment *k*'s energy sensitivity -- the local slope that becomes the
shadow price. Without it the DP has nothing to price energy against, which is
why this is the piece the whole planner waits on.

**Physical plausibility outranks MAE** (section 30). A tree model that fits
better while making a segment slower when you deploy into it has not won: it has
produced a transition that will send the planner the wrong way. ``a_k > 0``
everywhere, monotone in dE, is a hard constraint, not a diagnostic.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "SegmentResponse",
    "SegmentTimeError",
    "segment_time_s",
    "energy_sensitivity",
    "check_monotonic_in_energy",
    "check_sensitivity_by_segment_type",
    "extrapolation_sanity",
    "MAE_TARGET_S",
]

#: Held-out target, seconds per segment.
MAE_TARGET_S = 0.06


class SegmentTimeError(ValueError):
    """The transition cannot be evaluated or does not respect the physics."""


@dataclass(frozen=True)
class SegmentResponse:
    """One segment's fitted response."""

    segment_id: Any
    t_base_s: float
    a_k_s_per_mj: float
    c_k_s_per_unit_lift: float
    kind: str | None = None
    n_observations: int = 0

    def __post_init__(self) -> None:
        if self.a_k_s_per_mj <= 0:
            raise SegmentTimeError(
                f"segment {self.segment_id}: a_k={self.a_k_s_per_mj:g} is not positive. "
                "Deploying energy must never make a segment slower; a non-positive "
                "sensitivity is confounding (deployment correlates with defending or "
                "traffic), not a finding."
            )
        if self.c_k_s_per_unit_lift < 0:
            raise SegmentTimeError(
                f"segment {self.segment_id}: c_k={self.c_k_s_per_unit_lift:g} is negative, "
                "which says lifting saves time"
            )


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SegmentTimeError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise SegmentTimeError(f"{name} must be finite, got {number!r}")
    return number


def segment_time_s(response: SegmentResponse, delta_e_mj: Any, lift_amount: Any = 0.0) -> float:
    """Predicted segment time for an energy and lift choice."""
    return (response.t_base_s
            - response.a_k_s_per_mj * _finite(delta_e_mj, "delta_e_mj")
            + response.c_k_s_per_unit_lift * _finite(lift_amount or 0.0, "lift_amount"))


def energy_sensitivity(response: SegmentResponse) -> float:
    """d(time)/d(energy), negative by construction: more energy, less time."""
    return -response.a_k_s_per_mj


def check_monotonic_in_energy(
    response: SegmentResponse,
    *,
    energy_range_mj: tuple[float, float] = (0.0, 4.0),
    samples: int = 200,
) -> dict[str, Any]:
    """Dense sweep: time must strictly decrease in dE across the whole range.

    Checked densely rather than at the endpoints because a tree model can be
    monotone at the ends and fold in the middle, which is exactly where the
    planner will operate.
    """
    low, high = energy_range_mj
    if samples < 2 or not high > low:
        raise SegmentTimeError("need an increasing energy range and at least two samples")
    step = (high - low) / (samples - 1)
    times = [segment_time_s(response, low + step * i) for i in range(samples)]
    breaks = [i for i, (a, b) in enumerate(zip(times, times[1:])) if b >= a]
    return {
        "monotonic": not breaks,
        "n_breaks": len(breaks),
        "first_break_index": breaks[0] if breaks else None,
        "time_at_min_energy_s": times[0],
        "time_at_max_energy_s": times[-1],
    }


def check_sensitivity_by_segment_type(responses: Sequence[SegmentResponse]) -> dict[str, Any]:
    """a_k must vary with geometry: high on straights, near zero in slow corners.

    A flat profile means the model learned an average rather than the physics.
    It will still score well on MAE and will give the DP a flat shadow price,
    which is the documented way this produces nonsense downstream.
    """
    by_kind: dict[str, list[float]] = {}
    for response in responses:
        by_kind.setdefault(str(response.kind or "UNKNOWN"), []).append(response.a_k_s_per_mj)
    summary = {
        kind: {
            "n": len(values),
            "mean_a_k": sum(values) / len(values),
            "max_a_k": max(values),
            "min_a_k": min(values),
        }
        for kind, values in sorted(by_kind.items())
    }
    straights = summary.get("STRAIGHT", {}).get("mean_a_k")
    corners = summary.get("CORNER", {}).get("mean_a_k")
    return {
        "by_kind": summary,
        "straight_exceeds_corner": (None if straights is None or corners is None
                                    else straights > corners),
        "flat": (None if straights is None or corners is None
                 else math.isclose(straights, corners, rel_tol=0.05)),
    }


def extrapolation_sanity(
    response: SegmentResponse,
    *,
    observed_max_mj: float,
    probe_multiple: float = 2.0,
) -> dict[str, Any]:
    """Beyond the observed energy range, time must not go negative or invert.

    The planner will ask about actions it has never seen; a linear response
    extrapolated far enough eventually predicts a negative segment time, and the
    DP will happily choose it.
    """
    probe = observed_max_mj * probe_multiple
    time_at_probe = segment_time_s(response, probe)
    return {
        "probe_energy_mj": probe,
        "time_at_probe_s": time_at_probe,
        "goes_negative": time_at_probe <= 0,
        "safe_energy_ceiling_mj": (response.t_base_s / response.a_k_s_per_mj
                                   if response.a_k_s_per_mj > 0 else None),
    }
