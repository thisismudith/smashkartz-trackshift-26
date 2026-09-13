#!/usr/bin/env python3
"""Run M13 and Chain V development checkpoints on a labelled synthetic fixture.

This is an evidence runner, not a claim that synthetic trajectories are real
telemetry. All generated values are written outside Git and marked simulated.
Final mode is intentionally not attempted because official rule/C4/C5 gates
remain unresolved.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.planner.api import generate_baseline_plans, plan  # noqa: E402
from trackshift.rival.api import rival_state  # noqa: E402
from trackshift.serve.fixture import (  # noqa: E402
    RULE_VERSION,
    synthetic_era_report,
    synthetic_pass,
    synthetic_rival_model,
    synthetic_rival_rows,
    synthetic_rules,
    synthetic_segments,
    synthetic_state,
    synthetic_transition,
)
from trackshift.serve.replay import build_replay_bundle  # noqa: E402
from trackshift.sim.api import policy_registry, simulate  # noqa: E402
from trackshift.value.api import DPConfig, shadow_price  # noqa: E402
from trackshift.value.counterattack import evaluate_counterattack  # noqa: E402


def run(out: Path, *, episodes: int = 8, seed: int = 17) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    segments = synthetic_segments()
    state = synthetic_state()
    rules = synthetic_rules()
    dp_config = DPConfig(rule_configuration_version=RULE_VERSION, model_versions={"c5": "synthetic-c5-v1"})

    started = perf_counter()
    planner_result = plan(segments, state, rules, transition_fn=synthetic_transition, dp_config=dp_config)
    latency_samples = [(perf_counter() - started) * 1000.0]
    for _ in range(24):
        started = perf_counter()
        plan(segments, state, rules, transition_fn=synthetic_transition, dp_config=dp_config)
        latency_samples.append((perf_counter() - started) * 1000.0)
    latency_samples.sort()
    p95 = latency_samples[min(len(latency_samples) - 1, int(0.95 * (len(latency_samples) - 1)))]

    shadow = shadow_price(segments, state, rules, transition_fn=synthetic_transition, config=dp_config)
    counterattack = evaluate_counterattack(
        state,
        pass_prediction={"p_pass_by_outcome_horizon": 0.82, "provenance": "SIMULATED"},
        repass_prediction={"p_repass_within_horizon": 0.18, "provenance": "SIMULATED"},
    )
    baselines = generate_baseline_plans(segments, state, rules)
    simulation = simulate(
        state, segments, rules, our_policy="beam_dp", rival_policy="DEFEND_CONSERVE",
        n_episodes=episodes, seed=seed, transition_fn=synthetic_transition,
        pass_fn=synthetic_pass,
    )
    replay_dir = out / "replay"
    replay_manifest = build_replay_bundle(replay_dir)
    rival_model = synthetic_rival_model()
    rival_rows = synthetic_rival_rows()
    rival_response = rival_state(rival_rows[:8], rival_model)

    result = {
        "schema_version": "trackshift_closure_synthetic_v1",
        "status": "SYNTHETIC_DEVELOPMENT_COMPLETE",
        "provenance": "SIMULATED",
        "synthetic_fixture": "trackshift-synthetic-closure-v1",
        "final_mode_permitted": False,
        "release_ready": False,
        "british_gp_used_for_training_or_calibration": False,
        "rule_configuration_version": RULE_VERSION,
        "checkpoints": {
            "M13": synthetic_era_report(),
            "M22": {"status": shadow["status"], "marginal_value_per_mj": shadow.get("marginal_value_per_mj"), "units": shadow.get("marginal_value_units"), "provenance": shadow.get("provenance")},
            "M23": {"status": counterattack["status"], "explanation": counterattack["explanation"], "terminal_value": counterattack["terminal_value"], "provenance": counterattack["provenance"]},
            "M24": planner_result,
            "M25": baselines,
            "M26": simulation,
            "M27": policy_registry(),
            "C10": rival_response,
        },
        "latency_ms": {"planner_p95": p95, "samples": len(latency_samples), "provenance": "SIMULATED"},
        "rule_violations": int(simulation.get("summary", {}).get("rule_violations", 0)) + int(planner_result.get("rule_violations", 0)) + int(baselines.get("rule_violations", 0)),
        "replay_manifest": replay_manifest,
        "blockers": [
            "Synthetic evidence cannot satisfy real-race calibration or release claims",
            "Final 2026 rule configuration still has unresolved Detection Gap/deployment/store inputs",
            "Accepted public C4/C5 callbacks are unavailable",
        ],
    }
    (out / "closure_evidence.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Ignored/generated evidence directory")
    parser.add_argument("--episodes", type=int, default=8)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()
    print(json.dumps(run(args.out, episodes=args.episodes, seed=args.seed), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
