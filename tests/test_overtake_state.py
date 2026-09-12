"""CP-10 boundary tests for the deterministic 2026 Overtake machine."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.rules.state_machine import (  # noqa: E402
    ACTIVE,
    ARMED,
    DISABLED,
    NOT_ARMED,
    TRANSITION_TABLE,
    configured_line_provenance,
    step,
)


def rules(*, threshold: float = 1.0, detection: float = 100.0, activation: float = 120.0,
          zone_end: float = 200.0, tier: str = "RULE_FIA") -> dict:
    return {
        "overtake": {
            "enabled": True,
            "detection_gap_s": {"value": threshold, "value_source": tier, "source": "test"},
            "zones": [{
                "zone": 1,
                "detection_line_m": {"value": detection, "value_source": tier, "source": "test"},
                "activation_line_m": {"value": activation, "value_source": tier, "source": "test"},
                "zone_end_m": {"value": zone_end, "value_source": tier, "source": "test"},
            }],
        }
    }


def crossing(previous: float, *, previous_gap: float | None = 0.9, **extra) -> dict:
    return {"previous_position_m": previous, "previous_gap_s": previous_gap, **extra}


def test_transition_table_is_explicit_and_complete():
    assert {rule.name for rule in TRANSITION_TABLE} == {
        "race_control_disabled", "race_control_enabled", "detection_qualifies",
        "activation_after_arm", "zone_exit", "lift", "session_end",
    }


def test_detection_threshold_below_exactly_and_above():
    event_rules = rules(threshold=1.0)
    assert step(NOT_ARMED, 100.0, 0.999, crossing(90.0, previous_gap=0.999), event_rules) == ARMED
    assert step(NOT_ARMED, 100.0, 1.0, crossing(90.0, previous_gap=1.0), event_rules) == NOT_ARMED
    assert step(NOT_ARMED, 100.0, 1.001, crossing(90.0, previous_gap=1.001), event_rules) == NOT_ARMED


def test_detection_activation_zone_exit_lift_and_session_end():
    event_rules = rules()
    armed = step(NOT_ARMED, 100.0, 0.8, crossing(90.0, previous_gap=0.8), event_rules)
    assert armed == ARMED
    active = step(armed, 120.0, 0.8, crossing(100.0), event_rules)
    assert active == ACTIVE
    assert step(active, 200.0, 0.8, crossing(180.0), event_rules) == NOT_ARMED
    assert step(ACTIVE, 150.0, 0.8, crossing(140.0, lifted=True), event_rules) == NOT_ARMED
    assert step(ARMED, 110.0, 0.8, crossing(100.0, session_ended=True), event_rules) == NOT_ARMED


def test_observed_race_control_disable_and_enable_transitions():
    event_rules = rules()
    assert step(ARMED, 105.0, 0.8, crossing(100.0, overtake_disabled=True), event_rules) == DISABLED
    assert step(DISABLED, 105.0, 0.8, crossing(100.0, overtake_enabled=True), event_rules) == NOT_ARMED


def test_active_never_occurs_without_earlier_arm():
    event_rules = rules()
    assert step(NOT_ARMED, 120.0, 0.8, crossing(110.0), event_rules) == NOT_ARMED
    assert step(DISABLED, 120.0, 0.8, crossing(110.0), event_rules) == DISABLED


def test_same_20m_interval_orders_detection_before_activation():
    event_rules = rules(detection=101.0, activation=119.0)
    assert step(NOT_ARMED, 120.0, 0.7, crossing(100.0, previous_gap=0.7), event_rules) == ACTIVE


def test_no_transition_without_line_crossing_or_observed_control_event():
    event_rules = rules()
    assert step(NOT_ARMED, 99.0, 0.1, crossing(90.0, previous_gap=0.1), event_rules) == NOT_ARMED
    assert step(ARMED, 119.0, 5.0, crossing(110.0), event_rules) == ARMED
    assert step(ACTIVE, 150.0, 5.0, crossing(130.0), event_rules) == ACTIVE


def test_2026_step_has_no_drs_input_path():
    event_rules = rules()
    base = step(NOT_ARMED, 100.0, 0.7, crossing(90.0, previous_gap=0.7), event_rules)
    drs_noise = step(NOT_ARMED, 100.0, 0.7, crossing(90.0, previous_gap=0.7, drs_open=True, drs=99), event_rules)
    assert base == drs_noise == ARMED


def test_tier_c_line_provenance_is_preserved_for_manifest():
    provenance = configured_line_provenance(rules(tier="PROXY_HISTORICAL_DRS"))
    assert provenance[0]["detection_line_m"]["value_source"] == "PROXY_HISTORICAL_DRS"
    assert provenance[0]["activation_line_m"]["value_source"] == "PROXY_HISTORICAL_DRS"


def _builder_module():
    path = ROOT / "scripts" / "rules" / "apply_overtake_state.py"
    spec = importlib.util.spec_from_file_location("apply_overtake_state", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_accepts_only_fia_or_documented_tier_c_lines():
    builder = _builder_module()
    assert builder._usable_lines(rules(tier="RULE_FIA")) is True
    assert builder._usable_lines(rules(tier="PROXY_HISTORICAL_DRS")) is True
    assert builder._usable_lines(rules(tier="UNVERIFIED")) is False


def _rows(year: str = "2026") -> pd.DataFrame:
    return pd.DataFrame({
        "year": [year, year], "event": ["Test Grand Prix"] * 2, "session": ["Race"] * 2,
        "driver": ["AAA"] * 2, "lap": [2, 2], "distance_m": [100.0, 120.0],
        "lap_start_session_s": [0.0, 0.0], "lap_elapsed_s": [10.0, 12.0],
        "gap_ahead_m": [20.0, 20.0], "speed_kmh": [100.0, 100.0], "drs_open": [1, 1],
    })


def test_2026_builder_ignores_raw_drs_and_sets_historical_columns_null():
    builder = _builder_module()
    frame = _rows()
    out, _ = builder.apply_2026(frame, rules(), [{"kind": "enabled", "session_time_s": 0.0}])
    assert out["overtake_state"].tolist() == [ARMED, ACTIVE]
    assert out["overtake_eligible"].tolist() == [False, True]
    assert out["historical_drs_open"].isna().all()
    assert out["historical_drs_eligible"].isna().all()


def test_historical_drs_fields_only_appear_in_2022_to_2025():
    builder = _builder_module()
    historical = builder.apply_historical(_rows("2025"), [{"kind": "enabled", "session_time_s": 0.0}])
    assert historical["overtake_state"].isna().all()
    assert historical["overtake_eligible"].isna().all()
    assert historical["historical_drs_open"].tolist() == [True, True]
    assert historical["historical_drs_eligible"].tolist() == [True, True]


def test_numeric_race_control_time_is_already_session_relative():
    builder = _builder_module()
    messages, alignment = builder.align_race_control_messages(_rows(), [{"kind": "enabled", "time": 11.0, "lap": 2}])
    assert messages[0]["session_time_s"] == 11.0
    assert alignment["method"] == "unavailable"
