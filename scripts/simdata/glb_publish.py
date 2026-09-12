"""Publish the circuit GLBs to frontend/public/sim/glb/, reduced enough to be servable.

    python -m simdata.glb_publish                 # publish every registered circuit
    python -m simdata.glb_publish --inventory     # measure textures, write nothing
    python -m simdata.glb_publish british-grand-prix --dry-run

WHAT THIS DOES, AND THE ONE THING IT MUST NOT DO
------------------------------------------------
The published asset is the *same model* as the source, with large textures downscaled
and nothing else touched. Vertices, indices, node matrices and materials come through
byte for byte, because `config/circuits.yaml` holds a transform that was FITTED to the
source geometry. Move a vertex and that transform no longer describes the model: the
ring no longer lands on the road, the baked surface heights no longer match what is
drawn, and the cars float. So the safety argument here is not "the model still looks
right" -- it is a byte comparison of every non-image bufferView, a re-extraction of the
drive surface, and a re-run of the bake against the rewritten asset, which must
reproduce coverage and residual to 1e-6.

If any of that fails, the RAW asset is published unchanged and the VRAM cost is
reported honestly. A corrupted model is worse than a heavy one.

WHY REDUCTION AT ALL -- measured, not assumed (`--inventory` reprints this)
--------------------------------------------------------------------------
silverstone.glb, 165.8 MB on disk:

    69 images, 61.9 MB encoded, 196.4 MB of texture VRAM (261.9 MB with mips)
    img 59  4096x4096 PNG/RGB  28.67 MB encoded  67.11 MB VRAM  <- top2.001 baseColor
    img 60  4096x4096 PNG/RGB  14.12 MB encoded  67.11 MB VRAM  <- asphalt.001 baseColor
    img 61  2048x2048 PNG/RGB   8.06 MB encoded  16.78 MB VRAM  <- grass.001 baseColor
    ... the other 66 images are 512x512 or smaller and total 45.4 MB of VRAM

Three images are 151.0 MB of the 196.4 MB (76.9 %) and 50.8 MB of the file. One octave
down takes them to 2048/2048/1024 and removes 113.2 MB of VRAM (151.0 MB counting mips)
for ~34 MB of file, touching nothing else. That is the whole of the reduction, and it is
why nothing cleverer is attempted first.

shanghai.glb, 102.1 MB: 93 images, 12.2 MB encoded, 35.2 MB VRAM, and its maps already
cap at 256x256 apart from one 2048 and one 1024. Only the 2048 is above the threshold,
so this asset barely moves -- its weight is 89.8 MB of uint32-indexed geometry, which
this script is not allowed to touch. Published anyway: it costs nothing and keeps the
two paths symmetrical. The registry, not this script, decides which circuit is DRAWN --
chinese-grand-prix FAILS the surface gate and keeps the procedural ribbon.

THE PUBLISHED-ASSET CONTRACT (frozen; shared with the artifact builder and the renderer)
---------------------------------------------------------------------------------------
    path:  frontend/public/sim/glb/<slug>.<sha10>.glb
    url:   /sim/glb/<slug>.<sha10>.glb
    <sha10> is the first 10 hex characters of the sha256 of the PUBLISHED bytes.

The track artifact's `surface` block gains `assetUrl` and `assetSha256` naming that file;
`asset_fields()` below is the single producer of those two, so the naming rule is written
down once. `surface.sourceSha256` stays the hash of the SOURCE GLB in data/tracks -- the
bytes the transform was fitted against -- and is a different number from `assetSha256`.

frontend/public/sim/ is gitignored, so publishing touches no tracked file, and
build_sim_data.prune() walks only the top level of that directory (`path.is_file()`
skips directories), so it cannot delete these.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simdata.glb_surface import (CHUNK_BIN, CHUNK_JSON, GLB_MAGIC, DriveSurface, Fit,
                                 Glb, SurfaceBake, TriangleIndex, bake_surface,
                                 extract_drive_surface, load_registry, parse_glb)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PUBLISH_DIR = REPO_ROOT / "frontend" / "public" / "sim" / "glb"

#: Served URL prefix. `/sim` is frontend/public/sim, so this mirrors PUBLISH_DIR exactly.
URL_PREFIX = "/sim/glb"
#: How much of the sha256 goes in the filename. Matches build_sim_data.write_json.
SHA_PREFIX = 10
#: `<slug>.<10 hex>.glb` -- what this script writes, and the only thing it will delete.
PUBLISHED_RE = re.compile(r"^(?P<slug>.+)\.(?P<hash>[0-9a-f]{10})\.glb$")

# --- the reduction policy, stated rather than guessed -----------------------------
#: An image is reduced when max(width, height) >= this. 2048 is the smallest size that
#: is already a VRAM problem on an integrated GPU (16.8 MB base, 22.4 MB with mips) and
#: it is the threshold that selects exactly the three offenders measured above.
REDUCE_AT_PX = 2048
#: How many times to halve. ONE octave: 4096 -> 2048, 2048 -> 1024. Halving preserves
#: power-of-two dimensions, which both assets' samplers want (wrapS/wrapT = REPEAT).
REDUCE_OCTAVES = 1
#: Never produce a degenerate image.
MIN_SIDE_PX = 4

#: An RGBA8 texel. The GPU expands PNG/JPEG to this regardless of the encoded size,
#: which is why encoded bytes are a bad proxy for the cost actually being paid.
BYTES_PER_TEXEL = 4
#: A full mip chain adds 1/4 + 1/16 + ... = 1/3 again. Both assets' samplers ask for
#: mipmapped minification (minFilter 9987 = LINEAR_MIPMAP_LINEAR), so this is paid.
MIP_RATIO = 4.0 / 3.0
#: MB here is 1e6 bytes, the same convention env_sim.md's measurements use.
MB = 1e6

PNG_COMPRESS_LEVEL = 9
JPEG_QUALITY = 90

#: Verification tolerance. Geometry is compared byte for byte -- there is no tolerance
#: on it at all. This bounds the re-run of the bake, which reads the same triangles
#: through the same transform and must therefore reproduce exactly; 1e-6 is float noise.
BAKE_TOL = 1e-6

_FORMAT_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


# ===========================================================================
# 1. Texture inventory -- measure, and name the offenders
# ===========================================================================

@dataclass(frozen=True)
class ImageEntry:
    """One glTF image, measured. Nothing here is estimated except the mip tail."""
    index: int
    buffer_view: int | None
    mime: str                     # as DECLARED by the glTF, "" when absent
    fmt: str                      # as DECODED by Pillow: PNG / JPEG / ...
    mode: str                     # Pillow mode: RGB, RGBA, LA, P, ...
    width: int
    height: int
    encoded_bytes: int
    users: tuple[str, ...]        # "<material>.<channel>" for every reference
    note: str = ""                # why it could not be measured, if it could not

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def base_vram_bytes(self) -> int:
        """w * h * 4. The uncompressed upload, before mips."""
        return self.pixels * BYTES_PER_TEXEL

    @property
    def vram_bytes(self) -> float:
        """Base plus the mip tail."""
        return self.base_vram_bytes * MIP_RATIO

    @property
    def label(self) -> str:
        """What to call this image in a report. glTF images here carry no `name`, so
        the material that samples it is the only human-readable handle it has."""
        if len(self.users) == 1:
            return self.users[0]
        if self.users:
            return f"{self.users[0]} (+{len(self.users) - 1} more)"
        return f"image {self.index}"


def texture_users(gltf: dict) -> dict[int, tuple[str, ...]]:
    """image index -> ("<material>.<channel>", ...), so a report can NAME an offender.

    Walks the core material channels and any extension object carrying a textureInfo
    (`KHR_materials_specular` on both assets), because an image reachable only through
    an extension still costs full VRAM.
    """
    textures = gltf.get("textures") or []
    out: dict[int, list[str]] = {}

    def note(tex_index, who: str) -> None:
        if not isinstance(tex_index, int) or not 0 <= tex_index < len(textures):
            return
        src = textures[tex_index].get("source")
        if isinstance(src, int):
            out.setdefault(src, []).append(who)

    for mat in gltf.get("materials") or []:
        name = str(mat.get("name") or "material")
        pbr = mat.get("pbrMetallicRoughness") or {}
        for channel, info in (("baseColor", pbr.get("baseColorTexture")),
                              ("metallicRoughness", pbr.get("metallicRoughnessTexture")),
                              ("normal", mat.get("normalTexture")),
                              ("occlusion", mat.get("occlusionTexture")),
                              ("emissive", mat.get("emissiveTexture"))):
            if isinstance(info, dict):
                note(info.get("index"), f"{name}.{channel}")
        for ext_name, ext in (mat.get("extensions") or {}).items():
            if not isinstance(ext, dict):
                continue
            for key, info in ext.items():
                if isinstance(info, dict) and "index" in info:
                    note(info.get("index"), f"{name}.{ext_name}.{key}")
    return {k: tuple(v) for k, v in out.items()}


def image_bytes(glb: Glb, index: int) -> bytes:
    """The encoded bytes of image `index`. Pure: reads the already-parsed container."""
    image = (glb.gltf.get("images") or [])[index]
    view = image.get("bufferView")
    if view is None:
        uri = str(image.get("uri") or "")
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        raise ValueError(f"image {index} is an external URI ({uri!r}); "
                         "this reader only handles self-contained GLBs")
    bv = glb.gltf["bufferViews"][int(view)]
    if int(bv.get("buffer", 0)) != 0:
        raise ValueError(f"image {index} lives in buffer {bv.get('buffer')}, "
                         "not the BIN chunk")
    off = int(bv.get("byteOffset", 0))
    length = int(bv["byteLength"])
    if off + length > len(glb.binary):
        raise ValueError(f"image {index} runs past the end of the BIN chunk")
    return glb.binary[off:off + length]


def image_entries(glb: Glb) -> list[ImageEntry]:
    """Measure every image: pixel dimensions, encoded bytes, and who samples it.

    Pure with respect to the filesystem -- the parsed container in, measurements out.
    Dimensions come from the encoded header via Pillow's lazy open, so this costs a few
    milliseconds for a 62 MB texture set rather than decoding 196 MB of pixels.
    """
    from PIL import Image

    users = texture_users(glb.gltf)
    entries: list[ImageEntry] = []
    for index, image in enumerate(glb.gltf.get("images") or []):
        raw_view = image.get("bufferView")
        view = int(raw_view) if isinstance(raw_view, int) else None
        declared = str(image.get("mimeType") or "")
        who = users.get(index, ())
        try:
            data = image_bytes(glb, index)
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                fmt, mode = str(img.format or ""), str(img.mode)
            entries.append(ImageEntry(index=index, buffer_view=view, mime=declared,
                                      fmt=fmt, mode=mode, width=int(width),
                                      height=int(height), encoded_bytes=len(data),
                                      users=who))
        except Exception as exc:                    # noqa: BLE001 - recorded, not hidden
            # Not a silent skip: the entry is emitted with zero measurements and a note
            # saying why, so `--inventory` shows an unmeasurable image rather than
            # quietly under-reporting the VRAM total. An unmeasured image is also
            # never a reduction candidate.
            entries.append(ImageEntry(index=index, buffer_view=view, mime=declared,
                                      fmt="", mode="", width=0, height=0,
                                      encoded_bytes=0, users=who,
                                      note=f"{type(exc).__name__}: {exc}"))
    return entries


def inventory_totals(entries: Sequence[ImageEntry]) -> dict:
    """Totals a reader needs to judge whether an asset is servable."""
    measured = [e for e in entries if e.pixels]
    base = sum(e.base_vram_bytes for e in measured)
    return {"images": len(entries),
            "measured": len(measured),
            "unmeasured": [e.index for e in entries if not e.pixels],
            "encodedBytes": sum(e.encoded_bytes for e in entries),
            "baseVramBytes": base,
            "vramBytes": base * MIP_RATIO}


def inventory_lines(entries: Sequence[ImageEntry], *, top: int = 8) -> list[str]:
    """A human-readable texture report: the biggest consumers, named, then the totals."""
    totals = inventory_totals(entries)
    ordered = sorted(entries, key=lambda e: (-e.base_vram_bytes, e.index))
    lines = [f"  {totals['images']} images, "
             f"{totals['encodedBytes'] / MB:.1f} MB encoded, "
             f"{totals['baseVramBytes'] / MB:.1f} MB VRAM "
             f"({totals['vramBytes'] / MB:.1f} MB with mips)"]
    for e in ordered[:top]:
        if not e.pixels:
            lines.append(f"    img {e.index:3d}  UNMEASURED  {e.note}")
            continue
        share = (e.base_vram_bytes / totals["baseVramBytes"]
                 if totals["baseVramBytes"] else 0.0)
        lines.append(f"    img {e.index:3d}  {e.width:5d}x{e.height:<5d} "
                     f"{e.fmt or '?'}/{e.mode or '?':<4s} "
                     f"{e.encoded_bytes / MB:7.2f} MB enc  "
                     f"{e.base_vram_bytes / MB:7.2f} MB VRAM ({share:5.1%})  "
                     f"{e.label}")
    rest = [e for e in ordered[top:] if e.pixels]
    if rest:
        lines.append(f"    ... {len(rest)} more images, "
                     f"{sum(e.base_vram_bytes for e in rest) / MB:.1f} MB VRAM")
    return lines


# ===========================================================================
# 2. The reduction plan -- pure, and a stated threshold rather than a guess
# ===========================================================================

@dataclass(frozen=True)
class Reduction:
    """One planned downscale. `image` indexes glTF.images."""
    image: int
    from_px: tuple[int, int]
    to_px: tuple[int, int]
    label: str

    @property
    def base_vram_saved(self) -> int:
        return (self.from_px[0] * self.from_px[1]
                - self.to_px[0] * self.to_px[1]) * BYTES_PER_TEXEL


def _halved(width: int, height: int, octaves: int) -> tuple[int, int]:
    for _ in range(max(0, octaves)):
        width = max(MIN_SIDE_PX, width // 2)
        height = max(MIN_SIDE_PX, height // 2)
    return width, height


def plan_reductions(entries: Sequence[ImageEntry], *,
                    reduce_at_px: int = REDUCE_AT_PX,
                    octaves: int = REDUCE_OCTAVES) -> list[Reduction]:
    """Which images to downscale, and to what. Pure: measurements in, a plan out.

    The rule is one line: an image whose LONGEST side is at or above `reduce_at_px` is
    halved `octaves` times. At the defaults that is 4096 -> 2048 and 2048 -> 1024, and
    an image at 1024 is left alone -- which is exactly the three-image plan measured on
    silverstone.glb, and exactly one image on shanghai.glb.

    An image that could not be measured is NOT reduced: re-encoding something whose
    format is unknown is how a rewrite corrupts an asset.
    """
    plan: list[Reduction] = []
    for e in entries:
        if not e.pixels or e.buffer_view is None:
            continue
        if max(e.width, e.height) < reduce_at_px:
            continue
        target = _halved(e.width, e.height, octaves)
        if target == (e.width, e.height):
            continue
        plan.append(Reduction(image=e.index, from_px=(e.width, e.height),
                              to_px=target, label=e.label))
    return plan


def downscale(data: bytes, size: tuple[int, int], *,
              png_compress_level: int = PNG_COMPRESS_LEVEL,
              jpeg_quality: int = JPEG_QUALITY) -> tuple[bytes, str]:
    """Resample encoded image bytes to `size`, re-encode, return (bytes, mimeType).

    Pure: bytes in, bytes out, no filesystem. Lanczos, because a box/nearest halving of
    a tiled asphalt map aliases visibly at grazing angles -- which is most of a circuit.

    The source format is preserved (PNG stays PNG) so the material pipeline sees the
    same kind of image it was authored against. Palette and bilevel images are promoted
    to true colour first: Pillow silently falls back to NEAREST when asked to Lanczos a
    mode-P image, which would undo the point of resampling at all.
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        fmt = str(img.format or "PNG").upper()
        img.load()
        source_mode = img.mode
        if source_mode == "P":
            img = img.convert("RGBA" if "transparency" in img.info else "RGB")
        elif source_mode == "1":
            img = img.convert("L")
        small = img.resize((max(MIN_SIDE_PX, int(size[0])),
                            max(MIN_SIDE_PX, int(size[1]))), Image.LANCZOS)

    out = io.BytesIO()
    if fmt == "JPEG":
        if small.mode not in ("RGB", "L", "CMYK"):
            small = small.convert("RGB")
        small.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
    elif fmt == "WEBP":
        small.save(out, format="WEBP", quality=jpeg_quality, method=4)
    else:
        small.save(out, format="PNG", optimize=False,
                   compress_level=png_compress_level)
        fmt = "PNG"
    return out.getvalue(), _FORMAT_MIME.get(fmt, "image/png")


