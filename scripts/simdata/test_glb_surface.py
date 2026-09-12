"""Tests for the GLB drive-surface bake.

Three layers, deliberately:

  * pure unit tests on hand-built data, where the right answer is arithmetic: glTF's
    column-major node matrices, interleaved accessors, the barycentric raycast, the
    scorer's bands;
  * one SYNTHETIC circuit -- a chiral closed loop extruded into a road strip, written
    into a real in-memory GLB and then transformed by a known (scale, yaw, mirror,
    translation) -- which exercises parse -> extract -> index -> global fit -> bake
    end to end against an answer we chose, with structures deliberately hovering over
    the road to prove the scorer beats a "topmost surface" rule;
  * measurements against the REAL 158 MB Silverstone and 97 MB Shanghai assets in
    data/tracks, which are gitignored, so those tests skip when the assets are absent.

Every number quoted in a docstring below is measured, not illustrative.
"""
from __future__ import annotations

import functools
import json
import math
import pathlib
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from simdata.geom import Ring, close_ring
from simdata.glb_surface import (ACCEPT_NAME, GATE_MAX_RESIDUAL_STD_M, GATE_MIN_COVERAGE,
                                 MIN_DRIVEABLE_SCORE, REJECT_NAME,
                                 SCORE_ACCEPT_NAME, DriveSurface, Fit, Glb, Primitive,
                                 RingMatcher, TriangleIndex, accessor_array,
                                 bake_surface, barycentric_hits,
                                 extract_drive_surface, fit_ring_to_surface, load_glb,
                                 load_registry, load_surface, node_matrix, normal_score,
                                 parse_glb, primitive_static_score, registry_entry,
                                 sha256_file, surface_block, surface_colour_score,
                                 world_matrices, _climb, _fill_circular, _largest_gap)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TRACKS = REPO_ROOT / "data" / "tracks"


# ===========================================================================
# Building real GLBs in memory
# ===========================================================================

def _pad4(blob: bytes, fill: bytes = b"\0") -> bytes:
    return blob + fill * (-len(blob) % 4)


def make_glb(prims, node_matrices) -> bytes:
    """Assemble a real binary glTF from primitives and a chain of node matrices.

    `prims` entries: {name, material, positions (N,3), indices (M,), colour, map}.
    `node_matrices` is the parent-to-child chain the meshes hang off, each a 4x4 in
    ordinary column-vector convention -- this helper writes them out COLUMN-major, the
    way glTF stores them, so a reader that forgets to transpose fails these tests.
    """
    buf = bytearray()
    views, accessors, meshes, materials, nodes = [], [], [], [], []
    textures, images = [], []

    def add_view(data: bytes) -> int:
        while len(buf) % 4:
            buf.append(0)
        views.append({"buffer": 0, "byteOffset": len(buf), "byteLength": len(data)})
        buf.extend(data)
        return len(views) - 1

    for p in prims:
        pos = np.ascontiguousarray(p["positions"], dtype=np.float32)
        idx = np.ascontiguousarray(p["indices"], dtype=np.uint32)
        vi = add_view(pos.tobytes())
        ii = add_view(idx.tobytes())
        accessors.append({"bufferView": vi, "componentType": 5126, "count": len(pos),
                          "type": "VEC3", "min": pos.min(axis=0).tolist(),
                          "max": pos.max(axis=0).tolist()})
        accessors.append({"bufferView": ii, "componentType": 5125, "count": len(idx),
                          "type": "SCALAR"})
        pbr = {"baseColorFactor": list(p.get("colour", (1, 1, 1))) + [1.0]}
        if p.get("map"):
            images.append({"name": p["material"] + "_tex"})
            textures.append({"source": len(images) - 1})
            pbr["baseColorTexture"] = {"index": len(textures) - 1}
        materials.append({"name": p["material"], "pbrMetallicRoughness": pbr})
        meshes.append({"name": p["name"], "primitives": [
            {"attributes": {"POSITION": len(accessors) - 2},
             "indices": len(accessors) - 1, "material": len(materials) - 1, "mode": 4}]})

    for depth, m in enumerate(node_matrices):
        nodes.append({"name": f"chain{depth}",
                      "matrix": [float(v) for v in np.asarray(m).T.ravel()],
                      "children": []})
        if depth:
            nodes[depth - 1]["children"] = [depth]
    leaf_parent = len(node_matrices) - 1
    for i in range(len(meshes)):
        nodes.append({"name": f"Object_{i}", "mesh": i})
        nodes[leaf_parent]["children"].append(len(nodes) - 1)

    gltf = {"asset": {"version": "2.0", "generator": "trackshift-test"},
            "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes, "meshes": meshes,
            "materials": materials, "accessors": accessors, "bufferViews": views,
            "buffers": [{"byteLength": len(buf)}]}
    if textures:
        gltf["textures"] = textures
        gltf["images"] = images

    js = _pad4(json.dumps(gltf).encode("utf-8"), b" ")
    bn = _pad4(bytes(buf))
    total = 12 + 8 + len(js) + 8 + len(bn)
    return (struct.pack("<III", 0x46546C67, 2, total)
            + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(bn), 0x004E4942) + bn)


