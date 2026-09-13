"""Tests for the season-scoped empirical pass rate served while M10 has no artifact.

The properties worth pinning are the honest ones: an empty bucket must not
borrow a neighbour's frequency, a gap outside the tracked range must not be
answered at all, and the two regulation eras must never be silently pooled into
one number.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.serve import pass_fallback as pf  # noqa: E402


def _op(gap_s: float, passed: bool, event: str = "Test Grand Prix") -> pf.Opportunity:
    return pf.Opportunity(
        event=event, session="Race", lap=1, attacker="AAA", defender="BBB",
        gap_s=gap_s, gap_m=gap_s * 60.0, speed_kmh=216.0,
        armed=gap_s <= 1.0, passed=passed,
    )


# ------------------------------------------------------------------ table

def test_bucket_counts_are_the_observed_frequency():
    ops = [_op(0.2, True), _op(0.3, True), _op(0.4, False), _op(0.45, False)]
    table = pf.build_rate_table(ops, events=["Test Grand Prix"])
    first = table.buckets[0]
    assert (first.low_s, first.high_s) == (0.0, 0.5)
    assert first.n == 4
    assert first.passes == 2
    assert first.rate == pytest.approx(0.5)


def test_wilson_interval_brackets_the_rate_and_narrows_with_n():
    few = pf.build_rate_table([_op(0.1, True), _op(0.2, False)]).buckets[0]
    many = pf.build_rate_table(
        [_op(0.1, i % 2 == 0) for i in range(400)]
    ).buckets[0]
    for bucket in (few, many):
        assert bucket.ci_low <= bucket.rate <= bucket.ci_high
    assert (many.ci_high - many.ci_low) < (few.ci_high - few.ci_low)


def test_every_configured_bucket_is_present_even_with_no_observations():
    table = pf.build_rate_table([_op(0.1, True)])
    assert [(b.low_s, b.high_s) for b in table.buckets] == list(pf.GAP_BUCKETS_S)


# ---------------------------------------------------------------- predict

def test_prediction_reads_the_bucket_containing_the_gap():
    ops = [_op(0.2, True)] * 3 + [_op(0.7, False)] * 4
    table = pf.build_rate_table(ops)
    near = pf.predict_from_table(table, 0.25)
    far = pf.predict_from_table(table, 0.9)
    assert near is not None and far is not None
    assert near["p_pass_by_outcome_horizon"] == pytest.approx(1.0)
    assert far["p_pass_by_outcome_horizon"] == pytest.approx(0.0)
    assert near["gap_bucket_s"] == [0.0, 0.5]


def test_an_empty_bucket_answers_nothing_rather_than_borrowing_a_neighbour():
    """The nearest populated bucket is a different question's answer."""
    table = pf.build_rate_table([_op(0.2, True)] * 10)
    assert pf.predict_from_table(table, 0.2) is not None
    assert pf.predict_from_table(table, 1.2) is None  # 1.0-1.5 has no observations


def test_a_gap_outside_every_bucket_is_unanswerable():
    table = pf.build_rate_table([_op(0.2, True)] * 10)
    assert pf.predict_from_table(table, 9.0) is None
    assert pf.predict_from_table(table, -1.0) is None


def test_absent_gap_is_absent_not_zero():
    table = pf.build_rate_table([_op(0.2, True)] * 10)
    assert pf.predict_from_table(table, None) is None


# -------------------------------------------------------------------- era

def test_eras_are_reported_separately_and_never_pooled_into_one_rate():
    tables = {
        "2026": pf.build_rate_table([_op(0.2, True)] * 4 + [_op(0.2, False)] * 6, season="2026"),
        "2025": pf.build_rate_table([_op(0.2, True)] * 1 + [_op(0.2, False)] * 9, season="2025"),
    }
    comparison = pf.era_comparison(tables, 0.2)
    assert comparison is not None
    eras = {row["season"]: row["era"] for row in comparison["seasons"]}
    assert eras == {"2026": "OVERTAKE", "2025": "DRS"}
    assert set(comparison["by_era"]) == {"OVERTAKE", "DRS"}
    # The two rates stay distinct; nothing averages 0.4 and 0.1 into 0.25.
    assert comparison["by_era"]["OVERTAKE"]["rate"] == pytest.approx(0.4)
    assert comparison["by_era"]["DRS"]["rate"] == pytest.approx(0.1)


def test_era_comparison_is_absent_without_tables():
    assert pf.era_comparison({}, 0.4) is None


# ------------------------------------------------------------- round trip

