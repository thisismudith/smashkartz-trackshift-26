"""Tests for publishing the circuit GLBs.

Three layers, the same shape as test_glb_surface.py:

  * pure unit tests on REAL in-memory GLBs built here -- interleaved vertex buffers with
    a byteStride, an index buffer, and genuine PNG payloads -- because the thing most
    likely to go wrong is the container rewrite, and a rewrite that only works on tightly
    packed buffers is exactly the bug that ships scrambled geometry;
  * boundary tests on the reduction threshold, per AGENTS.md section 20: below, at, and
    above;
  * measurements against the real 158 MB Silverstone and 97 MB Shanghai assets in
    data/tracks, which are gitignored, so those tests skip when the assets are absent.

Every number quoted below is measured on this machine, not illustrative.
"""
from __future__ import annotations

import io
import json
import pathlib
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from simdata.glb_surface import (CHUNK_BIN, CHUNK_JSON, GLB_MAGIC, Glb, accessor_array,
                                 extract_drive_surface, load_glb, parse_glb,
                                 registry_entry)
from simdata.glb_publish import (BYTES_PER_TEXEL, MB, MIP_RATIO, PUBLISH_DIR,
                                 PUBLISHED_RE, REDUCE_AT_PX, SHA_PREFIX, URL_PREFIX,
                                 ImageEntry, asset_fields, compare_drive_surface,
                                 compare_geometry, downscale, image_bytes,
                                 image_entries, inventory_lines, inventory_totals,
                                 plan_reductions, publish_all, publish_asset,
                                 published_name, published_url, rebuild_glb,
                                 sha256_bytes, texture_users, view_owners)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TRACKS = REPO_ROOT / "data" / "tracks"


# ===========================================================================
# Building real GLBs in memory
# ===========================================================================

def png(width: int, height: int, mode: str = "RGB", seed: int = 0) -> bytes:
    """A real PNG with structure in it -- a flat fill would survive any resampler."""
    from PIL import Image
    rng = np.random.default_rng(seed)
    bands = {"RGB": 3, "RGBA": 4, "LA": 2, "L": 1, "P": 1}[mode]
    ramp = np.linspace(0, 255, width, dtype=np.uint8)[None, :, None]
    noise = rng.integers(0, 40, size=(height, width, bands), dtype=np.uint8)
    data = (np.clip(ramp.astype(np.int32) + noise, 0, 255)).astype(np.uint8)
    img = Image.fromarray(data.squeeze() if bands == 1 else data,
                          mode="L" if bands == 1 else ("LA" if bands == 2 else
                                                       ("RGBA" if bands == 4 else "RGB")))
    if mode == "P":
        img = img.convert("P")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def jpeg(width: int, height: int, seed: int = 0) -> bytes:
    from PIL import Image
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, size=(height, width, 3), dtype=np.uint8)
    out = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(out, format="JPEG", quality=95)
    return out.getvalue()


