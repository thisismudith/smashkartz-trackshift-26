"""Orchestrator: build every derived-data artifact the frontend needs and place them
under frontend/public/sim/. Read-only over data/2026; reproducible; deterministic given
the same raw data (aside from float rounding).

Usage: python scripts/build_sim_data.py [--events "British Grand Prix" ...]
Default: builds the catalogue, fitted parameters, and British Grand Prix (Race +
Sprint) track model and replay packs -- the plan's chosen first dataset.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simdata.build_track import build_track_model, write_artifact as write_track
from simdata.catalogue import build_catalogue
from simdata.fit_params import build_params
from simdata.replay import build_replay_pack

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "sim"


def write_json(obj: dict, out_dir: Path, name_prefix: str) -> str:
    import hashlib
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:10]
    fname = f"{name_prefix}.{digest}.json"
    (out_dir / fname).write_bytes(payload)
    return fname


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", nargs="*", default=["British Grand Prix"])
    ap.add_argument("--sessions", nargs="*", default=["Race", "Sprint"])
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"tracks": {}, "sessions": {}}

    print("== catalogue ==")
    cat = build_catalogue()
    manifest["catalogue"] = write_json(cat, OUT_DIR, "catalogue")
    print(f"  {manifest['catalogue']}")

    print("== fitted parameters ==")
    params = build_params()
    manifest["params"] = write_json(params, OUT_DIR, "params")
    print(f"  {manifest['params']}")

    for event in args.events:
        print(f"== track: {event} ==")
        model = build_track_model(event)
        path = write_track(model, OUT_DIR)
        manifest["tracks"][model["slug"]] = path.name
        print(f"  {path.name}  ring={model['ring']['lengthMetres']}m")

        for session in args.sessions:
            session_dir = (Path(__file__).resolve().parent.parent / "data" / "2026"
                            / event / session)
            if not session_dir.exists():
                continue
            print(f"== replay: {event}/{session} ==")
            blob, man = build_replay_pack(event, session)
            digest_bin = __import__("hashlib").sha256(blob).hexdigest()[:10]
            base = f"{man['trackSlug']}-{session.lower().replace(' ', '-')}"
            bin_name = f"{base}.{digest_bin}.bin"
            (OUT_DIR / bin_name).write_bytes(blob)
            man["binFile"] = bin_name
            man_name = write_json(man, OUT_DIR, base)
            manifest["sessions"].setdefault(model["slug"], {})[session] = {
                "manifest": man_name, "bin": bin_name,
            }
            print(f"  {bin_name} + {man_name}  {len(blob)} bytes raw")

    index_name = write_json(manifest, OUT_DIR, "index")
    # a stable, un-hashed pointer so the app always knows where to start
    (OUT_DIR / "index.json").write_bytes(
        json.dumps({"latest": index_name}, separators=(",", ":")).encode("utf-8"))
    print(f"\nindex: {index_name} (pointer: index.json)")


if __name__ == "__main__":
    main()
