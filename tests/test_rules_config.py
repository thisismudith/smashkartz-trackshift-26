"""Tests for the 2026 rule configuration (CP-03).

The configuration's job is to keep a *usable* number and a *claimable* number
distinguishable. So these tests care less about the values themselves than about
whether the provenance machinery actually stops an unsourced value from backing
a public claim (AGENTS.md sections 21, 57, 58).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.rules.config import (  # noqa: E402
    CLAIMABLE_TIERS,
    RULES_DIR,
    TIERS,
    RuleConfigError,
    UnsourcedValue,
    _walk,
    available_events,
    load_common,
    load_event_rules,
    resolve,
    unsourced_keys,
    validate_event_file,
)

SEASON = "2026"
EVENT_FILES = sorted(p for p in (RULES_DIR / SEASON).glob("*.yaml") if p.stem != "common")
ALL_FILES = EVENT_FILES + [RULES_DIR / SEASON / "common.yaml"]


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


# ------------------------------------------------------------------ inventory
def test_fourteen_event_files_exist():
    """13 complete Grands Prix plus the Practice-only Spanish GP. The three
    Pre-Season Testing directories are not Grands Prix and are excluded."""
    assert len(EVENT_FILES) == 14, [p.stem for p in EVENT_FILES]


def test_no_pre_season_testing_files():
    assert not [p for p in EVENT_FILES if "testing" in p.stem]


def test_available_events_matches_the_directory():
    assert set(available_events(SEASON)) == {p.stem for p in EVENT_FILES}


def test_incomplete_event_is_flagged_not_silently_dropped():
    """The Spanish GP has Practice 1 only. It is present and marked, so the gap
    is visible rather than quietly missing."""
    assert load_event_rules("spanish_grand_prix", SEASON)["complete_in_mirror"] is False


def test_thirteen_events_are_complete():
    complete = [p.stem for p in EVENT_FILES if _load(p).get("complete_in_mirror")]
    assert len(complete) == 13, complete


# ------------------------------------------------------------------ structure
@pytest.mark.parametrize("path", EVENT_FILES, ids=lambda p: p.stem)
def test_event_file_validates(path):
    problems = validate_event_file(path)
    assert problems == [], "\n  ".join(problems)


@pytest.mark.parametrize("path", EVENT_FILES, ids=lambda p: p.stem)
def test_event_key_matches_filename(path):
    assert _load(path)["event"] == path.stem


def test_common_file_is_versioned():
    common = load_common(SEASON)
    assert common["schema_version"] == 1
    assert common["season"] == 2026


def test_every_value_block_has_a_tier_and_a_source():
    for path in ALL_FILES:
        for dotted, node in _walk(_load(path)):
            assert node["value_source"] in TIERS, f"{path.name}:{dotted}"
            assert "source" in node, f"{path.name}:{dotted} has no source"


def test_no_rule_fia_value_still_carries_a_todo_source():
    """A value claiming to be cited but isn't is worse than an honest
    UNVERIFIED, because it reads as sourced."""
    offenders = [
        f"{p.name}:{d}" for p in ALL_FILES for d, n in _walk(_load(p))
        if n["value_source"] == "RULE_FIA" and "TODO" in str(n.get("source", "")).upper()
    ]
    assert not offenders, offenders


# -------------------------------------------------------------------- merging
def test_event_inherits_the_season_power_envelope():
    rules = load_event_rules("british_grand_prix", SEASON)
    assert resolve(rules, "power_envelope.normal").value["breakpoints_kmh"] == [0, 290, 340]


def test_event_specific_values_are_present():
    length = resolve(load_event_rules("british_grand_prix", SEASON), "lap_length_m")
    assert length.value_source == "DERIVED_TELEMETRY"
    assert 5700 < length.value < 5900


# ---------------------------------------------------------------- strict mode
def test_strict_mode_blocks_an_unverified_value():
    """The whole point: an unsourced number may be modelled with, never claimed."""
    rules = load_event_rules("british_grand_prix", SEASON, strict=True)
    with pytest.raises(UnsourcedValue, match="strict_mode"):
        resolve(rules, "power_envelope.normal")


def test_non_strict_mode_returns_the_same_value():
    """Development must not be blocked by a missing citation."""
    curve = resolve(load_event_rules("british_grand_prix", SEASON, strict=False), "power_envelope.normal")
    assert curve.value["max_power_kw"] == [350, 350, 0]
    assert curve.claimable is False


def test_strict_mode_allows_a_measured_value():
    """DERIVED_TELEMETRY is claimable: it is measured, not assumed."""
    rules = load_event_rules("british_grand_prix", SEASON, strict=True)
    assert resolve(rules, "lap_length_m").claimable is True


def test_proxy_historical_drs_is_never_claimable():
    """Historical DRS is not 2026 Overtake (section 41): it may shape the DP and
    never a claim."""
    assert "PROXY_HISTORICAL_DRS" in TIERS
    assert "PROXY_HISTORICAL_DRS" not in CLAIMABLE_TIERS


def test_unsourced_keys_lists_what_blocks_strict_mode():
    blocking = unsourced_keys(load_event_rules("british_grand_prix", SEASON))
    assert any("power_envelope" in k for k in blocking)
    assert "lap_length_m" not in blocking


def test_strictness_comes_from_the_configuration():
    """A caller cannot quietly opt out of strict mode."""
    rules = load_event_rules("british_grand_prix", SEASON)
    assert rules["_strict"] == load_common(SEASON)["strict_mode"]


# -------------------------------------------------------------------- lookups
def test_unknown_key_raises():
    with pytest.raises(RuleConfigError, match="no rule key"):
        resolve(load_event_rules("british_grand_prix", SEASON), "overtake.no_such_key")


def test_key_without_provenance_is_refused():
    """An unlabelled number is exactly what this module exists to prevent."""
    with pytest.raises(RuleConfigError, match="no value_source"):
        resolve(load_event_rules("british_grand_prix", SEASON), "geometry.corner_count")


def test_unknown_event_raises():
    with pytest.raises(RuleConfigError, match="not found"):
        load_event_rules("monte_carlo_grand_prix", SEASON)


def test_resolved_value_str_names_the_tier():
    assert "DERIVED_TELEMETRY" in str(resolve(load_event_rules("british_grand_prix", SEASON), "lap_length_m"))


# ------------------------------------------------------- measured value sanity
@pytest.mark.parametrize("path", EVENT_FILES, ids=lambda p: p.stem)
def test_lap_length_is_within_three_percent_of_published(path):
    """Not the +/-50 m the checkpoint first specified.

    Telemetry distance is integrated along the path actually driven, so it reads
    systematically short of the homologated circuit length -- on every circuit,
    by 0.6% at Monza to 2.6% at the Hungaroring, tracking corner density.
    Straight-line integration between samples undershoots the arc through a
    corner. The measured value is the correct one for CP-05, which segments on
    telemetry distance; the published figure is a cross-check, not an equivalent.
    """
    block = _load(path).get("lap_length_m", {})
    pct = block.get("delta_vs_published_pct")
    if pct is None:
        pytest.skip("no published cross-check for this circuit")
    assert -3.0 < pct < 0.5, f"{path.stem}: {pct}% vs published"


@pytest.mark.parametrize("path", EVENT_FILES, ids=lambda p: p.stem)
def test_lap_length_is_physically_plausible(path):
    value = _load(path).get("lap_length_m", {}).get("value")
    assert value is None or 3000 < value < 7500, f"{path.stem}: {value} m"


def test_corner_geometry_present_and_consistent_for_complete_events():
    for path in EVENT_FILES:
        data = _load(path)
        if not data.get("complete_in_mirror"):
            continue
        geometry = data["geometry"]
        assert geometry["corner_count"] > 0, path.stem
        assert len(geometry["corner_distances_m"]) == geometry["corner_count"], path.stem


def test_corner_distances_are_ordered_and_inside_the_lap():
    for path in EVENT_FILES:
        data = _load(path)
        length = (data.get("lap_length_m") or {}).get("value")
        distances = (data.get("geometry") or {}).get("corner_distances_m") or []
        if not distances or not length:
            continue
        assert distances == sorted(distances), path.stem
        assert 0 <= distances[0] and distances[-1] <= length * 1.02, path.stem


# --------------------------------------------------------------- race control
def test_race_control_artifact_exists_for_every_event():
    for path in EVENT_FILES:
        artifact = ROOT / _load(path)["race_control"]["overtake_windows_source"]
        assert artifact.exists(), f"{path.stem}: missing {artifact}"


def _rc_counts() -> dict:
    totals: dict[str, int] = {}
    for path in (ROOT / "artifacts" / "race_control").glob("2026_*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for session in doc["sessions"].values():
            for key, value in session.get("counts", {}).items():
                totals[key] = totals.get(key, 0) + value
    return totals


def test_season_overtake_message_totals():
    """Reconciles to the CP-03 figure: 51 enable / 23 disable across 2026."""
    counts = _rc_counts()
    assert (counts.get("overtake_enabled"), counts.get("overtake_disabled")) == (51, 23)


def test_vsc_deployments_are_detected():
    """Race control writes "VSC DEPLOYED", not the expanded form. Matching only
    the long spelling missed all 31 deployments while still catching the 26
    endings, which would have hidden VSC periods from the eligibility gate."""
    assert _rc_counts().get("vsc_deployed") == 31


def test_race_control_is_tagged_observed_not_verified():
    for path in (ROOT / "artifacts" / "race_control").glob("2026_*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["value_source"] == "OBSERVED_RCM"


def test_race_control_cannot_supply_line_positions():
    """Race control states whether Overtake is available, never where. Detection
    and Activation lines stay UNVERIFIED until an FIA event note is found."""
    zone = load_event_rules("british_grand_prix", SEASON)["overtake"]["zones"][0]
    assert zone["detection_line_m"]["value_source"] == "UNVERIFIED"
    assert zone["activation_line_m"]["value_source"] == "UNVERIFIED"