# ===========================================================================
# 3. Rewriting the container
# ===========================================================================

def _pad(blob: bytes, fill: bytes) -> bytes:
    return blob + fill * (-len(blob) % 4)


def view_owners(gltf: dict) -> dict[int, list[str]]:
    """bufferView index -> everything that reads it, as "image N" / "accessor N".

    A replaced image's bufferView must be read by that image and nothing else. Two
    images sharing a view, or an accessor aliasing one, would mean writing new bytes
    under something that is not expecting them -- silently, and visible only as
    scrambled geometry at runtime. So it is checked rather than assumed.
    """
    owners: dict[int, list[str]] = {}
    for i, image in enumerate(gltf.get("images") or []):
        if isinstance(image.get("bufferView"), int):
            owners.setdefault(int(image["bufferView"]), []).append(f"image {i}")
    for i, acc in enumerate(gltf.get("accessors") or []):
        if isinstance(acc.get("bufferView"), int):
            owners.setdefault(int(acc["bufferView"]), []).append(f"accessor {i}")
        sparse = acc.get("sparse") or {}
        for part in ("indices", "values"):
            sub = sparse.get(part) or {}
            if isinstance(sub.get("bufferView"), int):
                owners.setdefault(int(sub["bufferView"]), []).append(
                    f"accessor {i} sparse {part}")
    return owners


