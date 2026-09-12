"""Tests for static track segmentation (CP-05, M03, contract C1).

`segment_id` is the join key for baselines, pairwise features, opportunities and
the DP's state grid. If it ever means a different piece of tarmac, every one of
those tables is silently wrong and nothing crashes. So the tests here care most
about *stability* -- that boundaries are a property of the circuit and not of a
lap -- and only secondarily about whether the classification is pretty.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.track.segmentation import (  # noqa: E402
    CORNER_TYPES,
    DEFAULTS,
    SEGMENT_KINDS,
    Boundary,
    boundary_hash,
    build_segments,
    classify_corner_type,
    detect_brake_onsets,
    detect_envelope_taper,
    detect_throttle_returns,
    median_profile,
    merge_short_segments,
    smooth,
    track_heading_deg,
)

GEOMETRY_DIR = ROOT / "config" / "geometry"
SEGMENTS_DIR = ROOT / "data" / "processed" / "segments"


def synthetic_profile(length_m: float = 1000.0, spacing: float = 20.0) -> dict:
    """A straight, then braking, then a corner, then back to power."""
    n = int(length_m / spacing)
    distance = [i * spacing for i in range(n)]
    brake, throttle, speed, xs, ys = [], [], [], [], []
    for d in distance:
        braking = 400 <= d < 560
        cornering = 560 <= d < 700
        brake.append(1.0 if braking else 0.0)
        throttle.append(0.0 if braking or cornering else 100.0)
        speed.append(120.0 if cornering else (300.0 if d < 400 else 200.0))
        xs.append(d)
        # a curve only through the corner section
        ys.append(0.0 if not cornering else (d - 560) ** 2 / 400.0)
    return {"distance_m": distance, "brake_on": brake, "throttle_pct": throttle,
            "speed_kmh": speed, "x_m": xs, "y_m": ys,
            "lap_count": [10] * n}


# ------------------------------------------------------------- median_profile
def test_median_profile_collapses_laps_to_one_curve():
    """The step that makes boundaries a circuit property rather than a lap's."""
    rows = []
    for offset in (-1.0, 0.0, 1.0):  # three laps braking at slightly different points
        for d in (0.0, 20.0, 40.0):
            rows.append({"distance_m": d, "brake_on": 0.0, "throttle_pct": 100.0 + offset,
                         "speed_kmh": 200.0 + offset, "x_m": d, "y_m": 0.0})
    profile = median_profile(rows)
    assert profile["distance_m"] == [0.0, 20.0, 40.0]
    assert profile["lap_count"] == [3, 3, 3]
    assert profile["speed_kmh"] == [200.0, 200.0, 200.0]  # the median, not an outlier


def test_median_profile_tolerates_missing_channels():
    profile = median_profile([{"distance_m": 0.0, "speed_kmh": None, "x_m": 1.0, "y_m": 2.0}])
    assert profile["speed_kmh"] == [None]


def test_median_profile_ignores_rows_without_distance():
    assert median_profile([{"speed_kmh": 100.0}])["distance_m"] == []


# ----------------------------------------------------------------- detectors
def test_brake_onset_is_detected_once_per_braking_event():
    onsets = detect_brake_onsets(synthetic_profile())
    assert len(onsets) == 1
    assert onsets[0].distance_m == pytest.approx(400.0)
    assert onsets[0].source == "brake_onset"


def test_brief_brake_touch_is_not_an_onset():
    """One 20 m sample of brake is a brush or one driver's lift showing through
    the median, not a braking zone."""
    profile = synthetic_profile()
    profile["brake_on"] = [0.0] * len(profile["distance_m"])
    profile["brake_on"][5] = 1.0
    assert detect_brake_onsets(profile) == []


def test_throttle_return_is_detected():
    returns = detect_throttle_returns(synthetic_profile())
    assert any(b.distance_m == pytest.approx(700.0) for b in returns)


