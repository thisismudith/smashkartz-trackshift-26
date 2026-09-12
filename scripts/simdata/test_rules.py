"""Boundary tests for the rule engine (AGENTS.md sections 32 and 52).

Section 32: "For the envelope this means tests immediately below, exactly at, and
immediately above EVERY breakpoint of both curves, plus the clamped regions beyond the
first and last breakpoint, plus the speed at which the two curves separate."

The breakpoint sweep below is parametrised over the breakpoints read OUT of the
configuration, not over a hardcoded list, so correcting the curve in config automatically
re-points the tests at the new thresholds.
"""
import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from simdata.rules import (  # noqa: E402
    CLIFF_EPSILON_KMH,
    MODE_NORMAL,
    MODE_OVERRIDE,
    MODES,
    REPORTED_FORMULA_NORMAL,
    REPORTED_FORMULA_OVERRIDE,
    REPORTED_PEAK_ELECTRICAL_KW,
    EnergyQuantity,
    EventRules,
    PowerEnvelope,
    ReportedFormula,
    RuleConfigError,
    UnknownModeError,
    default_event_rules,
    derive_envelope_from_reported_formula,
    describe_compliance,
    envelope_breakpoints_kmh,
    envelope_separation_speed_kmh,
    DEFAULT_SAMPLE_STEP_KMH,
    event_rules_from_mapping,
    event_rules_to_mapping,
    max_electrical_power_kw,
    max_electrical_power_kw_series,
    sample_envelope_curve,
    sampled_curves_mapping,
    sampled_speeds_kmh,
    unverified_keys,
    with_power_envelope,
)

RULES = default_event_rules()
TOL = 1e-9

#: Smaller than CLIFF_EPSILON_KMH so "immediately below/above" stays inside the narrow
#: segment that encodes the normal-mode regulatory cliff at 340 km/h.
DELTA = CLIFF_EPSILON_KMH / 100.0

#: (mode, index) for every breakpoint of both curves.
ALL_BREAKPOINTS = [
    (mode, i)
    for mode in MODES
    for i in range(len(envelope_breakpoints_kmh(mode, RULES)))
]


def cap(v, mode=MODE_NORMAL):
    return max_electrical_power_kw(v, mode, RULES)


# ---------------------------------------------------------------------------
# Derivation: the breakpoints must come from the Math.md 7.2 formulas
# ---------------------------------------------------------------------------
def test_derived_breakpoints_match_the_hand_derivation():
    """override: taper start (350-7100)/-20 = 337.5, zero at 7100/20 = 355 (continuous).
    normal:   taper start (350-1850)/-5 = 300.0, formula still at 150 kW at the 340 cutoff,
              so the curve carries the documented cliff pair at 340-eps and 340."""
    assert REPORTED_FORMULA_OVERRIDE.taper_start_kmh == pytest.approx(337.5, abs=TOL)
    assert REPORTED_FORMULA_OVERRIDE.zero_crossing_kmh == pytest.approx(355.0, abs=TOL)
    assert REPORTED_FORMULA_NORMAL.taper_start_kmh == pytest.approx(300.0, abs=TOL)
    assert REPORTED_FORMULA_NORMAL.zero_crossing_kmh == pytest.approx(370.0, abs=TOL)

    assert envelope_breakpoints_kmh(MODE_OVERRIDE, RULES) == (0.0, 337.5, 355.0)
    assert envelope_breakpoints_kmh(MODE_NORMAL, RULES) == (
        0.0,
        300.0,
        340.0 - CLIFF_EPSILON_KMH,
        340.0,
    )


def test_derivation_string_is_recorded_on_every_envelope():
    for mode in MODES:
        e = RULES.power_envelope[mode]
        assert "taper start" in e.derivation and "UNVERIFIED" in e.derivation
        assert e.citation and "FIA" in e.citation


def test_taper_segments_reproduce_the_reported_formula_exactly():
    """Between the taper start and the end of the curve the interpolant IS the formula."""
    for v in (305.0, 320.0, 335.0, 339.0):
        assert cap(v, MODE_NORMAL) == pytest.approx(1850.0 - 5.0 * v, abs=1e-6)
    for v in (340.0, 345.0, 350.0, 354.0):
        assert cap(v, MODE_OVERRIDE) == pytest.approx(7100.0 - 20.0 * v, abs=1e-6)