def rebuild_glb(glb: Glb, payloads: Mapping[int, tuple[bytes, str]]) -> bytes:
    """Return a new, valid GLB with the given images replaced. Pure: no IO.

    `payloads` maps a glTF image index to (encoded bytes, mimeType).

    Every bufferView in buffer 0 is copied into a freshly packed BIN chunk in its
    existing order, 4-byte aligned, with byteOffset/byteLength rewritten. Recompacting
    rather than patching in place is what keeps the container valid when an image
    shrinks: leaving the old offsets would leave a 20 MB hole and, worse, leave every
    later bufferView pointing at bytes that no longer start where it thinks they do.

    4-byte alignment is not cosmetic. glTF requires an accessor's absolute offset to be
    a multiple of its component size, and every component type is 1, 2 or 4 bytes, so a
    4-aligned bufferView keeps every accessor legal without reasoning about them
    individually.
    """
    gltf = copy.deepcopy(glb.gltf)
    views = gltf.get("bufferViews") or []
    images = gltf.get("images") or []
    buffers = gltf.get("buffers") or []

    if payloads:
        if not buffers:
            raise ValueError("GLB has no buffers to rewrite")
        if buffers[0].get("uri") is not None:
            raise ValueError("buffer 0 is not the BIN chunk (it carries a uri); "
                             "this rewriter only repacks self-contained GLBs")

    owners = view_owners(glb.gltf)
    replacement: dict[int, bytes] = {}
    for image_index, (data, mime) in payloads.items():
        image = images[image_index]
        raw_view = image.get("bufferView")
        if not isinstance(raw_view, int):
            raise ValueError(f"image {image_index} has no bufferView to replace")
        view = int(raw_view)
        if int(views[view].get("buffer", 0)) != 0:
            raise ValueError(f"image {image_index} is not in buffer 0")
        shared = [o for o in owners.get(view, ()) if o != f"image {image_index}"]
        if shared:
            raise ValueError(f"bufferView {view} of image {image_index} is also read by "
                             f"{', '.join(shared)}; refusing to overwrite it")
        if view in replacement:
            raise ValueError(f"bufferView {view} would be written twice")
        replacement[view] = bytes(data)
        if mime:
            image["mimeType"] = mime

    out = bytearray()
    for i, bv in enumerate(views):
        if int(bv.get("buffer", 0)) != 0:
            continue                       # another buffer entirely; not ours to move
        if i in replacement:
            data = replacement[i]
        else:
            off = int(bv.get("byteOffset", 0))
            length = int(bv["byteLength"])
            if off + length > len(glb.binary):
                raise ValueError(f"bufferView {i} runs past the end of the BIN chunk")
            data = glb.binary[off:off + length]
        while len(out) % 4:
            out.append(0)
        bv["byteOffset"] = len(out)
        bv["byteLength"] = len(data)
        out += data

    if buffers:
        buffers[0]["byteLength"] = len(out)

    json_chunk = _pad(json.dumps(gltf, separators=(",", ":"), allow_nan=False,
                                 ensure_ascii=False).encode("utf-8"), b" ")
    body = struct.pack("<II", len(json_chunk), CHUNK_JSON) + json_chunk
    if out:
        bin_chunk = _pad(bytes(out), b"\0")
        body += struct.pack("<II", len(bin_chunk), CHUNK_BIN) + bin_chunk
    return struct.pack("<III", GLB_MAGIC, 2, 12 + len(body)) + body


