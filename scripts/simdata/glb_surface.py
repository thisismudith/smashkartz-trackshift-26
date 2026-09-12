"""Bake a real GLB circuit's DRIVE SURFACE onto the telemetry ring.

The telemetry ring is the ground truth, not the model. We already know to the metre the
path a car takes around each circuit, so a GLB never has to TELL us where its road is:

  * position, rotation AND scale come from fitting the ring onto the model's drivable
    surface -- a model in feet, half scale or arbitrary units is recovered by the fit
    rather than assumed away;
  * "which surface is the road" is answered by "the one the ring lands on";
  * the fit metric is the fraction of ring stations landing on drivable surface, which
    is also the quality gate.

Nothing here is race physics. It is presentation-side geometry: a per-station surface
height, slope and camber that let the renderer stand a car on the real model's road
instead of on a procedural ribbon. It still lives in Python because that is where this
project's maths lives, and because the browser must render results, not compute them.

THE SCORER
----------
Which of several surfaces under a station is "the road" is decided by one scorer, ported
from the e-delta prototype's `driveableScore`, that degrades gracefully from
well-named models to models with no usable metadata at all:

  * a hard REJECT list by name returns -inf. This -- not a "topmost" rule -- is what
    stops a pit structure OVER the track being sampled as the road; a topmost rule grabs
    its roof.
  * a broad ACCEPT list is a strong positive, not a requirement.
  * the surface normal: near-up strongly positive, near-vertical strongly negative.
  * material colour: dark AND desaturated is a small positive (asphalt is).
  * having a texture map is a small positive -- the least-assumptive fallback when every
    mesh is called Object_N, which is exactly Shanghai.
  * HYSTERESIS: strongly prefer the surface the PREVIOUS ring station landed on, and
    the height it landed at. Walking the ring in order gives that continuity for free,
    and it subsumes "disambiguate by nearest-to-telemetry-z, never topmost".

HONESTY
-------
A station with no accepted hit gets `valid=False` and NaN for z/slope/camber. There is no
default surface height: a plausible-looking fallback would be exactly the fabrication
this project forbids, and absence is `null`, not a seventh provenance. Coverage and
residual std are measured and gated; a circuit that fails the gate keeps the procedural
ribbon, which costs nothing.

The baked height is DERIVED, not OBSERVED. env_sim.md proposed OBSERVED, but no car ever
measured it: it is a third-party model's geometry read under a transform fitted to the
observed ring, and AGENTS.md 13.6 is explicit that a value produced FROM observed inputs
is not thereby observed.

Orientation is BAKED from the sampled height field and smoothed (~25 m), never read from
face normals: triangles are ~7 m across, so face normals are piecewise constant and roll
would step at every edge.

No new dependencies: struct + json + numpy for the geometry, `geom.Ring` /
`geom.smooth_circular` for the ring maths, PyYAML for the registry.

Usage:
    python -m simdata.glb_surface fit  "British Grand Prix"   # search from cold
    python -m simdata.glb_surface bake "British Grand Prix"   # from config/circuits.yaml
Both print the config/circuits.yaml entry they measured, ready to paste.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simdata.geom import Ring, smooth_circular

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = REPO_ROOT / "config" / "circuits.yaml"

# ---------------------------------------------------------------------------
# The quality gate. Every profile must report these two numbers, and a bake below
# either threshold is NOT shipped -- this is what stops a bad alignment shipping
# floating cars. Do not loosen them to make a circuit pass.
# Road-edge walk. Signed, because the ring is the RACING LINE and not the road centre:
# measured at Silverstone the road centre runs -10.00 m to +9.75 m from the ring, and at
# the grid the asphalt ends 1.75 m to the LEFT while running 17.5 m to the RIGHT. A
# single symmetric half-width cannot describe that, which is why the grid was laid out
# symmetrically about the ring and half the field ended up on the grass.
EDGE_STEP_M = 0.5
EDGE_MAX_M = 40.0

GATE_MIN_COVERAGE = 0.99
GATE_MAX_RESIDUAL_STD_M = 0.15


# ===========================================================================
# 1. GLB container
# ===========================================================================

GLB_MAGIC = 0x46546C67          # b"glTF"
CHUNK_JSON = 0x4E4F534A         # b"JSON"
CHUNK_BIN = 0x004E4942          # b"BIN\0"

# Extensions that change how vertex data is encoded. We read raw accessors, so a model
# requiring one of these must fail loudly rather than silently produce garbage geometry.
_UNSUPPORTED_REQUIRED = {"KHR_draco_mesh_compression", "EXT_meshopt_compression",
                         "KHR_mesh_quantization"}


@dataclass(frozen=True)
class Glb:
    """A parsed GLB: its glTF JSON and its binary chunk."""
    gltf: dict
    binary: bytes

    @property
    def generator(self) -> str:
        return str(self.gltf.get("asset", {}).get("generator", ""))


def parse_glb(blob: bytes) -> Glb:
    """Parse a binary glTF container. Pure: bytes in, structure out."""
    if len(blob) < 12:
        raise ValueError("not a GLB: shorter than the 12-byte header")
    magic, version, total = struct.unpack_from("<III", blob, 0)
    if magic != GLB_MAGIC:
        raise ValueError(f"not a GLB: magic 0x{magic:08x}")
    if version != 2:
        raise ValueError(f"unsupported GLB version {version}")
    if total > len(blob):
        raise ValueError(f"GLB declares {total} bytes but only {len(blob)} are present")

    gltf = None
    binary = b""
    off = 12
    while off + 8 <= total:
        clen, ctype = struct.unpack_from("<II", blob, off)
        off += 8
        if off + clen > total:
            raise ValueError("GLB chunk runs past the declared length")
        chunk = blob[off:off + clen]
        if ctype == CHUNK_JSON:
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == CHUNK_BIN:
            binary = chunk
        off += clen + (-clen % 4)     # chunks are 4-byte aligned

    if gltf is None:
        raise ValueError("GLB has no JSON chunk")
    required = set(gltf.get("extensionsRequired") or ())
    blocked = required & _UNSUPPORTED_REQUIRED
    if blocked:
        raise ValueError(f"GLB requires unsupported extensions: {sorted(blocked)}")
    return Glb(gltf=gltf, binary=binary)


def load_glb(path: Path | str) -> Glb:
    """Thin IO wrapper. All the maths below takes the parsed structure, never a path."""
    return parse_glb(Path(path).read_bytes())


def _buffer_bytes(glb: Glb, index: int) -> bytes:
    """Bytes of buffer `index`. GLB buffer 0 is the BIN chunk; data: URIs are decoded."""
    buf = glb.gltf["buffers"][index]
    uri = buf.get("uri")
    if uri is None:
        return glb.binary
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    raise ValueError(f"external buffer {uri!r}: this reader only handles self-contained GLBs")


# ===========================================================================
# 2. Accessors
# ===========================================================================

_COMPONENT_DTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16,
                    5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_COMPONENT_MAX = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
_TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
               "MAT2": 4, "MAT3": 9, "MAT4": 16}


def accessor_array(glb: Glb, index: int) -> np.ndarray:
    """Read accessor `index` as a (count, ncomp) array, honouring byteStride.

    Interleaved vertex buffers are the normal case in a Sketchfab export, so the stride
    path is not an edge case: reading it as if it were tightly packed silently mixes
    POSITION with NORMAL and produces a plausible-looking but wrong surface.
    """
    acc = glb.gltf["accessors"][index]
    if "sparse" in acc:
        raise ValueError("sparse accessors are not supported")
    ncomp = _TYPE_COUNT[acc["type"]]
    dtype = np.dtype(_COMPONENT_DTYPE[acc["componentType"]]).newbyteorder("<")
    count = int(acc["count"])
    elem = ncomp * dtype.itemsize

    if "bufferView" not in acc:
        return np.zeros((count, ncomp), dtype=np.float64)

    bv = glb.gltf["bufferViews"][acc["bufferView"]]
    data = _buffer_bytes(glb, bv.get("buffer", 0))
    stride = int(bv.get("byteStride") or elem)
    base = int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    span = stride * (count - 1) + elem if count else 0
    if base + span > len(data):
        raise ValueError("accessor runs past the end of its buffer")

    raw = np.frombuffer(data, dtype=np.uint8, count=span, offset=base)
    if stride == elem:
        out = raw.view(dtype).reshape(count, ncomp)
    else:
        rows = (np.arange(count)[:, None] * stride + np.arange(elem)[None, :])
        out = raw[rows].copy().view(dtype).reshape(count, ncomp)
    out = out.astype(np.float64)
    if acc.get("normalized") and acc["componentType"] in _COMPONENT_MAX:
        out = out / _COMPONENT_MAX[acc["componentType"]]
        if acc["componentType"] in (5120, 5122):
            out = np.maximum(out, -1.0)
    return out


# ===========================================================================
# 3. Node matrices
# ===========================================================================

def node_matrix(node: dict) -> np.ndarray:
    """Local 4x4 of one glTF node, in column-vector convention (world = M @ local).

    glTF stores `matrix` COLUMN-major, so the flat 16 must be reshaped (4, 4) and
    transposed. Getting that backwards transposes the rotation, which for these
    Sketchfab exports turns a +90 deg roll about X into a -90 deg roll -- a model that
    still looks like a circuit, mirrored through the ground plane.
    """
    if "matrix" in node:
        return np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
    m = np.eye(4)
    if "scale" in node:
        m = np.diag(list(node["scale"]) + [1.0]) @ m
    if "rotation" in node:
        x, y, z, w = (float(v) for v in node["rotation"])      # glTF quaternion is xyzw
        r = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0],
            [0.0, 0.0, 0.0, 1.0]])
        m = r @ m
    if "translation" in node:
        t = np.eye(4)
        t[:3, 3] = node["translation"]
        m = t @ m
    return m


def world_matrices(gltf: dict, scene: int | None = None) -> dict[int, np.ndarray]:
    """Compose every reachable node's world matrix by walking the scene graph.

    Returns {node index: 4x4}. Nodes not reachable from the scene are absent: a node the
    scene does not reference is not drawn, so its geometry must not be sampled either.
    """
    nodes = gltf.get("nodes", [])
    scenes = gltf.get("scenes", [])
    if scene is None:
        scene = int(gltf.get("scene", 0))
    roots = scenes[scene]["nodes"] if scenes else list(range(len(nodes)))

    out: dict[int, np.ndarray] = {}
    stack = [(int(r), np.eye(4)) for r in reversed(list(roots))]
    while stack:
        idx, parent = stack.pop()
        if idx in out:
            raise ValueError(f"node {idx} reached twice: the scene graph is not a tree")
        world = parent @ node_matrix(nodes[idx])
        out[idx] = world
        for child in reversed(nodes[idx].get("children", []) or []):
            stack.append((int(child), world))
    return out


# ===========================================================================
# 4. Primitives and the triangle soup
# ===========================================================================

# Ported from e-delta's main.js. Circuit exports share no material naming convention,
# so these cover the common names while the geometry/colour scoring below still carries
# a model whose meshes are all called Object_N.
ACCEPT_NAME = re.compile(
    r"(?:asphalt|asph|tarmac|road|track|circuit|raceway|pit[ _-]?lane|pitroad"
    r"|concrete|paving|pavement|curb|kerb|run[ _-]?off)", re.I)
REJECT_NAME = re.compile(
    r"(?:grass|terrain|ground|landscape|sand|gravel|dirt|soil|tree|bush|vegetation"
    r"|wall|barrier|fence|guardrail|building|grandstand|stand|roof|bridge|water|sky)",
    re.I)

SCORE_ACCEPT_NAME = 100.0
SCORE_NORMAL_UP = 16.0          # |n.y| >= 0.90
SCORE_NORMAL_TILTED = 9.0       # |n.y| >= 0.55
SCORE_NORMAL_VERTICAL = -25.0   # |n.y| <  0.15
SCORE_COLOUR_NEUTRAL = 12.0     # dark AND desaturated
SCORE_COLOUR_DARK = 7.0
SCORE_HAS_MAP = 4.0
SCORE_HYSTERESIS_SAME = 24.0    # same primitive as the previous ring station
MIN_DRIVEABLE_SCORE = 12.0

# Hysteresis in height. e-delta keeps the car's current road mesh; walking the ring in
# order gives the same continuity, and on a model like Shanghai -- where road, grass and
# buildings share ONE primitive -- primitive identity discriminates nothing, so the
# height the previous station landed at has to carry it. 6 points per metre means a 1 m
# jump costs less than the name bonus but decisively separates a road from a roof 10 m up.
SCORE_HYSTERESIS_PER_M = 6.0
SCORE_HYSTERESIS_CAP_M = 12.0

# Triangles flatter than this cannot meaningfully be hit by a vertical ray: their
# XZ projection has near-zero area. Dropping them keeps the spatial index small.
MIN_HORIZONTALITY = 0.05


@dataclass(frozen=True)
class Primitive:
    """One drawable mesh primitive, with everything the scorer reads about it."""
    index: int
    node: int
    mesh: int
    prim: int
    node_name: str
    mesh_name: str
    material_name: str
    texture_name: str
    base_color: tuple[float, float, float]
    has_map: bool
    tri_count: int

    @property
    def label(self) -> str:
        """What e-delta's `intersectionLabel` builds: every name attached to the hit."""
        return " ".join(p for p in (self.node_name, self.mesh_name,
                                    self.material_name, self.texture_name) if p)