def test_envelope_taper_boundary_marks_the_power_taper():
    """Above ~290 km/h the normal envelope tapers, so a megajoule buys less time.
    Splitting there makes the shadow price resolvable along a straight."""
    profile = synthetic_profile()
    taper = detect_envelope_taper(profile)
    assert taper and taper[0].source == "envelope_taper"
    assert taper[0].distance_m == pytest.approx(0.0)  # speed starts at 300


def test_envelope_taper_silent_below_the_threshold():
    profile = synthetic_profile()
    profile["speed_kmh"] = [150.0] * len(profile["distance_m"])
    assert detect_envelope_taper(profile) == []


def test_detectors_return_nothing_for_an_empty_profile():
    empty = {"distance_m": [], "brake_on": [], "throttle_pct": [], "speed_kmh": []}
    assert detect_brake_onsets(empty) == []
    assert detect_throttle_returns(empty) == []
    assert detect_envelope_taper(empty) == []


# -------------------------------------------------------------------- merging
def test_short_segments_are_merged_away():
    boundaries = [Boundary(0.0, "lap_start"), Boundary(10.0, "brake_onset"), Boundary(500.0, "corner")]
    kept = merge_short_segments(boundaries, 1000.0, min_length_m=40.0)
    assert [b.distance_m for b in kept] == [0.0, 500.0]


def test_merging_keeps_the_more_trusted_source():
    """A cited FIA line must never be dropped in favour of a detected brake point."""
    boundaries = [Boundary(0.0, "lap_start"), Boundary(100.0, "brake_onset"), Boundary(110.0, "fia_line")]
    kept = merge_short_segments(boundaries, 1000.0, min_length_m=40.0)
    sources = [b.source for b in kept]
    assert "fia_line" in sources
    assert "brake_onset" not in sources


def test_wrap_around_segment_also_respects_the_minimum():
    """A boundary 5 m before the line would make a 5 m final segment."""
    boundaries = [Boundary(0.0, "lap_start"), Boundary(500.0, "corner"), Boundary(995.0, "brake_onset")]
    kept = merge_short_segments(boundaries, 1000.0, min_length_m=40.0)
    assert kept[-1].distance_m == 500.0


# ------------------------------------------------------------- classification
@pytest.mark.parametrize("curvature,speed,expected", [
    (0.0, 300.0, "STRAIGHT"),
    (0.0001, 300.0, "STRAIGHT"),      # below the calibrated threshold
    (0.01, 80.0, "HAIRPIN_LEFT"),
    (-0.01, 80.0, "HAIRPIN_RIGHT"),
    (0.01, 140.0, "SLOW_LEFT"),
    (0.01, 200.0, "MEDIUM_LEFT"),
    (0.01, 260.0, "FAST_LEFT"),
    (-0.01, 260.0, "FAST_RIGHT"),
])
def test_corner_type_binning(curvature, speed, expected):
    assert classify_corner_type(curvature, speed) == expected


def test_chicane_needs_two_curvature_sign_changes():
    assert classify_corner_type(0.01, 140.0, sign_changes=2) == "CHICANE"


def test_curvature_decides_whether_it_is_a_corner_not_speed():
    """Binning on speed alone would call a flat-out kink a straight at one
    circuit and a FAST corner at another."""
    assert classify_corner_type(0.0, 90.0) == "STRAIGHT"       # slow but straight
    assert classify_corner_type(0.01, 90.0) == "HAIRPIN_LEFT"  # slow and curved


def test_missing_curvature_falls_back_to_straight():
    assert classify_corner_type(None, 200.0) == "STRAIGHT"


# ----------------------------------------------------------------- heading
def test_heading_is_zero_going_east_and_ninety_going_north():
    east = track_heading_deg([0, 1, 2, 3, 4], [0, 0, 0, 0, 0], window=1)
    assert east[2] == pytest.approx(0.0, abs=1e-6)
    north = track_heading_deg([0, 0, 0, 0, 0], [0, 1, 2, 3, 4], window=1)
    assert north[2] == pytest.approx(90.0, abs=1e-6)


def test_heading_applies_the_circuit_rotation():
    """M33 projects wind onto this, so a wrong frame silently corrupts the
    head/cross components."""
    rotated = track_heading_deg([0, 1, 2], [0, 0, 0], rotation_deg=92.0, window=1)
    assert rotated[1] == pytest.approx(92.0, abs=1e-6)


