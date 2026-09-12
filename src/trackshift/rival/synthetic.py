"""Seeded M09b synthetic rival trajectories; labels are never real-driver truth."""
from __future__ import annotations
from dataclasses import dataclass
from random import Random
from typing import Any

STATES = ("CONSERVING", "BALANCED", "DEPLOYING", "DERATING")
SYNTHETIC_PROVENANCE = "SYNTHETIC"
REGRESSION_SEED, BENCHMARK_SEED = 1701, 2903

@dataclass(frozen=True)
class SyntheticConfig:
    seed: int = BENCHMARK_SEED
    sequences: int = 24
    length: int = 32
    merge_conserving_derating: bool = False

def generate(config: SyntheticConfig = SyntheticConfig()) -> list[dict[str, Any]]:
    rng = Random(config.seed); rows: list[dict[str, Any]] = []
    for sequence in range(config.sequences):
        state = rng.choice(STATES)
        remaining = rng.randint(2, 7)
        for index in range(config.length):
            if remaining == 0:
                choices = [s for s in STATES if s != state]
                state, remaining = rng.choice(choices), rng.randint(2, 7)
            remaining -= 1
            signal = {"CONSERVING": -1.0, "BALANCED": 0.0, "DEPLOYING": 1.0, "DERATING": -0.7}[state]
            if config.merge_conserving_derating and state in {"CONSERVING", "DERATING"}: signal = -0.85
            noise = rng.uniform(-0.22, 0.22)
            rows.append({"sequence_id": f"synthetic_{sequence}", "segment_index": index, "hidden_state": state,
                         "pace_residual_delta_s": -0.18 * signal + noise, "relative_speed_to_ahead_mps": 2.2 * signal + noise,
                         "gap_rate_ahead_s_per_s": -0.06 * signal + noise / 20, "braking_intensity_delta": -0.35 * signal + noise,
                         "tyre_state": rng.uniform(0, 1), "synthetic_energy_state": rng.uniform(0.2, 0.9),
                         "event_geometry": rng.choice(("STRAIGHT", "CORNER")), "weather_noise": rng.uniform(-1, 1),
                         "provenance": SYNTHETIC_PROVENANCE, "generator_seed": config.seed,
                         "merged_scenario": config.merge_conserving_derating})
    return rows