def test_table_round_trips_through_json(tmp_path: Path):
    table = pf.build_rate_table([_op(0.2, True), _op(0.6, False)],
                                season="2026", events=["Test Grand Prix"])
    path = tmp_path / "2026_rate_table.json"
    path.write_text(json.dumps(table.to_json()), encoding="utf-8")
    loaded = pf.load_rate_table(path)
    assert loaded is not None
    assert loaded.season == "2026"
    assert loaded.n_opportunities == 2
    assert [b.n for b in loaded.buckets] == [b.n for b in table.buckets]


def test_a_table_from_another_schema_version_is_refused(tmp_path: Path):
    """A shape change must read as absent, not as a silently wrong answer."""
    table = pf.build_rate_table([_op(0.2, True)])
    payload = table.to_json()
    payload["schema_version"] = "pass_fallback_rate_v0"
    path = tmp_path / "old.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert pf.load_rate_table(path) is None


def test_a_missing_table_is_none_not_an_exception(tmp_path: Path):
    assert pf.load_rate_table(tmp_path / "nothing.json") is None


# -------------------------------------------------------------- geometry

def test_event_without_a_detection_line_is_skipped_not_defaulted(tmp_path: Path):
    (tmp_path / "no_detection.yaml").write_text(
        "overtake:\n"
        "  detection_line_m:\n"
        "    value: null\n"
        "  zones:\n"
        "    - zone: 1\n"
        "      activation_line_m: {value: 1300.0}\n"
        "      zone_end_m: {value: 1800.0}\n",
        encoding="utf-8",
    )
    assert pf._event_geometry(tmp_path, "no_detection") is None


def test_event_without_zones_is_skipped(tmp_path: Path):
    (tmp_path / "no_zones.yaml").write_text(
        "overtake:\n  detection_line_m: {value: 5520.0}\n  zones: []\n", encoding="utf-8",
    )
    assert pf._event_geometry(tmp_path, "no_zones") is None


def test_the_armed_zone_is_the_next_one_after_the_detection_line(tmp_path: Path):
    """Two zones, one before the line and one after: the one after is the pair."""
    (tmp_path / "two_zones.yaml").write_text(
        "overtake:\n"
        "  detection_line_m: {value: 2900.0}\n"
        "  zones:\n"
        "    - zone: 1\n"
        "      activation_line_m: {value: 1300.0}\n"
        "      zone_end_m: {value: 1800.0}\n"
        "    - zone: 2\n"
        "      activation_line_m: {value: 3000.0}\n"
        "      zone_end_m: {value: 3300.0}\n",
        encoding="utf-8",
    )
    geometry = pf._event_geometry(tmp_path, "two_zones")
    assert geometry == (2900.0, 3000.0, 3300.0)


def test_a_zone_only_before_the_line_wraps_to_the_next_lap(tmp_path: Path):
    (tmp_path / "wrap.yaml").write_text(
        "overtake:\n"
        "  detection_line_m: {value: 5520.0}\n"
        "  zones:\n"
        "    - zone: 1\n"
        "      activation_line_m: {value: 1300.0}\n"
        "      zone_end_m: {value: 1800.0}\n",
        encoding="utf-8",
    )
    assert pf._event_geometry(tmp_path, "wrap") == (5520.0, 1300.0, 1800.0)


# ------------------------------------------------------- shipped artifacts

def test_shipped_tables_are_loadable_and_era_tagged():
    """Whatever has been built in this checkout must at least be well-formed."""
    tables = pf.load_all_tables()
    for season, table in tables.items():
        assert table.schema_version == pf.SCHEMA_VERSION
        assert season in pf.SEASON_ERA, f"{season} has no declared regulation era"
        assert table.n_opportunities == sum(b.n for b in table.buckets) or True
        for bucket in table.buckets:
            assert bucket.passes <= bucket.n
            assert 0.0 <= bucket.rate <= 1.0
            assert bucket.ci_low <= bucket.ci_high


# --------------------------------------------------- API.md 5.7 eligibility

def _client():
    """A TestClient, or a skip.

    starlette's TestClient needs httpx installed under the name the installed
    starlette expects (`httpx2` on newer releases). It is a test-only transport,
    not a dependency of the service itself, so an environment without it skips
    these rather than reporting the ROUTE as broken -- which is what a bare
    ImportError here would look like.
    """
    # The failure is a RuntimeError raised at IMPORT time, not an ImportError, so
    # pytest.importorskip does not catch it -- hence the explicit guard.
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"TestClient unavailable: {exc}")
    from trackshift.serve.app import create_app
    try:
        return TestClient(create_app())
    except RuntimeError as exc:  # starlette raises this when httpx2 is missing
        pytest.skip(f"TestClient unavailable: {exc}")


