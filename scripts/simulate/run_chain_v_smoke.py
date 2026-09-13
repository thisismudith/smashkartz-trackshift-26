#!/usr/bin/env python3
"""Run deterministic development-only CP-10 through CP-15 smoke evidence.

The callbacks below deliberately model the public C3/C4/C5 shapes on a tiny
synthetic fixture.  They exercise contract plumbing and must never be used as
final planner, simulator, or replay evidence.
"""
from __future__ import annotations

import json
from statistics import quantiles
from time import perf_counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.planner.api import generate_baseline_plans, plan
from trackshift.sim.api import policy_registry, simulate
from trackshift.value.api import DPConfig, shadow_price, solve_dp
from trackshift.value.counterattack import evaluate_counterattack


RULES = {"test": True}
SEGMENTS = [
    {"segment_id": 1, "speed_kmh": 250.0, "kind": "STRAIGHT", "lap": 1},
    {"segment_id": 2, "speed_kmh": 200.0, "kind": "CORNER", "lap": 1},
]
STATE = {
    "energy": {"ers_soc_est_mj": {"value": 2.0, "provenance": "SIMULATED", "unit": "MJ"}},
    "gap": {"time_gap_s": {"value": 0.5, "provenance": "DERIVED", "unit": "s"}},
    "speed_kmh": 250.0,
    "overtake_state": {"state": "NOT_ARMED"},
    "ref": {"distance_m": 100.0},
}


def legal_actions(state: dict, rules: dict) -> dict:
    del state, rules
    return {
        "actions": [
            {"deploy_level": 0.0, "lift_amount": 0.0},
            {"deploy_level": 0.5, "lift_amount": 0.0},
            {"deploy_level": 1.0, "lift_amount": 0.0},
        ],
        "excluded": [{"action": {"deploy_level": 0.75, "lift_amount": 0.0}, "rule": "test-limit"}],
        "provenance": "RULE",
    }


def transition(state: dict, action: dict, segment: dict, *extra: object) -> dict:
    del segment, extra
    deploy = float(action["deploy_level"])
    return {
        "energy_mj": float(state["energy"]["ers_soc_est_mj"]["value"]) - deploy * 0.1,
        "gap_s": float(state["gap"]["time_gap_s"]["value"]) - deploy * 0.2,
        "eligibility": 0,
        "time_delta_s": 0.1 - deploy * 0.03,
    }


def pass_fn(state: dict, ours: dict, rival: dict, segment: dict, environment: dict) -> dict:
    del state, rival, segment, environment
    return {"passed": float(ours["deploy_level"]) >= 0.5, "repassed": False}


def main() -> int:
    dp = solve_dp(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions,
                  config=DPConfig(model_versions={"c5": "development-test-callback"}))
    shadow = shadow_price(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions)
    safe = evaluate_counterattack(STATE, pass_prediction={"p_pass_by_outcome_horizon": 0.9},
                                  repass_prediction={"p_repass_within_horizon": 0.1})
    exposed = evaluate_counterattack(STATE, pass_prediction={"p_pass_by_outcome_horizon": 0.9},
                                     repass_prediction={"p_repass_within_horizon": 0.9})
    latencies: list[float] = []
    planner = None
    for _ in range(100):
        started = perf_counter()
        planner = plan(SEGMENTS, STATE, RULES, transition_fn=transition, legal_actions_fn=legal_actions)
        latencies.append((perf_counter() - started) * 1000)
    baselines = generate_baseline_plans(SEGMENTS, STATE, RULES, legal_actions_fn=legal_actions)
    episode = simulate(STATE, SEGMENTS, RULES, our_policy="beam_dp", rival_policy="DEFEND_MIRROR", n_episodes=8,
                       seed=17, transition_fn=transition, pass_fn=pass_fn, legal_actions_fn=legal_actions)
    stub = simulate(STATE, SEGMENTS, RULES)
    output = {
        "development_only": True,
        "final_mode_permitted": False,
        "cp10": {"status": dp.status, "value": dp.value, "excluded_action_sets": len(dp.excluded_actions),
                 "shadow_status": shadow["status"], "shadow_same_state": shadow["same_full_state_except_energy"],
                 "shadow_time_based": shadow["time_based_shadow_price"]["value"]},
        "cp11": {"safe": safe["explanation"], "exposed": exposed["explanation"],
                 "safe_value": safe["terminal_value"], "exposed_value": exposed["terminal_value"]},
        "cp12": {"status": planner["status"] if planner else "UNAVAILABLE", "rule_violations": planner["rule_violations"] if planner else None,
                 "latency_ms_p50": sorted(latencies)[49], "latency_ms_p95": quantiles(latencies, n=100)[94]},
        "cp13": {"names": sorted(baselines["baselines"]),
                 "all_actions_legal": all(action in legal_actions(STATE, RULES)["actions"] for actions in baselines["baselines"].values() for action in actions if "upper_bound_only" not in action)},
        "cp14": {"status": episode["status"], "episodes": episode["summary"]["n_episodes"],
                 "rule_violations": episode["summary"]["rule_violations"], "seed": episode["summary"]["seed"], "stub_status": stub["status"]},
        "cp15": {"policies": [item["name"] for item in policy_registry()["policies"]]},
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