# ---------------------------------------------------------------------------
# Section 32: immediately below, exactly at, immediately above EVERY breakpoint
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode,i", ALL_BREAKPOINTS)
def test_exactly_at_breakpoint_returns_the_configured_cap(mode, i):
    e = RULES.power_envelope[mode]
    assert cap(e.breakpoints_kmh[i], mode) == pytest.approx(e.max_power_kw[i], abs=TOL)


@pytest.mark.parametrize("mode,i", ALL_BREAKPOINTS)
def test_immediately_below_breakpoint(mode, i):
    e = RULES.power_envelope[mode]
    v = e.breakpoints_kmh[i]
    here, below = e.max_power_kw[i], cap(v - DELTA, mode)
    assert below >= 0.0
    assert below >= here - TOL                      # non-increasing across the breakpoint
    if i == 0:
        assert below == pytest.approx(here, abs=TOL)  # clamped below the first breakpoint
    else:
        prev = e.max_power_kw[i - 1]
        assert min(prev, here) - TOL <= below <= max(prev, here) + TOL
        assert below == pytest.approx(here, abs=abs(prev - here) * (DELTA / (v - e.breakpoints_kmh[i - 1])) + TOL)


@pytest.mark.parametrize("mode,i", ALL_BREAKPOINTS)
def test_immediately_above_breakpoint(mode, i):
    e = RULES.power_envelope[mode]
    v = e.breakpoints_kmh[i]
    here, above = e.max_power_kw[i], cap(v + DELTA, mode)
    assert above >= 0.0
    assert above <= here + TOL                      # non-increasing across the breakpoint
    if i == len(e.breakpoints_kmh) - 1:
        assert above == pytest.approx(here, abs=TOL)  # clamped above the last breakpoint
    else:
        nxt = e.max_power_kw[i + 1]
        assert min(here, nxt) - TOL <= above <= max(here, nxt) + TOL


# ---------------------------------------------------------------------------
# Clamped regions beyond the first and last breakpoint
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", MODES)
def test_clamped_below_first_breakpoint(mode):
    e = RULES.power_envelope[mode]
    for v in (-1e9, -350.0, -1.0, -1e-12, 0.0):
        assert cap(v, mode) == pytest.approx(e.max_power_kw[0], abs=TOL)


@pytest.mark.parametrize("mode", MODES)
def test_clamped_above_last_breakpoint(mode):
    e = RULES.power_envelope[mode]
    for v in (400.0, 1000.0, 1e9, math.inf):
        assert cap(v, mode) == pytest.approx(e.max_power_kw[-1], abs=TOL)


def test_zero_speed_gives_full_reported_power_in_both_modes():
    assert cap(0.0, MODE_NORMAL) == pytest.approx(REPORTED_PEAK_ELECTRICAL_KW, abs=TOL)
    assert cap(0.0, MODE_OVERRIDE) == pytest.approx(REPORTED_PEAK_ELECTRICAL_KW, abs=TOL)


def test_huge_speed_gives_zero_not_a_negative_extrapolation():
    """The naive formula 1850 - 5v is -3150 kW at 1000 km/h. The engine must not be."""
    assert cap(1000.0, MODE_NORMAL) == 0.0
    assert cap(1000.0, MODE_OVERRIDE) == 0.0


def test_nan_speed_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        cap(float("nan"), MODE_NORMAL)


# ---------------------------------------------------------------------------
# Global properties over a dense grid
# ---------------------------------------------------------------------------
def _dense_grid():
    """Every breakpoint of both curves, each +-DELTA, plus a 0.25 km/h sweep."""
    pts = set()
    for mode in MODES:
        for v in envelope_breakpoints_kmh(mode, RULES):
            pts.update({v - DELTA, v, v + DELTA})
    pts.update(i * 0.25 for i in range(-40, 1801))   # -10 .. 450 km/h
    return sorted(pts)


GRID = _dense_grid()


@pytest.mark.parametrize("mode", MODES)
def test_never_negative_anywhere(mode):
    assert min(max_electrical_power_kw_series(GRID, mode, RULES)) >= 0.0


@pytest.mark.parametrize("mode", MODES)
def test_never_exceeds_the_reported_peak(mode):
    assert max(max_electrical_power_kw_series(GRID, mode, RULES)) <= REPORTED_PEAK_ELECTRICAL_KW + TOL