def _rx(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]], float)


# ===========================================================================
# The synthetic circuit
# ===========================================================================

TRUE_FIT = Fit(scale=2.0, yaw_rad=math.radians(23.0), mirror=-1,
               tx=-412.5, tz=911.25, ty=-67.5)
ROOF_STATIONS = (300, 450)          # a rejected structure hovering over the road
DECOY_STATIONS = (900, 1050)        # an UNNAMED horizontal slab hovering over the road
ROOF_HEIGHT_M = 8.0
DECOY_HEIGHT_M = 3.0


def _centreline():
    """A lumpy, chiral closed loop. Not an ellipse: an ellipse is 180-degree ambiguous
    and a circle is fully ambiguous, so neither would prove the fit recovers yaw."""
    th = np.linspace(0.0, 2 * math.pi, 4000, endpoint=False)
    r = 300.0 + 80.0 * np.cos(th) + 40.0 * np.sin(2 * th) + 25.0 * np.cos(3 * th)
    x, y = r * np.cos(th), r * np.sin(th)
    z = 50.0 + 4.0 * np.sin(3 * th) + 2.0 * np.cos(th)
    return close_ring(x, y, z, 1.0)[:3]


@functools.lru_cache(maxsize=1)
def synthetic():
    """(ring, glb bytes). The road's centre height IS the ring height, exactly."""
    ring = Ring(*_centreline(), 1.0)
    n = ring.n
    step = 6                                  # ~6 m triangles, like a real circuit export
    idx = np.arange(0, n, step)
    camber = 0.02 * np.sin(5.0 * 2 * math.pi * idx / n)      # radians, left side up
    half = 8.0

    def strip(sel, lateral=half):
        lx = ring.x[sel] + lateral * ring.nx[sel]
        ly = ring.y[sel] + lateral * ring.ny[sel]
        rx = ring.x[sel] - lateral * ring.nx[sel]
        ry = ring.y[sel] - lateral * ring.ny[sel]
        cam = 0.02 * np.sin(5.0 * 2 * math.pi * sel / n)
        lz = ring.z[sel] + lateral * np.tan(cam)
        rz = ring.z[sel] - lateral * np.tan(cam)
        pts = np.empty((2 * len(sel), 3))
        pts[0::2] = np.column_stack((lx, ly, lz))
        pts[1::2] = np.column_stack((rx, ry, rz))
        tri = []
        for i in range(len(sel)):
            a, b = 2 * i, 2 * i + 1
            c, d = (2 * i + 2) % len(pts), (2 * i + 3) % len(pts)
            tri += [a, b, c, b, d, c]
        return pts, np.asarray(tri, dtype=np.uint32)

    def slab(lo, hi, lift):
        sel = np.arange(lo, hi, step)
        pts, tri = strip(sel, lateral=half * 1.5)
        pts[:, 2] += lift
        return pts, tri

    half_n = len(idx) // 2
    road_a = strip(idx[:half_n + 1])
    road_b = strip(idx[half_n:])
    prims = [
        # The model NAMES the first half of its road and not the second: the scorer has
        # to carry both, which is the whole point of a broad accept list plus a
        # geometry/colour fallback.
        {"name": "road_named", "material": "asphalt.001", "colour": (1, 1, 1),
         "map": True, "positions": road_a[0], "indices": road_a[1]},
        {"name": "chunk_7", "material": "Merged_materials", "colour": (0.21, 0.21, 0.22),
         "map": True, "positions": road_b[0], "indices": road_b[1]},
        {"name": "roof", "material": "grandstand_roof", "colour": (0.6, 0.6, 0.6),
         "map": True, "positions": slab(*ROOF_STATIONS, ROOF_HEIGHT_M)[0],
         "indices": slab(*ROOF_STATIONS, ROOF_HEIGHT_M)[1]},
        {"name": "chunk_8", "material": "advert_side_a_3", "colour": (0.24, 0.24, 0.25),
         "map": True, "positions": slab(*DECOY_STATIONS, DECOY_HEIGHT_M)[0],
         "indices": slab(*DECOY_STATIONS, DECOY_HEIGHT_M)[1]},
    ]
    # Telemetry frame -> model world -> the model's own raw Z-up coordinates.
    for p in prims:
        wx, wy, wz = TRUE_FIT.to_world(p["positions"][:, 0], p["positions"][:, 1],
                                       p["positions"][:, 2])
        p["positions"] = np.column_stack((wx, wz, -wy))      # raw = (wx, wz, -wy)
    # Two nodes that compose to +90 about X, so world = (raw_x, -raw_z, raw_y).
    return ring, make_glb(prims, [_rx(-90.0), _rx(180.0)])


@functools.lru_cache(maxsize=1)
def synthetic_index():
    ring, blob = synthetic()
    surf = extract_drive_surface(parse_glb(blob), source="synthetic.glb")
    return ring, surf, TriangleIndex(surf)


# ===========================================================================
# 1. Container and accessors
# ===========================================================================