@dataclass
class DriveSurface:
    """A triangle soup in the model's Y-up world frame, plus per-primitive metadata.

    Only triangles that could plausibly be driven on are kept: primitives rejected by
    name are dropped whole, and near-vertical triangles are dropped because a downward
    ray cannot land on them.
    """
    verts: np.ndarray            # (V, 3) float64, world frame, Y up
    tris: np.ndarray             # (T, 3) int32 into verts
    tri_prim: np.ndarray         # (T,)   int32 into primitives
    tri_ny: np.ndarray           # (T,)   float64, |world normal . up| in [0, 1]
    primitives: list[Primitive]
    static_score: np.ndarray     # (P,) float64 -- name + colour + texture, per primitive
    rejected: list[Primitive] = field(default_factory=list)
    source: str = ""
    sha256: str = ""

    @property
    def tri_count(self) -> int:
        return int(self.tris.shape[0])


def _material_info(gltf: dict, index: int | None) -> tuple[str, str, tuple, bool]:
    """(material name, texture image name, base colour rgb, has base-colour map)."""
    if index is None:
        return "", "", (1.0, 1.0, 1.0), False
    mat = gltf.get("materials", [])[index]
    pbr = mat.get("pbrMetallicRoughness", {}) or {}
    colour = tuple(float(c) for c in (pbr.get("baseColorFactor") or (1, 1, 1, 1))[:3])
    tex_name = ""
    has_map = "baseColorTexture" in pbr
    if has_map:
        tex = gltf.get("textures", [])[pbr["baseColorTexture"]["index"]]
        src = tex.get("source")
        if src is not None:
            tex_name = str(gltf.get("images", [])[src].get("name", "") or "")
        tex_name = tex_name or str(tex.get("name", "") or "")
    return str(mat.get("name", "") or ""), tex_name, colour, has_map


def surface_colour_score(rgb) -> float:
    """e-delta's `surfaceColourScore`: unnamed asphalt is dark and neutral.

    Deliberately a small signal. Names and geometry always dominate it.
    """
    r, g, b = (float(v) for v in rgb[:3])
    hi, lo = max(r, g, b), min(r, g, b)
    lightness = 0.5 * (hi + lo)
    if hi == lo:
        sat = 0.0
    elif lightness <= 0.5:
        sat = (hi - lo) / (hi + lo)
    else:
        sat = (hi - lo) / (2.0 - hi - lo)
    if lightness <= 0.62 and sat <= 0.38:
        return SCORE_COLOUR_NEUTRAL
    if lightness <= 0.38:
        return SCORE_COLOUR_DARK
    return 0.0


def primitive_static_score(prim: Primitive) -> float:
    """Everything the scorer can know from a primitive's metadata alone.

    -inf means REJECT. That is the rule that stops a structure OVER the track being
    sampled as the road; a "topmost surface" rule grabs its roof instead.
    """
    label = prim.label
    if REJECT_NAME.search(label):
        return -math.inf
    score = 0.0
    if ACCEPT_NAME.search(label):
        score += SCORE_ACCEPT_NAME
    score += surface_colour_score(prim.base_color)
    if prim.has_map:
        score += SCORE_HAS_MAP
    return score


def normal_score(horizontality: np.ndarray | float):
    """Near-up strongly positive, near-vertical strongly negative (e-delta's bands).

    `horizontality` is |n . up|. The sign of the raw normal is not used: a double-sided
    export winds its road triangles both ways, so a signed test would reject half a road.
    """
    h = np.asarray(horizontality, dtype=np.float64)
    out = np.where(h >= 0.90, SCORE_NORMAL_UP,
                   np.where(h >= 0.55, SCORE_NORMAL_TILTED,
                            np.where(h < 0.15, SCORE_NORMAL_VERTICAL, 0.0)))
    return out if out.ndim else float(out)


