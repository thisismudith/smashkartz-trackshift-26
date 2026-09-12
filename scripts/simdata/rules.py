"""Event rule configuration (M18) and the deterministic rule engine (M19).

This module is the SOLE OWNER of

    max_electrical_power_kw(speed_kmh, mode, event_rules) -> float

per TrackShift AGENTS.md section 32 and section 20.1. Everything that needs an electrical
power cap -- the DP, the simulator, the energy twin's calibration diagnostics, the
override discriminator -- calls this one function. No second implementation of the curve
may exist anywhere in the system, and no numeric envelope constant may live in any other
Python module (section 21).

  KNOWN VIOLATION AT TIME OF WRITING: scripts/simdata/twin.py holds `TwinParams.ers_cap_kw
  = 350.0` and its own `ers_envelope_kw()`. Its own docstring says it "must be replaced by
  the rule engine's version once that lands". This module is that version. Deleting the
  twin's copy is a separate change to a file this module does not own.

PURE FUNCTIONS ONLY. Nothing here opens a file, reads an environment variable, or caches
global state. The default configuration is returned as plain data (dataclasses, and a
mapping in the exact shape of `config/rules/2026/*.yaml` from section 21) so a YAML loader
can be dropped in later without touching a line of the maths.

PROVENANCE: every quantity produced here is `RULE`.

VERIFICATION STATUS: every regulatory number below carries `verified=False`. Section 20.1
is explicit that the figures are *reported values, not verified regulation*. Therefore:

  - the system MAY be described as MODELLING a speed-dependent envelope;
  - the system MAY NOT be described as legal by construction under the 2026 regulations;
  - no validation report may present envelope-derived quantities as regulatory fact.

`describe_compliance()` returns that statement as data so a UI cannot forget to show it.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1

#: Provenance tag for every value this module produces (AGENTS.md section 10).
PROVENANCE = "RULE"

#: The two deployment modes section 20.1 distinguishes.
MODE_NORMAL = "normal"
MODE_OVERRIDE = "override"
MODES = (MODE_NORMAL, MODE_OVERRIDE)

#: Width of the speed step used to encode a regulatory CLIFF -- a formula that applies
#: "below X kph" with power defined as zero "at or above X kph" -- inside a piecewise
#: LINEAR curve, which cannot otherwise represent a discontinuity. The curve therefore
#: carries a breakpoint at (X - CLIFF_EPSILON_KMH, last formula value) and another at
#: (X, 0.0). 0.001 km/h is four orders of magnitude below the resolution of any speed
#: channel in data/2026, so it is not a physically meaningful interpolation region.
CLIFF_EPSILON_KMH = 0.001


class RuleConfigError(ValueError):
    """Raised when a rule configuration is missing a key or is internally inconsistent.

    Section 21: "The rule engine must refuse to run against a configuration with a missing
    key rather than substituting a default." There is no fallback path in this module.
    """


class UnknownModeError(ValueError):
    """Raised for a deployment mode the configuration does not define. Never defaults."""


# ---------------------------------------------------------------------------
# Reported regulation formulas (Math.md section 7.2)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReportedFormula:
    """A reported linear power limit of the form P(kW) = intercept + slope * v(kph).

    Math.md section 7.2 records, from the 2026 FIA Formula 1 Technical Regulations (Power
    Unit), Article 5.4.7 and related clauses:

        OVERRIDE : P = 7100 - 20 * v   for v < 355 kph ; P = 0 at or above 355 kph
        OTHER    : P = 1850 -  5 * v   for v < 340 kph

    `peak_cap_kw` is the flat machine ceiling that truncates the formula at low speed: at
    v = 0 the override formula alone would read 7100 kW, which is not a power unit, it is
    an artefact of extrapolating a high-speed taper to rest.

    UNVERIFIED. This is a transcription of a reported formula, not a verified regulation.
    """

    intercept_kw: float
    slope_kw_per_kmh: float          # negative: the cap falls with speed
    cutoff_kmh: float                # at or above this speed the reported limit is 0
    peak_cap_kw: float               # flat ceiling below the taper
    source: str
    citation: str
    verified: bool = False

    def __post_init__(self) -> None:
        if self.slope_kw_per_kmh >= 0.0:
            raise RuleConfigError("slope_kw_per_kmh must be negative (a taper)")
        if self.peak_cap_kw <= 0.0:
            raise RuleConfigError("peak_cap_kw must be positive")
        if self.cutoff_kmh <= 0.0:
            raise RuleConfigError("cutoff_kmh must be positive")
        if self.verified:
            raise RuleConfigError(
                "ReportedFormula is a reported value; section 20.1 forbids marking it "
                "verified until traced to a specific FIA article"
            )

    def power_kw(self, speed_kmh: float) -> float:
        """The raw reported formula, truncated by the machine ceiling and the cutoff.

        Exposed for the derivation and for tests. Callers wanting a cap must use
        `max_electrical_power_kw`, which evaluates the configured curve.
        """
        if speed_kmh >= self.cutoff_kmh:
            return 0.0
        raw = self.intercept_kw + self.slope_kw_per_kmh * speed_kmh
        return max(0.0, min(raw, self.peak_cap_kw))

    @property
    def taper_start_kmh(self) -> float:
        """Speed at which the falling formula meets the flat ceiling.

        Solve  intercept + slope * v = peak_cap  =>  v = (peak_cap - intercept) / slope.

            override : (350 - 7100) / -20 = 337.5 km/h
            normal   : (350 - 1850) /  -5 = 300.0 km/h
        """
        return (self.peak_cap_kw - self.intercept_kw) / self.slope_kw_per_kmh

    @property
    def zero_crossing_kmh(self) -> float:
        """Speed at which the falling formula reaches 0 kW: v = -intercept / slope.

            override : 7100 / 20 = 355.0 km/h  (equal to the cutoff -- continuous)
            normal   : 1850 /  5 = 370.0 km/h  (beyond the 340 cutoff -- a cliff)
        """
        return -self.intercept_kw / self.slope_kw_per_kmh


# ---------------------------------------------------------------------------
# Piecewise-linear envelope (section 20.1 "Envelope representation", Math.md 7.1)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PowerEnvelope:
    """A speed-dependent electrical power cap as an explicit piecewise-linear curve.

        breakpoints_kmh : ascending list of speeds
        max_power_kw    : the cap at each breakpoint

    Exactly the representation section 20.1 mandates, so the shape can be corrected from
    configuration when better documentation is obtained without changing any code.
    """

    breakpoints_kmh: tuple[float, ...]
    max_power_kw: tuple[float, ...]
    source: str
    citation: str
    derivation: str = ""
    verified: bool = False

    def __post_init__(self) -> None:
        bp, pw = self.breakpoints_kmh, self.max_power_kw
        if len(bp) != len(pw):
            raise RuleConfigError(
                f"breakpoints_kmh ({len(bp)}) and max_power_kw ({len(pw)}) differ in length"
            )
        if len(bp) < 2:
            raise RuleConfigError("an envelope needs at least two breakpoints")
        for a, b in zip(bp, bp[1:]):
            if not b > a:
                raise RuleConfigError(
                    f"breakpoints_kmh must be strictly ascending; {a} -> {b} is not"
                )
        for v, p in zip(bp, pw):
            if not math.isfinite(v) or not math.isfinite(p):
                raise RuleConfigError("breakpoints and powers must be finite")
            if p < 0.0:
                raise RuleConfigError(f"max_power_kw must never be negative; got {p} at {v}")

    @property
    def peak_kw(self) -> float:
        return max(self.max_power_kw)

    def is_monotonic_non_increasing(self) -> bool:
        """True when the cap never rises with speed.

        Checked rather than enforced: a future corrected curve is free to be a different
        shape, and the engine's hard guarantees are the clamps and non-negativity. The
        default 2026 curves are both non-increasing and the tests assert it.
        """
        return all(b <= a + 1e-12 for a, b in zip(self.max_power_kw, self.max_power_kw[1:]))


def derive_envelope_from_reported_formula(
    formula: ReportedFormula,
    *,
    cliff_epsilon_kmh: float = CLIFF_EPSILON_KMH,
) -> PowerEnvelope:
    """Turn a reported linear formula into the breakpoint curve section 20.1 requires.

    The derivation, in full:

    1. Below `taper_start_kmh` the formula exceeds the machine ceiling, so the cap is the
       flat `peak_cap_kw`. Two breakpoints, at 0 km/h and at the taper start, both at the
       ceiling. (0 km/h is used rather than a negative speed because the evaluator clamps
       below the first breakpoint anyway, and a negative speed is not a physical state.)

    2. Above the taper start the cap follows the formula, which is already linear, so it
       needs exactly one further breakpoint -- wherever it ends.

    3. It ends in one of two ways.

       a. CONTINUOUS, when the formula reaches 0 kW at or before the cutoff
          (`zero_crossing_kmh <= cutoff_kmh`). One breakpoint at the zero crossing, value
          0. This is the OVERRIDE case: 7100 - 20v hits zero at exactly 355 km/h, which is
          also the reported cutoff, so the reported "P = 0 at or above 355 kph" is the
          natural continuation of the taper rather than a separate rule.

       b. CLIFF, when the formula is still above zero at the cutoff. This is the NORMAL
          case: 1850 - 5v = 150 kW at 340 km/h, yet the limit is reported as applying only
          *below* 340 kph. A piecewise-linear curve cannot hold a discontinuity, so the
          step is encoded as two breakpoints `cliff_epsilon_kmh` apart -- the last formula
          value just below the cutoff, then 0 exactly at it. Evaluating at the cutoff
          returns 0, matching the reported rule; evaluating just below returns the formula
          value; the sub-millimetre-per-hour ramp between them is an encoding artefact and
          is documented as such.

    The reported *shape* in AGENTS.md section 20.1 is the cross-check, not the input:

        normal   taper from ~290 km/h, fallen away by ~340 km/h
                 derived: flat to 300.0 km/h, then down to 0 at 340 km/h.
                 The derived taper start is 300, not the reported "approximately 290".
                 300 is kept because it is the exact intersection of the reported formula
                 with the reported 350 kW ceiling, whereas 290 is given as an
                 approximation. Both are unverified; the discrepancy is recorded here so
                 whoever obtains the real document knows which number to check first.

        override ~350 kW to ~337 km/h, available to ~355 km/h
                 derived: flat 350 kW to 337.5 km/h, zero at 355.0 km/h. Matches.
    """
    if cliff_epsilon_kmh <= 0.0:
        raise RuleConfigError("cliff_epsilon_kmh must be positive")

    taper = formula.taper_start_kmh
    zero = formula.zero_crossing_kmh
    if taper <= 0.0:
        raise RuleConfigError(
            f"taper start {taper} km/h is not positive: the formula never reaches the cap"
        )
    if formula.cutoff_kmh <= taper:
        raise RuleConfigError(
            f"cutoff {formula.cutoff_kmh} km/h is at or below the taper start {taper} km/h"
        )

    if zero <= formula.cutoff_kmh:
        kind = "continuous"
        bps = (0.0, taper, zero)
        pws = (formula.peak_cap_kw, formula.peak_cap_kw, 0.0)
    else:
        kind = "cliff"
        just_below = formula.cutoff_kmh - cliff_epsilon_kmh
        if just_below <= taper:
            raise RuleConfigError("cliff_epsilon_kmh is wider than the taper region")
        bps = (0.0, taper, just_below, formula.cutoff_kmh)
        pws = (
            formula.peak_cap_kw,
            formula.peak_cap_kw,
            formula.intercept_kw + formula.slope_kw_per_kmh * just_below,
            0.0,
        )

    derivation = (
        f"P(kW) = {formula.intercept_kw:g} {formula.slope_kw_per_kmh:+g} * v(kph), "
        f"truncated at the reported {formula.peak_cap_kw:g} kW ceiling; taper start "
        f"v = ({formula.peak_cap_kw:g} - {formula.intercept_kw:g}) / "
        f"{formula.slope_kw_per_kmh:g} = {taper:g} km/h; formula zero crossing "
        f"v = {zero:g} km/h; reported cutoff {formula.cutoff_kmh:g} km/h; ending is "
        f"{kind}. See derive_envelope_from_reported_formula for the full argument. "
        "UNVERIFIED: reported values, not verified regulation (section 20.1)."
    )
    return PowerEnvelope(
        breakpoints_kmh=tuple(float(v) for v in bps),
        max_power_kw=tuple(float(p) for p in pws),
        source=formula.source,
        citation=formula.citation,
        derivation=derivation,
        verified=False,
    )


# ---------------------------------------------------------------------------
# The three separate regulatory energy quantities (section 28, section 34.1)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EnergyQuantity:
    """One regulatory energy limit in MJ, with its accounting window and provenance.

    Section 28 is emphatic that the Energy Store capacity, the deploy budget per accounting
    window, and the harvest budget per accounting window are THREE DISTINCT constraints:

        "A car can be short of deploy budget while its store is nearly full, or vice
         versa; conflating them produces a twin that is right about total energy and wrong
         about what the car may legally do with it next."

    Section 34.1 adds: do not assume "4 MJ" means battery capacity or per-lap energy
    without source verification. Every value here is a placeholder with verified=False, and
    none is derived from any other.
    """

    value_mj: float
    accounting_window: str            # "instantaneous_store" | "lap" | as the regs define
    source: str
    citation: str
    note: str = ""
    verified: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.value_mj) or self.value_mj <= 0.0:
            raise RuleConfigError("value_mj must be a positive finite number")
        if not self.accounting_window:
            raise RuleConfigError("accounting_window must be stated, not implied")


# ---------------------------------------------------------------------------
# Event rules
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EventRules:
    """Everything the rule engine needs for one event/session. Data, never behaviour."""

    season: int
    event: str
    session_type: str
    configuration_version: str
    power_envelope: Mapping[str, PowerEnvelope]
    ers_store_capacity: EnergyQuantity
    deploy_budget: EnergyQuantity
    harvest_budget: EnergyQuantity
    regulation_snapshot: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for mode in MODES:
            if mode not in self.power_envelope:
                raise RuleConfigError(
                    f"power_envelope is missing required mode {mode!r}; section 21 forbids "
                    "substituting a default for a missing key"
                )
            if not isinstance(self.power_envelope[mode], PowerEnvelope):
                raise RuleConfigError(f"power_envelope[{mode!r}] is not a PowerEnvelope")


# --- reported 2026 inputs, as data -----------------------------------------

_FIA_PU_CITATION = (
    "FIA 2026 Formula 1 Technical Regulations, Power Unit, Article 5.4.7 and related "
    "clauses (example document: fia_2026_formula_1_technical_regulations_pu_-_issue_4_-"
    "_2023-10-25.pdf), as transcribed in Math.md section 7.2. NOT independently verified "
    "against the document; see AGENTS.md section 20.1."
)

#: Reported flat ceiling common to both modes. It lives here, inside the rule engine, and
#: nowhere else in the system (section 32).
REPORTED_PEAK_ELECTRICAL_KW = 350.0

REPORTED_FORMULA_OVERRIDE = ReportedFormula(
    intercept_kw=7100.0,
    slope_kw_per_kmh=-20.0,
    cutoff_kmh=355.0,
    peak_cap_kw=REPORTED_PEAK_ELECTRICAL_KW,
    source="Math.md section 7.2 (override), AGENTS.md section 20.1 reported shape",
    citation=_FIA_PU_CITATION,
)

REPORTED_FORMULA_NORMAL = ReportedFormula(
    intercept_kw=1850.0,
    slope_kw_per_kmh=-5.0,
    cutoff_kmh=340.0,
    peak_cap_kw=REPORTED_PEAK_ELECTRICAL_KW,
    source="Math.md section 7.2 (other limits), AGENTS.md section 20.1 reported shape",
    citation=_FIA_PU_CITATION,
)

_PLACEHOLDER_ENERGY_SOURCE = (
    "PLACEHOLDER pending source verification. AGENTS.md section 34.1: the exact energy "
    "excursion / recharge accounting window must come from current FIA regulations, and "
    "\"4 MJ\" must not be assumed to mean battery capacity or per-lap energy."
)


def default_energy_quantities() -> dict[str, EnergyQuantity]:
    """The three separate section 28 quantities as documented placeholders.

    They are deliberately three different numbers with three different windows so that no
    consumer can accidentally read one as another. None of them is verified.
    """
    return {
        "ers_store_capacity": EnergyQuantity(
            value_mj=4.0,
            accounting_window="instantaneous_store",
            source=_PLACEHOLDER_ENERGY_SOURCE,
            citation="unsourced placeholder",
            note=(
                "How much energy the store may hold at one instant. This is the quantity "
                "`ers_soc_est_mj` is bounded by. It is NOT a per-lap allowance. The widely "
                "quoted '4 MJ' figure is the reason section 34.1 exists: it is used here "
                "only as a placeholder magnitude and must not be reported as the 2026 "
                "Energy Store capacity until traced to an article."
            ),
        ),
        "deploy_budget": EnergyQuantity(
            value_mj=8.5,
            accounting_window="lap",
            source=_PLACEHOLDER_ENERGY_SOURCE,
            citation="unsourced placeholder",
            note=(
                "How much may be DEPLOYED per accounting window; bounds "
                "`ers_deploy_budget_remaining_est_mj`. Independent of store capacity: a "
                "car can be out of budget with a full store. Deliberately not equal to "
                "the store capacity so the two cannot be silently interchanged."
            ),
        ),
        "harvest_budget": EnergyQuantity(
            value_mj=9.0,
            accounting_window="lap",
            source=_PLACEHOLDER_ENERGY_SOURCE,
            citation="unsourced placeholder",
            note=(
                "How much may be RECOVERED per accounting window; bounds "
                "`ers_harvest_budget_remaining_est_mj`. A recharge-per-lap limit is not "
                "battery capacity (section 28). Independent of both other values."
            ),
        ),
    }


def default_power_envelopes() -> dict[str, PowerEnvelope]:
    """Both 2026 envelopes, derived from the reported formulas. Pure."""
    return {
        MODE_NORMAL: derive_envelope_from_reported_formula(REPORTED_FORMULA_NORMAL),
        MODE_OVERRIDE: derive_envelope_from_reported_formula(REPORTED_FORMULA_OVERRIDE),
    }


def default_event_rules(
    event: str = "common",
    session_type: str = "Race",
    season: int = 2026,
    configuration_version: str = f"2026-common-v{SCHEMA_VERSION}",
) -> EventRules:
    """The season-wide default rule set, as it would be loaded from
    `config/rules/2026/common.yaml`. No file is read; this is the same data in code.
    """
    energy = default_energy_quantities()
    return EventRules(
        season=season,
        event=event,
        session_type=session_type,
        configuration_version=configuration_version,
        power_envelope=default_power_envelopes(),
        ers_store_capacity=energy["ers_store_capacity"],
        deploy_budget=energy["deploy_budget"],
        harvest_budget=energy["harvest_budget"],
        regulation_snapshot={
            "season": season,
            "source_documents": [_FIA_PU_CITATION],
            "retrieved_at": None,
            "verified": False,
            "note": (
                "No value in this configuration has been traced to a specific article of "
                "the FIA Technical or Sporting Regulations. See describe_compliance()."
            ),
        },
    )


# ---------------------------------------------------------------------------
# THE evaluator (section 32)
# ---------------------------------------------------------------------------
def _resolve(event_rules: EventRules | Mapping[str, Any]) -> EventRules:
    if isinstance(event_rules, EventRules):
        return event_rules
    if isinstance(event_rules, Mapping):
        return event_rules_from_mapping(event_rules)
    raise RuleConfigError(
        f"event_rules must be an EventRules or a config mapping, got {type(event_rules).__name__}"
    )


def normalise_mode(mode: str) -> str:
    """Fold mode spelling. Math.md writes 'NORMAL'/'OVERRIDE', section 20.1 writes
    'normal'/'override'; both resolve to the same curve. Anything else raises -- an
    unrecognised mode must never silently fall back to normal deployment.
    """
    if not isinstance(mode, str):
        raise UnknownModeError(f"mode must be a string, got {type(mode).__name__}")
    key = mode.strip().lower()
    if key not in MODES:
        raise UnknownModeError(f"unknown deployment mode {mode!r}; expected one of {MODES}")
    return key


def evaluate_envelope_kw(envelope: PowerEnvelope, speed_kmh: float) -> float:
    """Piecewise-linear interpolation of one curve (Math.md section 7.1).

        P(v) = P_i + (P_{i+1} - P_i)/(v_{i+1} - v_i) * (v - v_i)

    Clamps below the first breakpoint and above the last, per section 20.1, and never
    returns a negative cap. A NaN speed raises rather than producing a number.
    """
    v = float(speed_kmh)
    if math.isnan(v):
        raise ValueError("speed_kmh is NaN; the rule engine does not guess a cap")
    bps, pws = envelope.breakpoints_kmh, envelope.max_power_kw
    if v <= bps[0]:
        return max(0.0, pws[0])        # clamp below the first breakpoint (incl. v < 0)
    if v >= bps[-1]:
        return max(0.0, pws[-1])       # clamp above the last breakpoint (incl. +inf)
    i = bisect_right(bps, v) - 1
    v0, v1 = bps[i], bps[i + 1]
    p0, p1 = pws[i], pws[i + 1]
    alpha = (v - v0) / (v1 - v0)
    return max(0.0, (1.0 - alpha) * p0 + alpha * p1)


def max_electrical_power_kw(
    speed_kmh: float,
    mode: str,
    event_rules: EventRules | Mapping[str, Any],
) -> float:
    """Maximum permitted electrical power, kW, at this speed in this mode (section 32).

    THE one implementation of the section 20.1 curve. The DP, the simulator, the twin's
    calibration diagnostics and the override discriminator all call this.

    Behaviour at the edges, all deliberate and all tested:
      speed below the first breakpoint (including negative)  -> the first breakpoint's cap
      speed above the last breakpoint (including +inf)       -> the last breakpoint's cap
      NaN speed                                              -> ValueError
      unknown mode                                           -> UnknownModeError
    The return value is never negative.
    """
    rules = _resolve(event_rules)
    key = normalise_mode(mode)
    return evaluate_envelope_kw(rules.power_envelope[key], speed_kmh)


def max_electrical_power_kw_series(
    speeds_kmh: Iterable[float],
    mode: str,
    event_rules: EventRules | Mapping[str, Any],
) -> list[float]:
    """Vectorised convenience over the SAME scalar evaluator -- no second curve.

    Resolves the config and the mode once, then maps. Exists so the twin does not have to
    reimplement the envelope to process an array (section 32).
    """
    rules = _resolve(event_rules)
    envelope = rules.power_envelope[normalise_mode(mode)]
    return [evaluate_envelope_kw(envelope, v) for v in speeds_kmh]


def envelope_breakpoints_kmh(
    mode: str, event_rules: EventRules | Mapping[str, Any]
) -> tuple[float, ...]:
    """The configured breakpoint speeds for a mode. Lets tests and the UI enumerate the
    thresholds without hardcoding them (section 32 requires tests at every breakpoint)."""
    rules = _resolve(event_rules)
    return rules.power_envelope[normalise_mode(mode)].breakpoints_kmh


def envelope_separation_speed_kmh(
    event_rules: EventRules | Mapping[str, Any],
) -> float | None:
    """Speed above which the override and normal caps differ (section 20.2, section 32).

    At and below this speed the two envelopes coincide and the override discriminator
    carries no information: it must return "unknown", not "normal". Computed from the
    configured curves rather than asserted, so a corrected config moves it automatically.

    On the union of both breakpoint sets, each curve is linear between consecutive points,
    so two curves coincide on a sub-segment exactly when they agree at both of its ends.
    Walking the union therefore locates the separation exactly, and the value returned is
    the infimum of the set where the caps differ: if the caps agree at `a` and differ at
    the next union point `b`, they differ everywhere in (a, b], so `a` is returned.

    For the default 2026 config that is 300.0 km/h -- the normal taper start, where the
    normal curve leaves the shared 350 kW ceiling that override holds to 337.5 km/h.
    Returns None if the two curves are identical everywhere.
    """
    rules = _resolve(event_rules)
    normal = rules.power_envelope[MODE_NORMAL]
    override = rules.power_envelope[MODE_OVERRIDE]
    prev: float | None = None
    for v in sorted(set(normal.breakpoints_kmh) | set(override.breakpoints_kmh)):
        if abs(evaluate_envelope_kw(override, v) - evaluate_envelope_kw(normal, v)) > 1e-9:
            return float(v if prev is None else prev)
        prev = v
    return None


# ---------------------------------------------------------------------------
# Verification surface (section 20.1, section 57)
# ---------------------------------------------------------------------------
def unverified_keys(event_rules: EventRules | Mapping[str, Any]) -> tuple[str, ...]:
    """Dotted paths of every value in the configuration carrying verified=False."""
    rules = _resolve(event_rules)
    out: list[str] = []
    for mode in sorted(rules.power_envelope):
        if not rules.power_envelope[mode].verified:
            out.append(f"power_envelope.{mode}")
    for name in ("ers_store_capacity", "deploy_budget", "harvest_budget"):
        if not getattr(rules, name).verified:
            out.append(f"energy_budget.{name}")
    if not rules.regulation_snapshot.get("verified", False):
        out.append("regulation_snapshot")
    return tuple(out)


def describe_compliance(event_rules: EventRules | Mapping[str, Any]) -> dict[str, Any]:
    """The claim the system is allowed to make about this configuration, as data.

    Section 20.1 permits describing the system as MODELLING a speed-dependent envelope and
    forbids describing it as legal by construction. Returned as a dict so a UI or a
    validation report renders the disclaimer instead of inventing one.
    """
    rules = _resolve(event_rules)
    missing = unverified_keys(rules)
    return {
        "provenance": PROVENANCE,
        "models_speed_dependent_envelope": True,
        "legal_by_construction": False,
        "all_values_verified": len(missing) == 0,
        "unverified_keys": missing,
        "statement": (
            "TrackShift MODELS a speed-dependent electrical power envelope using reported "
            "2026 values. It is NOT legal by construction under the 2026 regulations. No "
            "envelope-derived quantity may be presented as regulatory fact while any key "
            "below is unverified (AGENTS.md sections 20.1 and 57)."
        ),
        "configuration_version": rules.configuration_version,
        "schema_version": SCHEMA_VERSION,
    }


# ---------------------------------------------------------------------------
# Config <-> mapping, in the exact shape of config/rules/2026/*.yaml (section 21)
# ---------------------------------------------------------------------------
def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if not isinstance(mapping, Mapping):
        raise RuleConfigError(f"{where} must be a mapping, got {type(mapping).__name__}")
    if key not in mapping:
        raise RuleConfigError(
            f"missing required key {where}.{key}; section 21 requires the engine to refuse "
            "to run rather than substitute a default"
        )
    return mapping[key]


def event_rules_to_mapping(event_rules: EventRules) -> dict[str, Any]:
    """Serialise to the section 21 YAML shape. Round-trips with `event_rules_from_mapping`."""
    rules = _resolve(event_rules)

    def envelope(e: PowerEnvelope) -> dict[str, Any]:
        return {
            "breakpoints_kmh": list(e.breakpoints_kmh),
            "max_power_kw": list(e.max_power_kw),
            "source": e.source,
            "citation": e.citation,
            "derivation": e.derivation,
            "verified": e.verified,
        }

    def energy(q: EnergyQuantity) -> dict[str, Any]:
        return {
            "value_mj": q.value_mj,
            "accounting_window": q.accounting_window,
            "source": q.source,
            "citation": q.citation,
            "note": q.note,
            "verified": q.verified,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": PROVENANCE,
        "regulation_snapshot": dict(rules.regulation_snapshot),
        "competition": {
            "season": rules.season,
            "event": rules.event,
            "session_type": rules.session_type,
            "configuration_version": rules.configuration_version,
        },
        "power_envelope": {m: envelope(e) for m, e in rules.power_envelope.items()},
        "energy_budget": {
            "ers_store_capacity": energy(rules.ers_store_capacity),
            "deploy_budget": energy(rules.deploy_budget),
            "harvest_budget": energy(rules.harvest_budget),
        },
        "compliance": describe_compliance(rules),
    }


def event_rules_from_mapping(mapping: Mapping[str, Any]) -> EventRules:
    """Build EventRules from the section 21 mapping shape.

    This is the seam a YAML loader plugs into: `yaml.safe_load(path)` produces exactly this
    mapping, and file IO stays outside the maths. Every required key is demanded; nothing
    is defaulted.
    """
    comp = _require(mapping, "competition", "config")
    env = _require(mapping, "power_envelope", "config")
    eb = _require(mapping, "energy_budget", "config")

    envelopes: dict[str, PowerEnvelope] = {}
    for mode in MODES:
        e = _require(env, mode, "config.power_envelope")
        envelopes[mode] = PowerEnvelope(
            breakpoints_kmh=tuple(
                float(v) for v in _require(e, "breakpoints_kmh", f"config.power_envelope.{mode}")
            ),
            max_power_kw=tuple(
                float(p) for p in _require(e, "max_power_kw", f"config.power_envelope.{mode}")
            ),
            source=_require(e, "source", f"config.power_envelope.{mode}"),
            citation=_require(e, "citation", f"config.power_envelope.{mode}"),
            derivation=e.get("derivation", ""),
            verified=bool(_require(e, "verified", f"config.power_envelope.{mode}")),
        )

    def energy(name: str) -> EnergyQuantity:
        q = _require(eb, name, "config.energy_budget")
        where = f"config.energy_budget.{name}"
        return EnergyQuantity(
            value_mj=float(_require(q, "value_mj", where)),
            accounting_window=_require(q, "accounting_window", where),
            source=_require(q, "source", where),
            citation=_require(q, "citation", where),
            note=q.get("note", ""),
            verified=bool(_require(q, "verified", where)),
        )

    return EventRules(
        season=int(_require(comp, "season", "config.competition")),
        event=_require(comp, "event", "config.competition"),
        session_type=_require(comp, "session_type", "config.competition"),
        configuration_version=_require(comp, "configuration_version", "config.competition"),
        power_envelope=envelopes,
        ers_store_capacity=energy("ers_store_capacity"),
        deploy_budget=energy("deploy_budget"),
        harvest_budget=energy("harvest_budget"),
        regulation_snapshot=dict(mapping.get("regulation_snapshot", {})),
    )


def with_power_envelope(
    event_rules: EventRules, mode: str, envelope: PowerEnvelope
) -> EventRules:
    """Return a copy with one mode's curve replaced. Pure; the input is untouched.

    This is how a corrected curve is applied once the regulation is sourced -- by swapping
    configuration, not by editing code (section 20.1).
    """
    rules = _resolve(event_rules)
    key = normalise_mode(mode)
    merged = dict(rules.power_envelope)
    merged[key] = envelope
    return replace(rules, power_envelope=merged)


__all__ = [
    "SCHEMA_VERSION",
    "PROVENANCE",
    "MODE_NORMAL",
    "MODE_OVERRIDE",
    "MODES",
    "CLIFF_EPSILON_KMH",
    "RuleConfigError",
    "UnknownModeError",
    "ReportedFormula",
    "PowerEnvelope",
    "EnergyQuantity",
    "EventRules",
    "REPORTED_PEAK_ELECTRICAL_KW",
    "REPORTED_FORMULA_NORMAL",
    "REPORTED_FORMULA_OVERRIDE",
    "derive_envelope_from_reported_formula",
    "default_power_envelopes",
    "default_energy_quantities",
    "default_event_rules",
    "normalise_mode",
    "evaluate_envelope_kw",
    "max_electrical_power_kw",
    "max_electrical_power_kw_series",
    "envelope_breakpoints_kmh",
    "envelope_separation_speed_kmh",
    "unverified_keys",
    "describe_compliance",
    "event_rules_to_mapping",
    "event_rules_from_mapping",
    "with_power_envelope",
]