def test_parse_glb_rejects_a_non_glb():
    with pytest.raises(ValueError, match="magic"):
        parse_glb(b"NOPE" + b"\0" * 32)
    with pytest.raises(ValueError, match="header"):
        parse_glb(b"abc")


def test_parse_glb_rejects_geometry_it_cannot_decode():
    """A Draco/meshopt model must fail loudly. Reading its accessors raw would produce a
    plausible-looking surface made of compressed bytes."""
    _, blob = synthetic()
    glb = parse_glb(blob)
    doctored = dict(glb.gltf)
    doctored["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    js = _pad4(json.dumps(doctored).encode("utf-8"), b" ")
    rebuilt = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js))
               + struct.pack("<II", len(js), 0x4E4F534A) + js)
    with pytest.raises(ValueError, match="unsupported extensions"):
        parse_glb(rebuilt)


def test_accessor_honours_bytestride():
    """Interleaved POSITION+NORMAL is the normal case in a Sketchfab export. Reading it
    as if it were tightly packed silently returns normals as positions."""
    pos = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32)
    nrm = np.array([[0, 1, 0], [0, 1, 0], [0, 1, 0]], dtype=np.float32)
    inter = np.empty((3, 6), dtype=np.float32)
    inter[:, :3], inter[:, 3:] = pos, nrm
    gltf = {"buffers": [{"byteLength": inter.nbytes}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": inter.nbytes,
                             "byteStride": 24}],
            "accessors": [{"bufferView": 0, "byteOffset": 0, "componentType": 5126,
                           "count": 3, "type": "VEC3"},
                          {"bufferView": 0, "byteOffset": 12, "componentType": 5126,
                           "count": 3, "type": "VEC3"}]}
    glb = Glb(gltf=gltf, binary=inter.tobytes())
    np.testing.assert_allclose(accessor_array(glb, 0), pos)
    np.testing.assert_allclose(accessor_array(glb, 1), nrm)


# ===========================================================================
# 2. Node matrices
# ===========================================================================

def test_node_matrix_is_column_major():
    """glTF stores `matrix` column-major. Forgetting the transpose turns a +90 degree
    roll about X into a -90 degree one -- a model that still looks like a circuit,
    mirrored through the ground plane."""
    m = _rx(90.0)
    node = {"matrix": [float(v) for v in m.T.ravel()]}
    got = node_matrix(node)
    np.testing.assert_allclose(got, m, atol=1e-12)
    v = got[:3, :3] @ np.array([1.0, 2.0, 3.0])
    np.testing.assert_allclose(v, [1.0, -3.0, 2.0], atol=1e-12)


def test_node_matrix_trs_order_is_t_r_s():
    node = {"translation": [10.0, 0.0, 0.0], "scale": [2.0, 2.0, 2.0],
            "rotation": [0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)]}
    m = node_matrix(node)
    got = m[:3, :3] @ np.array([1.0, 0.0, 0.0]) + m[:3, 3]
    np.testing.assert_allclose(got, [10.0, 2.0, 0.0], atol=1e-12)   # +90 about Z, x2, +10x


def test_world_matrices_compose_the_chain():
    """The synthetic chain is Rx(-90) then Rx(180), which must compose to Rx(+90),
    i.e. world = (raw_x, -raw_z, raw_y) -- the known answer for these Sketchfab exports.
    Composing in the wrong order gives Rx(-90) and flips the circuit."""
    _, blob = synthetic()
    worlds = world_matrices(parse_glb(blob).gltf)
    leaf = max(worlds)
    raw = np.array([3.0, 5.0, 7.0])
    got = worlds[leaf][:3, :3] @ raw + worlds[leaf][:3, 3]
    np.testing.assert_allclose(got, [raw[0], -raw[2], raw[1]], atol=1e-9)


@pytest.mark.parametrize("asset", ["silverstone.glb", "shanghai.glb"])
def test_real_models_use_the_documented_node_convention(asset):
    """Measured on both shipped assets: the Sketchfab node chain composes to
    world = (raw_x, -raw_z, raw_y) for every mesh-bearing node."""
    path = TRACKS / asset
    if not path.is_file():
        pytest.skip(f"{path} is gitignored and absent")
    gltf = load_glb(path).gltf
    worlds = world_matrices(gltf)
    expected = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float)
    checked = 0
    for idx, m in worlds.items():
        if "mesh" not in gltf["nodes"][idx]:
            continue
        np.testing.assert_allclose(m[:3, :3], expected, atol=1e-6)
        np.testing.assert_allclose(m[:3, 3], 0.0, atol=1e-6)
        checked += 1
    assert checked > 50


# ===========================================================================
# 3. The exact raycast
# ===========================================================================

def _one_triangle(a, b, c):
    verts = np.array([a, b, c], dtype=float)
    tris = np.array([[0, 1, 2]], dtype=np.int32)
    return verts, tris