# ===========================================================================
# 4. Verification -- the whole safety argument
# ===========================================================================

def geometry_view_indices(gltf: dict) -> list[int]:
    """Every bufferView an image does NOT own: geometry, and anything else non-image."""
    image_views = {int(im["bufferView"]) for im in (gltf.get("images") or [])
                   if isinstance(im.get("bufferView"), int)}
    return [i for i in range(len(gltf.get("bufferViews") or [])) if i not in image_views]


def _view_bytes(glb: Glb, index: int) -> bytes:
    bv = glb.gltf["bufferViews"][index]
    if int(bv.get("buffer", 0)) != 0:
        return b""
    off = int(bv.get("byteOffset", 0))
    return glb.binary[off:off + int(bv["byteLength"])]


def compare_geometry(before: Glb, after: Glb) -> list[str]:
    """Differences between two parsed GLBs, ignoring image payloads. Empty == identical.

    Compares the raw bytes of every non-image bufferView, not a decoded interpretation
    of them, because "byte-identical" is the claim being made. Accessor descriptors and
    the mesh/node trees are compared too: identical bytes read through a shifted
    accessor byteOffset would still be a changed model.
    """
    diffs: list[str] = []
    a, b = before.gltf, after.gltf
    for key in ("bufferViews", "accessors", "meshes", "nodes", "materials", "textures",
                "images", "samplers", "scenes"):
        na, nb = len(a.get(key) or []), len(b.get(key) or [])
        if na != nb:
            diffs.append(f"{key}: {na} -> {nb}")
    if diffs:
        return diffs

    for i, (aa, bb) in enumerate(zip(a.get("accessors") or [], b.get("accessors") or [])):
        for key in ("bufferView", "byteOffset", "componentType", "normalized", "count",
                    "type", "min", "max", "sparse"):
            if aa.get(key) != bb.get(key):
                diffs.append(f"accessor {i}.{key}: {aa.get(key)!r} -> {bb.get(key)!r}")
    for i, (aa, bb) in enumerate(zip(a.get("bufferViews") or [],
                                     b.get("bufferViews") or [])):
        if aa.get("byteStride") != bb.get("byteStride"):
            diffs.append(f"bufferView {i}.byteStride: "
                         f"{aa.get('byteStride')!r} -> {bb.get('byteStride')!r}")
        if int(bb.get("byteOffset", 0)) % 4:
            diffs.append(f"bufferView {i}.byteOffset {bb.get('byteOffset')} is not "
                         "4-byte aligned")
    if a.get("meshes") != b.get("meshes"):
        diffs.append("meshes differ")
    if a.get("nodes") != b.get("nodes"):
        diffs.append("nodes differ")

    for i in geometry_view_indices(a):
        if _view_bytes(before, i) != _view_bytes(after, i):
            diffs.append(f"bufferView {i} bytes differ")
    return diffs


