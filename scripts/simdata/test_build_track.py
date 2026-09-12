"""Tests for the geometry-session chooser, the sentinel wiring and the capabilities block.

The real-data tests measure against data/2026 rather than a fixture, because the whole
point of the chooser is that it discriminates between two sessions of the SAME circuit
and no synthetic fixture can stand in for the failure modes the feed actually has
(a stale-anchor sample-and-hold at Hungary, a "position unknown" sentinel at China, a
position channel that is absent on 93 % of laps at Monaco).

They are slow -- a session scan reads every lap file once, 2.3-7.3 s -- so they are
confined to the three circuits where the decision is live plus one clean control, and
they share build_track's per-path caches, which pytest keeps for the whole process.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from simdata import build_track as bt
from simdata.geom import Ring


DATA_PRESENT = bt.DATA_ROOT.exists()
needs_data = pytest.mark.skipif(not DATA_PRESENT, reason="data/2026 not available")


# --------------------------------------------------------------- pure / fast


def test_score_weights_sum_to_one():
    """A score has to read as a fraction of what a session would need to supply."""
    total = (bt.W_LAP_QUALITY + bt.W_POSITION_COVERAGE + bt.W_POSITION_INTEGRITY
             + bt.W_VOLUME + bt.W_DRIVER_SPREAD)
    assert total == pytest.approx(1.0)


def _clean_quality(**over):
    """A lap measured like every healthy session: British Race medians."""
    q = {"xyFraction": 1.0, "pathOverSpan": 1.0016, "uniqueFrac": 1.0,
         "repeatBackFrac": 0.0, "endGapM": 2.73, "medianStepM": 6.87}
    q.update(over)
    return q


def test_position_gate_accepts_a_healthy_lap():
    assert bt.lap_position_usable(_clean_quality())


def test_position_gate_rejects_the_hungarian_stale_anchor():
    """Measured Hungary Race geometry laps: uniqueFrac 0.40, repeatBack 0.46, step 0.017 m.

    pathOverSpan alone does NOT separate them -- 15.8 % of the corrupt laps sit inside
    [0.97, 1.03] -- so the gate must fail on the structural clauses.
    """
    hungary = _clean_quality(uniqueFrac=0.4046, repeatBackFrac=0.4643,
                             medianStepM=0.0166, pathOverSpan=1.0956)
    assert not bt.lap_position_usable(hungary)
    # each structural clause has to reject on its own, with pathOverSpan left healthy
    assert not bt.lap_position_usable(_clean_quality(uniqueFrac=0.4046))
    assert not bt.lap_position_usable(_clean_quality(repeatBackFrac=0.4643))
    assert not bt.lap_position_usable(_clean_quality(medianStepM=0.0166))


def test_position_gate_names_the_clause_that_failed():
    """A gate that only says "no" cannot report what a session lost."""
    assert bt.position_gate_reason(_clean_quality()) is None
    assert bt.position_gate_reason(_clean_quality(uniqueFrac=0.40)) == "unique-frac"
    assert bt.position_gate_reason(_clean_quality(repeatBackFrac=0.46)) == "repeat-back"
    assert bt.position_gate_reason(_clean_quality(medianStepM=0.017)) == "median-step"
    assert bt.position_gate_reason(_clean_quality(pathOverSpan=1.31)) == "path-over-span"
    assert bt.position_gate_reason(_clean_quality(endGapM=1147.6)) == "end-gap"
    assert bt.position_gate_reason(_clean_quality(xyFraction=0.0)) == "xy-fraction"


def test_position_gate_rejects_an_unclosed_lap():
    """build_ring welds first sample to last, so a 1147 m end gap adds a phantom straight."""
    assert not bt.lap_position_usable(_clean_quality(endGapM=1147.6))


def test_position_gate_rejects_missing_measurements_rather_than_assuming_them():
    """An unmeasurable lap is not a usable lap: null must never read as 'fine'."""
    for key in ("uniqueFrac", "medianStepM", "pathOverSpan", "endGapM"):
        assert not bt.lap_position_usable(_clean_quality(**{key: None}))


def _seed_score(tmp_path, name, score, **components):
    """Register a fake scored session so ranking can be tested without reading laps."""
    sdir = tmp_path / name
    sdir.mkdir(parents=True, exist_ok=True)
    comps = {"lapQuality": 1.0, "positionCoverage": 1.0, "positionIntegrity": 1.0,
             "volume": 1.0, "driverSpread": 1.0}
    comps.update(components)
    bt._SCORE_CACHE[str(sdir.resolve())] = {
        "session": name, "score": score, "eligible": True,
        "components": comps,
        "measured": {"sessionLaps": 100, "sessionLapsWithPositions": 100,
                     "sessionLapNumbers": 50, "sessionLapNumbersWithPositions": 50,
                     "positionsWithdrawn": 0, "withdrawnFraction": 0.0},
        "provenance": "DERIVED (test fixture)",
    }
    return sdir


def test_a_tie_keeps_the_race(tmp_path):
    """Equal sessions must not flip the geometry away from the session everything else
    is read from. Only a measured margin may move it."""
    _seed_score(tmp_path, "Race", 1.0)
    _seed_score(tmp_path, "Qualifying", 1.0)
    assert bt.geometry_choice(tmp_path)["chosen"] == "Race"
    # and a Qualifying lead smaller than the tie margin is still a tie
    bt._SCORE_CACHE[str((tmp_path / "Race").resolve())]["score"] = 0.99
    choice = bt.geometry_choice(tmp_path)
    assert choice["chosen"] == "Race"
    assert "within" in choice["reason"]


def test_a_measured_margin_moves_the_geometry(tmp_path):
    _seed_score(tmp_path, "Race", 0.45, lapQuality=0.0, volume=0.0)
    _seed_score(tmp_path, "Qualifying", 1.0)
    choice = bt.geometry_choice(tmp_path)
    assert choice["chosen"] == "Qualifying"
    assert choice["margin"] == pytest.approx(0.55)
    assert "lapQuality" in choice["reason"]


def test_grid_session_prefers_a_standing_start(tmp_path):
    """Qualifying lap 1 is an out-lap: the grid must never follow the geometry there."""
    (tmp_path / "Race").mkdir()
    (tmp_path / "Qualifying").mkdir()
    assert bt.grid_session(tmp_path, tmp_path / "Qualifying")[1] == "Race"


def test_grid_session_falls_back_to_the_geometry_session(tmp_path):
    (tmp_path / "Qualifying").mkdir()
    gdir, gname = bt.grid_session(tmp_path, tmp_path / "Qualifying")
    assert (gdir, gname) == (tmp_path / "Qualifying", "Qualifying")


def _choice_fixture(coverage=1.0, integrity=1.0, quality=1.0, laps=920, lap_numbers=56):
    """One scored session, kept internally consistent: the counts match the fractions."""
    samples = 700_000
    row = {
        "session": "Race", "score": 1.0, "eligible": True,
        "components": {"lapQuality": quality, "positionCoverage": coverage,
                       "positionIntegrity": integrity, "volume": 1.0,
                       "driverSpread": 1.0},
        "measured": {
            "sessionLaps": laps,
            "sessionLapsWithPositions": round(coverage * laps),
            "sessionLapNumbers": lap_numbers,
            "sessionLapNumbersWithPositions": round(coverage * lap_numbers),
            "positionsWithdrawn": round((1.0 - integrity) * samples),
            "withdrawnFraction": round(1.0 - integrity, 6),
        },
    }
    return {"chosen": "Race", "candidates": [row]}


_TL = {"sf": {"station": 0.0}, "s1": {"station": 1.0}, "s2": {"station": 2.0}}
_PIT = {"entryStation": 10.0, "mergeStation": 20.0}
_CORNERS = {"corners": [{"number": 1}]}


def test_capabilities_do_not_claim_positions_when_the_grid_is_unrecoverable():
    """China: the on-track trace is clean, but every lap-1 first sample is the sentinel,
    so 0 of 18 cars can be placed. hasPositions must not be True for that session."""
    caps = bt._capabilities(_choice_fixture(), {"order": []}, 18, "Race",
                            _TL, _CORNERS, _PIT)
    assert caps["hasGrid"] is False
    assert caps["hasPositions"] is False
    # ...and the narrower, still-true claim is kept separately rather than being lost
    assert caps["hasTrackPositions"] is True
    assert caps["grid"] == {"session": "Race", "ordered": 0, "fieldSize": 18,
                            "coverage": 0.0, "trustFraction": bt.GRID_TRUST_FRACTION}
    assert any("0 of 18" in n for n in caps["notes"])


def test_capabilities_claim_positions_only_with_a_trusted_grid():
    caps = bt._capabilities(_choice_fixture(), {"order": list("ABCDEFGHIJKLMNOPQRST")},
                            20, "Race", _TL, _CORNERS, _PIT)
    assert caps["hasGrid"] is True and caps["hasPositions"] is True
    assert caps["notes"] == []


def test_capabilities_report_a_session_whose_positions_are_absent():
    """Monaco Race: 106 of 1452 laps carry a position. That cannot read as hasPositions."""
    choice = _choice_fixture(coverage=106 / 1452, laps=1452, lap_numbers=78)
    caps = bt._capabilities(choice, {"order": ["A", "B"]}, 22, "Race",
                            _TL, _CORNERS, _PIT)
    assert caps["sessions"]["Race"]["hasPositions"] is False
    assert caps["hasTrackPositions"] is False and caps["hasPositions"] is False
    note = next(n for n in caps["notes"] if n.startswith("Race:"))
    assert "106 of 1452" in note


def test_capabilities_notes_name_the_criterion_that_failed():
    """Hungary's Race has a position on every lap and withdraws nothing -- what it
    lacks is a trace. A note citing only coverage would read as if nothing were wrong."""
    caps = bt._capabilities(_choice_fixture(quality=0.081), {"order": []}, 22, "Race",
                            _TL, _CORNERS, _PIT)
    note = next(n for n in caps["notes"] if n.startswith("Race:"))
    assert "geometry gate" in note and "8.1%" in note
    assert "sentinel" not in note      # nothing was withdrawn, so do not imply it was
    assert "of 920 laps" not in note   # coverage is fine, so do not imply it is not


def test_capabilities_are_measured_not_defaulted():
    """No capability may be True without the measurement behind it being present."""
    caps = bt._capabilities(_choice_fixture(), {"order": []}, 0, "Race",
                            {"sf": None}, None, {"entryStation": None,
                                                  "mergeStation": None})
    assert caps["hasTimingLines"] is False
    assert caps["hasSectorLines"] is False
    assert caps["hasCorners"] is False
    assert caps["hasPitLane"] is False
    assert caps["hasGrid"] is False
    assert json.dumps(caps)  # every value must survive strict JSON


def _circle_ring(radius=100.0, ds=1.0):
    th = np.arange(0.0, 2 * np.pi, ds / radius)
    return Ring(radius * np.cos(th), radius * np.sin(th), np.zeros_like(th), ds)


def test_ring_emission_error_is_small_for_the_polyline_actually_written():
    ring = _circle_ring()
    x_cm = [int(round(v * 100)) for v in ring.x]
    y_cm = [int(round(v * 100)) for v in ring.y]
    assert bt._ring_emission_error(ring, x_cm, y_cm) < 0.5


def test_ring_emission_error_catches_a_declared_length_that_is_wrong():
    """Hungary shipped lengthMetres 4583.0 for a 4308.5 m polyline. The guard must see
    a 6.4 % overstatement, which is what that was."""
    ring = _circle_ring()
    ring.length = ring.length * 1.064
    x_cm = [int(round(v * 100)) for v in ring.x]
    y_cm = [int(round(v * 100)) for v in ring.y]
    assert bt._ring_emission_error(ring, x_cm, y_cm) > 0.5


# --------------------------------------------------------------- real data


@needs_data
def test_hungary_race_position_channel_fails_the_gate_and_qualifying_does_not():
    """Measured: Hungary's Race position channel is a stale-anchor sample-and-hold.

    The ratio is taken over the session's whole CLEAN-lap pool on purpose. Scoring only
    the laps the picker survives would hide this, because the picker upstream already
    discards them -- the session would then look as good as its leftovers.
    """
    race = bt.scan_session(bt.DATA_ROOT / "Hungarian Grand Prix" / "Race")
    qual = bt.scan_session(bt.DATA_ROOT / "Hungarian Grand Prix" / "Qualifying")
    assert race["cleanLaps"] > 500          # it is not short of laps...
    assert race["lapQuality"] < 0.5         # ...they just are not positions
    assert qual["lapQuality"] > 0.95
    # and the loss is attributed, not just counted
    assert sum(race["gateRejects"].values()) == race["cleanLaps"] - race["cleanLapsUsable"]
    assert race["gateRejects"]


@needs_data
def test_hungary_geometry_comes_from_qualifying():
    """Before: geometry_session took the first session clearing a lap floor, i.e. Race,
    and the ring came out 4583 m for a 4308.5 m polyline."""
    choice = bt.geometry_choice(bt.DATA_ROOT / "Hungarian Grand Prix")
    assert choice["chosen"] == "Qualifying"
    assert choice["margin"] > bt.SCORE_TIE_MARGIN
    sdir, name = bt.geometry_session(bt.DATA_ROOT / "Hungarian Grand Prix")
    assert (sdir.name, name) == ("Qualifying", "Qualifying")


@needs_data
def test_monaco_geometry_comes_from_qualifying_on_coverage_not_lap_count():
    """Monaco's Race clears any lap-count floor yet has a position on 106 of 1452 laps,
    spanning 5 lap numbers of 78. The chooser has to see that, not the lap count."""
    race = bt.score_session(bt.DATA_ROOT / "Monaco Grand Prix" / "Race", "Race")
    m = race["measured"]
    assert m["geometryLaps"] >= bt.MIN_RING_LAPS        # it clears the old floor...
    assert m["sessionLapsWithPositions"] < 0.1 * m["sessionLaps"]   # ...and is still blind
    assert m["sessionLapNumbersWithPositions"] <= 6
    assert race["components"]["positionCoverage"] < 0.1
    choice = bt.geometry_choice(bt.DATA_ROOT / "Monaco Grand Prix")
    assert choice["chosen"] == "Qualifying"
    assert choice["margin"] > bt.SCORE_TIE_MARGIN


@needs_data
def test_china_keeps_the_race_because_qualifying_is_the_sentinel_session():
    """The chooser must not simply prefer Qualifying: China's Qualifying is 40 % sentinel
    while its Race is under 1 %."""
    race = bt.score_session(bt.DATA_ROOT / "Chinese Grand Prix" / "Race", "Race")
    qual = bt.score_session(bt.DATA_ROOT / "Chinese Grand Prix" / "Qualifying",
                            "Qualifying")
    assert race["measured"]["withdrawnFraction"] < 0.02
    assert qual["measured"]["withdrawnFraction"] > 0.35
    assert race["score"] > qual["score"]
    assert bt.geometry_choice(bt.DATA_ROOT / "Chinese Grand Prix")["chosen"] == "Race"


@needs_data
def test_the_chinese_sentinel_is_discovered_and_withdrawn_not_left_in_place():
    """The sentinel must be found once per session and every sample on it withdrawn."""
    scan = bt.scan_session(bt.DATA_ROOT / "Chinese Grand Prix" / "Race")
    assert scan["sentinelCount"] == 1
    px, py = scan["sentinelPoints"][0]
    assert math.hypot(px - (-832.5), py - (-705.8)) < 2.0
    assert scan["positionsWithdrawn"] > 5000
    # a withdrawn sample is ABSENT, not replaced: nothing may still sit on the sentinel
    from simdata.rawio import LapTable, load_lap
    table = LapTable(bt.DATA_ROOT / "Chinese Grand Prix" / "Race")
    rows = [r for r in table.rows() if r["lap"] == 1][:4]
    for r in rows:
        lap = load_lap(bt.DATA_ROOT / "Chinese Grand Prix" / "Race", r["drv"], r["lap"])
        before = int(scan["_sentinel"].mask(lap.x, lap.y).sum())
        dropped = bt.apply_sentinel([lap], scan["_sentinel"])
        assert dropped == before
        assert not scan["_sentinel"].mask(lap.x, lap.y)[np.isfinite(lap.x)].any()


@needs_data
def test_a_clean_circuit_keeps_the_race():
    """Regression guard: nothing may move the eleven circuits whose Race measures clean."""
    choice = bt.geometry_choice(bt.DATA_ROOT / "British Grand Prix")
    assert choice["chosen"] == "Race"
    race = choice["candidates"][0]
    assert race["components"]["lapQuality"] > 0.95
    assert race["measured"]["positionsWithdrawn"] == 0
    assert race["measured"]["sentinelPoints"] == []
    assert race["measured"]["gateRejects"] == {}