@pytest.mark.parametrize("mode", MODES)
def test_monotonic_non_increasing_in_speed(mode):
    caps = max_electrical_power_kw_series(GRID, mode, RULES)
    bad = [(GRID[i], caps[i], GRID[i + 1], caps[i + 1])
           for i in range(len(GRID) - 1) if caps[i + 1] > caps[i] + TOL]
    assert not bad, f"cap rises with speed at {bad[:3]}"
    assert RULES.power_envelope[mode].is_monotonic_non_increasing()


def test_override_is_at_least_normal_at_every_speed():
    n = max_electrical_power_kw_series(GRID, MODE_NORMAL, RULES)
    o = max_electrical_power_kw_series(GRID, MODE_OVERRIDE, RULES)
    bad = [(v, a, b) for v, a, b in zip(GRID, n, o) if b < a - TOL]
    assert not bad, f"override cap below normal cap at {bad[:3]}"


# ---------------------------------------------------------------------------
# Section 20.2: the speed at which the two curves separate
# ---------------------------------------------------------------------------
def test_envelope_separation_speed_is_the_normal_taper_start():
    sep = envelope_separation_speed_kmh(RULES)
    assert sep == pytest.approx(300.0, abs=TOL)


def test_curves_are_identical_at_and_below_the_separation_speed():
    sep = envelope_separation_speed_kmh(RULES)
    for v in (0.0, 50.0, 150.0, 250.0, sep - DELTA, sep):
        assert cap(v, MODE_NORMAL) == pytest.approx(cap(v, MODE_OVERRIDE), abs=TOL)


def test_curves_do_separate_immediately_above_it():
    sep = envelope_separation_speed_kmh(RULES)
    assert cap(sep + DELTA, MODE_OVERRIDE) > cap(sep + DELTA, MODE_NORMAL)
    assert cap(sep + 5.0, MODE_OVERRIDE) > cap(sep + 5.0, MODE_NORMAL) + 1.0


# ---------------------------------------------------------------------------
# The reported shape in AGENTS.md 20.1 is the cross-check
# ---------------------------------------------------------------------------
def test_reported_shape_normal_tapers_and_has_fallen_away_by_340():
    assert cap(280.0, MODE_NORMAL) == pytest.approx(REPORTED_PEAK_ELECTRICAL_KW, abs=TOL)
    assert cap(310.0, MODE_NORMAL) < REPORTED_PEAK_ELECTRICAL_KW - 1.0
    assert cap(340.0, MODE_NORMAL) == 0.0


def test_reported_shape_override_holds_350_to_337_and_survives_to_355():
    assert cap(337.0, MODE_OVERRIDE) == pytest.approx(REPORTED_PEAK_ELECTRICAL_KW, abs=TOL)
    assert cap(354.9, MODE_OVERRIDE) > 0.0
    assert cap(355.0, MODE_OVERRIDE) == 0.0


def test_cap_is_not_the_constant_350():
    """Section 20.1 forbids P_electrical_max = 350 kW."""
    values = {round(cap(v, MODE_OVERRIDE), 6) for v in (100.0, 340.0, 350.0, 360.0)}
    assert len(values) == 4


# ---------------------------------------------------------------------------
# Mode handling
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("spelling", ["normal", "NORMAL", " Normal "])
def test_mode_spelling_is_folded(spelling):
    assert cap(320.0, spelling) == pytest.approx(cap(320.0, MODE_NORMAL), abs=TOL)


@pytest.mark.parametrize("bad", ["", "overtake", "NORMAL_MODE", "off", None, 1])
def test_unknown_mode_never_silently_falls_back(bad):
    with pytest.raises(UnknownModeError):
        max_electrical_power_kw(300.0, bad, RULES)


def test_series_matches_the_scalar_evaluator():
    speeds = [0.0, 120.0, 299.9, 300.0, 337.5, 339.9995, 340.0, 355.0, 500.0]
    for mode in MODES:
        assert max_electrical_power_kw_series(speeds, mode, RULES) == [
            max_electrical_power_kw(v, mode, RULES) for v in speeds
        ]