def compare_drive_surface(before: DriveSurface, after: DriveSurface) -> list[str]:
    """The drivable triangles themselves, compared exactly. Empty == identical.

    This is the array the fitted transform was measured against, so it is the one that
    matters: equality here means the published asset still puts cars where the bake says
    the road is.
    """
    diffs: list[str] = []
    for name in ("verts", "tris", "tri_prim", "tri_ny"):
        x, y = getattr(before, name), getattr(after, name)
        if x.shape != y.shape:
            diffs.append(f"{name}: shape {x.shape} -> {y.shape}")
        elif x.tobytes() != y.tobytes():
            diffs.append(f"{name}: values differ")
    if [p.label for p in before.primitives] != [p.label for p in after.primitives]:
        diffs.append("primitive labels differ")
    return diffs


def compare_bakes(before: SurfaceBake, after: SurfaceBake, *,
                  tol: float = BAKE_TOL) -> list[str]:
    """Coverage, residual and the per-station height field, compared to `tol`."""
    diffs: list[str] = []
    for name in ("coverage", "road_coverage", "residual_std_m", "residual_max_m"):
        x, y = float(getattr(before, name)), float(getattr(after, name))
        if not abs(x - y) <= tol:
            diffs.append(f"{name}: {x:.9f} -> {y:.9f}")
    if int(before.largest_gap_stations) != int(after.largest_gap_stations):
        diffs.append(f"largestGapStations: {before.largest_gap_stations} -> "
                     f"{after.largest_gap_stations}")
    if before.valid.shape != after.valid.shape:
        diffs.append(f"stations: {before.valid.shape} -> {after.valid.shape}")
    elif not np.array_equal(before.valid, after.valid):
        diffs.append(f"validMask differs at "
                     f"{int((before.valid != after.valid).sum())} stations")
    elif not np.allclose(before.z_m, after.z_m, atol=tol, rtol=0.0, equal_nan=True):
        worst = float(np.nanmax(np.abs(before.z_m - after.z_m)))
        diffs.append(f"surface height differs by up to {worst:.9f} m")
    return diffs


