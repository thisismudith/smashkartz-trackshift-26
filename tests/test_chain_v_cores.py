from __future__ import annotations

from pathlib import Path
from random import Random
import sys
import json

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.planner.api import generate_baseline_plans, plan  # noqa: E402
from trackshift.rival.api import evaluate_era_strategies, materialise_historical_m08  # noqa: E402
from trackshift.sim.api import UnknownPolicyError, choose_policy_action, policy_registry, simulate  # noqa: E402
from trackshift.value.api import DPConfig, load_dp_config, shadow_price, solve_dp  # noqa: E402
from trackshift.value.counterattack import evaluate_counterattack  # noqa: E402


RULES = {"test": True}
SEGMENTS = [{"segment_id": 1, "speed_kmh": 250.0, "kind": "STRAIGHT", "lap": 1}, {"segment_id": 2, "speed_kmh": 200.0, "kind": "CORNER", "lap": 1}]
STATE = {"energy": {"ers_soc_est_mj": {"value": 2.0, "provenance": "SIMULATED", "unit": "MJ"}}, "gap": {"time_gap_s": {"value": 0.5, "provenance": "DERIVED", "unit": "s"}}, "speed_kmh": 250.0, "overtake_state": {"state": "NOT_ARMED"}, "ref": {"distance_m": 100.0}}


def legal_actions(state, rules):
    return {"actions": [{"deploy_level": 0.0, "lift_amount": 0.0}, {"deploy_level": 0.5, "lift_amount": 0.0}, {"deploy_level": 1.0, "lift_amount": 0.0}], "excluded": [{"rule": "test-limit"}], "provenance": "RULE"}


def transition(state, action, segment, *extra):
    energy = float(state["energy"]["ers_soc_est_mj"]["value"]) - float(action["deploy_level"]) * 0.1
    gap = float(state["gap"]["time_gap_s"]["value"]) - float(action["deploy_level"]) * 0.2
    return {"energy_mj": energy, "gap_s": gap, "eligibility": 0, "time_delta_s": 0.1 - float(action["deploy_level"]) * 0.03}


def pass_fn(state, ours, rival, segment, environment):
    return {"passed": ours["deploy_level"] >= 0.5, "repassed": False}


def test_cp08_is_fail_closed_without_historical_m08(tmp_path: Path):
    audit = materialise_historical_m08(processed_root=tmp_path)
    assert audit["status"] == "BLOCKED"
    report = evaluate_era_strategies([{"year": 2026, "event": "Australian Grand Prix"}], split_version="c9", rule_configuration_version="rules-v1", materialisation=audit)
    assert report["status"] == "BLOCKED" and report["selected"] == "2026_only"
    assert report["historical_drs_is_not_2026_overtake"]
    with pytest.raises(ValueError):
        evaluate_era_strategies([{"year": 2026, "event": "British Grand Prix", "training": True}], split_version="c9", rule_configuration_version="r")


def test_dp_uses_c3_actions_and_shadow_price_same_state():
    result = solve_dp(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions, config=DPConfig(model_versions={"c5": "test"}))
    assert result.status == "COMPLETE" and result.value is not None
    assert result.excluded_actions and result.metadata["c3_public_boundary"]
    price = shadow_price(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions)
    assert price["same_full_state_except_energy"] and price["marginal_value_per_mj"] is not None
    assert "value_s_per_mj" not in price and price["time_based_shadow_price"]["value"] is None
    blocked = solve_dp(SEGMENTS, STATE, RULES, transition_fn=None, legal_actions_fn=legal_actions)
    assert blocked.status == "STUB_RESPONSE" and blocked.value is None


def test_counterattack_can_make_a_pass_worse_and_is_not_ground_truth():
    safe = evaluate_counterattack(STATE, pass_prediction={"p_pass_by_outcome_horizon": 0.9}, repass_prediction={"p_repass_within_horizon": 0.1})
    exposed = evaluate_counterattack(STATE, pass_prediction={"p_pass_by_outcome_horizon": 0.9}, repass_prediction={"p_repass_within_horizon": 0.9})
    assert exposed["terminal_value"] < safe["terminal_value"]
    assert exposed["explanation"] == "PASS_BUT_EXPOSED" and exposed["explanation_is_not_ground_truth"]
    assert evaluate_counterattack(STATE)["status"] == "STUB_RESPONSE"