def test_barycentric_hit_on_a_hand_built_triangle():
    """A tilted triangle whose height at the query point is known by hand.

    a = (0, 0, 0), b = (10, 5, 0), c = (0, 2, 10) in (x, y, z), so the plane through them
    is y = 0.5 x + 0.2 z and every expected value below is that expression evaluated.
    """
    verts, tris = _one_triangle((0, 0, 0), (10, 5, 0), (0, 2, 10))
    for (px, pz, want) in [(1.0, 1.0, 0.7),          # inside
                           (0.0, 0.0, 0.0),          # exactly on vertex a
                           (5.0, 5.0, 3.5),          # exactly on edge b-c
                           (0.0, 5.0, 1.0)]:         # exactly on edge a-c
        qi = np.zeros(1, dtype=np.int64)
        ti = np.zeros(1, dtype=np.int32)
        gi, gt, y = barycentric_hits(verts, tris, qi, ti,
                                     np.array([px]), np.array([pz]))
        assert len(y) == 1, (px, pz)
        assert y[0] == pytest.approx(want, abs=1e-9)


def test_barycentric_misses_outside_the_triangle():
    verts, tris = _one_triangle((0, 0, 0), (10, 5, 0), (0, 2, 10))
    gi, gt, y = barycentric_hits(verts, tris, np.zeros(1, np.int64), np.zeros(1, np.int32),
                                 np.array([-0.5]), np.array([5.0]))
    assert len(y) == 0


def test_barycentric_is_winding_agnostic():
    """A double-sided export winds its road triangles both ways. A test that only
    accepts one winding drops half of a road."""
    for tri in ([[0, 1, 2]], [[0, 2, 1]]):
        verts = np.array([(0, 0, 0), (10, 5, 0), (0, 2, 10)], dtype=float)
        gi, gt, y = barycentric_hits(verts, np.asarray(tri, np.int32),
                                     np.zeros(1, np.int64), np.zeros(1, np.int32),
                                     np.array([1.0]), np.array([1.0]))
        assert y[0] == pytest.approx(0.7, abs=1e-9)


def test_shared_edge_is_hit_by_both_triangles():
    """No crack: a station exactly on the seam between two road triangles must not fall
    through, or a lap picks up a NaN where the surface is continuous."""
    verts = np.array([(0, 0, 0), (10, 0, 0), (10, 0, 10), (0, 0, 10)], dtype=float)
    tris = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    qi = np.zeros(2, dtype=np.int64)
    ti = np.array([0, 1], dtype=np.int32)
    gi, gt, y = barycentric_hits(verts, tris, qi, ti, np.array([5.0, 5.0]),
                                 np.array([5.0, 5.0]))
    assert sorted(gt.tolist()) == [0, 1]


def test_index_finds_an_oversized_triangle():
    """One huge ground triangle covers more grid cells than the bucket cap, so it goes
    on the oversized list. If that list were dropped, the surface under it vanishes."""
    big = np.array([(-5000, 3.0, -5000), (5000, 3.0, -5000), (0, 3.0, 5000)], float)
    small = np.array([(0, 1.0, 0), (4, 1.0, 0), (0, 1.0, 4)], float)
    verts = np.vstack((big, small))
    surf = DriveSurface(verts=verts,
                        tris=np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32),
                        tri_prim=np.array([0, 1], dtype=np.int32),
                        tri_ny=np.array([1.0, 1.0]),
                        primitives=[Primitive(0, 0, 0, 0, "a", "a", "asphalt", "", (1, 1, 1),
                                              False, 1),
                                    Primitive(1, 1, 1, 0, "b", "b", "asphalt", "", (1, 1, 1),
                                              False, 1)],
                        static_score=np.array([SCORE_ACCEPT_NAME, SCORE_ACCEPT_NAME]))
    index = TriangleIndex(surf, cell_m=4.0)
    assert len(index.oversized) == 1
    qi, ti = index.candidates(np.array([1.0]), np.array([1.0]))
    assert set(ti.tolist()) == {0, 1}


# ===========================================================================
# 4. The scorer
# ===========================================================================

def _prim(material, *, colour=(1, 1, 1), has_map=False, node="Object_9", mesh="Object_9"):
    return Primitive(0, 0, 0, 0, node, mesh, material, "", colour, has_map, 1)


def test_reject_list_returns_minus_infinity():
    """The reject list -- not a topmost rule -- is what stops a structure OVER the track
    being sampled as the road."""
    for name in ("grandstand_roof", "Grass.001", "tyreswall.001", "barriers1.003",
                 "NewBridge.structure.001", "trees.001", "sand_new.001"):
        assert primitive_static_score(_prim(name)) == -math.inf, name


def test_accept_list_is_a_strong_positive_not_a_requirement():
    assert primitive_static_score(_prim("asphalt.001")) >= SCORE_ACCEPT_NAME
    unnamed = primitive_static_score(_prim("Merged_materials", colour=(0.21, 0.21, 0.22),
                                           has_map=True))
    assert 0 < unnamed < SCORE_ACCEPT_NAME
    # ...and the unnamed, dark, textured, horizontal surface still clears the bar. This
    # is the only thing that carries a model whose every mesh is called Object_N.
    assert unnamed + float(normal_score(1.0)) >= MIN_DRIVEABLE_SCORE