# ---------------------------------------------------------------------------
# Section 28 / 34.1: three separate energy quantities
# ---------------------------------------------------------------------------
def test_store_deploy_and_harvest_are_three_distinct_unverified_quantities():
    q = (RULES.ers_store_capacity, RULES.deploy_budget, RULES.harvest_budget)
    assert len({x.value_mj for x in q}) == 3, "the three limits must not collapse to one"
    assert all(x.verified is False for x in q)
    assert all(x.note and x.source for x in q)
    assert RULES.ers_store_capacity.accounting_window == "instantaneous_store"
    assert RULES.deploy_budget.accounting_window == "lap"
    assert RULES.harvest_budget.accounting_window == "lap"


def test_deploy_budget_is_not_the_store_capacity():
    assert RULES.deploy_budget.value_mj != RULES.ers_store_capacity.value_mj


def test_energy_quantity_rejects_a_missing_accounting_window():
    with pytest.raises(RuleConfigError):
        EnergyQuantity(value_mj=4.0, accounting_window="", source="s", citation="c")


def test_energy_quantity_rejects_non_positive_values():
    with pytest.raises(RuleConfigError):
        EnergyQuantity(value_mj=0.0, accounting_window="lap", source="s", citation="c")


# ---------------------------------------------------------------------------
# Section 20.1 / 57: verification surface
# ---------------------------------------------------------------------------
def test_nothing_is_marked_verified():
    keys = unverified_keys(RULES)
    assert "power_envelope.normal" in keys
    assert "power_envelope.override" in keys
    assert "energy_budget.ers_store_capacity" in keys
    assert "energy_budget.deploy_budget" in keys
    assert "energy_budget.harvest_budget" in keys
    assert "regulation_snapshot" in keys


def test_compliance_statement_refuses_legal_by_construction():
    c = describe_compliance(RULES)
    assert c["legal_by_construction"] is False
    assert c["models_speed_dependent_envelope"] is True
    assert c["all_values_verified"] is False
    assert c["provenance"] == "RULE"
    assert "NOT legal by construction" in c["statement"]


def test_reported_formula_cannot_be_marked_verified():
    with pytest.raises(RuleConfigError):
        ReportedFormula(
            intercept_kw=1850.0, slope_kw_per_kmh=-5.0, cutoff_kmh=340.0,
            peak_cap_kw=350.0, source="s", citation="c", verified=True,
        )


# ---------------------------------------------------------------------------
# Section 21: configuration is data, and a missing key refuses to run
# ---------------------------------------------------------------------------
def test_mapping_round_trip_is_lossless_for_the_evaluator():
    m = event_rules_to_mapping(RULES)
    back = event_rules_from_mapping(m)
    for mode in MODES:
        for v in GRID[::37]:
            assert max_electrical_power_kw(v, mode, back) == pytest.approx(
                max_electrical_power_kw(v, mode, RULES), abs=TOL
            )
    assert back.deploy_budget.value_mj == RULES.deploy_budget.value_mj


def test_a_mapping_is_accepted_directly_by_the_evaluator():
    m = event_rules_to_mapping(RULES)
    assert max_electrical_power_kw(320.0, MODE_NORMAL, m) == pytest.approx(250.0, abs=1e-6)


@pytest.mark.parametrize(
    "path",
    [
        ("competition",),
        ("power_envelope",),
        ("energy_budget",),
        ("power_envelope", "override"),
        ("energy_budget", "harvest_budget"),
    ],
)
def test_missing_key_refuses_to_run_instead_of_defaulting(path):
    m = event_rules_to_mapping(RULES)
    node = m
    for p in path[:-1]:
        node = node[p]
    del node[path[-1]]
    with pytest.raises(RuleConfigError):
        event_rules_from_mapping(m)


def test_missing_mode_in_event_rules_is_rejected():
    with pytest.raises(RuleConfigError):
        EventRules(
            season=2026, event="e", session_type="Race", configuration_version="v",
            power_envelope={MODE_NORMAL: RULES.power_envelope[MODE_NORMAL]},
            ers_store_capacity=RULES.ers_store_capacity,
            deploy_budget=RULES.deploy_budget,
            harvest_budget=RULES.harvest_budget,
        )


def test_bad_event_rules_type_is_rejected():
    with pytest.raises(RuleConfigError):
        max_electrical_power_kw(300.0, MODE_NORMAL, "config.yaml")


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------
def test_envelope_rejects_mismatched_lengths():
    with pytest.raises(RuleConfigError):
        PowerEnvelope((0.0, 100.0), (350.0,), source="s", citation="c")