def test_planner_and_baselines_only_return_legal_actions():
    result = plan(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions)
    assert result["rule_violations"] == 0 and result["recommended_action"] in legal_actions(STATE, RULES)["actions"]
    baselines = generate_baseline_plans(SEGMENTS, STATE, RULES, legal_actions_fn=legal_actions)
    assert set(baselines["baselines"]) == {"greedy_attack", "longest_straight", "lap_time_only", "dp", "beam_dp", "oracle_rival_state"}
    assert all(action in legal_actions(STATE, RULES)["actions"] for actions in baselines["baselines"].values() for action in actions if "upper_bound_only" not in action)
    assert baselines["baselines"]["oracle_rival_state"][0]["deployable"] is False


def test_policies_and_simulator_are_deterministic_and_ui_ready():
    assert {item["name"] for item in policy_registry()["policies"]} == {"DEFEND_CONSERVE", "DEFEND_MIRROR", "ATTACK_GREEDY"}
    with pytest.raises(UnknownPolicyError):
        choose_policy_action("NOPE", STATE, RULES, legal_actions_fn=legal_actions)
    first = simulate(STATE, SEGMENTS, RULES, our_policy="beam_dp", rival_policy="DEFEND_MIRROR", n_episodes=3, seed=17, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    second = simulate(STATE, SEGMENTS, RULES, our_policy="beam_dp", rival_policy="DEFEND_MIRROR", n_episodes=3, seed=17, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    assert first == second and first["provenance"] == "SIMULATED" and first["summary"]["rule_violations"] == 0
    assert simulate(STATE, SEGMENTS, RULES, transition_fn=None, pass_fn=None)["status"] == "STUB_RESPONSE"


def test_simulated_episodes_are_trajectories_and_declare_when_they_are_not_a_distribution():
    """The simulator used to copy the transition's flat keys onto a state read by the
    nested path, so every segment re-ran from the initial energy and gap: the store
    rose while deploying and the gap never moved. `transition` here subtracts a fixed
    amount per segment, so a real trajectory can only read 1.95/1.90 MJ and 0.40/0.30 s.

    The episode count is asserted alongside it because a set of identical rollouts is
    not a sample: `p_ahead_at_horizon` over it would be a 0 or a 1 dressed as a
    frequency over futures, so it is null with a reason until the supplied callbacks
    actually respond to the per-episode environment.
    """
    run = simulate(STATE, SEGMENTS, RULES, our_policy="beam_dp", rival_policy="DEFEND_CONSERVE", n_episodes=3, seed=17, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    trace = run["episodes"][0]["trace"]
    assert [round(step["energy_mj"], 4) for step in trace] == [1.95, 1.90]
    assert [round(step["gap_s"], 4) for step in trace] == [0.40, 0.30]
    assert run["summary"]["n_distinct_episodes"] == 1
    assert run["summary"]["p_ahead_at_horizon"] is None
    assert "one deterministic rollout" in run["summary"]["p_ahead_at_horizon_reason"]

    def environment_sensitive(state, action, segment, *extra):
        moved = transition(state, action, segment)
        moved["gap_s"] += Random(extra[0]["seed"]).uniform(-0.05, 0.05)
        return moved

    sampled = simulate(STATE, SEGMENTS, RULES, our_policy="beam_dp", rival_policy="DEFEND_CONSERVE", n_episodes=3, seed=17, transition_fn=environment_sensitive, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    assert sampled["summary"]["n_distinct_episodes"] == 3
    assert isinstance(sampled["summary"]["p_ahead_at_horizon"], float)
    assert "p_ahead_at_horizon_reason" not in sampled["summary"]
    assert sampled == simulate(STATE, SEGMENTS, RULES, our_policy="beam_dp", rival_policy="DEFEND_CONSERVE", n_episodes=3, seed=17, transition_fn=environment_sensitive, pass_fn=pass_fn, legal_actions_fn=legal_actions)


def test_an_our_policy_the_simulator_cannot_run_is_refused_not_substituted():
    """An unrecognised our_policy used to fall through to the middle of the C3 legal
    set, and the response never said so: the panel would report a different policy's
    trajectory under the name that was asked for. DEFEND_MIRROR is recognised but
    needs an opponent action that is only chosen after ours, so it is refused too.
    """
    refused = simulate(STATE, SEGMENTS, RULES, our_policy="NOPE", rival_policy="DEFEND_CONSERVE", n_episodes=2, seed=17, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    assert refused["status"] == "UNAVAILABLE" and refused["our_policy"] == "NOPE" and "NOPE" in refused["reason"]
    assert refused["episodes"] == []
    mirror = simulate(STATE, SEGMENTS, RULES, our_policy="DEFEND_MIRROR", rival_policy="DEFEND_CONSERVE", n_episodes=2, seed=17, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    assert mirror["status"] == "UNAVAILABLE" and "opponent action" in mirror["reason"]
    fallback = simulate(STATE, SEGMENTS, RULES, our_policy="beam_dp", rival_policy="DEFEND_CONSERVE", n_episodes=1, seed=17, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    assert fallback["our_policy"] == "beam_dp" and fallback["our_policy_resolved"] == "C3_MIDPOINT_FALLBACK"
    assert any("beam_dp was not run" in note for note in fallback["assumptions"])
    ran = simulate(STATE, SEGMENTS, RULES, our_policy="ATTACK_GREEDY", rival_policy="DEFEND_CONSERVE", n_episodes=1, seed=17, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    assert ran["status"] == "COMPLETE" and ran["our_policy_resolved"] == "POLICY"


@pytest.mark.parametrize("field", ["energy", "gap", "eligibility"])
def test_missing_required_state_inputs_never_become_defaults(field):
    state = json.loads(json.dumps(STATE))
    if field == "energy":
        state["energy"]["ers_soc_est_mj"] = {"value": None, "unit": "MJ", "provenance": "SIMULATED", "reason": "not supplied"}
    elif field == "gap":
        state["gap"]["time_gap_s"] = {"value": None, "unit": "s", "provenance": "DERIVED", "reason": "not supplied"}
    else:
        state.pop("overtake_state")
    result = solve_dp(SEGMENTS, state, RULES, transition_fn=transition, legal_actions_fn=legal_actions)
    assert result.status == "UNAVAILABLE" and result.value is None
    assert field in result.reason
    assert result.policy == {}


def test_missing_c3_does_not_create_actions():
    result = solve_dp(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=lambda *_: None)
    assert result.status == "UNAVAILABLE" and result.policy == {}


def test_grid_and_utility_values_are_loaded_from_versioned_config():
    loaded = load_dp_config()
    cfg = DPConfig()
    assert loaded["development_only"] is True
    assert cfg.config_version == loaded["schema_version"]
    assert list(cfg.energy_grid_mj) == list(loaded["energy_grid_mj"])
    assert cfg.terminal_utility_definition == loaded["terminal_utility"]
    assert cfg.shadow_delta_energy_mj == loaded["shadow_finite_difference_delta_mj"]


def test_final_mode_rejects_abstract_or_stub_outputs():
    with pytest.raises(ValueError):
        solve_dp(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions, final_mode=True)
    with pytest.raises(ValueError):
        shadow_price(SEGMENTS, STATE, RULES, transition_fn=None, legal_actions_fn=legal_actions, final_mode=True)
    with pytest.raises(ValueError):
        plan(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions, final_mode=True)


def test_public_outputs_are_json_safe_and_finite():
    outputs = [
        solve_dp(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions).to_dict(),
        shadow_price(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions),
        plan(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions),
        generate_baseline_plans(SEGMENTS, STATE, RULES, legal_actions_fn=legal_actions),
        simulate(STATE, SEGMENTS, RULES, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions),
    ]
    for output in outputs:
        encoded = json.dumps(output, allow_nan=False)
        assert "NaN" not in encoded and "Infinity" not in encoded