def test_colour_score_prefers_dark_and_desaturated():
    assert surface_colour_score((0.22, 0.22, 0.23)) > 0        # asphalt
    assert surface_colour_score((0.10, 0.10, 0.10)) > 0        # very dark
    assert surface_colour_score((0.95, 0.15, 0.10)) == 0       # bright saturated red
    assert surface_colour_score((0.90, 0.90, 0.90)) == 0       # bright neutral


def test_normal_score_bands():
    assert normal_score(1.0) > normal_score(0.7) > 0
    assert normal_score(0.05) < 0
    got = normal_score(np.array([1.0, 0.7, 0.05]))
    assert got.shape == (3,)


def test_name_lists_do_not_collide_on_real_material_names():
    """`tractor` must not read as `track`, `distanz` must not read as `stand`."""
    for benign in ("tractor.001", "distanz.001", "TV_Stuff.001", "marshall.001"):
        assert not ACCEPT_NAME.search(benign), benign
        assert not REJECT_NAME.search(benign), benign
    assert REJECT_NAME.search("gstands1.001")                  # grandstand
    assert ACCEPT_NAME.search("asph_pitlane.001")


# ===========================================================================
# 5. The transform
# ===========================================================================

def test_fit_round_trips_height():
    f = Fit(scale=2.0, yaw_rad=0.3, mirror=-1, tx=10.0, tz=-20.0, ty=5.0)
    z = np.array([100.0, 101.5, 99.25])
    _, wy, _ = f.to_world(np.zeros(3), np.zeros(3), z)
    np.testing.assert_allclose(f.telemetry_z(wy), z, atol=1e-12)


def test_fit_matches_the_env_sim_raw_glb_form():
    """env_sim.md quotes the transform in the model's RAW Z-up coordinates:
    glb_x = telem_x + dx, glb_y = -telem_y + dy, glb_z = -telem_z + dz. Because the node
    chain gives world = (raw_x, -raw_z, raw_y), that is raw = (wx, wz, -wy)."""
    f = Fit(scale=1.0, yaw_rad=0.0, mirror=-1, tx=-276.01, tz=443.01, ty=-203.679)
    x, y, z = np.array([12.0]), np.array([-34.0]), np.array([200.0])
    wx, wy, wz = f.to_world(x, y, z)
    raw = (wx[0], wz[0], -wy[0])
    assert raw[0] == pytest.approx(x[0] - 276.01)
    assert raw[1] == pytest.approx(-y[0] + 443.01)
    assert raw[2] == pytest.approx(-z[0] + 203.679)
    assert f.raw_glb() == {"glbXOffsetM": -276.01, "glbYOffsetM": 443.01,
                           "glbZOffsetM": 203.679}


def test_fit_serialises_round_trip():
    f = Fit(scale=0.998, yaw_rad=math.radians(1.25), mirror=-1, tx=1.0, tz=2.0, ty=3.0)
    back = Fit.from_dict(f.as_dict())
    assert back.scale == pytest.approx(f.scale)
    assert back.yaw_rad == pytest.approx(f.yaw_rad)
    assert (back.mirror, back.tx, back.tz, back.ty) == (f.mirror, f.tx, f.tz, f.ty)


# ===========================================================================
# 6. Gap handling and the gate
# ===========================================================================

def test_fill_circular_wraps():
    v = np.array([0.0, np.nan, np.nan, 3.0])
    got = _fill_circular(v, np.array([True, False, False, True]))
    np.testing.assert_allclose(got, [0.0, 1.0, 2.0, 3.0])
    # A gap that straddles station 0 is filled the short way round, never extrapolated:
    # station 0 interpolates between station 2 (seen at -2) and station 1.
    v = np.array([np.nan, 2.0, 4.0, np.nan])
    got = _fill_circular(v, np.array([False, True, True, False]))
    assert got[0] == pytest.approx(4.0 - 2.0 / 3.0 * 2.0)
    assert got[3] == pytest.approx(4.0 - 1.0 / 3.0 * 2.0)
    assert 2.0 <= got.min() and got.max() <= 4.0


def test_largest_gap_is_circular():
    assert _largest_gap(np.array([False, True, True, False])) == 2
    assert _largest_gap(np.ones(5, dtype=bool)) == 0
    assert _largest_gap(np.zeros(5, dtype=bool)) == 5


def _fake_bake(coverage, std):
    n = 100
    valid = np.zeros(n, dtype=bool)
    valid[: int(round(coverage * n))] = True
    from simdata.glb_surface import SurfaceBake
    return SurfaceBake(z_m=np.zeros(n), slope_rad=np.zeros(n), camber_rad=np.zeros(n),
                       camber_base_m=np.full(n, 2.0), valid=valid, camber_valid=valid,
                       residual_m=np.zeros(n),
                       edge_left_m=np.full(n, 8.0), edge_right_m=np.full(n, 6.5),
                       fit=Fit(1.0, 0.0, -1, 0.0, 0.0, 0.0), coverage=coverage,
                       road_coverage=coverage, residual_std_m=std, residual_max_m=std * 3,
                       largest_gap_stations=0, ds_m=1.0, source="", sha256="",
                       primitives={})