def extract_drive_surface(glb: Glb, *, min_horizontality: float = MIN_HORIZONTALITY,
                          source: str = "", sha256: str = "") -> DriveSurface:
    """Build the candidate drivable triangle soup in the model's Y-up world frame.

    Pure: the parsed GLB in, geometry out. Primitives rejected by name never enter the
    soup, which is both correct (they are not road) and what keeps the spatial index
    small enough to raycast a whole ring in milliseconds.
    """
    gltf = glb.gltf
    worlds = world_matrices(gltf)
    meshes = gltf.get("meshes", [])

    primitives: list[Primitive] = []
    rejected: list[Primitive] = []
    static: list[float] = []
    vert_blocks: list[np.ndarray] = []
    tri_blocks: list[np.ndarray] = []
    prim_blocks: list[np.ndarray] = []
    vert_base = 0

    for node_idx in sorted(worlds):
        node = gltf["nodes"][node_idx]
        if "mesh" not in node:
            continue
        mesh = meshes[node["mesh"]]
        world = worlds[node_idx]
        for prim_idx, prim in enumerate(mesh.get("primitives", [])):
            if int(prim.get("mode", 4)) != 4:
                continue                                   # points/lines/strips: not road
            if "POSITION" not in prim.get("attributes", {}):
                continue
            mat_name, tex_name, colour, has_map = _material_info(gltf, prim.get("material"))
            if "indices" in prim:
                idx = accessor_array(glb, prim["indices"]).astype(np.int64).ravel()
            else:
                n = int(gltf["accessors"][prim["attributes"]["POSITION"]]["count"])
                idx = np.arange(n, dtype=np.int64)
            info = Primitive(
                index=len(primitives), node=node_idx, mesh=int(node["mesh"]), prim=prim_idx,
                node_name=str(node.get("name", "") or ""),
                mesh_name=str(mesh.get("name", "") or ""),
                material_name=mat_name, texture_name=tex_name,
                base_color=colour, has_map=has_map, tri_count=len(idx) // 3)
            score = primitive_static_score(info)
            if not math.isfinite(score):
                rejected.append(info)
                continue

            pos = accessor_array(glb, prim["attributes"]["POSITION"])[:, :3]
            pos = pos @ world[:3, :3].T + world[:3, 3]
            faces = idx[: (len(idx) // 3) * 3].reshape(-1, 3)

            a, b, c = pos[faces[:, 0]], pos[faces[:, 1]], pos[faces[:, 2]]
            nrm = np.cross(b - a, c - a)
            mag = np.linalg.norm(nrm, axis=1)
            keep = mag > 0
            horiz = np.zeros(len(faces))
            horiz[keep] = np.abs(nrm[keep, 1]) / mag[keep]
            keep &= horiz >= min_horizontality
            if not keep.any():
                primitives.append(info)
                static.append(score)
                continue

            faces = faces[keep]
            used, remap = np.unique(faces, return_inverse=True)
            vert_blocks.append(pos[used])
            tri_blocks.append(remap.reshape(-1, 3).astype(np.int64) + vert_base)
            prim_blocks.append(np.full(len(faces), len(primitives), dtype=np.int32))
            vert_base += len(used)
            primitives.append(info)
            static.append(score)

    if vert_blocks:
        verts = np.concatenate(vert_blocks)
        tris = np.concatenate(tri_blocks).astype(np.int32)
        tri_prim = np.concatenate(prim_blocks)
    else:
        verts = np.zeros((0, 3))
        tris = np.zeros((0, 3), dtype=np.int32)
        tri_prim = np.zeros(0, dtype=np.int32)

    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    nrm = np.cross(b - a, c - a)
    mag = np.linalg.norm(nrm, axis=1)
    mag[mag == 0] = 1.0
    tri_ny = np.abs(nrm[:, 1]) / mag

    return DriveSurface(verts=verts, tris=tris, tri_prim=tri_prim, tri_ny=tri_ny,
                        primitives=primitives,
                        static_score=np.asarray(static, dtype=np.float64),
                        rejected=rejected, source=source, sha256=sha256)


# ===========================================================================
# 5. Exact vertical raycast
# ===========================================================================

class TriangleIndex:
    """Uniform XZ bucket grid over a triangle soup, for exact downward raycasting.

    A KD-tree over centroids would need a k big enough for the largest triangle; a bucket
    grid keyed by each triangle's own XZ footprint is exact for every size, so no hit is
    ever missed because a triangle happened to be large.
    """

    # A triangle whose footprint covers more cells than this is checked against every
    # query point instead of being written into that many buckets. Measured on
    # Silverstone: at an 8 m cell this leaves 21 oversized triangles out of 549,852,
    # against 204 at a cap of 256 -- and every oversized triangle costs one candidate
    # pair per ring station, which is what dominates a raycast.
    MAX_CELLS_PER_TRI = 4096

    def __init__(self, surface: DriveSurface, cell_m: float | None = None):
        self.surface = surface
        tris, verts = surface.tris, surface.verts
        if len(tris) == 0:
            raise ValueError("cannot index an empty drive surface")
        tv = verts[tris]                               # (T, 3, 3)
        self.lo = tv[:, :, [0, 2]].min(axis=1)         # (T, 2) xz
        self.hi = tv[:, :, [0, 2]].max(axis=1)
        if cell_m is None:
            # p90 rather than the median: the median triangle is sub-metre decal
            # geometry, and sizing the grid to that buries every road triangle in
            # dozens of buckets. Measured on Silverstone: p50 0.44 m, p90 4.47 m,
            # p99 12.8 m, max 709 m.
            span = np.percentile(np.maximum(self.hi - self.lo, 0.0).max(axis=1), 90)
            cell_m = float(max(4.0, min(32.0, span * 2.0)))
        self.cell = float(cell_m)
        self.origin = self.lo.min(axis=0) - self.cell
        extent = self.hi.max(axis=0) + self.cell - self.origin
        self.nx = int(math.ceil(extent[0] / self.cell)) + 1
        self.nz = int(math.ceil(extent[1] / self.cell)) + 1

        i0 = np.floor((self.lo - self.origin) / self.cell).astype(np.int64)
        i1 = np.floor((self.hi - self.origin) / self.cell).astype(np.int64)
        cells = (i1[:, 0] - i0[:, 0] + 1) * (i1[:, 1] - i0[:, 1] + 1)
        self.oversized = np.where(cells > self.MAX_CELLS_PER_TRI)[0].astype(np.int32)

        normal = np.where(cells <= self.MAX_CELLS_PER_TRI)[0]
        keys: list[np.ndarray] = []
        owners: list[np.ndarray] = []
        # Group by footprint shape so the expansion stays vectorised.
        shape = np.column_stack((i1[normal, 0] - i0[normal, 0] + 1,
                                 i1[normal, 1] - i0[normal, 1] + 1))
        for (w, h) in {tuple(r) for r in shape}:
            sel = normal[(shape[:, 0] == w) & (shape[:, 1] == h)]
            dx = np.arange(w)[None, :, None]
            dz = np.arange(h)[None, None, :]
            gx = i0[sel, 0][:, None, None] + dx
            gz = i0[sel, 1][:, None, None] + dz
            keys.append((gz * self.nx + gx).ravel())
            owners.append(np.repeat(sel.astype(np.int32), w * h))
        flat_key = np.concatenate(keys) if keys else np.zeros(0, dtype=np.int64)
        flat_tri = np.concatenate(owners) if owners else np.zeros(0, dtype=np.int32)
        order = np.argsort(flat_key, kind="stable")
        self.entry_tri = flat_tri[order]
        sorted_key = flat_key[order]
        self.keys, starts, self.counts = np.unique(sorted_key, return_index=True,
                                                   return_counts=True)
        self.offsets = starts.astype(np.int64)

    def candidates(self, x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(query index, triangle index) pairs whose footprints contain each point."""
        x = np.asarray(x, dtype=np.float64)
        z = np.asarray(z, dtype=np.float64)
        gx = np.floor((x - self.origin[0]) / self.cell).astype(np.int64)
        gz = np.floor((z - self.origin[1]) / self.cell).astype(np.int64)
        inside = (gx >= 0) & (gx < self.nx) & (gz >= 0) & (gz < self.nz)
        qkey = np.where(inside, gz * self.nx + gx, -1)
        pos = np.searchsorted(self.keys, qkey)
        pos_clamped = np.minimum(pos, len(self.keys) - 1)
        hit = inside & (len(self.keys) > 0) & (self.keys[pos_clamped] == qkey)
        cnt = np.where(hit, self.counts[pos_clamped], 0)
        total = int(cnt.sum())
        qi = np.repeat(np.arange(len(x)), cnt)
        if total:
            starts = np.repeat(self.offsets[pos_clamped], cnt)
            within = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
            ti = self.entry_tri[starts + within]
        else:
            ti = np.zeros(0, dtype=np.int32)
        if len(self.oversized):
            qi = np.concatenate((qi, np.repeat(np.arange(len(x)), len(self.oversized))))
            ti = np.concatenate((ti, np.tile(self.oversized, len(x))))
        return qi, ti


def barycentric_hits(verts: np.ndarray, tris: np.ndarray, qi: np.ndarray,
                     ti: np.ndarray, x: np.ndarray, z: np.ndarray):
    """Exact point-in-triangle test in XZ, with the hit height by barycentric weights.

    Returns (qi, ti, y) for the pairs that actually intersect. The test is the three
    edge cross-products with a consistent sign; a point on a shared edge passes for both
    triangles, so a ring station never falls through a crack between two road triangles.
    """
    if len(qi) == 0:
        return qi, ti, np.zeros(0)
    tv = verts[tris[ti]]                                   # (N, 3, 3)
    ax, ay, az = tv[:, 0, 0], tv[:, 0, 1], tv[:, 0, 2]
    bx, by, bz = tv[:, 1, 0], tv[:, 1, 1], tv[:, 1, 2]
    cx, cy, cz = tv[:, 2, 0], tv[:, 2, 1], tv[:, 2, 2]
    px, pz = x[qi], z[qi]

    # Twice the signed area of each sub-triangle, in the XZ plane.
    wa = (bx - px) * (cz - pz) - (cx - px) * (bz - pz)
    wb = (cx - px) * (az - pz) - (ax - px) * (cz - pz)
    wc = (ax - px) * (bz - pz) - (bx - px) * (az - pz)
    area = wa + wb + wc
    scale = np.maximum(np.abs(area), 1e-12)
    eps = 1e-9 * scale
    inside = (np.abs(area) > 0) & (((wa >= -eps) & (wb >= -eps) & (wc >= -eps))
                                   | ((wa <= eps) & (wb <= eps) & (wc <= eps)))
    if not inside.any():
        return qi[:0], ti[:0], np.zeros(0)
    wa, wb, wc = wa[inside], wb[inside], wc[inside]
    area = area[inside]
    area = np.where(area == 0, 1e-12, area)
    y = (wa * ay[inside] + wb * by[inside] + wc * cy[inside]) / area
    return qi[inside], ti[inside], y


# ===========================================================================
# 6. The telemetry -> model transform
# ===========================================================================

@dataclass(frozen=True)
class Fit:
    """Telemetry (x, y, z) metres -> the model's Y-up world frame.

        u  = (scale * x, scale * mirror * y)
        wx = cos(yaw) * u.x - sin(yaw) * u.y + tx
        wz = sin(yaw) * u.x + cos(yaw) * u.y + tz
        wy = scale * z + ty

    `mirror` is a parameter, not an assumption: telemetry is right-handed with z up and
    a Y-up model frame is reached by (x, z, -y), so the horizontal map picks up a
    reflection that the fit has to be free to confirm or reject.
    """
    scale: float
    yaw_rad: float
    mirror: int
    tx: float
    tz: float
    ty: float

    def to_world(self, x, y, z=None):
        c, s = math.cos(self.yaw_rad), math.sin(self.yaw_rad)
        ux = self.scale * np.asarray(x, dtype=np.float64)
        uy = self.scale * self.mirror * np.asarray(y, dtype=np.float64)
        wx = c * ux - s * uy + self.tx
        wz = s * ux + c * uy + self.tz
        if z is None:
            return wx, wz
        wy = self.scale * np.asarray(z, dtype=np.float64) + self.ty
        return wx, wy, wz

    def telemetry_z(self, world_y):
        """Model world height back to a telemetry z, so residuals are in feed units."""
        return (np.asarray(world_y, dtype=np.float64) - self.ty) / self.scale

    def replace(self, **kw) -> "Fit":
        return Fit(**{**self.__dict__, **kw})

    def raw_glb(self) -> dict:
        """The same fit expressed in the model's raw Z-up coordinates.

        These Sketchfab node chains compose to world = (raw_x, -raw_z, raw_y), so
        raw_x = wx, raw_y = wz, raw_z = -wy. This is the form env_sim.md quotes.
        """
        return {"glbXOffsetM": self.tx, "glbYOffsetM": self.tz, "glbZOffsetM": -self.ty}

    def as_dict(self) -> dict:
        return {"scale": self.scale, "yawDeg": math.degrees(self.yaw_rad),
                "mirror": self.mirror, "txM": self.tx, "tzM": self.tz, "tyM": self.ty}

    @staticmethod
    def from_dict(d: dict) -> "Fit":
        return Fit(scale=float(d["scale"]), yaw_rad=math.radians(float(d["yawDeg"])),
                   mirror=int(d["mirror"]), tx=float(d["txM"]),
                   tz=float(d["tzM"]), ty=float(d["tyM"]))


# ===========================================================================
# 7. Sampling the surface under the ring
# ===========================================================================

@dataclass
class Sample:
    """What one raycast pass measured at every station."""
    valid: np.ndarray        # (n,) bool
    world_y: np.ndarray      # (n,) float, NaN where invalid
    tri: np.ndarray          # (n,) int32, -1 where invalid
    prim: np.ndarray         # (n,) int32, -1 where invalid
    score: np.ndarray        # (n,) float, -inf where invalid


def _hit_table(index: TriangleIndex, x: np.ndarray, z: np.ndarray):
    """All accepted-by-geometry hits under the given XZ points, grouped by point."""
    surf = index.surface
    qi, ti = index.candidates(x, z)
    qi, ti, y = barycentric_hits(surf.verts, surf.tris, qi, ti, x, z)
    base = surf.static_score[surf.tri_prim[ti]] + normal_score(surf.tri_ny[ti])
    return qi, ti, y, base


def sample_nearest_height(index: TriangleIndex, x: np.ndarray, z: np.ndarray,
                          expected_y: np.ndarray) -> Sample:
    """Vectorised pass: best score, ties broken by proximity to the expected height.

    Used inside the fit loop, where the sequential walk's hundreds of Python iterations
    would dominate the cost. `expected_y` is the telemetry height under the current fit
    -- the ring is the ground truth, so "nearest to where the car actually was" is the
    honest disambiguator, and it is exactly what the sequential hysteresis converges to.
    """
    n = len(x)
    surf = index.surface
    qi, ti, y, base = _hit_table(index, x, z)
    valid = np.zeros(n, dtype=bool)
    out_y = np.full(n, np.nan)
    out_tri = np.full(n, -1, dtype=np.int32)
    out_prim = np.full(n, -1, dtype=np.int32)
    out_score = np.full(n, -np.inf)
    if len(qi) == 0:
        return Sample(valid, out_y, out_tri, out_prim, out_score)

    drop = np.minimum(np.abs(y - expected_y[qi]), SCORE_HYSTERESIS_CAP_M)
    score = base - SCORE_HYSTERESIS_PER_M * drop
    keep = base >= MIN_DRIVEABLE_SCORE
    if not keep.any():
        return Sample(valid, out_y, out_tri, out_prim, out_score)
    qi, ti, y, score = qi[keep], ti[keep], y[keep], score[keep]

    order = np.lexsort((-score, qi))
    qi, ti, y, score = qi[order], ti[order], y[order], score[order]
    first = np.concatenate(([True], qi[1:] != qi[:-1]))
    sel = np.where(first)[0]
    idx = qi[sel]
    valid[idx] = True
    out_y[idx] = y[sel]
    out_tri[idx] = ti[sel]
    out_prim[idx] = surf.tri_prim[ti[sel]]
    out_score[idx] = score[sel]
    return Sample(valid, out_y, out_tri, out_prim, out_score)


def sample_with_hysteresis(index: TriangleIndex, x: np.ndarray, z: np.ndarray,
                           expected_y: np.ndarray, passes: int = 2) -> Sample:
    """e-delta's hysteresis, walked around the ring in station order.

    Each station strongly prefers the primitive the previous accepted station landed on
    and the height it landed at. That is what stops a terrain mesh stealing the car
    mid-elevation-change, and -- on a model where road, grass and buildings share one
    primitive -- what stops a pit roof being read as the road.

    The walk is run twice so the answer does not depend on where the ring happens to
    start: the second pass is seeded from the first pass's last accepted station.
    """
    n = len(x)
    surf = index.surface
    qi, ti, y, base = _hit_table(index, x, z)
    # Drop below-threshold hits BEFORE the walk. Filtering after the argmax would let a
    # rejected hit that happened to win on hysteresis veto an acceptable one underneath
    # it, which showed up as ~7 % of camber probes reporting "no surface" over asphalt.
    keep = base >= MIN_DRIVEABLE_SCORE
    qi, ti, y, base = qi[keep], ti[keep], y[keep], base[keep]
    order = np.argsort(qi, kind="stable")
    qi, ti, y, base = qi[order], ti[order], y[order], base[order]
    starts = np.searchsorted(qi, np.arange(n))
    ends = np.searchsorted(qi, np.arange(n) + 1)
    prim_of = surf.tri_prim

    valid = np.zeros(n, dtype=bool)
    out_y = np.full(n, np.nan)
    out_tri = np.full(n, -1, dtype=np.int32)
    out_prim = np.full(n, -1, dtype=np.int32)
    out_score = np.full(n, -np.inf)

    prev_prim = -1
    prev_y = None
    for _ in range(max(1, passes)):
        for i in range(n):
            a, b = starts[i], ends[i]
            if a == b:
                continue
            ref = prev_y if prev_y is not None else expected_y[i]
            seg_y = y[a:b]
            seg_prim = prim_of[ti[a:b]]
            drop = np.minimum(np.abs(seg_y - ref), SCORE_HYSTERESIS_CAP_M)
            score = base[a:b] - SCORE_HYSTERESIS_PER_M * drop
            if prev_prim >= 0:
                score = score + np.where(seg_prim == prev_prim, SCORE_HYSTERESIS_SAME, 0.0)
            j = int(np.argmax(score))
            valid[i] = True
            out_y[i] = seg_y[j]
            out_tri[i] = ti[a + j]
            out_prim[i] = seg_prim[j]
            out_score[i] = score[j]
            prev_prim = int(seg_prim[j])
            prev_y = float(seg_y[j])
    return Sample(valid, out_y, out_tri, out_prim, out_score)


# ===========================================================================
# 8. Fitting the ring onto the model
# ===========================================================================

FIT_COVERAGE_WEIGHT = 10.0     # metres of residual that 100 % of coverage is worth


def _fit_ty(fit: Fit, ring_z: np.ndarray, world_y: np.ndarray, valid: np.ndarray) -> float:
    """Vertical offset as the median over stations that actually landed on something."""
    if not valid.any():
        return fit.ty
    return float(np.median(world_y[valid] - fit.scale * ring_z[valid]))


def fit_quality(index: TriangleIndex, ring: Ring, fit: Fit,
                stride: int = 1) -> tuple[float, float, float, Fit]:
    """(cost, coverage, residual std, fit with its vertical offset re-centred).

    The objective is not coverage alone: Silverstone's RUNOFF is the same asphalt as its
    road, so coverage plateaus over metres of translation and a coverage-only fit lands
    several metres out with a spurious yaw. The elevation residual is the sharp signal,
    because the telemetry z profile is known to centimetres; coverage keeps the fit from
    buying a small residual by dropping stations.
    """
    x, y, z = ring.x[::stride], ring.y[::stride], ring.z[::stride]
    wx, wy_expected, wz = fit.to_world(x, y, z)
    sample = sample_nearest_height(index, wx, wz, wy_expected)
    coverage = float(sample.valid.mean())
    if coverage <= 0.0:
        return math.inf, 0.0, math.inf, fit
    ty = _fit_ty(fit, z, sample.world_y, sample.valid)
    tuned = fit.replace(ty=ty)
    resid = sample.world_y[sample.valid] - (fit.scale * z[sample.valid] + ty)
    std = float(np.std(resid))
    cost = (1.0 - coverage) * FIT_COVERAGE_WEIGHT + std
    return cost, coverage, std, tuned


def strong_index(surface: DriveSurface) -> TriangleIndex:
    """A raycastable index over only the strongly drivable triangles."""
    strong = _strong_mask(surface)
    return TriangleIndex(DriveSurface(
        verts=surface.verts, tris=surface.tris[strong],
        tri_prim=surface.tri_prim[strong], tri_ny=surface.tri_ny[strong],
        primitives=surface.primitives, static_score=surface.static_score,
        source=surface.source, sha256=surface.sha256))


def _occupancy(index: TriangleIndex, cell: float, chunk: int = 20000):
    """XZ occupancy of the drivable surface, by raycasting every cell centre.

    Marking the cells that triangle VERTICES fall in is much cheaper and quietly wrong:
    a road built from 6 m triangles rasterised at 4 m leaves holes everywhere between
    the sample points, and the ring then scores 0.66 at the transform it was BUILT from.
    Raycasting each cell centre through the same exact test the bake uses cannot
    disagree with the bake about where the road is.
    """
    surf = index.surface
    lo = index.lo.min(axis=0) - 4 * cell
    hi = index.hi.max(axis=0) + 4 * cell
    nx = int(math.ceil((hi[0] - lo[0]) / cell)) + 1
    nz = int(math.ceil((hi[1] - lo[1]) / cell)) + 1
    gx, gz = np.meshgrid(np.arange(nx), np.arange(nz))
    cx = (lo[0] + (gx.ravel() + 0.5) * cell)
    cz = (lo[1] + (gz.ravel() + 0.5) * cell)
    grid = np.zeros(nz * nx, dtype=bool)
    for start in range(0, len(cx), chunk):
        x, z = cx[start:start + chunk], cz[start:start + chunk]
        qi, ti = index.candidates(x, z)
        qi, _, _ = barycentric_hits(surf.verts, surf.tris, qi, ti, x, z)
        if len(qi):
            grid[start + np.unique(qi)] = True
    return grid.reshape(nz, nx), lo, cell


def _strong_mask(surface: DriveSurface) -> np.ndarray:
    """Triangles confident enough to anchor a global search.

    Name-accepted road if the model has usable names; otherwise near-horizontal geometry,
    which is the least-assumptive signal a model like Shanghai leaves us.
    """
    named = surface.static_score[surface.tri_prim] >= SCORE_ACCEPT_NAME
    named &= surface.tri_ny >= 0.55
    if named.sum() >= 2000:
        return np.where(named)[0]
    return np.where(surface.tri_ny >= 0.90)[0]


def _scale_bounds(surface: DriveSurface, strong: np.ndarray, ring: Ring):
    """Bracket the model's unit scale from drivable AREA against known ring LENGTH.

    A drivable surface of area A in model units, wrapped around a circuit of known
    length L metres, has an average drivable width w = A / (s^2 * L). Widths outside
    8..140 m are not a race circuit's road-plus-runoff, so the scale is bracketed
    without ever assuming the model is already in metres.
    """
    tv = surface.verts[surface.tris[strong]]
    ex, ez = tv[:, 1, 0] - tv[:, 0, 0], tv[:, 1, 2] - tv[:, 0, 2]
    fx, fz = tv[:, 2, 0] - tv[:, 0, 0], tv[:, 2, 2] - tv[:, 0, 2]
    area = float(0.5 * np.abs(ex * fz - ez * fx).sum())     # XZ footprint, not slant area
    lo = math.sqrt(area / (ring.length * 140.0))
    hi = math.sqrt(area / (ring.length * 8.0))
    return max(lo / 1.5, 1e-3), hi * 1.5


class RingMatcher:
    """Scores one (mirror, scale, yaw) hypothesis and solves its translation EXACTLY.

    For a fixed orientation the best translation is the argmax of the cross-correlation
    between the rasterised ring and the drivable occupancy -- and an FFT evaluates every
    translation at once. So a four-parameter global search collapses to a two-parameter
    one (scale, yaw) per mirror, which is small enough to grid coarsely and then walk
    downhill. The score returned is the fraction of ring cells landing on drivable
    surface: the same quantity the gate measures, at the coarse cell size.
    """

    def __init__(self, surface: DriveSurface | TriangleIndex, ring: Ring, *,
                 cell: float = 8.0, stride: int = 4):
        index = surface if isinstance(surface, TriangleIndex) else strong_index(surface)
        self.grid, self.lo, self.cell = _occupancy(index, cell)
        nz, nx = self.grid.shape
        self.pad_z = 1 << int(math.ceil(math.log2(max(nz * 2, 16))))
        self.pad_x = 1 << int(math.ceil(math.log2(max(nx * 2, 16))))
        self.fgrid = np.fft.rfft2(self.grid.astype(np.float64),
                                  s=(self.pad_z, self.pad_x))
        rx, ry = ring.x[::stride], ring.y[::stride]
        self.cx, self.cy = float(rx.mean()), float(ry.mean())
        self.rx, self.ry = rx - self.cx, ry - self.cy

    def __call__(self, scale: float, yaw_rad: float, mirror: int) -> tuple[float, Fit]:
        c, s = math.cos(yaw_rad), math.sin(yaw_rad)
        ux, uy = scale * self.rx, scale * mirror * self.ry
        px = c * ux - s * uy
        pz = s * ux + c * uy
        gx = np.floor((px - px.min()) / self.cell).astype(np.int64)
        gz = np.floor((pz - pz.min()) / self.cell).astype(np.int64)
        zero = Fit(scale=scale, yaw_rad=yaw_rad, mirror=mirror, tx=0.0, tz=0.0, ty=0.0)
        if gx.max() >= self.pad_x or gz.max() >= self.pad_z:
            return 0.0, zero                      # the ring does not fit in the transform
        mask = np.zeros((self.pad_z, self.pad_x))
        mask[gz, gx] = 1.0
        cells = float(mask.sum())
        corr = np.fft.irfft2(self.fgrid * np.conj(np.fft.rfft2(mask)),
                             s=(self.pad_z, self.pad_x))
        flat = int(np.argmax(corr))
        dz_cell, dx_cell = divmod(flat, self.pad_x)
        if dz_cell > self.pad_z // 2:
            dz_cell -= self.pad_z
        if dx_cell > self.pad_x // 2:
            dx_cell -= self.pad_x
        tx = (self.lo[0] + dx_cell * self.cell - px.min()
              - (c * scale * self.cx - s * scale * mirror * self.cy))
        tz = (self.lo[1] + dz_cell * self.cell - pz.min()
              - (s * scale * self.cx + c * scale * mirror * self.cy))
        frac = float(corr.flat[flat]) / max(cells, 1.0)
        return frac, Fit(scale=float(scale), yaw_rad=float(yaw_rad), mirror=int(mirror),
                         tx=float(tx), tz=float(tz), ty=0.0)


def _climb(match: RingMatcher, fit: Fit, *, d_log_scale: float = 0.06,
           d_yaw_deg: float = 1.0, min_d_log_scale: float = 5e-4) -> tuple[float, Fit]:
    """Pattern search on (log scale, yaw) with the translation re-solved every step."""
    best_frac, best = match(fit.scale, fit.yaw_rad, fit.mirror)
    dls, dyaw = d_log_scale, math.radians(d_yaw_deg)
    while dls >= min_d_log_scale:
        moved = False
        for scale, yaw in ((best.scale * math.exp(dls), best.yaw_rad),
                           (best.scale * math.exp(-dls), best.yaw_rad),
                           (best.scale, best.yaw_rad + dyaw),
                           (best.scale, best.yaw_rad - dyaw)):
            frac, trial = match(scale, yaw, best.mirror)
            if frac > best_frac + 1e-9:
                best_frac, best = frac, trial
                moved = True
                break
        if not moved:
            dls *= 0.5
            dyaw *= 0.5
    return best_frac, best


def coarse_align(index: TriangleIndex, ring: Ring, *,
                 coarse_cell: float = 8.0, fine_cell: float = 4.0,
                 yaw_step_deg: float = 2.0, n_scales: int = 9, stride: int = 4,
                 keep: int = 6, verbose: bool = False) -> list[Fit]:
    """Global (mirror, scale, yaw, translation) search, coarse to fine.

    Round 0 grids (mirror, scale, yaw) at the coarse cell; round 1 walks each survivor
    downhill in (scale, yaw) at the fine cell. Both rounds solve translation exactly by
    FFT, so neither can be trapped by a bad starting position -- only by a bad scale or
    yaw, which is what the rounds are for.

    The fraction is biased towards SMALL scales: shrink the ring far enough and it sits
    entirely inside any dense blob of drivable cells. On Silverstone the true hypothesis
    still wins outright after round 1 (0.995 against 0.557 for the best shrunken one),
    but that margin is a property of a model whose road is a thin ribbon. Shanghai is
    42 % near-horizontal geometry, and there MANY hypotheses reach frac 1.000 -- so the
    best row at every (mirror, scale) is returned alongside the global best, and the
    caller settles the choice with the real objective, which reads the elevation
    residual and cannot be fooled by a shrunken or a flattened ring.
    """
    surface = index.surface
    s_lo, s_hi = _scale_bounds(surface, _strong_mask(surface), ring)
    strong = strong_index(surface)                # built once, shared by both rounds
    coarse = RingMatcher(strong, ring, cell=coarse_cell, stride=stride)

    grid_rows: list[tuple[float, Fit]] = []
    for mirror in (-1, 1):
        for scale in np.geomspace(s_lo, s_hi, n_scales):
            for yaw_deg in np.arange(0.0, 360.0, yaw_step_deg):
                grid_rows.append(coarse(float(scale), math.radians(float(yaw_deg)),
                                        mirror))
    grid_rows.sort(key=lambda r: -r[0])

    # Keep the global best plus the best row at every (mirror, scale): the round-0
    # ranking cannot be trusted to put the true scale first, only inside the list.
    seeds: list[tuple[float, Fit]] = list(grid_rows[:keep])
    seen = {(f.mirror, round(f.scale, 9)) for _, f in seeds}
    for frac, f in grid_rows:
        key = (f.mirror, round(f.scale, 9))
        if key not in seen:
            seen.add(key)
            seeds.append((frac, f))

    fine = RingMatcher(strong, ring, cell=fine_cell, stride=stride)
    climbed = [_climb(fine, f) for _, f in seeds]
    climbed.sort(key=lambda r: -r[0])

    out: list[Fit] = []
    for frac, f in climbed:
        if any(abs(math.log(f.scale / g.scale)) < 0.01 and g.mirror == f.mirror
               and abs((f.yaw_rad - g.yaw_rad + math.pi) % (2 * math.pi) - math.pi) < 0.01
               for g in out):
            continue                              # a duplicate of an already-kept basin
        out.append(f)
        if verbose:
            print(f"    coarse frac={frac:.4f} scale={f.scale:.5f} "
                  f"yaw={math.degrees(f.yaw_rad):+8.3f} mirror={f.mirror:+d} "
                  f"t=({f.tx:.1f}, {f.tz:.1f})")
        if len(out) >= keep:
            break
    return out


def _pivoted(fit: Fit, cx: float, cy: float, *, yaw: float | None = None,
             scale: float | None = None) -> Fit:
    """Change yaw or scale about the RING CENTROID, not about the frame origin.

    The model origin is hundreds of metres outside the circuit, so a bare 0.001 change
    in scale sweeps the whole ring metres sideways and always looks worse than doing
    nothing -- the search then never finds the scale. Measured on Silverstone: without
    this the fit stalls at scale 0.9990 for a residual std of 0.0499 m, when 1.0000 with
    translation re-solved gives 0.0495 m. Pivoting on the centroid decouples scale and
    yaw from translation.
    """
    px, pz = fit.to_world(cx, cy)
    moved = fit.replace(**({"yaw_rad": yaw} if yaw is not None else {"scale": scale}))
    qx, qz = moved.to_world(cx, cy)
    return moved.replace(tx=moved.tx + float(px - qx), tz=moved.tz + float(pz - qz))


def refine(index: TriangleIndex, ring: Ring, fit: Fit, *,
           step_m: float = 8.0, step_yaw_deg: float = 0.5, step_scale: float = 0.02,
           min_step_m: float = 0.005, stride: int = 1,
           verbose: bool = False) -> tuple[Fit, float, float, float]:
    """Compass search on (tx, tz, yaw, scale). Deterministic, no optimiser dependency."""
    cost, cov, std, fit = fit_quality(index, ring, fit, stride)
    cx, cy = float(ring.x.mean()), float(ring.y.mean())
    dt, dyaw, dsc = step_m, math.radians(step_yaw_deg), step_scale
    while dt >= min_step_m:
        improved = False
        for name, delta in (("tx", dt), ("tz", dt), ("yaw_rad", dyaw), ("scale", dsc)):
            for sign in (+1, -1):
                if name == "tx":
                    trial = fit.replace(tx=fit.tx + sign * delta)
                elif name == "tz":
                    trial = fit.replace(tz=fit.tz + sign * delta)
                elif name == "yaw_rad":
                    trial = _pivoted(fit, cx, cy, yaw=fit.yaw_rad + sign * delta)
                else:
                    trial = _pivoted(fit, cx, cy, scale=fit.scale + sign * delta)
                c, cv, sd, trial = fit_quality(index, ring, trial, stride)
                if c < cost - 1e-9:
                    fit, cost, cov, std = trial, c, cv, sd
                    improved = True
                    break
        if not improved:
            dt *= 0.5
            dyaw *= 0.5
            dsc *= 0.5
            if verbose:
                print(f"    refine cost={cost:.4f} cov={cov:.4%} std={std:.4f} "
                      f"step={dt:.3f} m")
    return fit, cost, cov, std


def fit_ring_to_surface(index: TriangleIndex, ring: Ring, *,
                        warm_start: Fit | None = None,
                        verbose: bool = False) -> tuple[Fit, dict]:
    """Fit the ring onto the model's drivable surface. Returns (fit, diagnostics)."""
    t0 = time.time()
    if warm_start is not None:
        candidates = [warm_start]
        coarse_ms = 0.0
    else:
        candidates = coarse_align(index, ring, verbose=verbose)
        coarse_ms = (time.time() - t0) * 1000.0

    # Three stages, each an order of magnitude dearer than the last, each seeing an
    # order of magnitude fewer hypotheses: a cheap stride-8 pass ranks every seed, the
    # two survivors get a stride-8 polish, and only the winner is refined against all
    # ~5800 stations.
    rough = []
    for seed in candidates:
        fit, cost, cov, std = refine(index, ring, seed, step_m=16.0, step_yaw_deg=1.0,
                                     step_scale=0.05, min_step_m=0.5, stride=8)
        rough.append((cost, fit))
        if verbose:
            print(f"    seed -> cost={cost:.4f} cov={cov:.4%} std={std:.4f} "
                  f"scale={fit.scale:.4f} yaw={math.degrees(fit.yaw_rad):+.3f}")
    rough.sort(key=lambda r: r[0])

    best = None
    for _, seed in rough[:2]:
        mid, _, _, _ = refine(index, ring, seed, step_m=4.0, step_yaw_deg=0.2,
                              step_scale=0.01, min_step_m=0.05, stride=8)
        fit, cost, cov, std = refine(index, ring, mid, step_m=1.0,
                                     step_yaw_deg=0.02, step_scale=0.001,
                                     min_step_m=0.005, stride=1, verbose=verbose)
        if best is None or cost < best[1]:
            best = (fit, cost, cov, std)
    fit, cost, cov, std = best
    return fit, {"coarseMs": coarse_ms, "totalMs": (time.time() - t0) * 1000.0,
                 "cost": cost, "fitCoverage": cov, "fitResidualStdM": std,
                 "seeds": len(candidates)}


# ===========================================================================
# 9. The bake
# ===========================================================================

SMOOTH_WINDOW_M = 25.0
CAMBER_HALF_WIDTHS_M = (2.0, 1.5, 1.0)   # widest first; the widest both sides support wins


@dataclass
class SurfaceBake:
    """Per-station drive surface, in TELEMETRY units, plus the numbers the gate reads."""
    z_m: np.ndarray                # (n,) telemetry-frame surface height, NaN if invalid
    slope_rad: np.ndarray          # (n,) along-track, + uphill; NaN if invalid
    camber_rad: np.ndarray         # (n,) + means the LEFT of travel is higher; NaN if invalid
    camber_base_m: np.ndarray      # (n,) half-width the camber probe actually used
    valid: np.ndarray              # (n,) bool
    camber_valid: np.ndarray       # (n,) bool
    residual_m: np.ndarray
    edge_left_m: np.ndarray
    edge_right_m: np.ndarray         # (n,) surface z - ring z, NaN if invalid
    fit: Fit
    coverage: float
    road_coverage: float           # stations on a NAME-ACCEPTED surface, a stricter read
    residual_std_m: float
    residual_max_m: float
    largest_gap_stations: int
    ds_m: float
    source: str
    sha256: str
    primitives: dict[str, int]     # primitive label -> stations landed on it

    def gate(self) -> tuple[bool, list[str]]:
        """coverage >= 99 % and residual std <= 0.15 m. Never loosen this to pass."""
        fails = []
        if self.coverage < GATE_MIN_COVERAGE:
            fails.append(f"coverage {self.coverage:.4%} < {GATE_MIN_COVERAGE:.0%}")
        if not (self.residual_std_m <= GATE_MAX_RESIDUAL_STD_M):
            fails.append(f"residual std {self.residual_std_m:.3f} m > "
                         f"{GATE_MAX_RESIDUAL_STD_M:.2f} m")
        return (not fails), fails

    def summary(self) -> dict:
        ok, fails = self.gate()
        return {"stations": int(len(self.z_m)),
                "stationsValid": int(self.valid.sum()),
                "coverage": self.coverage,
                "roadCoverage": self.road_coverage,
                "residualStdM": self.residual_std_m,
                "residualMaxM": self.residual_max_m,
                "largestGapStations": self.largest_gap_stations,
                "camberCoverage": float(self.camber_valid.mean()),
                "camberBaseMedianM": (float(np.nanmedian(self.camber_base_m))
                                      if self.camber_valid.any() else None),
                "gate": "pass" if ok else "FAIL",
                "gateFailures": fails}


def _fill_circular(v: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Linear interpolation across invalid stations, wrapping around the ring.

    Used ONLY so a short gap does not poison the derivative of its neighbours. The
    filled stations stay invalid and are emitted as NaN: this interpolates for a
    gradient, it does not invent a surface height.
    """
    n = len(v)
    if valid.all():
        return v.copy()
    if not valid.any():
        return np.full(n, np.nan)
    idx = np.where(valid)[0]
    ext_idx = np.concatenate((idx - n, idx, idx + n))
    ext_val = np.concatenate((v[idx], v[idx], v[idx]))
    return np.interp(np.arange(n), ext_idx, ext_val)


def _largest_gap(valid: np.ndarray) -> int:
    """Longest circular run of invalid stations."""
    if valid.all():
        return 0
    if not valid.any():
        return int(len(valid))
    doubled = np.concatenate((~valid, ~valid))
    best = run = 0
    for flag in doubled:
        run = run + 1 if flag else 0
        best = max(best, run)
    return int(min(best, len(valid)))


def bake_surface(index: TriangleIndex, ring: Ring, fit: Fit, *,
                 smooth_window_m: float = SMOOTH_WINDOW_M,
                 camber_half_widths: tuple[float, ...] = CAMBER_HALF_WIDTHS_M
                 ) -> SurfaceBake:
    """Sample the model's road under every ring station and bake z, slope and camber.

    Orientation is derived from the SAMPLED height field, not from face normals: the
    triangles are metres across, so a face normal is piecewise constant and roll would
    step at every edge. Pitch taken from the same z array the car is placed on is
    self-consistent; a triangle normal is not.

    `ring` must already be in its FINAL frame -- the one `prepare_ring` returns, rotated
    so start/finish is station 0. Baking earlier and rotating afterwards would silently
    desynchronise: `Ring.rotated` rolls x, y and z, and knows nothing about the arrays
    this function adds.
    """
    n = ring.n
    wx, wy_expected, wz = fit.to_world(ring.x, ring.y, ring.z)
    centre = sample_with_hysteresis(index, wx, wz, wy_expected)

    ty = _fit_ty(fit, ring.z, centre.world_y, centre.valid)
    fit = fit.replace(ty=ty)
    wy_expected = fit.scale * ring.z + ty
    centre = sample_with_hysteresis(index, wx, wz, wy_expected)

    z_m = np.full(n, np.nan)
    z_m[centre.valid] = fit.telemetry_z(centre.world_y[centre.valid])
    residual = np.full(n, np.nan)
    residual[centre.valid] = z_m[centre.valid] - ring.z[centre.valid]
    coverage = float(centre.valid.mean())
    resid_ok = residual[centre.valid]
    resid_std = float(np.std(resid_ok)) if len(resid_ok) else math.inf
    resid_max = float(np.max(np.abs(resid_ok))) if len(resid_ok) else math.inf

    # Camber: probe the same surface either side of the racing line. The widest
    # baseline both sides support is used, because the telemetry line is the RACING
    # line, not the road centre -- measured at Silverstone it runs within 2 m of the
    # asphalt edge at 7.3 % of stations, and a fixed 2 m probe simply falls off the road
    # there. A narrower baseline is still a measurement; inventing the missing side
    # would not be. `camber_base_m` records which baseline each station actually used.
    camber = np.full(n, np.nan)
    camber_base = np.full(n, np.nan)
    camber_valid = np.zeros(n, dtype=bool)
    for h in sorted(camber_half_widths, reverse=True):
        todo = centre.valid & ~camber_valid
        if not todo.any():
            break
        lx, ly = ring.x + h * ring.nx, ring.y + h * ring.ny     # ring.n* is the LEFT normal
        rx, ry = ring.x - h * ring.nx, ring.y - h * ring.ny
        lwx, _, lwz = fit.to_world(lx, ly, ring.z)
        rwx, _, rwz = fit.to_world(rx, ry, ring.z)
        left = sample_with_hysteresis(index, lwx, lwz, wy_expected)
        right = sample_with_hysteresis(index, rwx, rwz, wy_expected)
        got = todo & left.valid & right.valid
        if not got.any():
            continue
        dz = fit.telemetry_z(left.world_y[got]) - fit.telemetry_z(right.world_y[got])
        camber[got] = np.arctan2(dz, 2.0 * h)
        camber_base[got] = h
        camber_valid |= got
    if camber_valid.any():
        camber = smooth_circular(_fill_circular(camber, camber_valid),
                                 smooth_window_m, ring.ds)
    camber[~camber_valid] = np.nan

    filled = _fill_circular(z_m, centre.valid)
    dz_ds = np.gradient(filled) / ring.ds
    dz_ds[0] = (filled[1] - filled[-1]) / (2.0 * ring.ds)
    dz_ds[-1] = (filled[0] - filled[-2]) / (2.0 * ring.ds)
    slope = np.arctan(smooth_circular(dz_ds, smooth_window_m, ring.ds))
    slope[~centre.valid] = np.nan

    # SIGNED road extent: how far the drivable surface reaches either side of the ring.
    # Walked outward in EDGE_STEP_M steps and stopped at the first step that is not
    # drivable, so the result is the CONTIGUOUS road under the car -- not the nearest
    # patch of asphalt, which at a circuit with asphalt run-off would jump the kerb and
    # report the run-off as road. Both sides are reported separately and either may be
    # null: a station where the walk cannot even take its first step has no measured
    # edge on that side, which is a fact worth keeping rather than a zero.
    edge_left = np.full(n, np.nan)
    edge_right = np.full(n, np.nan)
    live_l = centre.valid.copy()
    live_r = centre.valid.copy()
    d = EDGE_STEP_M
    while d <= EDGE_MAX_M and (live_l.any() or live_r.any()):
        if live_l.any():
            lx, ly = ring.x + d * ring.nx, ring.y + d * ring.ny
            lwx, _, lwz = fit.to_world(lx, ly, ring.z)
            hit = sample_with_hysteresis(index, lwx, lwz, wy_expected)
            adv = live_l & hit.valid
            edge_left[adv] = d
            live_l &= adv
        if live_r.any():
            rx, ry = ring.x - d * ring.nx, ring.y - d * ring.ny
            rwx, _, rwz = fit.to_world(rx, ry, ring.z)
            hit = sample_with_hysteresis(index, rwx, rwz, wy_expected)
            adv = live_r & hit.valid
            edge_right[adv] = d
            live_r &= adv
        d += EDGE_STEP_M

    labels: dict[str, int] = {}
    for p in np.unique(centre.prim[centre.valid]):
        labels[index.surface.primitives[int(p)].label] = int((centre.prim == p).sum())

    # A stricter reading of the same bake: how many stations landed on a surface the
    # model itself NAMES as road. It gates nothing -- a model with no usable names
    # scores zero here and can still be perfectly aligned -- but where a model does have
    # names it is the number that is directly comparable across profiles.
    named = index.surface.static_score >= SCORE_ACCEPT_NAME
    on_named = centre.valid & (centre.prim >= 0) & named[np.maximum(centre.prim, 0)]
    road_coverage = float(on_named.mean())

    return SurfaceBake(z_m=z_m, slope_rad=slope, camber_rad=camber,
                       camber_base_m=camber_base, valid=centre.valid,
                       camber_valid=camber_valid, residual_m=residual,
                       edge_left_m=edge_left, edge_right_m=edge_right, fit=fit,
                       coverage=coverage, road_coverage=road_coverage,
                       residual_std_m=resid_std,
                       residual_max_m=resid_max,
                       largest_gap_stations=_largest_gap(centre.valid), ds_m=ring.ds,
                       source=index.surface.source, sha256=index.surface.sha256,
                       primitives=dict(sorted(labels.items(), key=lambda kv: -kv[1])))


def surface_block(bake: SurfaceBake, profile: str) -> dict:
    """The cm/permille JSON block a track artifact would carry.

    Provided so the contract is written down once; nothing here writes it into a shipped
    artifact. `null` -- not a default height -- is what an invalid station emits.
    """
    def q(v, k):
        return [None if not math.isfinite(x) else int(round(x * k)) for x in v]

    return {
        "dsMetres": bake.ds_m,
        "source": bake.source,
        "sourceSha256": bake.sha256,
        "profile": profile,
        "transform": bake.fit.as_dict() | bake.fit.raw_glb(),
        "zCm": q(bake.z_m, 100),
        "slopePermille": q(np.tan(bake.slope_rad), 1000),
        "camberPermille": q(np.tan(bake.camber_rad), 1000),
        "validMask": [int(v) for v in bake.valid],
        # SIGNED road extent, centimetres, measured outward from the ring along its own
        # left normal. null on a side the walk could not take a single step on. These
        # are NOT a half-width: left and right differ by up to 20 m at Silverstone,
        # because the ring is the racing line, so a consumer placing anything by width
        # must use both and must not average them.
        "edgeLeftCm": q(bake.edge_left_m, 100),
        "edgeRightCm": q(bake.edge_right_m, 100),
        "residual": {"stdM": bake.residual_std_m, "maxM": bake.residual_max_m},
        "coverage": bake.coverage,
        "roadCoverage": bake.road_coverage,
        # DERIVED, not OBSERVED. This height was never measured from a car: it is a
        # third-party circuit model's geometry, read by an exact raycast under a
        # transform fitted to the OBSERVED telemetry ring. AGENTS.md 13.6 is explicit
        # that producing a value FROM observed inputs does not make it OBSERVED, and a
        # station the raycast missed is `null` here rather than DEFAULT -- there is no
        # value, so there is nothing for a provenance to describe.
        "provenance": ("DERIVED (exact vertical raycast onto the GLB's scored drive "
                       "surface, under a transform fitted to the telemetry ring, "
                       f"orientation smoothed {SMOOTH_WINDOW_M:.0f} m)"),
    }


# ===========================================================================
# 10. Registry + CLI
# ===========================================================================

def load_registry(path: Path = REGISTRY_PATH) -> dict:
    import yaml
    if not path.exists():
        return {"version": 1, "gate": {}, "circuits": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def registry_entry(slug: str, path: Path = REGISTRY_PATH) -> dict | None:
    return (load_registry(path).get("circuits") or {}).get(slug)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def load_surface(glb_path: Path) -> DriveSurface:
    """Thin IO loader: read the GLB, hash it, hand the pure extractor the bytes."""
    glb = load_glb(glb_path)
    return extract_drive_surface(glb, source=glb_path.name,
                                 sha256=sha256_file(glb_path))


def _slug(event: str) -> str:
    return event.lower().replace(" ", "-")


def _cli_ring(event: str) -> Ring:
    from simdata.build_track import prepare_ring
    return prepare_ring(event)[0]


def _run(event: str, *, glb: Path, warm: bool, verbose: bool,
         profile: str = "edelta-scorer"):
    print(f"== {event}  <-  {glb.name}")
    t0 = time.time()
    ring = _cli_ring(event)
    print(f"   ring: {ring.n} stations, {ring.length:.1f} m, ds {ring.ds:.4f} m "
          f"({time.time() - t0:.1f}s)")

    t0 = time.time()
    surf = load_surface(glb)
    print(f"   surface: {surf.tri_count} candidate tris from "
          f"{len(surf.primitives)} primitives, {len(surf.rejected)} primitives rejected "
          f"by name ({time.time() - t0:.1f}s)")

    index = TriangleIndex(surf)
    print(f"   index: cell {index.cell:.1f} m, {len(index.keys)} buckets, "
          f"{len(index.oversized)} oversized tris")

    entry = registry_entry(_slug(event)) if warm else None
    seed = Fit.from_dict(entry["fit"]) if entry and entry.get("fit") else None
    fit, diag = fit_ring_to_surface(index, ring, warm_start=seed, verbose=verbose)
    print(f"   fit: scale {fit.scale:.6f}  yaw {math.degrees(fit.yaw_rad):+.4f} deg  "
          f"mirror {fit.mirror:+d}  tx {fit.tx:+.3f}  tz {fit.tz:+.3f}  "
          f"({diag['totalMs'] / 1000:.1f}s, {diag['seeds']} seeds)")

    bake = bake_surface(index, ring, fit)
    print(f"   raw-GLB offsets: {json.dumps(bake.fit.raw_glb())}")
    print(f"   bake: {json.dumps(bake.summary(), indent=None)}")
    for label, count in list(bake.primitives.items())[:6]:
        print(f"      {count:6d} stations on  {label}")
    print(registry_block(event, glb, bake, profile))
    return ring, surf, index, fit, bake


def registry_block(event: str, glb: Path, bake: SurfaceBake, profile: str) -> str:
    """The config/circuits.yaml entry this run measured, ready to paste.

    Printed rather than written, so a re-fit can never silently overwrite the recorded
    numbers -- or the comments that say what the gate means -- without a human looking
    at the diff. `measured.gate` records what the bake ACTUALLY did, pass or fail.
    """
    s = bake.summary()
    f = bake.fit
    return "\n".join([
        f"  {_slug(event)}:",
        f"    event: {event}",
        f"    glb: {glb.relative_to(REPO_ROOT).as_posix()}",
        f"    sha256: {bake.sha256}",
        f"    profile: {profile}",
        "    fit:",
        f"      scale: {f.scale:.6f}",
        f"      yawDeg: {math.degrees(f.yaw_rad):.6f}",
        f"      mirror: {f.mirror}",
        f"      txM: {f.tx:.4f}",
        f"      tzM: {f.tz:.4f}",
        f"      tyM: {f.ty:.4f}",
        "    measured:",
        f"      stations: {s['stations']}",
        f"      stationsValid: {s['stationsValid']}",
        f"      coverage: {s['coverage']:.6f}",
        f"      roadCoverage: {s['roadCoverage']:.6f}",
        f"      residualStdM: {s['residualStdM']:.6f}",
        f"      residualMaxM: {s['residualMaxM']:.6f}",
        f"      largestGapStations: {s['largestGapStations']}",
        f"      camberCoverage: {s['camberCoverage']:.6f}",
        f"      gate: {s['gate']}",
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("fit", "bake"),
                    help="fit: search from cold. bake: start from the recorded transform")
    ap.add_argument("event")
    ap.add_argument("--glb", default=None, help="path to the circuit GLB")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    entry = registry_entry(_slug(args.event)) or {}
    glb = Path(args.glb) if args.glb else REPO_ROOT / entry.get("glb", "")
    if not glb.is_file():
        raise SystemExit(f"no GLB for {args.event}: {glb}")
    _run(args.event, glb=glb, warm=args.action == "bake", verbose=args.verbose,
         profile=entry.get("profile", "edelta-scorer"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