def test_envelope_rejects_non_ascending_breakpoints():
    with pytest.raises(RuleConfigError):
        PowerEnvelope((0.0, 300.0, 300.0), (350.0, 350.0, 0.0), source="s", citation="c")


def test_envelope_rejects_negative_power():
    with pytest.raises(RuleConfigError):
        PowerEnvelope((0.0, 300.0), (350.0, -1.0), source="s", citation="c")


def test_envelope_rejects_a_single_point():
    with pytest.raises(RuleConfigError):
        PowerEnvelope((0.0,), (350.0,), source="s", citation="c")


def test_derivation_rejects_a_cutoff_below_the_taper_start():
    with pytest.raises(RuleConfigError):
        derive_envelope_from_reported_formula(
            ReportedFormula(
                intercept_kw=1850.0, slope_kw_per_kmh=-5.0, cutoff_kmh=100.0,
                peak_cap_kw=350.0, source="s", citation="c",
            )
        )


# ---------------------------------------------------------------------------
# The curve is configuration, not code (section 20.1)
# ---------------------------------------------------------------------------
def test_replacing_the_curve_changes_the_answer_without_touching_code():
    corrected = PowerEnvelope(
        breakpoints_kmh=(0.0, 250.0, 320.0),
        max_power_kw=(400.0, 400.0, 0.0),
        source="hypothetical corrected document",
        citation="test",
    )
    swapped = with_power_envelope(RULES, MODE_NORMAL, corrected)
    assert max_electrical_power_kw(200.0, MODE_NORMAL, swapped) == pytest.approx(400.0)
    assert max_electrical_power_kw(320.0, MODE_NORMAL, swapped) == 0.0
    # the original is untouched: pure functions, no shared mutable state
    assert max_electrical_power_kw(200.0, MODE_NORMAL, RULES) == pytest.approx(350.0)


# ---------------------------------------------------------------------------
# UI.md section 6.4 / API.md section 5.3a: the sampled curve table
#
# The table exists so no consumer implements the curve a second time (section 32). Its
# entire value is the identity below: a sampled power IS max_electrical_power_kw at that
# speed. Everything else here guards the grid the identity is asserted on.
# ---------------------------------------------------------------------------
SAMPLED = sampled_curves_mapping(RULES)


@pytest.mark.parametrize("mode", MODES)
def test_every_sampled_value_equals_the_evaluator(mode):
    """THE guarantee: the number a UI reads off the table is the number the optimiser saw."""
    for point in SAMPLED["curves"][mode]:
        assert point["max_power_kw"] == max_electrical_power_kw(
            point["speed_kmh"], mode, RULES
        )


@pytest.mark.parametrize("mode", MODES)
def test_sample_grid_covers_every_breakpoint_exactly(mode):
    """Including the cliff pair at 339.999 / 340.0, which a 5 km/h step walks straight past."""
    speeds = [p["speed_kmh"] for p in SAMPLED["curves"][mode]]
    for v in envelope_breakpoints_kmh(mode, RULES):
        assert v in speeds, f"breakpoint {v} km/h is not a sample"


def test_sample_grid_is_shared_by_both_modes():
    """Same x set for both curves, so a consumer compares caps by index (the /lab readout)."""
    speeds = {mode: [p["speed_kmh"] for p in SAMPLED["curves"][mode]] for mode in MODES}
    assert speeds[MODE_NORMAL] == speeds[MODE_OVERRIDE] == list(sampled_speeds_kmh(RULES))


def test_sample_grid_runs_the_declared_step_from_zero_to_the_top_breakpoint():
    speeds = list(sampled_speeds_kmh(RULES))
    top = max(max(envelope_breakpoints_kmh(m, RULES)) for m in MODES)
    assert speeds[0] == 0.0
    assert speeds[-1] == top == 355.0
    assert speeds == sorted(set(speeds)), "the grid must be ascending and free of duplicates"
    step = SAMPLED["step_kmh"]
    assert step == DEFAULT_SAMPLE_STEP_KMH
    for i in range(int(top / step) + 1):          # every step multiple is present
        assert i * step in speeds
    extra = [v for v in speeds if abs(v / step - round(v / step)) > 1e-9]
    assert set(extra) <= {337.5, 340.0 - CLIFF_EPSILON_KMH}, (
        "the only off-step samples may be breakpoints"
    )


