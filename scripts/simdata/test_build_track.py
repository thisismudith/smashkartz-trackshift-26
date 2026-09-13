"""Tests for the geometry-session chooser, the sentinel wiring and the capabilities block.

The real-data tests measure against the raw mirror rather than a fixture, because the whole
point of the chooser is that it discriminates between two sessions of the SAME circuit
and no synthetic fixture can stand in for the failure modes the feed actually has
(a stale-anchor sample-and-hold at Hungary, a "position unknown" sentinel at China, a
position channel that is absent on 93 % of laps at Monaco).

They are slow -- a session scan reads every lap file once, 2.3-7.3 s -- so they are
confined to the three circuits where the decision is live plus one clean control, and
they share build_track's per-path caches, which pytest keeps for the whole process.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from simdata import build_track as bt
from simdata.glb_surface import (GATE_MAX_RESIDUAL_STD_M, GATE_MIN_COVERAGE, REPO_ROOT,
                                 Fit, SurfaceBake, load_registry, registry_entry)
from simdata.paths import data_root
from simdata.geom import Ring

#: AGENTS.md 13.6 plus DEFAULT. There is no seventh word; absence is `null`.
PROVENANCE_VOCABULARY = ("OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "RULE",
                         "DEFAULT")


DATA_PRESENT = data_root().exists()
needs_data = pytest.mark.skipif(not DATA_PRESENT,
                                 reason=f"no raw mirror at {data_root()}")


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
    race = bt.scan_session(data_root() / "Hungarian Grand Prix" / "Race")
    qual = bt.scan_session(data_root() / "Hungarian Grand Prix" / "Qualifying")
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
    choice = bt.geometry_choice(data_root() / "Hungarian Grand Prix")
    assert choice["chosen"] == "Qualifying"
    assert choice["margin"] > bt.SCORE_TIE_MARGIN
    sdir, name = bt.geometry_session(data_root() / "Hungarian Grand Prix")
    assert (sdir.name, name) == ("Qualifying", "Qualifying")


@needs_data
def test_monaco_geometry_comes_from_qualifying_on_coverage_not_lap_count():
    """Monaco's Race clears any lap-count floor yet has a position on 106 of 1452 laps,
    spanning 5 lap numbers of 78. The chooser has to see that, not the lap count."""
    race = bt.score_session(data_root() / "Monaco Grand Prix" / "Race", "Race")
    m = race["measured"]
    assert m["geometryLaps"] >= bt.MIN_RING_LAPS        # it clears the old floor...
    assert m["sessionLapsWithPositions"] < 0.1 * m["sessionLaps"]   # ...and is still blind
    assert m["sessionLapNumbersWithPositions"] <= 6
    assert race["components"]["positionCoverage"] < 0.1
    choice = bt.geometry_choice(data_root() / "Monaco Grand Prix")
    assert choice["chosen"] == "Qualifying"
    assert choice["margin"] > bt.SCORE_TIE_MARGIN


@needs_data
def test_china_keeps_the_race_because_qualifying_is_the_sentinel_session():
    """The chooser must not simply prefer Qualifying: China's Qualifying is 40 % sentinel
    while its Race is under 1 %."""
    race = bt.score_session(data_root() / "Chinese Grand Prix" / "Race", "Race")
    qual = bt.score_session(data_root() / "Chinese Grand Prix" / "Qualifying",
                            "Qualifying")
    assert race["measured"]["withdrawnFraction"] < 0.02
    assert qual["measured"]["withdrawnFraction"] > 0.35
    assert race["score"] > qual["score"]
    assert bt.geometry_choice(data_root() / "Chinese Grand Prix")["chosen"] == "Race"


@needs_data
def test_the_chinese_sentinel_is_discovered_and_withdrawn_not_left_in_place():
    """The sentinel must be found once per session and every sample on it withdrawn."""
    scan = bt.scan_session(data_root() / "Chinese Grand Prix" / "Race")
    assert scan["sentinelCount"] == 1
    px, py = scan["sentinelPoints"][0]
    assert math.hypot(px - (-832.5), py - (-705.8)) < 2.0
    assert scan["positionsWithdrawn"] > 5000
    # a withdrawn sample is ABSENT, not replaced: nothing may still sit on the sentinel
    from simdata.rawio import LapTable, load_lap
    table = LapTable(data_root() / "Chinese Grand Prix" / "Race")
    rows = [r for r in table.rows() if r["lap"] == 1][:4]
    for r in rows:
        lap = load_lap(data_root() / "Chinese Grand Prix" / "Race", r["drv"], r["lap"])
        before = int(scan["_sentinel"].mask(lap.x, lap.y).sum())
        dropped = bt.apply_sentinel([lap], scan["_sentinel"])
        assert dropped == before
        assert not scan["_sentinel"].mask(lap.x, lap.y)[np.isfinite(lap.x)].any()


@needs_data
def test_a_clean_circuit_keeps_the_race():
    """Regression guard: nothing may move the eleven circuits whose Race measures clean."""
    choice = bt.geometry_choice(data_root() / "British Grand Prix")
    assert choice["chosen"] == "Race"
    race = choice["candidates"][0]
    assert race["components"]["lapQuality"] > 0.95
    assert race["measured"]["positionsWithdrawn"] == 0
    assert race["measured"]["sentinelPoints"] == []
    assert race["measured"]["gateRejects"] == {}


# --------------------------------------------------------------- 3D surface
#
# The rule these tests exist to hold: "for available use the 3d environment, otherwise
# normal sim as it is working". A circuit the registry does not admit must come out of
# the build byte-for-byte as it does today, apart from the schemaVersion bump -- so every
# way of declining is tested, and the passing circuit is tested against the SAME build
# with the registry taken away.


_FITTED_SHA = "c0ffee11" + "0" * 56          # 64 hex chars, as sha256_file returns
_OTHER_SHA = "deadbeef" + "1" * 56


def _fake_bake(n, *, coverage=None, residual_std=0.05, sha=_FITTED_SHA):
    """A SurfaceBake with the shape a real one has, without a 158 MB asset.

    One station is deliberately invalid: a station the raycast missed must survive the
    whole path as `null`, never as a plausible-looking height.
    """
    valid = np.ones(n, dtype=bool)
    valid[17] = False
    z = np.linspace(1.0, 1.7, n)
    slope = np.full(n, 0.0100)
    camber = np.full(n, 0.0050)
    residual = np.zeros(n)
    edge_left = np.full(n, 8.0)
    edge_right = np.full(n, 6.5)
    for arr in (z, slope, camber, residual, edge_left, edge_right):
        arr[~valid] = np.nan
    return SurfaceBake(
        z_m=z, slope_rad=slope, camber_rad=camber, camber_base_m=np.full(n, 2.0),
        valid=valid, camber_valid=valid.copy(), residual_m=residual,
        edge_left_m=edge_left, edge_right_m=edge_right,
        fit=Fit(scale=1.0, yaw_rad=0.0, mirror=-1, tx=1.0, tz=2.0, ty=3.0),
        coverage=float(valid.mean()) if coverage is None else coverage,
        road_coverage=0.99, residual_std_m=residual_std,
        residual_max_m=residual_std * 3.0, largest_gap_stations=1, ds_m=1.0,
        source="fake.glb", sha256=sha, primitives={"asphalt.001": int(valid.sum())})


def _fake_entry(**over):
    entry = {"event": "Fake Grand Prix", "glb": "tracks/fake.glb",
             "sha256": _FITTED_SHA, "profile": "edelta-scorer",
             "fit": {"scale": 1.0, "yawDeg": 0.0, "mirror": -1,
                     "txM": 1.0, "tzM": 2.0, "tyM": 3.0},
             "measured": {"coverage": 0.9984, "residualStdM": 0.05, "gate": "pass"}}
    entry.update(over)
    return entry


def _publish(tmp_path, slug, blob=b"published glb bytes, reduced textures"):
    """Write a fake published asset under the fake repo root, named after its own bytes.

    The publisher downscales textures, so the published bytes are NOT the source bytes
    and the two hashes differ -- which is exactly what these tests have to reproduce.
    """
    sha = hashlib.sha256(blob).hexdigest()
    out = tmp_path.joinpath(*bt.GLB_PUBLISH_SUBDIR)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{slug}.{sha[:bt.GLB_SHA_CHARS]}.glb").write_bytes(blob)
    return sha


def _install(monkeypatch, tmp_path, entry, bake=None, *, exc=None, asset=True):
    """Point _surface_for at a fake registry, a fake asset root and a fake bake.

    Returns the list the bake seam appends to, so a test can prove the expensive half
    was never reached.
    """
    reached = []
    if asset:
        (tmp_path / "tracks").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tracks" / "fake.glb").write_bytes(b"not a real glb")
    monkeypatch.setattr(bt, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bt, "registry_entry", lambda slug: entry)

    def seam(e, path, ring):
        reached.append(path)
        if exc is not None:
            raise exc
        return bake

    monkeypatch.setattr(bt, "_bake_surface_for", seam)
    return reached


def test_a_circuit_with_no_registry_entry_gets_nothing_and_reads_no_asset(monkeypatch,
                                                                          tmp_path):
    """Eleven of thirteen circuits are this case. It must cost nothing at all."""
    ring = _circle_ring()
    reached = _install(monkeypatch, tmp_path, None, _fake_bake(ring.n))
    assert bt._surface_for("monaco-grand-prix", ring) is None
    assert reached == []


def test_a_failed_gate_gets_nothing_and_reads_no_asset(monkeypatch, tmp_path):
    """chinese-grand-prix: residual std 0.306 m against a 0.15 m limit. The gate is an
    admission test, so this returns None -- it does not fail the build."""
    ring = _circle_ring()
    entry = _fake_entry(measured={"residualStdM": 0.306146, "gate": "FAIL"})
    reached = _install(monkeypatch, tmp_path, entry, _fake_bake(ring.n))
    assert bt._surface_for("chinese-grand-prix", ring) is None
    assert reached == []


def test_an_absent_asset_gets_nothing(monkeypatch, tmp_path):
    """data/ is gitignored, so a checkout with no 97-158 MB asset is the normal case."""
    ring = _circle_ring()
    reached = _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n),
                       asset=False)
    assert bt._surface_for("british-grand-prix", ring) is None
    assert reached == []


def test_a_bake_that_raises_gets_nothing_and_the_build_continues(monkeypatch, tmp_path):
    ring = _circle_ring()
    reached = _install(monkeypatch, tmp_path, _fake_entry(),
                       exc=RuntimeError("KHR_draco_mesh_compression"))
    assert bt._surface_for("british-grand-prix", ring) is None
    assert len(reached) == 1          # it tried, it failed, it declined


def test_an_entry_missing_its_transform_or_profile_gets_nothing(monkeypatch, tmp_path):
    """Neither is defaulted. A block that cannot say which extractor produced it, or
    under what transform, is not a block we are willing to ship."""
    ring = _circle_ring()
    for over in ({"fit": None}, {"profile": None}):
        reached = _install(monkeypatch, tmp_path, _fake_entry(**over),
                           _fake_bake(ring.n))
        assert bt._surface_for("british-grand-prix", ring) is None
        assert reached == []


def test_a_passing_circuit_gains_a_block_whose_arrays_match_the_ring(monkeypatch,
                                                                     tmp_path):
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n))
    block = bt._surface_for("british-grand-prix", ring)
    assert block is not None
    for key in bt.SURFACE_STATION_ARRAYS:
        assert len(block[key]) == ring.n, key
    assert block["profile"] == "edelta-scorer"


def test_the_surface_arrays_are_centimetre_quantised_ints_or_null(monkeypatch, tmp_path):
    """Same quantisation as ring.xCm -- and a station the raycast missed is `null`,
    which is what absence looks like. There is no seventh provenance and no default."""
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n))
    block = bt._surface_for("british-grand-prix", ring)
    for key in ("zCm", "slopePermille", "camberPermille"):
        assert all(v is None or isinstance(v, int) for v in block[key]), key
        assert block[key][17] is None, key
    assert set(block["validMask"]) == {0, 1}
    assert all(isinstance(v, int) for v in block["validMask"])
    assert block["validMask"][17] == 0
    assert block["zCm"][0] == 100          # 1.0 m -> 100 cm
    assert block["slopePermille"][0] == 10 # tan(0.01 rad) -> 10 permille


def test_the_surface_block_is_derived_and_survives_strict_json(monkeypatch, tmp_path):
    """DERIVED, not OBSERVED: no car measured these heights."""
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n))
    block = bt._surface_for("british-grand-prix", ring)
    assert block["provenance"].split()[0] in PROVENANCE_VOCABULARY
    assert block["provenance"].startswith("DERIVED")
    json.dumps(block, allow_nan=False)


def test_the_asset_url_names_the_published_bytes_not_the_source(monkeypatch, tmp_path):
    """The frozen contract is /sim/glb/<slug>.<sha10>.glb where sha10 is of the PUBLISHED
    bytes. simdata.glb_publish downscales three textures first, so those bytes are not
    the bytes the transform was fitted to -- silverstone.glb hashes 228c897e5c in
    data/tracks and cfb1d61f58 in frontend/public/sim/glb. Naming the URL after the
    source hash would be a 404."""
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n))
    published = _publish(tmp_path, "british-grand-prix")
    assert published != _FITTED_SHA
    block = bt._surface_for("british-grand-prix", ring)
    assert block["assetSha256"] == published
    assert block["assetUrl"] == f"/sim/glb/british-grand-prix.{published[:10]}.glb"
    # ...and the source hash is still recorded, separately, as what the fit was measured
    # against. Conflating the two would claim a fit to a file that never existed.
    assert block["sourceSha256"] == _FITTED_SHA
    assert bt.glb_asset_url("x-grand-prix", _OTHER_SHA) == \
        f"/sim/glb/x-grand-prix.{_OTHER_SHA[:10]}.glb"


def test_an_unpublished_asset_leaves_the_url_null_and_keeps_the_heights(monkeypatch,
                                                                        tmp_path):
    """Publishing is a separate step with a separate owner. Before it has run there is
    no published filename to know, so both fields are null -- absence, never a guess --
    and the block still carries the heights, which are useful on the ribbon on their
    own. The artifact must be rebuilt after publishing for the model to load."""
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n))
    block = bt._surface_for("british-grand-prix", ring)
    assert block["assetUrl"] is None and block["assetSha256"] is None
    assert len(block["zCm"]) == ring.n


def test_a_published_file_that_is_not_its_own_name_is_not_advertised(monkeypatch,
                                                                     tmp_path):
    """A filename is a claim; the bytes are the evidence. A half-written or hand-copied
    asset must not be advertised as a hash the browser would be told to trust."""
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n))
    out = tmp_path.joinpath(*bt.GLB_PUBLISH_SUBDIR)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"british-grand-prix.{_OTHER_SHA[:10]}.glb").write_bytes(b"different bytes")
    block = bt._surface_for("british-grand-prix", ring)
    assert block["assetUrl"] is None and block["assetSha256"] is None


def test_the_published_path_agrees_with_the_publisher():
    """The other half of the frozen contract lives in simdata.glb_publish. If the two
    ever disagree the artifact points at a file nothing wrote."""
    pub = pytest.importorskip("simdata.glb_publish")
    assert bt.REPO_ROOT.joinpath(*bt.GLB_PUBLISH_SUBDIR) == pub.PUBLISH_DIR
    assert bt.GLB_URL_PREFIX == pub.URL_PREFIX
    assert bt.GLB_SHA_CHARS == pub.SHA_PREFIX
    assert bt.glb_asset_url("british-grand-prix", _OTHER_SHA) == \
        pub.published_url("british-grand-prix", _OTHER_SHA)


def test_an_asset_that_is_not_the_one_that_was_fitted_is_refused(monkeypatch, tmp_path):
    """A recorded transform belongs to specific bytes. Different bytes, different model:
    the fit means nothing and the published sha10 would name the wrong file."""
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n, sha=_OTHER_SHA))
    assert bt._surface_for("british-grand-prix", ring) is None


def test_a_registry_pass_does_not_override_what_this_build_measures(monkeypatch,
                                                                    tmp_path):
    """A stale `measured` block is how a circuit that has stopped aligning keeps
    shipping. The bake has already computed both numbers, so re-reading the gate here
    is free -- and it is the difference between a surface and a claim about one."""
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n, residual_std=0.4))
    assert bt._surface_for("british-grand-prix", ring) is None
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n, coverage=0.93))
    assert bt._surface_for("british-grand-prix", ring) is None


def test_arrays_that_are_not_ring_length_are_refused(monkeypatch, tmp_path):
    """Station i of the surface must be station i of the ring. A length mismatch would
    stand every car on the wrong part of the circuit, quietly."""
    ring = _circle_ring()
    _install(monkeypatch, tmp_path, _fake_entry(), _fake_bake(ring.n - 1))
    assert bt._surface_for("british-grand-prix", ring) is None


def test_the_registry_admits_exactly_the_circuit_that_passes():
    """The shipped registry, not a fixture: one circuit in, one out, eleven absent."""
    circuits = load_registry().get("circuits") or {}
    admitted = {slug for slug, e in circuits.items()
                if (e.get("measured") or {}).get("gate") == "pass"}
    assert admitted == {"british-grand-prix"}
    assert set(circuits) - admitted == {"chinese-grand-prix"}


# --------------------------------------------------- 3D surface, real data

_BRITISH_GLB = REPO_ROOT / ((registry_entry("british-grand-prix") or {}).get("glb") or "")
needs_glb = pytest.mark.skipif(not _BRITISH_GLB.is_file(),
                               reason=f"{_BRITISH_GLB} is gitignored and absent")


@needs_data
@needs_glb
def test_british_gains_a_surface_and_changes_nothing_else():
    """The whole contract, on the real asset, in one build pair.

    Both halves matter: the surface has to be there AND the rest of the artifact has to
    be exactly what it is without it. The second build reuses the cached ring and
    session scan, so it costs seconds, not another read of a 158 MB model.
    """
    model = bt.build_track_model("British Grand Prix")
    assert model["schemaVersion"] == 2
    surface = model["surface"]

    n = len(model["ring"]["xCm"])
    for key in bt.SURFACE_STATION_ARRAYS:
        assert len(surface[key]) == n, key
    assert all(v is None or isinstance(v, int) for v in surface["zCm"])

    # the measured numbers, re-read from the artifact rather than from the registry
    assert surface["coverage"] >= GATE_MIN_COVERAGE
    assert surface["residual"]["stdM"] <= GATE_MAX_RESIDUAL_STD_M
    assert surface["profile"] == registry_entry("british-grand-prix")["profile"]
    # sourceSha256 is the asset the fit was measured against; assetSha256 is whatever
    # the publisher wrote, which is a different file once its textures are reduced.
    assert surface["sourceSha256"] == registry_entry("british-grand-prix")["sha256"]
    if surface["assetUrl"] is None:
        assert surface["assetSha256"] is None      # nothing published on this machine
    else:
        sha10 = surface["assetSha256"][:10]
        assert surface["assetUrl"] == f"/sim/glb/british-grand-prix.{sha10}.glb"
        assert bt.REPO_ROOT.joinpath(*bt.GLB_PUBLISH_SUBDIR,
                                     f"british-grand-prix.{sha10}.glb").is_file()
    assert surface["provenance"].startswith("DERIVED")
    assert model["provenance"]["surface"] == surface["provenance"]

    # ...and now the same event with no registry entry at all: version 1's artifact,
    # key for key, plus the schemaVersion bump.
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(bt, "registry_entry", lambda slug: None)
        plain = bt.build_track_model("British Grand Prix")
    finally:
        mp.undo()
    assert plain["schemaVersion"] == 2
    assert "surface" not in plain and "surface" not in plain["provenance"]

    # A registered circuit ALSO measures the model's own height under the pit lane --
    # the one other thing in the artifact that was being drawn at the racing surface's
    # height for want of a measurement. It is a real measurement of a real road, so it
    # must differ from that drape rather than reproduce it.
    for seg in model["pitLanePath"]["segments"]:
        assert len(seg["surfaceZCm"]) == len(seg["xCm"])
        assert 0.0 <= seg["surfaceCoverage"] <= 1.0
        assert any(v is not None for v in seg["surfaceZCm"]), seg["role"]
    assert "surfaceElevation" in model["pitLanePath"]["provenance"]
    assert all("surfaceZCm" not in s for s in plain["pitLanePath"]["segments"])

    stripped = dict(model)
    stripped.pop("surface")
    stripped["provenance"] = {k: v for k, v in model["provenance"].items()
                              if k != "surface"}
    stripped["pitLanePath"] = _without_baked_pit_z(model["pitLanePath"])
    assert _canonical(stripped) == _canonical(plain)


def _without_baked_pit_z(pit_path: dict) -> dict:
    """`pitLanePath` as a circuit with no model would emit it: the baked height gone."""
    out = dict(pit_path)
    out["segments"] = [{k: v for k, v in s.items()
                        if k not in ("surfaceZCm", "surfaceCoverage")}
                       for s in pit_path["segments"]]
    out["provenance"] = {k: v for k, v in pit_path["provenance"].items()
                         if k != "surfaceElevation"}
    return out


@needs_data
def test_a_failing_circuit_is_the_same_artifact_and_never_opens_its_asset():
    """chinese-grand-prix is registered, has its 97 MB asset on disk, and fails the gate.
    It must produce today's artifact and must not spend a second reading the model."""
    mp = pytest.MonkeyPatch()
    mp.setattr(bt, "_bake_surface_for", _must_not_be_called)
    try:
        model = bt.build_track_model("Chinese Grand Prix")
    finally:
        mp.undo()
    assert model["schemaVersion"] == 2
    assert "surface" not in model
    assert "surface" not in model["provenance"]


def _must_not_be_called(*a, **kw):
    raise AssertionError("a circuit that fails the gate must never read its asset")


def _canonical(model: dict) -> str:
    """The artifact as it would be written: the same _json_safe the writer uses."""
    from build_sim_data import _json_safe
    return json.dumps(_json_safe(model), sort_keys=True, allow_nan=False)