# ===========================================================================
# 5. Names, URLs and the two artifact fields
# ===========================================================================

def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def published_name(slug: str, sha256: str) -> str:
    """`<slug>.<sha10>.glb`, where sha10 is of the PUBLISHED bytes."""
    if len(sha256) < SHA_PREFIX:
        raise ValueError(f"sha256 {sha256!r} is too short")
    return f"{slug}.{sha256[:SHA_PREFIX]}.glb"


def published_url(slug: str, sha256: str) -> str:
    return f"{URL_PREFIX}/{published_name(slug, sha256)}"


# ===========================================================================
# 6. Publishing
# ===========================================================================

@dataclass
class PublishedAsset:
    """What one circuit's published GLB is, and what it cost."""
    slug: str
    url: str
    sha256: str                      # of the PUBLISHED bytes -- what <sha10> comes from
    bytes: int
    vram_before_mb: float
    vram_after_mb: float
    # --- diagnostics, deliberately outside the six-field contract -------------
    path: Path | None = None
    source: str = ""
    source_sha256: str = ""
    source_bytes: int = 0
    reduced: list[dict] = field(default_factory=list)
    rewritten: bool = False
    fallback: str = ""               # why the RAW asset was published, if it was
    verified: dict = field(default_factory=dict)
    gate: str | None = None          # what the registry says, restated not recomputed
    written: bool = False
    pruned: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        """The frozen six fields, and only those."""
        return {"slug": self.slug, "url": self.url, "sha256": self.sha256,
                "bytes": self.bytes,
                "vramBeforeMb": round(self.vram_before_mb, 2),
                "vramAfterMb": round(self.vram_after_mb, 2)}

    def diagnostics(self) -> dict:
        return {"source": self.source, "sourceSha256": self.source_sha256,
                "sourceBytes": self.source_bytes, "rewritten": self.rewritten,
                "fallback": self.fallback or None, "reduced": self.reduced,
                "verified": self.verified, "gate": self.gate,
                "written": self.written, "pruned": self.pruned,
                "elapsedS": round(self.elapsed_s, 1)}


def asset_fields(asset: PublishedAsset) -> dict:
    """The two fields the track artifact's `surface` block gains. One producer, here.

    `assetSha256` is the hash of the PUBLISHED bytes, which is what `assetUrl` is named
    after. It is NOT `surface.sourceSha256`, which stays the hash of the source GLB the
    transform was fitted against -- once textures are downscaled the two differ, and
    conflating them would make the artifact claim it was fitted to a file that never
    existed.
    """
    return {"assetUrl": asset.url, "assetSha256": asset.sha256}


def _prune_stale(out_dir: Path, slug: str, keep: str) -> list[str]:
    """Delete other `<slug>.<sha10>.glb` files. Each is 100 MB+, and nothing reads them.

    Only files matching the published pattern for THIS slug are touched -- never another
    slug's asset, never anything that is not a content-hashed GLB.
    """
    removed = []
    for path in sorted(out_dir.glob(f"{slug}.*.glb")):
        match = PUBLISHED_RE.match(path.name)
        if not match or match.group("slug") != slug or path.name == keep:
            continue
        path.unlink()
        removed.append(path.name)
    return removed