def test_heading_stays_within_zero_to_360():
    headings = track_heading_deg([0, -1, -2], [0, -1, -2], rotation_deg=300.0, window=1)
    assert all(h is None or 0.0 <= h < 360.0 for h in headings)


def test_smooth_preserves_length_and_tolerates_none():
    assert len(smooth([1.0, None, 3.0], 3)) == 3
    assert smooth([1.0, 2.0, 3.0], 1) == [1.0, 2.0, 3.0]


# ------------------------------------------------------------------- assembly
def test_build_segments_covers_the_lap_exactly():
    segments = build_segments(synthetic_profile(), lap_length_m=1000.0)
    assert segments[0].start_distance_m == 0.0
    assert segments[-1].end_distance_m == 1000.0
    assert sum(s.length_m for s in segments) == pytest.approx(1000.0)


def test_segment_ids_are_sequential_from_one():
    segments = build_segments(synthetic_profile(), lap_length_m=1000.0)
    assert [s.segment_id for s in segments] == list(range(1, len(segments) + 1))


def test_no_segment_is_shorter_than_the_minimum():
    segments = build_segments(synthetic_profile(), lap_length_m=1000.0)
    assert all(s.length_m >= DEFAULTS["min_segment_length_m"] for s in segments)


def test_every_kind_is_recognised():
    segments = build_segments(synthetic_profile(), lap_length_m=1000.0)
    assert all(s.kind in SEGMENT_KINDS for s in segments)


def test_corner_markers_are_attached_to_the_containing_segment():
    corners = [{"CornerNumber": 1, "Distance": 600.0}]
    segments = build_segments(synthetic_profile(), 1000.0, corners=corners)
    owner = [s for s in segments if s.corner_id == 1]
    assert len(owner) == 1
    assert owner[0].start_distance_m <= 600.0 < owner[0].end_distance_m


def test_duplicate_corner_distances_produce_one_boundary():
    """The Hungaroring repeats three distances; one marker is one boundary."""
    corners = [{"CornerNumber": 1, "Distance": 600.0}, {"CornerNumber": 2, "Distance": 600.0}]
    segments = build_segments(synthetic_profile(), 1000.0, corners=corners)
    starts = [s.start_distance_m for s in segments]
    assert starts.count(600.0) <= 1


def test_fia_lines_become_boundaries_when_supplied():
    lines = [{"distance_m": 250.0, "kind": "DETECTION"}]
    segments = build_segments(synthetic_profile(), 1000.0, fia_lines=lines)
    assert any(s.start_distance_m == 250.0 for s in segments)


def test_build_is_deterministic():
    a = build_segments(synthetic_profile(), 1000.0)
    b = build_segments(synthetic_profile(), 1000.0)
    assert [s.to_dict() for s in a] == [s.to_dict() for s in b]


def test_empty_profile_yields_no_segments():
    assert build_segments({"distance_m": []}, 1000.0) == []


# ----------------------------------------------------------------- hashing
def test_boundary_hash_is_stable_and_order_independent():
    assert boundary_hash([0.0, 100.0, 200.0]) == boundary_hash([200.0, 0.0, 100.0])


def test_boundary_hash_changes_when_a_boundary_moves():
    """This is how a stale downstream table becomes detectable."""
    assert boundary_hash([0.0, 100.0]) != boundary_hash([0.0, 101.0])


# ------------------------------------------------- against the derived maps
DERIVED = sorted(GEOMETRY_DIR.glob("*.yaml")) if GEOMETRY_DIR.is_dir() else []
requires_maps = pytest.mark.skipif(not DERIVED, reason="no geometry derived yet; run derive_track_geometry.py")