def test_gate_thresholds_are_not_negotiable():
    assert _fake_bake(0.999, 0.05).gate()[0]
    assert _fake_bake(GATE_MIN_COVERAGE, GATE_MAX_RESIDUAL_STD_M).gate()[0]
    ok, fails = _fake_bake(0.98, 0.05).gate()
    assert not ok and "coverage" in fails[0]
    ok, fails = _fake_bake(1.0, 0.16).gate()
    assert not ok and "residual" in fails[0]
    ok, fails = _fake_bake(1.0, float("nan")).gate()
    assert not ok, "a NaN residual must fail the gate, not slip through a < comparison"


# ===========================================================================
# 7. The synthetic circuit, end to end
# ===========================================================================

def test_synthetic_extraction_drops_rejected_primitives_whole():
    _, surf, _ = synthetic_index()
    kept = {p.material_name for p in surf.primitives}
    assert "grandstand_roof" in {p.material_name for p in surf.rejected}
    assert "grandstand_roof" not in {surf.primitives[int(i)].material_name
                                     for i in np.unique(surf.tri_prim)}
    assert {"asphalt.001", "Merged_materials"} <= kept


def test_synthetic_fit_recovers_the_known_transform():
    """A full cold start: no warm start, no hint of the answer. Scale 2.0, yaw 23 deg and
    a mirror are all recovered from the drivable surface alone."""
    ring, _, index = synthetic_index()
    fit, _ = fit_ring_to_surface(index, ring)
    assert fit.mirror == TRUE_FIT.mirror
    assert fit.scale == pytest.approx(TRUE_FIT.scale, rel=2e-3)
    assert math.degrees(fit.yaw_rad) == pytest.approx(math.degrees(TRUE_FIT.yaw_rad),
                                                      abs=0.15)
    assert fit.tx == pytest.approx(TRUE_FIT.tx, abs=2.0)
    assert fit.tz == pytest.approx(TRUE_FIT.tz, abs=2.0)


def test_synthetic_bake_is_exact_and_passes_the_gate():
    ring, _, index = synthetic_index()
    bake = bake_surface(index, ring, TRUE_FIT)
    assert bake.coverage == 1.0
    assert bake.residual_std_m < 0.02
    assert bake.gate()[0], bake.summary()


def test_hysteresis_beats_a_topmost_rule():
    """The decisive test. Two structures hover over the road: a REJECTED grandstand roof
    8 m up, and an UNNAMED slab 3 m up that scores exactly as well as the unnamed half of
    the road itself. A topmost rule takes both. Name rejection handles the first; only
    the height hysteresis handles the second."""
    from simdata.glb_surface import _hit_table
    ring, surf, index = synthetic_index()
    bake = bake_surface(index, ring, TRUE_FIT)

    for lo, hi in (ROOF_STATIONS, DECOY_STATIONS):
        band = slice(lo + 20, hi - 20)
        assert bake.valid[band].all()
        assert np.nanmax(np.abs(bake.residual_m[band])) < 0.05

    # ...and a topmost rule really would have been wrong here: the decoy IS above.
    wx, _, wz = TRUE_FIT.to_world(ring.x, ring.y, ring.z)
    qi, ti, y, base = _hit_table(index, wx, wz)
    mid = (DECOY_STATIONS[0] + DECOY_STATIONS[1]) // 2
    stack = y[qi == mid]
    assert len(stack) >= 2
    topmost_z = TRUE_FIT.telemetry_z(stack.max())
    assert topmost_z - ring.z[mid] == pytest.approx(DECOY_HEIGHT_M, abs=0.2)
    assert abs(bake.z_m[mid] - ring.z[mid]) < 0.05


def test_synthetic_camber_is_measured_not_assumed():
    """The synthetic road is built with camber = 0.02 sin(5 theta) rad, left side up.
    The bake must recover that amplitude and its SIGN."""
    ring, _, index = synthetic_index()
    bake = bake_surface(index, ring, TRUE_FIT)
    want = 0.02 * np.sin(5.0 * 2 * math.pi * np.arange(ring.n) / ring.n)
    ok = bake.camber_valid
    assert ok.mean() > 0.95
    err = bake.camber_rad[ok] - want[ok]
    assert np.abs(err).max() < 0.004, float(np.abs(err).max())
    assert np.corrcoef(bake.camber_rad[ok], want[ok])[0, 1] > 0.99


def test_synthetic_slope_tracks_the_height_field():
    ring, _, index = synthetic_index()
    bake = bake_surface(index, ring, TRUE_FIT)
    dz = np.gradient(ring.z) / ring.ds
    ok = bake.valid
    assert np.corrcoef(np.tan(bake.slope_rad[ok]), dz[ok])[0, 1] > 0.99