def _verify_rewrite(glb: Glb, candidate: bytes, source_name: str, source_sha: str, *,
                    event: str | None, fit: Fit | None, verify: bool, verify_bake: bool,
                    report: dict, log: Callable[[str], None]) -> list[str]:
    """Re-parse, compare geometry, re-bake. Returns the reasons to reject, if any.

    `null`, never a default, is what an unrunnable check reports: if the telemetry ring
    is not on this machine the bake comparison is recorded as not-run WITH the reason,
    and the geometry comparison -- which needs no telemetry and is the stronger check --
    still decides.
    """
    if not verify:
        report["skipped"] = "verification disabled by the caller"
        return []

    try:
        after = parse_glb(candidate)
    except Exception as exc:                        # noqa: BLE001 - reported, not hidden
        report["reparse"] = f"{type(exc).__name__}: {exc}"
        return [f"the rewritten asset does not re-parse: {exc}"]
    report["reparse"] = "ok"

    diffs = compare_geometry(glb, after)
    report["geometryIdentical"] = not diffs
    if diffs:
        report["geometryDiffs"] = diffs[:8]
        return [f"geometry changed ({len(diffs)} difference(s); first: {diffs[0]})"]
    log("    verified: every non-image bufferView is byte-identical")

    before_surface = extract_drive_surface(glb, source=source_name, sha256=source_sha)
    after_surface = extract_drive_surface(after, source=source_name, sha256=source_sha)
    surf_diffs = compare_drive_surface(before_surface, after_surface)
    report["driveSurfaceIdentical"] = not surf_diffs
    report["driveSurfaceTris"] = int(before_surface.tri_count)
    if surf_diffs:
        report["driveSurfaceDiffs"] = surf_diffs
        return [f"the drivable geometry changed: {surf_diffs[0]}"]
    log(f"    verified: the drive surface re-extracts identically "
        f"({before_surface.tri_count} triangles)")

    if not verify_bake:
        report["bake"] = None
        report["bakeNote"] = "bake comparison disabled by the caller"
        return []
    if fit is None or not event:
        report["bake"] = None
        report["bakeNote"] = ("no fitted transform in config/circuits.yaml, so there is "
                              "nothing to re-bake against")
        log("    bake comparison: NOT RUN (no fitted transform recorded)")
        return []
    try:
        from simdata.build_track import prepare_ring
        ring = prepare_ring(event)[0]
    except Exception as exc:                        # noqa: BLE001 - reported, not hidden
        report["bake"] = None
        report["bakeNote"] = (f"the telemetry ring for {event!r} could not be built "
                              f"({type(exc).__name__}: {exc}), so the bake comparison "
                              "did not run")
        log(f"    bake comparison: NOT RUN -- {type(exc).__name__}: {exc}")
        return []

    before_bake = bake_surface(TriangleIndex(before_surface), ring, fit)
    after_bake = bake_surface(TriangleIndex(after_surface), ring, fit)
    bake_diffs = compare_bakes(before_bake, after_bake)
    report["bake"] = {"identical": not bake_diffs,
                      "tolerance": BAKE_TOL,
                      "coverage": before_bake.coverage,
                      "coverageAfter": after_bake.coverage,
                      "residualStdM": before_bake.residual_std_m,
                      "residualStdMAfter": after_bake.residual_std_m,
                      "residualMaxM": before_bake.residual_max_m,
                      "residualMaxMAfter": after_bake.residual_max_m}
    if bake_diffs:
        report["bakeDiffs"] = bake_diffs
        return [f"the bake changed: {bake_diffs[0]}"]
    log(f"    verified: the bake reproduces -- coverage {after_bake.coverage:.6f}, "
        f"residual std {after_bake.residual_std_m:.6f} m, "
        f"max {after_bake.residual_max_m:.6f} m (unchanged to {BAKE_TOL:g})")
    return []


def publish_asset(slug: str, glb_path: Path, *,
                  out_dir: Path = PUBLISH_DIR,
                  event: str | None = None,
                  fit: Fit | None = None,
                  gate: str | None = None,
                  reduce_at_px: int = REDUCE_AT_PX,
                  octaves: int = REDUCE_OCTAVES,
                  verify: bool = True,
                  verify_bake: bool = True,
                  write: bool = True,
                  prune: bool = True,
                  log: Callable[[str], None] = print) -> PublishedAsset:
    """Reduce one circuit GLB and publish it. The only function here that does IO.

    On ANY verification failure the source bytes are published unchanged and `fallback`
    records why: a heavy model that renders correctly beats a light one that does not.
    The source file in data/tracks is opened read-only and never rewritten.
    """
    started = time.time()
    source_blob = glb_path.read_bytes()
    source_sha = sha256_bytes(source_blob)
    glb = parse_glb(source_blob)

    entries = image_entries(glb)
    totals_before = inventory_totals(entries)
    for line in inventory_lines(entries):
        log(line)

    plan = plan_reductions(entries, reduce_at_px=reduce_at_px, octaves=octaves)
    asset = PublishedAsset(
        slug=slug, url="", sha256=source_sha, bytes=len(source_blob),
        vram_before_mb=totals_before["vramBytes"] / MB,
        vram_after_mb=totals_before["vramBytes"] / MB,
        source=glb_path.name, source_sha256=source_sha,
        source_bytes=len(source_blob), gate=gate)

    blob = source_blob
    if not plan:
        log(f"  no image at or above {reduce_at_px} px: publishing the asset unchanged")
    else:
        payloads: dict[int, tuple[bytes, str]] = {}
        for red in plan:
            data, mime = downscale(image_bytes(glb, red.image), red.to_px)
            payloads[red.image] = (data, mime)
            was = next(e for e in entries if e.index == red.image)
            asset.reduced.append({
                "image": red.image, "label": red.label,
                "from": list(red.from_px), "to": list(red.to_px),
                "encodedBefore": was.encoded_bytes, "encodedAfter": len(data),
                "vramSavedMb": round(red.base_vram_saved * MIP_RATIO / MB, 2),
                "mimeType": mime})
            log(f"    {red.from_px[0]}x{red.from_px[1]} -> "
                f"{red.to_px[0]}x{red.to_px[1]}"
                f"  {was.encoded_bytes / MB:6.2f} -> {len(data) / MB:6.2f} MB enc"
                f"  -{red.base_vram_saved * MIP_RATIO / MB:6.2f} MB VRAM   {red.label}")

        candidate = rebuild_glb(glb, payloads)
        problems = _verify_rewrite(glb, candidate, glb_path.name, source_sha,
                                   event=event, fit=fit, verify=verify,
                                   verify_bake=verify_bake, report=asset.verified,
                                   log=log)
        if problems:
            asset.fallback = "; ".join(problems)
            asset.reduced = []
            log(f"  !! rewrite REJECTED: {asset.fallback}")
            log("     publishing the RAW asset unchanged -- the VRAM figure below is "
                "the UNREDUCED cost")
        else:
            blob = candidate
            asset.rewritten = True
            asset.vram_after_mb = (inventory_totals(image_entries(parse_glb(blob)))
                                   ["vramBytes"] / MB)

    asset.sha256 = sha256_bytes(blob)
    asset.bytes = len(blob)
    name = published_name(slug, asset.sha256)
    asset.url = published_url(slug, asset.sha256)
    asset.path = out_dir / name

    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        asset.path.write_bytes(blob)
        asset.written = True
        if prune:
            asset.pruned = _prune_stale(out_dir, slug, name)
    asset.elapsed_s = time.time() - started
    return asset


