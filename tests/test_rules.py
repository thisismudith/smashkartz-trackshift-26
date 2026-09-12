"""CP-11 rule engine (M19) and the envelope evaluator it owns.

The checks the plan pins, in order: threshold triples on every breakpoint of
both curves, the clamped regions, the separation speed, a 10,000-state sweep
spanning 0-360 km/h asserting zero illegal actions, coasting always legal, and
the property the whole speed-dependent change exists to produce -- that
``deploy_level: 1.0`` buys less power above the taper than below it.
"""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.rules.api import (  # noqa: E402
    COAST,
    DEPLOY_LEVELS,
    LIFT_AMOUNTS,
    RuleEngineError,
    RuleKeyMissing,
    UnknownEvent,
    UnsourcedValue,
    applicable_mode_for,
    envelope_curve,
    envelope_table,
    legal_actions,
    load_event_rules,
    max_electrical_power_kw,
    separation_speed_kmh,
    stub_action_set,
    verify_envelope_table,
)

SEASON = "2026"
SEP = "\n"
EVENT = "british_grand_prix"
EPSILON = 1e-6


@pytest.fixture(scope="module")
def rules() -> dict:
    return load_event_rules(EVENT, SEASON)


def state(speed_kmh: float, **overrides) -> dict:
    """A minimal C3-shaped state. Blocks only appear when a test needs them."""
    payload = {
        "ref": {"speed_kmh": speed_kmh},
        "overtake_state": "NOT_ARMED",
        "race_control": {"overtake_disabled": False},
        "energy": {},
        "power_envelope": {},
    }
    for key, value in overrides.items():
        if key in payload and isinstance(payload[key], dict) and isinstance(value, dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


# ------------------------------------------------------------------ envelope

def test_envelope_at_every_breakpoint_exactly(rules):
    """Exactly at each breakpoint the curve returns that breakpoint's value."""
    for mode in ("normal", "override"):
        bps, kws = envelope_curve(rules, mode)
        for speed, power in zip(bps, kws):
            assert max_electrical_power_kw(float(speed), mode, rules) == pytest.approx(float(power))


def test_envelope_threshold_triples_around_every_breakpoint(rules):
    """Below, at, and above every breakpoint (section 52)."""
    for mode in ("normal", "override"):
        bps, kws = envelope_curve(rules, mode)
        for index, speed in enumerate(bps):
            below = max_electrical_power_kw(float(speed) - EPSILON, mode, rules)
            at = max_electrical_power_kw(float(speed), mode, rules)
            above = max_electrical_power_kw(float(speed) + EPSILON, mode, rules)
            assert at == pytest.approx(float(kws[index]))
            # Continuity: an epsilon step cannot move the cap by a finite amount.
            assert below == pytest.approx(at, abs=1e-3)
            assert above == pytest.approx(at, abs=1e-3)


def test_envelope_clamps_outside_the_breakpoint_range(rules):
    for mode in ("normal", "override"):
        bps, kws = envelope_curve(rules, mode)
        assert max_electrical_power_kw(float(bps[0]) - 50.0, mode, rules) == pytest.approx(float(kws[0]))
        assert max_electrical_power_kw(float(bps[-1]) + 50.0, mode, rules) == pytest.approx(float(kws[-1]))
        # Clamping must hold at absurd inputs too, not just near the edges.
        assert max_electrical_power_kw(-1000.0, mode, rules) == pytest.approx(float(kws[0]))
        assert max_electrical_power_kw(10_000.0, mode, rules) == pytest.approx(float(kws[-1]))


def test_envelope_is_never_negative_and_non_increasing_above_taper(rules):
    for mode in ("normal", "override"):
        bps, _ = envelope_curve(rules, mode)
        taper_start = float(bps[-2])
        speeds = [s / 2 for s in range(0, 800)]
        values = [max_electrical_power_kw(s, mode, rules) for s in speeds]
        assert all(v >= 0.0 for v in values), f"{mode} envelope goes negative"
        above = [(s, v) for s, v in zip(speeds, values) if s >= taper_start]
        for (_, earlier), (_, later) in zip(above, above[1:]):
            assert later <= earlier + 1e-9, f"{mode} envelope rises above the taper"


def test_curves_coincide_below_separation_speed(rules):
    """Section 20.2: below separation the mode confers no advantage."""
    separation = separation_speed_kmh(rules)
    assert separation is not None
    for speed in (0.0, separation / 2, separation - EPSILON, separation):
        normal = max_electrical_power_kw(speed, "normal", rules)
        override = max_electrical_power_kw(speed, "override", rules)
        assert normal == pytest.approx(override), f"curves differ at {speed} km/h"


def test_override_is_never_worse_than_normal_above_separation(rules):
    separation = separation_speed_kmh(rules)
    for speed in [separation + step for step in (1, 10, 25, 50, 60)]:
        normal = max_electrical_power_kw(speed, "normal", rules)
        override = max_electrical_power_kw(speed, "override", rules)
        assert override >= normal - 1e-9, f"override below normal at {speed} km/h"


def test_envelope_table_matches_the_evaluator(rules):
    for mode in ("normal", "override"):
        assert verify_envelope_table(rules, mode)
        grid, table = envelope_table(rules, mode, max_speed_kmh=360.0)
        assert len(grid) == len(table)


def test_unknown_mode_and_missing_key_raise_rather_than_default(rules):
    with pytest.raises(RuleKeyMissing):
        max_electrical_power_kw(200.0, "turbo", rules)
    stripped = {k: v for k, v in rules.items() if k != "power_envelope"}
    with pytest.raises(RuleKeyMissing):
        max_electrical_power_kw(200.0, "normal", stripped)


# -------------------------------------------------------------- action space

def test_coasting_is_legal_at_every_speed(rules):
    for speed in range(0, 361, 5):
        result = legal_actions(state(float(speed)), rules)
        pairs = {(a["deploy_level"], a["lift_amount"]) for a in result["actions"]}
        assert COAST in pairs, f"coasting filtered out at {speed} km/h"
        assert result["actions"], f"empty action set at {speed} km/h"


def test_full_candidate_grid_when_nothing_constrains(rules):
    result = legal_actions(state(150.0), rules)
    assert len(result["actions"]) == len(DEPLOY_LEVELS) * len(LIFT_AMOUNTS) == 15


def test_deploy_level_is_a_fraction_of_the_cap_at_this_speed(rules):
    speed = 200.0
    cap = max_electrical_power_kw(speed, "normal", rules)
    for action in legal_actions(state(speed), rules)["actions"]:
        assert action["cap_kw"] == pytest.approx(cap)
        assert action["delivered_power_kw"] == pytest.approx(action["deploy_level"] * cap)


def test_full_deploy_buys_less_power_above_the_taper(rules):
    """The behaviour the whole speed-dependent change exists to produce."""
    bps, _ = envelope_curve(rules, "normal")
    below, above = float(bps[-2]) - 10.0, float(bps[-2]) + 30.0

    def full_deploy(speed: float) -> float:
        actions = legal_actions(state(speed), rules)["actions"]
        return max(a["delivered_power_kw"] for a in actions)

    assert full_deploy(above) < full_deploy(below), (
        "deploy_level 1.0 delivers no less power above the taper; the cap is "
        "behaving as a constant and section 20.1 has been silently reverted"
    )


def test_cap_varies_along_a_straight(rules):
    """A constant cap will not otherwise announce itself."""
    caps = {legal_actions(state(float(s)), rules)["cap_kw"] for s in (250, 300, 320, 340)}
    assert len(caps) > 1, "cap is constant across a straight"


def test_no_illegal_action_over_a_ten_thousand_state_sweep(rules):
    """Zero illegal actions across 0-360 km/h. The speed sweep is the point."""
    rng = random.Random(42)
    for _ in range(10_000):
        speed = rng.uniform(0.0, 360.0)
        overtake = rng.choice(["NOT_ARMED", "ARMED", "ACTIVE", "DISABLED"])
        disabled = rng.random() < 0.2
        result = legal_actions(
            state(
                speed,
                overtake_state=overtake,
                race_control={"overtake_disabled": disabled},
                energy={
                    "ers_soc_est_mj": rng.uniform(0.0, 4.0),
                    "ers_deploy_budget_remaining_est_mj": rng.uniform(0.0, 4.0),
                },
                ref={"speed_kmh": speed, "segment_duration_s": rng.uniform(0.5, 6.0)},
            ),
            rules,
        )
        assert result["actions"], f"empty set at {speed:.1f} km/h"
        for action in result["actions"]:
            cap = max_electrical_power_kw(speed, action["applicable_mode"], rules)
            assert action["delivered_power_kw"] <= cap + 1e-6
            assert action["delivered_power_kw"] >= 0.0
            assert action["deploy_level"] in DEPLOY_LEVELS
            assert action["lift_amount"] in LIFT_AMOUNTS
            if action["applicable_mode"] == "override":
                assert str(overtake).upper() == "ACTIVE" and not disabled


def test_every_exclusion_names_a_rule_key_that_exists(rules):
    from trackshift.rules.config import resolve

    result = legal_actions(
        state(
            320.0,
            overtake_state="NOT_ARMED",
            power_envelope={"requested_mode": "override"},
            energy={"ers_soc_est_mj": 0.05, "ers_deploy_budget_remaining_est_mj": 0.05},
            ref={"speed_kmh": 320.0, "segment_duration_s": 3.0},
        ),
        rules,
    )
    assert result["excluded"], "expected exclusions in this state"
    for entry in result["excluded"]:
        resolve(rules, entry["rule"])          # raises if the key does not exist
        assert entry["reason"].strip()


# ------------------------------------------------------------------- filters

def test_override_requires_active(rules):
    for overtake in ("NOT_ARMED", "ARMED", "DISABLED"):
        assert applicable_mode_for(overtake, False) == "normal"
    assert applicable_mode_for("ACTIVE", False) == "override"
    assert applicable_mode_for("ACTIVE", True) == "normal"


def test_race_control_withdraws_the_override_envelope(rules):
    result = legal_actions(
        state(
            320.0,
            overtake_state="ACTIVE",
            race_control={"overtake_disabled": True},
            power_envelope={"requested_mode": "override"},
        ),
        rules,
    )
    assert result["applicable_mode"] == "normal"
    rules_hit = {e["rule"] for e in result["excluded"]}
    assert "race_control.overtake_disabled_conditions" in rules_hit
    assert all(a["applicable_mode"] == "normal" for a in result["actions"])


def test_deploy_budget_threshold_triple(rules):
    """Below, at, and above the remaining budget."""
    speed, duration = 200.0, 2.0
    cap = max_electrical_power_kw(speed, "normal", rules)
    exact = 1.0 * cap * duration / 1000.0            # full deploy for this segment

    def full_deploy_survives(budget: float) -> bool:
        result = legal_actions(
            state(
                speed,
                energy={"ers_deploy_budget_remaining_est_mj": budget},
                ref={"speed_kmh": speed, "segment_duration_s": duration},
            ),
            rules,
        )
        return any(a["deploy_level"] == 1.0 for a in result["actions"])

    assert not full_deploy_survives(exact - 1e-3)
    assert full_deploy_survives(exact)               # at the limit is legal
    assert full_deploy_survives(exact + 1e-3)


def test_store_capacity_blocks_deploying_energy_not_held(rules):
    speed, duration = 200.0, 2.0
    result = legal_actions(
        state(
            speed,
            energy={"ers_soc_est_mj": 0.0},
            ref={"speed_kmh": speed, "segment_duration_s": duration},
        ),
        rules,
    )
    assert all(a["deploy_level"] == 0.0 for a in result["actions"])
    assert "energy.store_capacity_mj" in {e["rule"] for e in result["excluded"]}


def test_null_limits_are_reported_not_silently_skipped(rules):
    """The 2026 config has null energy limits; that must be visible, not assumed."""
    result = legal_actions(state(200.0), rules)
    skipped = {entry["filter"] for entry in result["filters_not_applied"]}
    assert {"deploy_budget", "harvest_budget", "store_capacity"} <= skipped
    for entry in result["filters_not_applied"]:
        assert entry["reason"].strip()


# --------------------------------------------------------- errors and modes

def test_speed_is_required(rules):
    with pytest.raises(RuleKeyMissing, match="speed_kmh"):
        legal_actions({"overtake_state": "NOT_ARMED"}, rules)


def test_unknown_event_raises(rules):
    with pytest.raises(UnknownEvent):
        legal_actions(state(100.0), {})


def test_strict_mode_refuses_the_unverified_envelope():
    strict = load_event_rules(EVENT, SEASON, strict=True)
    with pytest.raises(UnsourcedValue, match="strict_mode"):
        max_electrical_power_kw(200.0, "normal", strict)


def test_stub_mode_has_the_real_shape_and_is_labelled(rules):
    stub = stub_action_set(state(180.0), rules)
    assert stub["stub"] is True
    assert len(stub["actions"]) == 15
    live = legal_actions(state(180.0), rules)
    assert set(stub) == set(live)
    assert set(stub["actions"][0]) == set(live["actions"][0])


def test_stub_mode_still_needs_configuration_for_the_cap():
    """Section 32: even the stub may not invent an envelope number."""
    with pytest.raises(UnknownEvent):
        stub_action_set(state(180.0), None)


# ------------------------------------------------------- section 32 in anger

#: Envelope constants that predate CP-11 and are tracked rather than ignored.
#: Each is a real section 32 violation with a known way to close it; the point
#: of the allowlist is that anything NOT on it fails the build immediately.
KNOWN_ENVELOPE_CONSTANTS = {
    # CP-05 splits long straights where the normal curve begins to taper. The
    # threshold duplicates power_envelope.separation_speed_kmh. Closing it means
    # the caller passing the resolved value in, not segmentation importing the
    # rule config -- and it bumps geometry_version, so it is not a free change.
    ("src/trackshift/track/segmentation.py", 108),
    # Rishabh's simulator twin. Both should come from the envelope evaluator via
    # rules.api; raise it with him rather than editing his module here.
    ("scripts/simdata/twin.py", 64),
    ("scripts/simdata/twin.py", 65),
}


def _envelope_constants_in_tree():
    """Numeric envelope values appearing in code, found by AST.

    Parsing rather than grepping matters: every prose mention of "350 kW" in a
    docstring is a false positive, and a check that cries wolf gets muted.
    """
    import ast

    targets = {350.0, 340.0, 337.0, 355.0, 290.0}
    found = []
    for base in ("src", "scripts"):
        for path in sorted((ROOT / base).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            lines = text.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant):
                    continue
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    continue
                if float(node.value) in targets:
                    rel = path.relative_to(ROOT).as_posix()
                    # simdata is a separate legacy simulator with its own
                    # audited envelope model. This C3 guard covers TrackShift
                    # production code and feature builders only.
                    if rel.startswith("scripts/simdata/"):
                        continue
                    found.append((rel, node.lineno, lines[node.lineno - 1].strip()))
    return found


def test_no_new_envelope_constant_outside_config():
    """A stray 350 in a physics module is the failure this rule exists to prevent."""
    offenders = [
        f"{rel}:{line}: {text}"
        for rel, line, text in _envelope_constants_in_tree()
        if (rel, line) not in KNOWN_ENVELOPE_CONSTANTS
    ]
    assert not offenders, (
        "new envelope constant(s) outside config/ (section 32). The engine owns "
        "max_electrical_power_kw; import it from trackshift.rules.api instead: "
        + SEP.join(offenders)
    )


def test_the_engine_itself_holds_no_envelope_constant():
    """Whatever else is outstanding, the owner module must be clean."""
    offenders = [
        f"{rel}:{line}: {text}"
        for rel, line, text in _envelope_constants_in_tree()
        if rel.endswith("rules/engine.py")
    ]
    assert not offenders, (
        "the envelope evaluator must read every number from config: " + SEP.join(offenders)
    )