def test_orientation_is_baked_not_read_off_face_normals():
    """Triangles are ~6 m across, so a face normal is piecewise constant and roll steps
    at every edge. The baked camber must be far smoother than the per-triangle normal
    that a face-normal implementation would hand the renderer."""
    ring, surf, index = synthetic_index()
    bake = bake_surface(index, ring, TRUE_FIT)
    from simdata.glb_surface import sample_with_hysteresis
    wx, wy, wz = TRUE_FIT.to_world(ring.x, ring.y, ring.z)
    hit = sample_with_hysteresis(index, wx, wz, wy)
    face = surf.tri_ny[hit.tri[hit.valid]]
    face_steps = int((np.abs(np.diff(face)) > 1e-9).sum())
    baked = bake.camber_rad[bake.camber_valid]
    assert face_steps > 100, "the fixture must actually change triangle, or this proves nothing"
    assert np.abs(np.diff(baked)).max() < 1e-3


def test_a_station_off_the_model_is_null_not_a_default():
    """Translate the fit far enough that part of the ring leaves the model. Those
    stations must come back invalid with NaN, never with a plausible height."""
    ring, _, index = synthetic_index()
    off = TRUE_FIT.replace(tx=TRUE_FIT.tx + 700.0)
    bake = bake_surface(index, ring, off)
    assert 0.0 < bake.coverage < 1.0
    assert np.isnan(bake.z_m[~bake.valid]).all()
    assert np.isnan(bake.slope_rad[~bake.valid]).all()
    assert np.isnan(bake.camber_rad[~bake.valid]).all()
    assert not bake.gate()[0]
    block = surface_block(bake, "synthetic")
    assert block["zCm"][int(np.where(~bake.valid)[0][0])] is None


def test_occupancy_agrees_with_the_bake_about_where_the_road_is():
    """The coarse objective must score 1.000 at the transform the model was BUILT from.
    Marking only the cells that triangle vertices land in scores 0.66 there instead --
    6 m triangles rasterised at 4 m leave holes between the sample points -- and a global
    search cannot find a peak it has punched holes in."""
    ring, surf, _ = synthetic_index()
    match = RingMatcher(surf, ring, cell=4.0)
    frac, _ = match(TRUE_FIT.scale, TRUE_FIT.yaw_rad, TRUE_FIT.mirror)
    assert frac == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("d_scale,d_yaw_deg", [(1.0, 0.0), (1.05, 3.0), (1.10, 6.0)])
def test_climb_is_global_in_translation(d_scale, d_yaw_deg):
    """The FFT step solves translation exactly, so a seed displaced by hundreds of metres
    still lands on the right answer; only scale and yaw have to be walked."""
    ring, surf, _ = synthetic_index()
    match = RingMatcher(surf, ring, cell=4.0)
    lost = TRUE_FIT.replace(tx=TRUE_FIT.tx + 400.0, tz=TRUE_FIT.tz - 250.0,
                            scale=TRUE_FIT.scale * d_scale,
                            yaw_rad=TRUE_FIT.yaw_rad + math.radians(d_yaw_deg))
    frac, got = _climb(match, lost)
    assert frac > 0.95
    assert got.scale == pytest.approx(TRUE_FIT.scale, rel=0.02)
    assert got.tx == pytest.approx(TRUE_FIT.tx, abs=20.0)
    assert got.tz == pytest.approx(TRUE_FIT.tz, abs=20.0)


# ===========================================================================
# 8. The real assets
# ===========================================================================

def _asset(slug: str) -> pathlib.Path:
    entry = registry_entry(slug)
    assert entry, f"{slug} is not in config/circuits.yaml"
    path = REPO_ROOT / entry["glb"]
    if not path.is_file():
        pytest.skip(f"{path} is gitignored and absent")
    return path


@functools.lru_cache(maxsize=2)
def _real(slug: str):
    from simdata.build_track import prepare_ring
    entry = registry_entry(slug)
    path = _asset(slug)
    ring = prepare_ring(entry["event"])[0]
    surf = load_surface(path)
    return ring, surf, TriangleIndex(surf)


def test_registry_lists_both_assets_with_the_shared_gate():
    reg = load_registry()
    assert reg["gate"]["minCoverage"] == GATE_MIN_COVERAGE
    assert reg["gate"]["maxResidualStdM"] == GATE_MAX_RESIDUAL_STD_M
    assert set(reg["circuits"]) == {"british-grand-prix", "chinese-grand-prix"}


def test_silverstone_asphalt_matches_the_documented_geometry():
    """env_sim.md: asphalt.001 is 26,764 triangles with bbox min [-532.6, -889.6, -4.1],
    max [576.8, 874.5, 7.5] in RAW coordinates. Measured here after the |n.y| >= 0.05
    filter: 26,758 -- the six dropped are near-vertical."""
    _, surf, _ = _real("british-grand-prix")
    sel = [i for i, p in enumerate(surf.primitives) if p.material_name == "asphalt.001"]
    assert len(sel) == 1
    tris = surf.tri_prim == sel[0]
    assert int(tris.sum()) == 26758
    v = surf.verts[surf.tris[tris]].reshape(-1, 3)
    raw = np.column_stack((v[:, 0], v[:, 2], -v[:, 1]))     # world -> raw Z-up
    np.testing.assert_allclose(raw.min(axis=0), [-532.6, -889.6, -4.1], atol=0.3)
    np.testing.assert_allclose(raw.max(axis=0), [576.8, 874.5, 7.5], atol=0.3)