def test_a_slider_stepping_at_5_always_lands_on_a_sample():
    """The /lab calculator's contract: it looks up, it never interpolates."""
    table = {p["speed_kmh"]: p["max_power_kw"] for p in SAMPLED["curves"][MODE_OVERRIDE]}
    for i in range(0, 72):
        assert float(i * 5) in table


def test_the_table_stays_small():
    for mode in MODES:
        assert len(SAMPLED["curves"][mode]) < 100


def test_sampled_curves_ride_in_the_config_mapping():
    m = event_rules_to_mapping(RULES)
    assert m["sampled_curves"]["curves"].keys() == {MODE_NORMAL, MODE_OVERRIDE}
    assert m["sampled_curves"] == SAMPLED


def test_sampled_curves_round_trip_through_the_mapping():
    """from_mapping -> to_mapping reproduces the block, so an artifact rebuilt from a
    config file carries the same table as one built from the dataclasses."""
    m = event_rules_to_mapping(RULES)
    again = event_rules_to_mapping(event_rules_from_mapping(m))
    assert again["sampled_curves"] == m["sampled_curves"]


def test_sampled_block_is_badged_unverified_and_tagged_rule():
    assert SAMPLED["provenance"] == "RULE"
    assert SAMPLED["verified"] is False
    assert "UNVERIFIED" in SAMPLED["note"]


def test_sampled_separation_speed_is_the_engine_value():
    assert SAMPLED["separation_speed_kmh"] == envelope_separation_speed_kmh(RULES)


def test_modes_are_indistinguishable_in_the_table_at_and_below_the_separation_speed():
    """A UI reading only the table must reach the same conclusion as section 20.2."""
    sep = SAMPLED["separation_speed_kmh"]
    n = {p["speed_kmh"]: p["max_power_kw"] for p in SAMPLED["curves"][MODE_NORMAL]}
    o = {p["speed_kmh"]: p["max_power_kw"] for p in SAMPLED["curves"][MODE_OVERRIDE]}
    below = [v for v in n if v <= sep]
    above = [v for v in n if v > sep]
    assert all(n[v] == o[v] for v in below), "override must confer nothing below separation"
    assert any(o[v] > n[v] for v in above), "the table must show the curves parting"


@pytest.mark.parametrize("step", [1.0, 2.5, 10.0, 355.0])
def test_a_custom_step_keeps_the_identity_and_the_breakpoints(step):
    block = sampled_curves_mapping(RULES, step_kmh=step)
    assert block["step_kmh"] == step
    for mode in MODES:
        points = block["curves"][mode]
        speeds = [p["speed_kmh"] for p in points]
        assert speeds[0] == 0.0 and speeds[-1] == 355.0
        for v in envelope_breakpoints_kmh(mode, RULES):
            assert v in speeds
        for p in points:
            assert p["max_power_kw"] == max_electrical_power_kw(p["speed_kmh"], mode, RULES)


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
def test_a_non_positive_or_non_finite_step_refuses_to_sample(bad):
    with pytest.raises(RuleConfigError):
        sampled_speeds_kmh(RULES, step_kmh=bad)


def test_sampling_follows_a_corrected_curve_from_configuration():
    """Section 20.1: the shape is config. Swap a curve and the table must move with it."""
    flat = PowerEnvelope(
        breakpoints_kmh=(0.0, 200.0),
        max_power_kw=(120.0, 60.0),
        source="test",
        citation="test",
    )
    rules = with_power_envelope(RULES, MODE_NORMAL, flat)
    block = sampled_curves_mapping(rules)
    normal = {p["speed_kmh"]: p["max_power_kw"] for p in block["curves"][MODE_NORMAL]}
    assert normal[0.0] == pytest.approx(120.0, abs=TOL)
    assert normal[100.0] == pytest.approx(90.0, abs=TOL)
    assert normal[200.0] == pytest.approx(60.0, abs=TOL)
    assert normal[355.0] == pytest.approx(60.0, abs=TOL)     # clamped past the last point
    assert 200.0 in normal, "the replaced curve's breakpoints must join the grid"


def test_unknown_mode_is_not_sampled_into_a_default_curve():
    with pytest.raises(UnknownModeError):
        sample_envelope_curve("overtake", RULES)