def publish_all(slugs: Iterable[str] | None = None, *,
                out_dir: Path = PUBLISH_DIR,
                registry: dict | None = None,
                log: Callable[[str], None] = print,
                **kw) -> list[PublishedAsset]:
    """Publish every registered circuit whose GLB is on disk.

    A slug with no registry entry, or whose asset is missing, is SKIPPED with a message
    -- the same degradation the renderer already makes when there is no environment to
    draw. It is never an error, because eleven of the thirteen circuits are in exactly
    that state by design.

    Both registered circuits are published, including the one that FAILS the surface
    gate: publishing costs nothing and keeps the two paths symmetrical. The registry
    decides what is drawn.
    """
    reg = registry if registry is not None else load_registry()
    circuits = reg.get("circuits") or {}
    wanted = list(slugs) if slugs else list(circuits)
    out: list[PublishedAsset] = []
    for slug in wanted:
        entry = circuits.get(slug)
        if not entry:
            log(f"== {slug}: no entry in config/circuits.yaml -- skipped "
                "(it keeps the procedural ribbon)")
            continue
        path = REPO_ROOT / str(entry.get("glb") or "")
        if not path.is_file():
            log(f"== {slug}: {path} is absent -- skipped "
                "(it keeps the procedural ribbon)")
            continue
        log(f"== {slug}  <-  {path.name}  ({path.stat().st_size / MB:.1f} MB), "
            f"registry gate {(entry.get('measured') or {}).get('gate')}")
        fit = Fit.from_dict(entry["fit"]) if entry.get("fit") else None
        asset = publish_asset(slug, path, out_dir=out_dir, event=entry.get("event"),
                              fit=fit, gate=(entry.get("measured") or {}).get("gate"),
                              log=log, **kw)
        recorded = str(entry.get("sha256") or "")
        asset.verified["sourceHashMatchesRegistry"] = (
            None if not recorded else recorded == asset.source_sha256)
        if recorded and recorded != asset.source_sha256:
            # Not fatal to publishing, but it means the registry's fit was measured
            # against different bytes than the ones just published.
            log(f"   !! the GLB on disk hashes {asset.source_sha256[:10]}, but the "
                f"registry recorded {recorded[:10]} -- the recorded transform was "
                "fitted to different bytes")
        out.append(asset)
        log(f"   -> {json.dumps(asset.as_dict())}")
    return out


# ===========================================================================
# 7. CLI
# ===========================================================================

def _inventory_cli(slugs: Sequence[str], reduce_at: int, octaves: int) -> int:
    reg = load_registry()
    for slug, entry in (reg.get("circuits") or {}).items():
        if slugs and slug not in slugs:
            continue
        path = REPO_ROOT / str(entry.get("glb") or "")
        if not path.is_file():
            print(f"== {slug}: {path} is absent")
            continue
        print(f"== {slug}  <-  {path.name}  ({path.stat().st_size / MB:.1f} MB)")
        entries = image_entries(parse_glb(path.read_bytes()))
        for line in inventory_lines(entries, top=10):
            print(line)
        plan = plan_reductions(entries, reduce_at_px=reduce_at, octaves=octaves)
        saved = sum(r.base_vram_saved for r in plan) * MIP_RATIO
        print(f"  plan: {len(plan)} image(s) at or above {reduce_at} px, "
              f"-{saved / MB:.1f} MB VRAM")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug", nargs="*",
                    help="circuit slugs (default: every registered one)")
    ap.add_argument("--out", default=str(PUBLISH_DIR), help="output directory")
    ap.add_argument("--inventory", action="store_true",
                    help="measure and report textures only; write nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="do the whole reduction and verification, write nothing")
    ap.add_argument("--reduce-at", type=int, default=REDUCE_AT_PX,
                    help=f"downscale images at or above this many pixels "
                         f"(default {REDUCE_AT_PX})")
    ap.add_argument("--octaves", type=int, default=REDUCE_OCTAVES,
                    help=f"how many times to halve (default {REDUCE_OCTAVES})")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the geometry and bake comparison (NOT recommended)")
    ap.add_argument("--no-bake", action="store_true",
                    help="skip only the bake comparison; still compare geometry")
    ap.add_argument("--keep-stale", action="store_true",
                    help="keep superseded <slug>.<hash>.glb files")
    args = ap.parse_args(argv)

    if args.inventory:
        return _inventory_cli(args.slug, args.reduce_at, args.octaves)

    assets = publish_all(args.slug or None, out_dir=Path(args.out),
                         reduce_at_px=args.reduce_at, octaves=args.octaves,
                         verify=not args.no_verify, verify_bake=not args.no_bake,
                         write=not args.dry_run, prune=not args.keep_stale)
    print()
    print(json.dumps([a.as_dict() for a in assets], indent=2))
    for a in assets:
        if a.fallback:
            print(f"NOTE {a.slug}: published UNREDUCED -- {a.fallback}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