def test_silverstone_bake_reproduces_the_measured_numbers():
    """The gate numbers env_sim.md already measured against this same geometry:
    coverage >= 99.8 %, residual std ~0.054 m, max ~0.179 m, scale 1.0. Treat a material
    deviation here as a bug in the bake, not a new result."""
    slug = "british-grand-prix"
    entry = registry_entry(slug)
    assert entry.get("fit"), "config/circuits.yaml carries no fitted transform"
    ring, _, index = _real(slug)
    bake = bake_surface(index, ring, Fit.from_dict(entry["fit"]))
    assert bake.coverage >= 0.998
    assert bake.residual_std_m < 0.06
    assert bake.residual_max_m < 0.20
    assert bake.fit.scale == pytest.approx(1.0, abs=0.002)
    assert bake.gate()[0], bake.summary()


def test_silverstone_lands_on_the_asphalt_the_model_names():
    """Nothing forces this: the scorer would happily take any dark horizontal surface.
    That >99 % of stations land on `asphalt.001` is evidence the alignment is right."""
    slug = "british-grand-prix"
    ring, _, index = _real(slug)
    bake = bake_surface(index, ring, Fit.from_dict(registry_entry(slug)["fit"]))
    assert bake.road_coverage > 0.99
    top = max(bake.primitives.items(), key=lambda kv: kv[1])
    assert "asphalt" in top[0]


@pytest.mark.parametrize("slug", ["british-grand-prix", "chinese-grand-prix"])
def test_registry_measurements_are_reproducible(slug):
    """The registry must record what the bake actually measures -- including a FAILURE.
    A stale `measured` block is how a circuit that no longer aligns keeps shipping."""
    entry = registry_entry(slug)
    if not entry.get("fit"):
        pytest.skip(f"{slug} has no fitted transform recorded")
    ring, surf, index = _real(slug)
    assert surf.sha256 == entry["sha256"], "the GLB on disk is not the one that was fitted"
    bake = bake_surface(index, ring, Fit.from_dict(entry["fit"]))
    m = entry["measured"]
    assert bake.coverage == pytest.approx(m["coverage"], abs=1e-4)
    assert bake.residual_std_m == pytest.approx(m["residualStdM"], abs=2e-3)
    assert bake.residual_max_m == pytest.approx(m["residualMaxM"], abs=2e-3)
    assert ("pass" if bake.gate()[0] else "FAIL") == m["gate"]


def test_shanghai_is_reported_honestly():
    """Shanghai has no usable metadata: 100 materials called `Merged_materials` and
    `advert_side_a_1..97`, meshes called Object_N, road and grass and buildings mixed
    into single 61.5k-triangle chunks. The scorer is expected to carry it on geometry
    and colour alone -- but if it cannot clear the gate, the registry must say so and
    the circuit keeps the procedural ribbon. This test asserts the HONESTY, not a pass."""
    entry = registry_entry("chinese-grand-prix")
    if not entry.get("measured"):
        pytest.skip("chinese-grand-prix has not been measured yet")
    _, surf, _ = _real("chinese-grand-prix")
    assert not any(ACCEPT_NAME.search(p.label) for p in surf.primitives), \
        "if this model has gained usable road names, re-plan its profile"
    m = entry["measured"]
    passes = (m["coverage"] >= GATE_MIN_COVERAGE
              and m["residualStdM"] <= GATE_MAX_RESIDUAL_STD_M)
    assert m["gate"] == ("pass" if passes else "FAIL")


PROVENANCE_VOCABULARY = ("OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "RULE",
                         "DEFAULT")


def test_surface_block_provenance_is_one_of_the_six_and_is_not_observed():
    """The vocabulary is exactly six words and absence is `null`, never a seventh.

    And this block is DERIVED, not OBSERVED: no car measured these heights. They are a
    third-party model's geometry read under a transform fitted to the observed ring, and
    AGENTS.md 13.6 forbids calling that OBSERVED merely because its inputs were.
    """
    ring, _, index = synthetic_index()
    bake = bake_surface(index, ring, TRUE_FIT.replace(tx=TRUE_FIT.tx + 700.0))
    block = surface_block(bake, "synthetic")
    assert block["provenance"].split()[0] in PROVENANCE_VOCABULARY
    assert block["provenance"].startswith("DERIVED")
    missing = int(np.where(~bake.valid)[0][0])
    for key in ("zCm", "slopePermille", "camberPermille"):
        assert block[key][missing] is None, key
    assert block["validMask"][missing] == 0
    assert 0.0 <= block["coverage"] < 1.0


def test_sha256_matches_the_registry():
    for slug in ("british-grand-prix", "chinese-grand-prix"):
        entry = registry_entry(slug)
        if not entry.get("sha256"):
            continue
        assert sha256_file(_asset(slug)) == entry["sha256"]