def build_glb(images=(), *, quads: int = 2, stride_pad: int = 0,
              share_image_view: bool = False) -> bytes:
    """A valid GLB: one interleaved POSITION+NORMAL buffer, an index buffer, N images.

    `images` entries are (bytes, mimeType, material_name, channel). The vertex buffer is
    INTERLEAVED with an explicit byteStride, and `stride_pad` can add slack bytes to it,
    because a rewriter that silently repacks tightly would pass a non-interleaved test
    and destroy a Sketchfab export.
    """
    buf = bytearray()
    views, accessors = [], []

    def add_view(data: bytes, **extra) -> int:
        while len(buf) % 4:
            buf.append(0)
        views.append({"buffer": 0, "byteOffset": len(buf),
                      "byteLength": len(data), **extra})
        buf.extend(data)
        return len(views) - 1

    # A horizontal strip of quads in the XZ plane, Y up -- drivable-looking geometry.
    verts, faces = [], []
    for q in range(quads):
        base = len(verts)
        x0, x1 = 10.0 * q, 10.0 * (q + 1)
        for x, z in ((x0, -5.0), (x1, -5.0), (x1, 5.0), (x0, 5.0)):
            verts.append((x, 0.25 * q, z))
        faces += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    pos = np.asarray(verts, dtype=np.float32)
    nrm = np.tile(np.asarray([0.0, 1.0, 0.0], dtype=np.float32), (len(pos), 1))
    stride = 24 + stride_pad
    inter = np.zeros((len(pos), stride), dtype=np.uint8)
    inter[:, 0:12] = pos.view(np.uint8).reshape(len(pos), 12)
    inter[:, 12:24] = nrm.view(np.uint8).reshape(len(pos), 12)
    vi = add_view(inter.tobytes(), byteStride=stride, target=34962)
    idx = np.asarray(faces, dtype=np.uint32).ravel()
    ii = add_view(idx.tobytes(), target=34963)

    accessors.append({"bufferView": vi, "byteOffset": 0, "componentType": 5126,
                      "count": len(pos), "type": "VEC3",
                      "min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()})
    accessors.append({"bufferView": vi, "byteOffset": 12, "componentType": 5126,
                      "count": len(pos), "type": "VEC3"})
    accessors.append({"bufferView": ii, "byteOffset": 0, "componentType": 5125,
                      "count": len(idx), "type": "SCALAR"})

    gltf_images, textures, materials = [], [], []
    shared_view = None
    for n, (data, mime, mat_name, channel) in enumerate(images):
        if share_image_view and shared_view is not None:
            view = shared_view
        else:
            view = add_view(data)
            shared_view = view
        gltf_images.append({"bufferView": view, "mimeType": mime})
        textures.append({"source": n, "sampler": 0})
        info = {"index": n}
        mat = {"name": mat_name, "pbrMetallicRoughness": {}}
        if channel == "baseColor":
            mat["pbrMetallicRoughness"]["baseColorTexture"] = info
        elif channel == "specular":
            mat["extensions"] = {"KHR_materials_specular": {"specularTexture": info}}
        else:
            mat[f"{channel}Texture"] = info
        materials.append(mat)
    if not materials:
        materials.append({"name": "asphalt.001", "pbrMetallicRoughness": {}})

    gltf = {
        "asset": {"version": "2.0", "generator": "test_glb_publish"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "asphalt.001"}],
        "meshes": [{"name": "asphalt.001", "primitives": [
            {"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2,
             "material": 0, "mode": 4}]}],
        "accessors": accessors, "bufferViews": views,
        "buffers": [{"byteLength": len(buf)}],
        "materials": materials,
        "samplers": [{"magFilter": 9729, "minFilter": 9987,
                      "wrapS": 10497, "wrapT": 10497}],
    }
    if gltf_images:
        gltf["images"] = gltf_images
        gltf["textures"] = textures

    def pad(blob, fill):
        return blob + fill * (-len(blob) % 4)

    js = pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bn = pad(bytes(buf), b"\0")
    body = (struct.pack("<II", len(js), CHUNK_JSON) + js
            + struct.pack("<II", len(bn), CHUNK_BIN) + bn)
    return struct.pack("<III", GLB_MAGIC, 2, 12 + len(body)) + body


SILVERSTONE_SHAPED = [
    (png(2048, 2048, seed=1), "image/png", "asphalt.001", "baseColor"),
    (png(512, 512, seed=2), "image/png", "barriers1.001", "baseColor"),
    (png(256, 256, "RGBA", seed=3), "image/png", "grass.001", "specular"),
]


# ===========================================================================
# 1. The inventory
# ===========================================================================

def test_inventory_measures_dimensions_encoded_bytes_and_vram():
    """VRAM is w*h*4 with a 4/3 mip tail -- and that is what makes a 4096 map a
    problem, not its 28 MB of PNG."""
    glb = parse_glb(build_glb(SILVERSTONE_SHAPED))
    entries = image_entries(glb)
    assert [(e.width, e.height) for e in entries] == [(2048, 2048), (512, 512),
                                                      (256, 256)]
    assert [e.fmt for e in entries] == ["PNG"] * 3
    assert [e.mode for e in entries] == ["RGB", "RGB", "RGBA"]
    assert entries[0].base_vram_bytes == 2048 * 2048 * BYTES_PER_TEXEL
    assert entries[0].vram_bytes == pytest.approx(2048 * 2048 * 4 * MIP_RATIO)
    for entry in entries:
        assert entry.encoded_bytes == len(image_bytes(glb, entry.index))
        assert entry.note == ""

    totals = inventory_totals(entries)
    assert totals["images"] == 3 and totals["measured"] == 3
    assert totals["unmeasured"] == []
    assert totals["baseVramBytes"] == sum(e.base_vram_bytes for e in entries)
    assert totals["vramBytes"] == pytest.approx(totals["baseVramBytes"] * MIP_RATIO)


def test_inventory_names_the_offender_including_through_an_extension():
    """glTF images here carry no `name`, so an unnamed 67 MB texture would be reported
    as "image 59" and nobody could act on it. The material that samples it is the only
    handle it has -- including when the only reference is inside KHR_materials_specular,
    which still costs full VRAM."""
    glb = parse_glb(build_glb(SILVERSTONE_SHAPED))
    users = texture_users(glb.gltf)
    assert users[0] == ("asphalt.001.baseColor",)
    assert users[2] == ("grass.001.KHR_materials_specular.specularTexture",)
    entries = image_entries(glb)
    assert entries[0].label == "asphalt.001.baseColor"
    assert "asphalt.001.baseColor" in "\n".join(inventory_lines(entries))


def test_an_undecodable_image_is_reported_not_silently_dropped():
    """A silent skip would UNDER-report the VRAM total, which is the one number this
    module exists to tell the truth about. The entry survives with a note instead."""
    glb = parse_glb(build_glb([(b"not a png at all", "image/png", "x", "baseColor")]))
    entries = image_entries(glb)
    assert len(entries) == 1
    assert entries[0].pixels == 0 and entries[0].note
    assert inventory_totals(entries)["unmeasured"] == [0]
    assert "UNMEASURED" in "\n".join(inventory_lines(entries))
    assert plan_reductions(entries) == [], "an unmeasured image must not be re-encoded"


# ===========================================================================
# 2. The reduction plan -- boundary tests, AGENTS.md section 20
# ===========================================================================

def _entry(width, height, index=0):
    return ImageEntry(index=index, buffer_view=index, mime="image/png", fmt="PNG",
                      mode="RGB", width=width, height=height, encoded_bytes=1,
                      users=("m.baseColor",))


@pytest.mark.parametrize("size,expected", [
    (1024, None),                     # below the threshold: untouched
    (2047, None),                     # just below
    (2048, (1024, 1024)),             # exactly at it
    (4096, (2048, 2048)),             # above
])
def test_plan_reduces_at_and_above_the_threshold_only(size, expected):
    """The rule is stated, not guessed: longest side >= REDUCE_AT_PX is halved once.
    At the defaults that is exactly the three images measured on silverstone.glb."""
    plan = plan_reductions([_entry(size, size)])
    if expected is None:
        assert plan == []
    else:
        assert len(plan) == 1
        assert plan[0].from_px == (size, size) and plan[0].to_px == expected
        assert plan[0].base_vram_saved == (size * size - expected[0] * expected[1]) * 4


def test_plan_halves_a_non_square_image_on_both_axes():
    plan = plan_reductions([_entry(4096, 512)])
    assert plan[0].to_px == (2048, 256)


def test_plan_threshold_and_octaves_are_caller_settable():
    entries = [_entry(4096, 4096)]
    assert plan_reductions(entries, octaves=2)[0].to_px == (1024, 1024)
    assert plan_reductions(entries, reduce_at_px=8192) == []


# ===========================================================================
# 3. Re-encoding
# ===========================================================================

def test_downscale_keeps_png_and_alpha():
    from PIL import Image
    data, mime = downscale(png(256, 256, "RGBA", seed=7), (128, 128))
    assert mime == "image/png"
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (128, 128) and img.format == "PNG" and img.mode == "RGBA"


def test_downscale_promotes_a_palette_image_before_resampling():
    """Pillow silently falls back to NEAREST when asked to Lanczos a mode-P image, so a
    palette map would come out aliased. It is promoted to true colour first."""
    from PIL import Image
    data, mime = downscale(png(256, 256, "P", seed=8), (64, 64))
    assert mime == "image/png"
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (64, 64) and img.mode in ("RGB", "RGBA")


def test_downscale_keeps_jpeg_as_jpeg():
    from PIL import Image
    data, mime = downscale(jpeg(256, 256, seed=9), (128, 128))
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG" and img.size == (128, 128)


# ===========================================================================
# 4. The container rewrite
# ===========================================================================

def _walk_chunks(blob: bytes):
    magic, version, total = struct.unpack_from("<III", blob, 0)
    assert magic == GLB_MAGIC and version == 2
    assert total == len(blob), "the header must declare the real length"
    out, off = [], 12
    while off < total:
        clen, ctype = struct.unpack_from("<II", blob, off)
        assert clen % 4 == 0, "chunk lengths must be 4-byte aligned"
        out.append((ctype, blob[off + 8: off + 8 + clen]))
        off += 8 + clen
    assert off == total, "the chunk walk must land exactly on the declared length"
    return out


def test_rewrite_produces_a_structurally_valid_container():
    source = build_glb(SILVERSTONE_SHAPED)
    glb = parse_glb(source)
    small, mime = downscale(image_bytes(glb, 0), (1024, 1024))
    blob = rebuild_glb(glb, {0: (small, mime)})

    chunks = _walk_chunks(blob)
    assert [c[0] for c in chunks] == [CHUNK_JSON, CHUNK_BIN]
    assert chunks[0][1].rstrip(b" ")[-1:] == b"}", "JSON chunk pads with spaces"
    after = parse_glb(blob)
    bin_len = len(after.binary)
    declared = after.gltf["buffers"][0]["byteLength"]
    assert 0 <= bin_len - declared <= 3, "the BIN chunk may exceed the buffer by padding"
    for i, bv in enumerate(after.gltf["bufferViews"]):
        assert bv["byteOffset"] % 4 == 0, f"bufferView {i} is not 4-byte aligned"
        assert bv["byteOffset"] + bv["byteLength"] <= declared


def test_rewrite_keeps_every_accessor_bit_exact_through_an_interleaved_buffer():
    """The offenders live between the geometry bufferViews, so shrinking one shifts
    everything after it. This is the check that the shift was applied to the OFFSETS and
    not to the meaning: an interleaved stride read as tightly packed mixes POSITION with
    NORMAL and yields a plausible-looking but wrong surface."""
    source = build_glb(SILVERSTONE_SHAPED, quads=6, stride_pad=8)
    glb = parse_glb(source)
    small, mime = downscale(image_bytes(glb, 0), (256, 256))
    after = parse_glb(rebuild_glb(glb, {0: (small, mime)}))

    assert len(small) < len(image_bytes(glb, 0)), "the fixture must actually shrink"
    for i in range(len(glb.gltf["accessors"])):
        before_arr, after_arr = accessor_array(glb, i), accessor_array(after, i)
        assert before_arr.tobytes() == after_arr.tobytes(), f"accessor {i} changed"
    assert compare_geometry(glb, after) == []


def test_rewrite_replaces_the_payload_and_updates_the_mime_type():
    glb = parse_glb(build_glb([(jpeg(64, 64), "image/jpeg", "m", "baseColor")]))
    after = parse_glb(rebuild_glb(glb, {0: (png(32, 32), "image/png")}))
    assert after.gltf["images"][0]["mimeType"] == "image/png"
    assert image_bytes(after, 0) == png(32, 32)
    assert image_entries(after)[0].width == 32


def test_rewrite_of_nothing_still_round_trips():
    glb = parse_glb(build_glb(SILVERSTONE_SHAPED))
    after = parse_glb(rebuild_glb(glb, {}))
    assert compare_geometry(glb, after) == []
    for i in range(3):
        assert image_bytes(glb, i) == image_bytes(after, i)


def test_rewrite_refuses_a_bufferview_two_images_share():
    """Writing new bytes under a view something else reads is invisible until runtime,
    so it is refused rather than attempted."""
    glb = parse_glb(build_glb(SILVERSTONE_SHAPED[:2], share_image_view=True))
    assert len(view_owners(glb.gltf)[glb.gltf["images"][0]["bufferView"]]) == 2
    with pytest.raises(ValueError, match="also read by"):
        rebuild_glb(glb, {0: (png(16, 16), "image/png")})


def test_rewrite_refuses_a_bufferview_an_accessor_also_reads():
    glb = parse_glb(build_glb(SILVERSTONE_SHAPED))
    gltf = json.loads(json.dumps(glb.gltf))
    gltf["images"][0]["bufferView"] = gltf["accessors"][0]["bufferView"]
    aliased = Glb(gltf=gltf, binary=glb.binary)
    with pytest.raises(ValueError, match="accessor 0"):
        rebuild_glb(aliased, {0: (png(16, 16), "image/png")})


# ===========================================================================
# 5. Verification -- the whole safety argument
# ===========================================================================

def test_compare_geometry_catches_a_moved_vertex():
    """If this ever stopped failing, the published asset could drift from the fitted
    transform and nothing downstream would notice until the cars floated."""
    source = build_glb(SILVERSTONE_SHAPED)
    glb = parse_glb(source)
    view = glb.gltf["bufferViews"][glb.gltf["accessors"][0]["bufferView"]]
    moved = bytearray(glb.binary)
    at = view["byteOffset"]
    moved[at] ^= 0xFF
    tampered = Glb(gltf=glb.gltf, binary=bytes(moved))

    diffs = compare_geometry(glb, tampered)
    assert diffs and "bytes differ" in diffs[0]
    surf_diffs = compare_drive_surface(
        extract_drive_surface(glb), extract_drive_surface(tampered))
    assert surf_diffs and "verts" in surf_diffs[0]


def test_compare_geometry_catches_a_shifted_accessor_offset():
    """Identical bytes read through a shifted accessor is still a changed model."""
    glb = parse_glb(build_glb(SILVERSTONE_SHAPED))
    gltf = json.loads(json.dumps(glb.gltf))
    gltf["accessors"][0]["byteOffset"] = 12
    diffs = compare_geometry(glb, Glb(gltf=gltf, binary=glb.binary))
    assert any("accessor 0.byteOffset" in d for d in diffs)


def test_compare_drive_surface_is_empty_for_a_texture_only_rewrite():
    glb = parse_glb(build_glb(SILVERSTONE_SHAPED, quads=4))
    small, mime = downscale(image_bytes(glb, 0), (256, 256))
    after = parse_glb(rebuild_glb(glb, {0: (small, mime)}))
    before_surface, after_surface = extract_drive_surface(glb), extract_drive_surface(after)
    assert before_surface.tri_count == 8, "the fixture must have real drivable triangles"
    assert compare_drive_surface(before_surface, after_surface) == []


# ===========================================================================
# 6. The frozen published-asset contract
# ===========================================================================

def test_published_name_and_url_follow_the_frozen_contract():
    sha = sha256_bytes(b"whatever")
    assert published_name("british-grand-prix", sha) == \
        f"british-grand-prix.{sha[:SHA_PREFIX]}.glb"
    assert published_url("british-grand-prix", sha) == \
        f"{URL_PREFIX}/british-grand-prix.{sha[:SHA_PREFIX]}.glb"
    assert PUBLISHED_RE.match(published_name("british-grand-prix", sha))
    assert PUBLISH_DIR == REPO_ROOT / "frontend" / "public" / "sim" / "glb"
    assert URL_PREFIX == "/sim/glb"


def test_published_hash_is_of_the_published_bytes_not_the_source(tmp_path):
    """The two differ the moment a texture is downscaled. Naming the file after the
    source hash would point the browser at bytes it will never receive."""
    source = build_glb(SILVERSTONE_SHAPED)
    path = tmp_path / "src.glb"
    path.write_bytes(source)
    asset = publish_asset("british-grand-prix", path, out_dir=tmp_path / "out",
                          verify_bake=False)
    assert asset.rewritten
    assert asset.sha256 != asset.source_sha256
    assert asset.sha256 == sha256_bytes(asset.path.read_bytes())
    assert asset.path.name.split(".")[1] == asset.sha256[:SHA_PREFIX]
    assert asset_fields(asset) == {"assetUrl": asset.url, "assetSha256": asset.sha256}
    assert asset_fields(asset)["assetSha256"] != asset.source_sha256


def test_publish_returns_exactly_the_six_contract_fields(tmp_path):
    path = tmp_path / "src.glb"
    path.write_bytes(build_glb(SILVERSTONE_SHAPED))
    asset = publish_asset("british-grand-prix", path, out_dir=tmp_path / "out",
                          verify_bake=False)
    assert set(asset.as_dict()) == {"slug", "url", "sha256", "bytes",
                                    "vramBeforeMb", "vramAfterMb"}
    assert asset.as_dict()["bytes"] == asset.path.stat().st_size
    assert asset.as_dict()["vramAfterMb"] < asset.as_dict()["vramBeforeMb"]
    # 2048 -> 1024 on one image: 12.6 MB of base VRAM, 16.8 MB with mips.
    saved = (asset.vram_before_mb - asset.vram_after_mb) * MB
    assert saved == pytest.approx((2048 ** 2 - 1024 ** 2) * 4 * MIP_RATIO, rel=1e-6)


def test_publishing_does_not_touch_the_source(tmp_path):
    path = tmp_path / "src.glb"
    source = build_glb(SILVERSTONE_SHAPED)
    path.write_bytes(source)
    publish_asset("x", path, out_dir=tmp_path / "out", verify_bake=False)
    assert path.read_bytes() == source


def test_dry_run_verifies_but_writes_nothing(tmp_path):
    path = tmp_path / "src.glb"
    path.write_bytes(build_glb(SILVERSTONE_SHAPED))
    out = tmp_path / "out"
    asset = publish_asset("x", path, out_dir=out, write=False, verify_bake=False)
    assert asset.rewritten and asset.verified["geometryIdentical"] is True
    assert not asset.written and not out.exists()


# ===========================================================================
# 7. Degrading honestly
# ===========================================================================

def test_an_unsafe_rewrite_publishes_the_raw_asset_and_says_so(tmp_path, monkeypatch):
    """The contract: if a safe rewrite is not achievable, publish the RAW asset and
    report the VRAM cost honestly. A corrupted model is worse than a heavy one."""
    import simdata.glb_publish as pub

    source = build_glb(SILVERSTONE_SHAPED)
    real_rebuild = pub.rebuild_glb

    def corrupting_rebuild(glb, payloads):
        """A correct rewrite, then one flipped bit inside the vertex bufferView."""
        blob = bytearray(real_rebuild(glb, payloads))
        view = parse_glb(bytes(blob)).gltf["bufferViews"][0]
        bin_data = 12 + 8 + struct.unpack_from("<I", blob, 12)[0] + 8
        blob[bin_data + view["byteOffset"]] ^= 0xFF
        return bytes(blob)

    monkeypatch.setattr(pub, "rebuild_glb", corrupting_rebuild)

    path = tmp_path / "src.glb"
    path.write_bytes(source)
    asset = publish_asset("british-grand-prix", path, out_dir=tmp_path / "out",
                          verify_bake=False)

    assert asset.fallback and "geometry changed" in asset.fallback
    assert not asset.rewritten and asset.reduced == []
    assert asset.path.read_bytes() == source, "the RAW bytes must be what is published"
    assert asset.sha256 == asset.source_sha256
    assert asset.vram_after_mb == asset.vram_before_mb, \
        "the reported VRAM must be the unreduced cost, not the one that was rejected"


def test_a_rewrite_that_does_not_reparse_is_rejected(tmp_path, monkeypatch):
    import simdata.glb_publish as pub
    monkeypatch.setattr(pub, "rebuild_glb", lambda glb, payloads: b"garbage")
    path = tmp_path / "src.glb"
    source = build_glb(SILVERSTONE_SHAPED)
    path.write_bytes(source)
    asset = publish_asset("x", path, out_dir=tmp_path / "out", verify_bake=False)
    assert "does not re-parse" in asset.fallback
    assert asset.path.read_bytes() == source


def test_a_bake_that_cannot_run_is_recorded_as_null_with_a_reason(tmp_path):
    """Absence is null, never a default and never a silent pass. Without a fitted
    transform there is nothing to re-bake against, and the geometry byte comparison --
    which needs no telemetry -- is what decides."""
    path = tmp_path / "src.glb"
    path.write_bytes(build_glb(SILVERSTONE_SHAPED))
    asset = publish_asset("x", path, out_dir=tmp_path / "out", fit=None, event=None)
    assert asset.rewritten
    assert asset.verified["bake"] is None
    assert "no fitted transform" in asset.verified["bakeNote"]
    assert asset.verified["geometryIdentical"] is True
    assert asset.diagnostics()["verified"]["bake"] is None


def test_an_asset_with_nothing_above_the_threshold_is_published_unchanged(tmp_path):
    path = tmp_path / "src.glb"
    source = build_glb([(png(512, 512), "image/png", "m", "baseColor")])
    path.write_bytes(source)
    asset = publish_asset("x", path, out_dir=tmp_path / "out", verify_bake=False)
    assert asset.reduced == [] and not asset.rewritten and not asset.fallback
    assert asset.path.read_bytes() == source
    assert asset.vram_after_mb == asset.vram_before_mb


def test_publish_all_skips_an_unregistered_slug_and_a_missing_asset(tmp_path, capsys):
    """Two of the three fallback points, exercised here; the third -- a failed gate --
    is the registry's business, and publishing happens either way."""
    path = tmp_path / "present.glb"
    path.write_bytes(build_glb(SILVERSTONE_SHAPED))
    # `glb` is resolved against REPO_ROOT; an absolute path resolves to itself.
    registry = {"circuits": {
        "present": {"event": "Present GP", "glb": str(path)},
        "absent": {"event": "Absent GP", "glb": "data/tracks/nothing-here.glb"},
    }}
    assets = publish_all(["present", "absent", "never-registered"], registry=registry,
                         out_dir=tmp_path / "out", verify_bake=False)
    assert [a.slug for a in assets] == ["present"]
    printed = capsys.readouterr().out
    assert "never-registered: no entry" in printed
    assert "procedural ribbon" in printed
    assert assets[0].verified["sourceHashMatchesRegistry"] is None


def test_publish_prunes_only_superseded_copies_of_the_same_slug(tmp_path):
    """Each published asset is 100 MB+, so stale copies matter. Only `<slug>.<sha10>.glb`
    for THIS slug is ever deleted."""
    out = tmp_path / "out"
    out.mkdir()
    stale = out / "british-grand-prix.0123456789.glb"
    other = out / "chinese-grand-prix.abcdef0123.glb"
    keeper = out / "british-grand-prix-notes.txt"
    for p in (stale, other, keeper):
        p.write_bytes(b"x")

    path = tmp_path / "src.glb"
    path.write_bytes(build_glb(SILVERSTONE_SHAPED))
    asset = publish_asset("british-grand-prix", path, out_dir=out, verify_bake=False)

    assert asset.pruned == [stale.name]
    assert not stale.exists()
    assert other.exists() and keeper.exists() and asset.path.exists()


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


def test_silverstone_texture_inventory_matches_the_measured_offenders():
    """Measured on silverstone.glb (165.8 MB): 69 images, 61.9 MB encoded, 196.4 MB of
    texture VRAM. Two 4096-square and one 2048-square are 151.0 MB of that 196.4 MB
    (76.9 %) and 50.8 MB of the file. The other 66 are 512-square or smaller.

    This is the measurement the whole reduction rests on, so it is asserted rather than
    quoted -- if a re-export changes it, the plan must be re-derived, not assumed."""
    entries = image_entries(load_glb(_asset("british-grand-prix")))
    totals = inventory_totals(entries)
    assert totals["images"] == 69 and totals["unmeasured"] == []
    assert totals["encodedBytes"] / MB == pytest.approx(61.9, abs=0.2)
    assert totals["baseVramBytes"] / MB == pytest.approx(196.4, abs=0.2)

    big = sorted((e for e in entries if max(e.width, e.height) >= REDUCE_AT_PX),
                 key=lambda e: -e.base_vram_bytes)
    assert [(e.width, e.height) for e in big] == [(4096, 4096), (4096, 4096),
                                                  (2048, 2048)]
    assert sum(e.base_vram_bytes for e in big) / MB == pytest.approx(151.0, abs=0.1)
    assert (sum(e.base_vram_bytes for e in big) / totals["baseVramBytes"]
            == pytest.approx(0.769, abs=0.005))
    assert sum(e.encoded_bytes for e in big) / MB == pytest.approx(50.8, abs=0.2)
    assert max(max(e.width, e.height) for e in entries if e not in big) == 512

    # And the offenders are named, not guessed at.
    labels = {e.label for e in big}
    assert any("asphalt.001" in label for label in labels)
    assert any("grass.001" in label for label in labels)


def test_shanghai_texture_inventory_is_the_opposite_problem():
    """Measured on shanghai.glb (102.1 MB): 93 images but only 35.2 MB of VRAM, because
    its maps cap at 256-square apart from one 2048 and one 1024. Its weight is 89.8 MB
    of geometry, which this module is not allowed to touch -- so reduction barely moves
    it, and the honest report is that it stays a ~98 MB asset."""
    entries = image_entries(load_glb(_asset("chinese-grand-prix")))
    totals = inventory_totals(entries)
    assert totals["images"] == 93 and totals["unmeasured"] == []
    assert totals["baseVramBytes"] / MB == pytest.approx(35.2, abs=0.2)
    plan = plan_reductions(entries)
    assert len(plan) == 1 and plan[0].from_px == (2048, 2048)


@pytest.mark.parametrize("slug", ["british-grand-prix", "chinese-grand-prix"])
def test_the_published_asset_is_the_source_geometry_byte_for_byte(slug):
    """The claim the whole publish rests on, checked against whatever is actually on
    disk in frontend/public/sim/glb right now: same triangles, smaller textures.

    Skips when nothing has been published yet -- frontend/public/sim is gitignored."""
    source = _asset(slug)
    published = sorted(PUBLISH_DIR.glob(f"{slug}.*.glb"))
    if not published:
        pytest.skip(f"nothing published for {slug} yet; run python -m simdata.glb_publish")
    assert len(published) == 1, f"stale published copies: {[p.name for p in published]}"
    path = published[0]

    blob = path.read_bytes()
    assert path.name == published_name(slug, sha256_bytes(blob)), \
        "the filename must be the sha10 of the bytes it holds"

    before, after = load_glb(source), parse_glb(blob)
    assert compare_geometry(before, after) == []
    assert inventory_totals(image_entries(after))["baseVramBytes"] < \
        inventory_totals(image_entries(before))["baseVramBytes"]


def test_the_published_silverstone_keeps_its_drivable_triangles():
    """549,852 candidate drivable triangles, identical between source and published."""
    source = _asset("british-grand-prix")
    published = sorted(PUBLISH_DIR.glob("british-grand-prix.*.glb"))
    if not published:
        pytest.skip("nothing published yet; run python -m simdata.glb_publish")
    before = extract_drive_surface(load_glb(source))
    after = extract_drive_surface(parse_glb(published[0].read_bytes()))
    assert before.tri_count == 549852
    assert compare_drive_surface(before, after) == []
