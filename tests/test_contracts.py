"""Focused CP-00 tests for Owner A public contracts."""
from __future__ import annotations

import importlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from trackshift.contracts.strategic_state import (
    REQUIRED_BLOCKS,
    StrategicStateValidationError,
    validate_strategic_state,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "contracts" / "c1_c6_and_state.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_contract_fixture_is_valid_json_and_covers_c1_to_c6() -> None:
    fixture = load_fixture()
    assert {f"c{number}_{name}" for number, name in (
        (1, "segments"), (2, "baselines"), (3, "rules"),
        (4, "pass_prediction"), (5, "energy_twin"), (6, "opportunity_features"),
    )}.issubset(fixture)


@pytest.mark.parametrize("name", ["strategic_state_normal", "strategic_state_ineligible"])
def test_strategic_state_accepts_complete_json_fixture(name: str) -> None:
    state = load_fixture()[name]
    assert validate_strategic_state(state) == state


@pytest.mark.parametrize("missing", REQUIRED_BLOCKS)
def test_strategic_state_rejects_each_missing_required_block(missing: str) -> None:
    state = deepcopy(load_fixture()["strategic_state_normal"])
    del state[missing]
    with pytest.raises(StrategicStateValidationError, match=missing):
        validate_strategic_state(state)


def test_unavailable_modelled_value_requires_reason() -> None:
    state = deepcopy(load_fixture()["strategic_state_normal"])
    del state["energy"]["ers_soc_est_mj"]["reason"]
    with pytest.raises(StrategicStateValidationError, match="reason"):
        validate_strategic_state(state)


@pytest.mark.parametrize(
    "module_name",
    [
        "trackshift.features.api",
        "trackshift.rival.api",
        "trackshift.value.api",
        "trackshift.planner.api",
        "trackshift.sim.api",
    ],
)
def test_owner_a_public_boundaries_import_on_cpu(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None
