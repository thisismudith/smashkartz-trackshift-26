"""Override / ERS-mode discriminator (M35, CP-18b).

The normal and override envelopes coincide below the separation speed and part
above it. Above that speed, a car deploying more than the normal cap allows
**cannot be in normal mode**. That is unusual in this project: public telemetry
constraining a rival's energy decision rather than hinting at it.

Three rules keep it honest.

**The discriminability gate comes first, not last.** Below the separation speed
the curves are identical, so the test has no power at all. Returning ``NORMAL``
there would be a fabricated claim -- the single most likely way to make this
component dishonest -- so it returns ``UNKNOWN`` with a null probability.

**The margin scales with the twin's own uncertainty**, not a fixed kW figure. A
tight threshold on a poorly calibrated twin produces confident nonsense.

**It is never a training label** (section 24). It is a feature and a prior for
the rival-state model (M09). The temptation to treat it as ground truth is
strong precisely because it feels observational, and it is not.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PROVENANCE",
    "UNKNOWN",
    "NORMAL",
    "OVERRIDE",
    "OverrideInference",
    "OverrideError",
    "discriminate",
    "historical_false_positive_rate",
]

#: Section 24. Inferred from a simulated estimate; never OBSERVED.
PROVENANCE = "INFERRED"

UNKNOWN, NORMAL, OVERRIDE = "UNKNOWN", "NORMAL", "OVERRIDE"

#: Evidence threshold in units of the twin's own sigma.
DEFAULT_K_SIGMA = 2.0


class OverrideError(ValueError):
    """The discriminator cannot be evaluated with the inputs supplied."""


@dataclass(frozen=True)
class OverrideInference:
    """One sample's mode inference, with the reason it is or is not usable."""

    discriminable: bool
    ers_mode_inferred: str
    override_active_inferred: float | None
    excess_kw: float | None
    normal_cap_kw: float | None
    sigma_kw: float | None
    reason: str | None = None
    provenance: str = PROVENANCE


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OverrideError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise OverrideError(f"{name} must be finite, got {number!r}")
    return number


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def discriminate(
    speed_kmh: Any,
    ers_deploy_power_est_kw: Any,
    normal_cap_kw: Any,
    *,
    separation_speed_kmh: Any,
    sigma_kw: Any,
    k_sigma: float = DEFAULT_K_SIGMA,
) -> OverrideInference:
    """Infer ERS mode from deployment above the normal cap.

    ``sigma_kw`` is the twin's own uncertainty at this sample (CP-22). It sets
    the scale of the test: the probability returned is
    ``P(excess > 0)`` under that uncertainty, and the label only flips to
    ``OVERRIDE`` once the excess clears ``k_sigma`` multiples of it.
    """
    speed = _finite(speed_kmh, "speed_kmh")
    separation = _finite(separation_speed_kmh, "separation_speed_kmh")

    # Gate first. Section 20.2: below separation the mode is not observable at
    # all, and no amount of excess power is evidence of anything.
    if speed <= separation:
        return OverrideInference(
            discriminable=False,
            ers_mode_inferred=UNKNOWN,
            override_active_inferred=None,
            excess_kw=None,
            normal_cap_kw=None,
            sigma_kw=None,
            reason=(f"{speed:.1f} km/h is at or below the separation speed "
                    f"{separation:.1f} km/h, where the normal and override envelopes "
                    "coincide and the mode is not observable"),
        )

    sigma = _finite(sigma_kw, "sigma_kw")
    if sigma <= 0:
        raise OverrideError(
            f"sigma_kw must be positive, got {sigma}. A zero-uncertainty twin would "
            "make every excess infinitely significant, which is how this component "
            "starts producing confident nonsense."
        )
    if k_sigma <= 0:
        raise OverrideError(f"k_sigma must be positive, got {k_sigma}")

    cap = _finite(normal_cap_kw, "normal_cap_kw")
    excess = _finite(ers_deploy_power_est_kw, "ers_deploy_power_est_kw") - cap
    probability = _normal_cdf(excess / sigma)
    mode = OVERRIDE if excess > k_sigma * sigma else NORMAL

    return OverrideInference(
        discriminable=True,
        ers_mode_inferred=mode,
        override_active_inferred=probability,
        excess_kw=excess,
        normal_cap_kw=cap,
        sigma_kw=sigma,
        reason=None,
    )


def historical_false_positive_rate(inferences: list[OverrideInference]) -> dict[str, Any]:
    """Every OVERRIDE on a pre-2026 season is a false positive by construction.

    There was no override mechanism before 2026, so running the discriminator on
    2022-2025 gives a control group with a known answer. It is the cleanest
    validation available here and it costs nothing -- CP-18b's gate asks for it
    before the output is used anywhere.
    """
    discriminable = [item for item in inferences if item.discriminable]
    positives = [item for item in discriminable if item.ers_mode_inferred == OVERRIDE]
    return {
        "samples": len(inferences),
        "discriminable": len(discriminable),
        "false_positives": len(positives),
        "false_positive_rate": (len(positives) / len(discriminable)) if discriminable else None,
        "note": ("Every OVERRIDE here is false: the mechanism did not exist before 2026. "
                 "A non-trivial rate means the twin over-estimates power or the margin "
                 "is too tight -- fix the twin in CP-20 rather than widening the margin, "
                 "which would hide the calibration bug instead of removing it."),
    }
