from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.planner.api import generate_baseline_plans, plan  # noqa: E402
from trackshift.rival.api import evaluate_era_strategies, materialise_historical_m08  # noqa: E402
from trackshift.sim.api import UnknownPolicyError, choose_policy_action, policy_registry, simulate  # noqa: E402
from trackshift.value.api import DPConfig, shadow_price, solve_dp  # noqa: E402
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
    assert price["same_full_state_except_energy"] and price["value_s_per_mj"] is not None
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