def _load(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


@requires_maps
@pytest.mark.parametrize("path", DERIVED, ids=lambda p: p.stem)
def test_derived_map_is_internally_consistent(path):
    data = _load(path)
    segments = data["segments"]
    assert len(segments) == data["segment_count"]
    assert [s["segment_id"] for s in segments] == list(range(1, len(segments) + 1))
    total = sum(s["segment_length_m"] for s in segments)
    assert total == pytest.approx(data["lap_length_m"], abs=20.0)


@requires_maps
@pytest.mark.parametrize("path", DERIVED, ids=lambda p: p.stem)
def test_derived_segments_meet_the_minimum_length(path):
    data = _load(path)
    shortest = min(s["segment_length_m"] for s in data["segments"])
    assert shortest >= data["options"]["min_segment_length_m"] - 0.51  # rounding


@requires_maps
@pytest.mark.parametrize("path", DERIVED, ids=lambda p: p.stem)
def test_segment_count_is_reasonable(path):
    """30-40 is the target (section 13). Low-corner circuits legitimately come in
    under it -- the Red Bull Ring has 10 corners -- so the floor here is looser
    than the target and the real check is that it is not absurd."""
    data = _load(path)
    assert 15 <= data["segment_count"] <= 55, f"{path.stem}: {data['segment_count']}"


@requires_maps
@pytest.mark.parametrize("path", DERIVED, ids=lambda p: p.stem)
def test_boundaries_are_ordered_and_contiguous(path):
    segments = _load(path)["segments"]
    for a, b in zip(segments, segments[1:]):
        assert a["end_distance_m"] == pytest.approx(b["start_distance_m"])


@requires_maps
def test_silverstone_lands_in_the_target_range():
    """The circuit the checkpoint calls out: 18 corners, expected near 36."""
    british = GEOMETRY_DIR / "british.yaml"
    if not british.exists():
        pytest.skip("Silverstone not derived")
    data = _load(british)
    assert 30 <= data["segment_count"] <= 40
    assert data["corner_count"] == 18


@requires_maps
def test_every_corner_marker_is_assigned():
    british = GEOMETRY_DIR / "british.yaml"
    if not british.exists():
        pytest.skip("Silverstone not derived")
    data = _load(british)
    assigned = {s["corner_id"] for s in data["segments"] if s["corner_id"] is not None}
    assert len(assigned) == data["corner_count"]


# ------------------------------------------- against the built C1 table
BUILT = sorted(SEGMENTS_DIR.glob("circuit=*/segments.parquet")) if SEGMENTS_DIR.is_dir() else []
requires_built = pytest.mark.skipif(not BUILT, reason="segments not built yet; run build_segments.py")


@requires_built
@pytest.mark.parametrize("path", BUILT, ids=lambda p: p.parent.name)
def test_segment_id_always_means_the_same_tarmac(path):
    """The invariant the whole checkpoint exists to guarantee.

    A lap may be missing trailing segments when its telemetry ends early -- that
    is fine. What must never happen is one segment_id spanning two different
    distance ranges.
    """
    pd = pytest.importorskip("pandas")
    frame = pd.read_parquet(path)
    counts = frame.groupby("segment_id")[["start_distance_m", "end_distance_m"]].nunique()
    assert (counts["start_distance_m"] == 1).all()
    assert (counts["end_distance_m"] == 1).all()


@requires_built
@pytest.mark.parametrize("path", BUILT, ids=lambda p: p.parent.name)
def test_one_boundary_hash_per_table(path):
    """Two hashes in one table would mean it was built against two maps."""
    pd = pytest.importorskip("pandas")
    frame = pd.read_parquet(path)
    assert frame["boundary_hash"].nunique() == 1
    assert frame["geometry_version"].nunique() == 1


@requires_built
@pytest.mark.parametrize("path", BUILT, ids=lambda p: p.parent.name)
def test_offline_columns_are_suffixed(path):
    """A full-segment summary is only knowable once the segment is over, so it
    must be distinguishable from an entry value (sections 13, 44)."""
    pd = pytest.importorskip("pandas")
    frame = pd.read_parquet(path)
    for column in ("min_speed_kmh_offline", "segment_time_s_offline", "exit_speed_kmh_offline"):
        assert column in frame.columns
    assert "entry_speed_kmh" in frame.columns
    assert not any(c.startswith("entry_") and c.endswith("_offline") for c in frame.columns)
