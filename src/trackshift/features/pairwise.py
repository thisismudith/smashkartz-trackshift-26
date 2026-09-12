"""Causal C8/M06 pairwise features.

This module deliberately has a small surface: one row is emitted for one C8
battle segment.  It reads the contemporaneous C1 segment for each car and
only the already-emitted rows of *that same battle* for trailing estimates.
It never reads a battle summary, pass result, episode end, or later segment.

C2 baselines and C5 energy/fuel estimates are deliberately represented as
unavailable Quantities until their public contracts are materialised.  They
are not approximated from telemetry in this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

__all__ = [
    "PAIRWISE_FEATURE_SCHEMA_VERSION",
    "PAIRWISE_FEATURE_SCHEMA",
    "PairwiseBuildResult",
    "build_pairwise_features",
]

PAIRWISE_FEATURE_SCHEMA_VERSION = "m06_pairwise_segment_features_v1"
TRAILING_WINDOW_SEGMENTS = 3

# This compact schema is also written into the producer manifest.  It is
# intentionally explicit about signs; unit-bearing field names alone are not
# enough to stop a future consumer from treating a closing rate as a gap.
PAIRWISE_FEATURE_SCHEMA: dict[str, dict[str, Any]] = {
    "time_gap_entry_s": {"unit": "s", "provenance": "DERIVED", "live_safe": True,
                           "definition": "Attacker-to-immediate-ahead entry time gap; never derived from metres."},
    "distance_gap_entry_m": {"unit": "m", "provenance": "DERIVED", "live_safe": True,
                               "definition": "Attacker-to-immediate-ahead entry distance gap; never derived from seconds."},
    "relative_speed_to_ahead_mps": {"unit": "m/s", "provenance": "DERIVED", "live_safe": True,
                                      "definition": "Attacker entry speed minus defender entry speed; positive means attacker faster."},
    "relative_acceleration_to_ahead_mps2": {"unit": "m/s2", "provenance": "DERIVED", "live_safe": True,
                                             "definition": "Attacker trailing entry-speed acceleration minus defender's; positive means attacker accelerates more."},
    "gap_rate_ahead_s_per_s": {"unit": "s/s", "provenance": "DERIVED", "live_safe": True,
                                 "definition": "Trailing difference in time gap divided by elapsed time; negative means the attacker is closing."},
    "relative_speed_to_ahead_trailing_mean_3_mps": {"unit": "m/s", "provenance": "DERIVED", "live_safe": True,
                                                       "definition": "Current plus at most two preceding same-battle relative-speed observations."},
    "time_gap_entry_trailing_mean_3_s": {"unit": "s", "provenance": "DERIVED", "live_safe": True,
                                            "definition": "Current plus at most two preceding same-battle time gaps."},
    "distance_gap_entry_trailing_mean_3_m": {"unit": "m", "provenance": "DERIVED", "live_safe": True,
                                                "definition": "Current plus at most two preceding same-battle distance gaps."},
    "gap_rate_ahead_trailing_mean_3_s_per_s": {"unit": "s/s", "provenance": "DERIVED", "live_safe": True,
                                                  "definition": "Current plus at most two preceding same-battle time-gap rates."},
}


@dataclass(frozen=True)
class PairwiseBuildResult:
    """Rows and an audit-ready count of rows excluded before feature creation."""

    rows: list[dict[str, Any]]
    excluded_rows_by_reason: dict[str, int]
    missing_defender_segments: int


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _first_number(row: Mapping[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        value = _number(row.get(name))
        if value is not None:
            return value
    return None


def _segment_key(row: Mapping[str, Any], driver: Any) -> tuple[Any, ...]:
    """The C1/C8 contemporaneous alignment key, without an outcome field."""
    return tuple(row.get(name) for name in ("year", "event", "session", "lap", "segment_id")) + (driver,)


def _sort_key(row: Mapping[str, Any], index: int) -> tuple[Any, ...]:
    return (
        _number(row.get("segment_index")) is None,
        _number(row.get("segment_index")) or float("inf"),
        _number(row.get("lap")) is None,
        _number(row.get("lap")) or float("inf"),
        _number(row.get("entry_distance_m")) is None,
        _number(row.get("entry_distance_m")) or float("inf"),
        index,
    )


def _entry_time_s(row: Mapping[str, Any]) -> float | None:
    explicit = _number(row.get("session_time_s"))
    if explicit is not None:
        return explicit
    lap_start = _number(row.get("lap_start_session_s"))
    lap_elapsed = _first_number(row, ("entry_lap_elapsed_s", "lap_elapsed_s"))
    return lap_start + lap_elapsed if lap_start is not None and lap_elapsed is not None else None


def _entry_speed_mps(row: Mapping[str, Any] | None) -> float | None:
    if row is None:
        return None
    speed_kmh = _first_number(row, ("entry_speed_kmh", "speed_kmh"))
    return speed_kmh / 3.6 if speed_kmh is not None else None


def _time_gap_s(row: Mapping[str, Any]) -> float | None:
    # Do not include a distance field in this list.  Null is the only honest
    # answer when the source has no entry time gap.
    return _first_number(row, ("time_gap_ahead_s_entry", "gap_ahead_s_entry", "time_gap_entry_s", "gap_ahead_s"))


def _distance_gap_m(row: Mapping[str, Any]) -> float | None:
    # Symmetrically, never turn the time gap into metres using an assumed speed.
    return _first_number(row, ("distance_gap_ahead_m_entry", "gap_ahead_m_entry", "distance_gap_entry_m", "gap_ahead_m"))


def _trailing_mean(history: list[float | None], value: float | None) -> float | None:
    values = [candidate for candidate in (history[-(TRAILING_WINDOW_SEGMENTS - 1):] + [value]) if candidate is not None]
    return sum(values) / len(values) if values else None


def _quantity(value: float | None, provenance: str, unit: str, reason: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"value": value, "provenance": provenance, "unit": unit}
    if value is None:
        payload["reason"] = reason or "source unavailable"
    return payload


def _unavailable_quantities(
    *, braking_available: bool, degradation_available: bool,
) -> dict[str, dict[str, Any]]:
    """API.md Quantity-compatible unavailable fields, never fake zeroes."""
    unavailable: dict[str, dict[str, Any]] = {
        "baseline_residual_delta_s": _quantity(
            None, "DERIVED", "s", "C2 public baseline tables are not materialised locally"
        ),
        "fuel_load_delta_kg_est": _quantity(
            None, "INFERRED", "kg", "C5 energy_state public contract is not available; fuel is not proxied"
        ),
        "fuel_load_delta_uncertainty_kg": _quantity(
            None, "INFERRED", "kg", "C5 energy_state public contract is not available"
        ),
        "ers_energy_delta_kj_est": _quantity(
            None, "SIMULATED", "kJ", "C5 energy_state public contract is not available; ERS is not invented"
        ),
        "ers_energy_delta_uncertainty_kj": _quantity(
            None, "SIMULATED", "kJ", "C5 energy_state public contract is not available"
        ),
    }
    if not braking_available:
        unavailable["braking_intensity_delta"] = _quantity(
            None, "DERIVED", "ratio", "C1 has no entry-aligned braking intensity; brake_fraction_offline is OFFLINE_ONLY"
        )
    if not degradation_available:
        unavailable["tyre_degradation_delta"] = _quantity(
            None, "DERIVED", "ratio", "C1 tyre_degradation_proxy is not materialised for both cars"
        )
    return unavailable


def _add_dynamic_unavailable(row: Mapping[str, Any], unavailable: dict[str, dict[str, Any]], *, defender_found: bool) -> None:
    """Give every null live quantity an API.md-compatible explanation."""
    specifications = {
        "time_gap_entry_s": ("DERIVED", "s", "C1/C8 has no published entry time-gap field; distance is not substituted"),
        "distance_gap_entry_m": ("DERIVED", "m", "C1/C8 has no published entry distance-gap field"),
        "attacker_speed_entry_mps": ("DERIVED", "m/s", "C8 attacker row has no entry speed"),
        "defender_speed_entry_mps": ("DERIVED", "m/s", "No contemporaneous C1 defender segment" if not defender_found else "C1 defender row has no entry speed"),
        "relative_speed_to_ahead_mps": ("DERIVED", "m/s", "Both contemporaneous entry speeds are required"),
        "attacker_acceleration_entry_mps2": ("DERIVED", "m/s2", "No earlier same-battle entry speed with a positive elapsed time"),
        "defender_acceleration_entry_mps2": ("DERIVED", "m/s2", "No earlier same-battle defender speed with a positive elapsed time"),
        "relative_acceleration_to_ahead_mps2": ("DERIVED", "m/s2", "Both trailing acceleration estimates are required"),
        "gap_rate_ahead_s_per_s": ("DERIVED", "s/s", "Current and preceding published time gaps are required; metres are not substituted"),
        "attacker_braking_intensity": ("DERIVED", "ratio", "C1 has no entry-aligned braking intensity; brake_fraction_offline is OFFLINE_ONLY"),
        "defender_braking_intensity": ("DERIVED", "ratio", "C1 has no entry-aligned braking intensity; brake_fraction_offline is OFFLINE_ONLY"),
        "attacker_tyre_life_laps": ("OBSERVED", "laps", "C8 attacker row has no tyre-life value"),
        "defender_tyre_life_laps": ("OBSERVED", "laps", "No contemporaneous C1 defender tyre-life value"),
        "tyre_life_delta_laps": ("OBSERVED", "laps", "Both observed tyre-life values are required"),
        "attacker_tyre_degradation_proxy": ("DERIVED", "ratio", "C1 attacker tyre_degradation_proxy is not materialised"),
        "defender_tyre_degradation_proxy": ("DERIVED", "ratio", "C1 defender tyre_degradation_proxy is not materialised"),
        "relative_speed_to_ahead_trailing_mean_3_mps": ("DERIVED", "m/s", "No current or trailing same-battle relative speed is available"),
        "time_gap_entry_trailing_mean_3_s": ("DERIVED", "s", "No current or trailing same-battle published time gap is available"),
        "distance_gap_entry_trailing_mean_3_m": ("DERIVED", "m", "No current or trailing same-battle distance gap is available"),
        "gap_rate_ahead_trailing_mean_3_s_per_s": ("DERIVED", "s/s", "No current or trailing same-battle time-gap rate is available"),
    }
    for name, (provenance, unit, reason) in specifications.items():
        if row.get(name) is None and name not in unavailable:
            unavailable[name] = _quantity(None, provenance, unit, reason)


def _value_or_none(row: Mapping[str, Any] | None, name: str) -> float | None:
    return _number(row.get(name)) if row is not None else None


def build_pairwise_features(
    battle_rows: Iterable[Mapping[str, Any]], segment_rows: Iterable[Mapping[str, Any]],
) -> PairwiseBuildResult:
    """Build one live-safe M06 row per valid C8 battle row.

    ``segment_rows`` is C1 and is only used as a contemporaneous lookup for
    the defender.  Acceleration and trends are derived from current and prior
    rows inside one battle; a C7 transition clears that history defensively.
    """
    segment_index: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    duplicate_keys: set[tuple[Any, ...]] = set()
    for row in segment_rows:
        driver = row.get("driver")
        key = _segment_key(row, driver)
        if key in segment_index:
            duplicate_keys.add(key)
        else:
            segment_index[key] = row

    grouped: dict[Any, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(battle_rows):
        grouped[row.get("battle_id")].append((index, row))

    output: list[dict[str, Any]] = []
    excluded = Counter()
    missing_defender_segments = 0
    for battle_id, stream in grouped.items():
        if not battle_id:
            excluded["MISSING_BATTLE_ID"] += len(stream)
            continue
        stream.sort(key=lambda item: _sort_key(item[1], item[0]))
        history: dict[str, list[float | None]] = defaultdict(list)
        previous: dict[str, float | None] | None = None
        for _, battle in stream:
            # C8 rows should already meet these conditions.  Retaining the
            # checks protects a direct caller and makes a transition a true
            # history break rather than merely a filtering annotation.
            if battle.get("race_control_transition_flag") is True or battle.get("pit_transition_flag") is True:
                excluded["C7_TRANSITION_ROW"] += 1
                history = defaultdict(list)
                previous = None
                continue
            if battle.get("normal_race_model_eligible") is not True:
                excluded["NOT_NORMAL_RACE_MODEL_ELIGIBLE"] += 1
                history = defaultdict(list)
                previous = None
                continue
            attacker = battle.get("attacker")
            defender = battle.get("defender")
            if not attacker or not defender:
                excluded["MISSING_ATTACKER_OR_DEFENDER"] += 1
                previous = None
                history = defaultdict(list)
                continue

            defender_key = _segment_key(battle, defender)
            defender_row = None if defender_key in duplicate_keys else segment_index.get(defender_key)
            if defender_row is None:
                missing_defender_segments += 1

            attacker_speed = _entry_speed_mps(battle)
            defender_speed = _entry_speed_mps(defender_row)
            relative_speed = attacker_speed - defender_speed if attacker_speed is not None and defender_speed is not None else None
            current_time = _entry_time_s(battle)

            attacker_acceleration = defender_acceleration = relative_acceleration = gap_rate = None
            if previous is not None and current_time is not None and previous["time"] is not None:
                elapsed_s = current_time - previous["time"]
                # Non-positive clocks are not an excuse to use a future sample.
                if elapsed_s > 0.0:
                    if attacker_speed is not None and previous["attacker_speed"] is not None:
                        attacker_acceleration = (attacker_speed - previous["attacker_speed"]) / elapsed_s
                    if defender_speed is not None and previous["defender_speed"] is not None:
                        defender_acceleration = (defender_speed - previous["defender_speed"]) / elapsed_s
                    if attacker_acceleration is not None and defender_acceleration is not None:
                        relative_acceleration = attacker_acceleration - defender_acceleration
                    if (time_gap := _time_gap_s(battle)) is not None and previous["time_gap"] is not None:
                        gap_rate = (time_gap - previous["time_gap"]) / elapsed_s

            attacker_brake = _value_or_none(battle, "braking_intensity_proxy")
            defender_brake = _value_or_none(defender_row, "braking_intensity_proxy")
            braking_delta = attacker_brake - defender_brake if attacker_brake is not None and defender_brake is not None else None
            attacker_deg = _value_or_none(battle, "tyre_degradation_proxy")
            defender_deg = _value_or_none(defender_row, "tyre_degradation_proxy")
            degradation_delta = attacker_deg - defender_deg if attacker_deg is not None and defender_deg is not None else None
            time_gap = _time_gap_s(battle)
            distance_gap = _distance_gap_m(battle)
            attacker_life = _value_or_none(battle, "tyre_life_laps")
            defender_life = _value_or_none(defender_row, "tyre_life_laps")

            row = {
                "year": battle.get("year"), "event": battle.get("event"), "session": battle.get("session"),
                "lap": battle.get("lap"), "segment_id": battle.get("segment_id"),
                "entry_distance_m": battle.get("entry_distance_m"), "battle_id": battle_id,
                "segment_index": battle.get("segment_index"), "attacker": attacker, "defender": defender,
                "time_gap_entry_s": time_gap, "distance_gap_entry_m": distance_gap,
                "attacker_speed_entry_mps": attacker_speed, "defender_speed_entry_mps": defender_speed,
                "relative_speed_to_ahead_mps": relative_speed,
                "attacker_acceleration_entry_mps2": attacker_acceleration,
                "defender_acceleration_entry_mps2": defender_acceleration,
                "relative_acceleration_to_ahead_mps2": relative_acceleration,
                "gap_rate_ahead_s_per_s": gap_rate,
                "attacker_braking_intensity": attacker_brake,
                "defender_braking_intensity": defender_brake,
                "braking_intensity_delta": braking_delta,
                "attacker_tyre_life_laps": attacker_life,
                "defender_tyre_life_laps": defender_life,
                "tyre_life_delta_laps": attacker_life - defender_life if attacker_life is not None and defender_life is not None else None,
                "tyre_compound_pair": f"{battle.get('tyre_compound')}|{defender_row.get('tyre_compound')}" if defender_row is not None else None,
                "attacker_tyre_degradation_proxy": attacker_deg,
                "defender_tyre_degradation_proxy": defender_deg,
                "tyre_degradation_delta": degradation_delta,
                "relative_speed_to_ahead_trailing_mean_3_mps": _trailing_mean(history["relative_speed"], relative_speed),
                "time_gap_entry_trailing_mean_3_s": _trailing_mean(history["time_gap"], time_gap),
                "distance_gap_entry_trailing_mean_3_m": _trailing_mean(history["distance_gap"], distance_gap),
                "gap_rate_ahead_trailing_mean_3_s_per_s": _trailing_mean(history["gap_rate"], gap_rate),
                # Reserved fields stay null until C2/C5 are genuinely available.
                "baseline_residual_delta_s": None,
                "fuel_load_delta_kg_est": None,
                "fuel_load_delta_uncertainty_kg": None,
                "ers_energy_delta_kj_est": None,
                "ers_energy_delta_uncertainty_kj": None,
            }
            unavailable = _unavailable_quantities(
                braking_available=braking_delta is not None, degradation_available=degradation_delta is not None,
            )
            _add_dynamic_unavailable(row, unavailable, defender_found=defender_row is not None)
            row["unavailable_quantities"] = unavailable
            output.append(row)
            for key, value in (("relative_speed", relative_speed), ("time_gap", time_gap), ("distance_gap", distance_gap), ("gap_rate", gap_rate)):
                history[key].append(value)
            previous = {
                "time": current_time, "attacker_speed": attacker_speed, "defender_speed": defender_speed,
                "time_gap": time_gap,
            }
    return PairwiseBuildResult(output, dict(sorted(excluded.items())), missing_defender_segments)