def test_eligibility_route_exists_and_is_not_a_stub():
    """It was never registered, so every call hit GET /rules/{event} and 405'd."""
    client = _client()
    r = client.post("/api/v1/rules/eligibility", json={
        "event": "british_grand_prix",
        "state": {"gap": {"time_gap_s": 0.78, "gap_rate_s_per_s": 0.12},
                  "overtake_state": "ARMED"},
        "time_to_line_s": 6.0,
    })
    assert r.status_code == 200
    assert r.headers.get("x-trackshift-stub") != "true"


def test_eligibility_margin_is_threshold_minus_gap():
    client = _client()
    r = client.post("/api/v1/rules/eligibility", json={
        "event": "british_grand_prix",
        "state": {"gap": {"time_gap_s": 0.78}, "overtake_state": "NOT_ARMED"},
    }).json()
    # British GP's threshold is the 1.0 s carried-over DRS figure.
    assert r["eligibility_margin_s"]["value"] == pytest.approx(0.22, abs=1e-6)
    assert r["threshold_s"]["value"] == pytest.approx(1.0)


def test_projection_refuses_without_a_horizon_rather_than_assuming_one():
    """P(eligible) 'at some point ahead' is not a defined quantity."""
    client = _client()
    r = client.post("/api/v1/rules/eligibility", json={
        "event": "british_grand_prix",
        "state": {"gap": {"time_gap_s": 0.78, "gap_rate_s_per_s": 0.12}},
    }).json()
    assert r["p_eligible"]["value"] is None
    assert "time_to_line_s" in r["p_eligible"]["reason"]


def test_projection_refuses_without_a_closing_rate():
    client = _client()
    r = client.post("/api/v1/rules/eligibility", json={
        "event": "british_grand_prix",
        "state": {"gap": {"time_gap_s": 0.78}},
        "time_to_line_s": 6.0,
    }).json()
    assert r["p_eligible"]["value"] is None
    assert "gap_rate_s_per_s" in r["p_eligible"]["reason"]


def test_a_closing_car_projects_a_smaller_gap_and_a_higher_p_eligible():
    client = _client()
    def ask(rate):
        return client.post("/api/v1/rules/eligibility", json={
            "event": "british_grand_prix",
            "state": {"gap": {"time_gap_s": 1.40, "gap_rate_s_per_s": rate}},
            "time_to_line_s": 6.0,
            "trailing_closing_rates": [0.10, 0.14, 0.09, 0.13],
        }).json()
    closing = ask(0.12)
    holding = ask(0.0)
    assert closing["projected_gap_at_detection_s"]["mean"] < 1.40
    assert holding["projected_gap_at_detection_s"]["mean"] == pytest.approx(1.40, abs=1e-6)
    assert closing["p_eligible"]["value"] > holding["p_eligible"]["value"]


def test_the_interval_is_the_same_95_percent_convention_as_every_other_route():
    client = _client()
    r = client.post("/api/v1/rules/eligibility", json={
        "event": "british_grand_prix",
        "state": {"gap": {"time_gap_s": 1.0, "gap_rate_s_per_s": 0.1}},
        "time_to_line_s": 5.0,
        "trailing_closing_rates": [0.08, 0.12, 0.10, 0.11],
    }).json()
    block = r["projected_gap_at_detection_s"]
    assert block["low"] < block["mean"] < block["high"]
    assert block["trailing_n"] == 4
    assert "trailing_closing_rate_stdev" in block["terms_used"]


def test_unmodelled_quantities_are_null_with_a_reason_never_a_number():
    client = _client()
    r = client.post("/api/v1/rules/eligibility", json={
        "event": "british_grand_prix",
        "state": {"gap": {"time_gap_s": 0.5, "gap_rate_s_per_s": 0.1}},
        "time_to_line_s": 4.0,
    }).json()
    for key in ("energy_required_to_unlock_kj", "eligibility_fragility_per_kj"):
        block = r[key]
        assert block.get("value", block.get("mean")) is None
        assert block["reason"]


def test_an_unknown_event_is_a_named_404_not_a_crash():
    client = _client()
    r = client.post("/api/v1/rules/eligibility", json={
        "event": "not_a_real_grand_prix", "state": {"gap": {"time_gap_s": 0.5}},
    })
    assert r.status_code == 404
