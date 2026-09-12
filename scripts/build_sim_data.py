"""Orchestrator: build every derived-data artifact the frontend needs and place them
under frontend/public/sim/. Read-only over the raw mirror; reproducible; deterministic
given the same raw data (aside from float rounding).

The mirror is data/raw/tracinginsights/<year>, resolved by simdata/paths.py, which also
accepts the two earlier layouts (data/raw/<year>, data/<year>) so an un-migrated machine
keeps building. --year picks which mirror feeds the build, and every run prints the path
it resolved to.

ONE YEAR PER ARTIFACT SET. Artifact filenames are keyed by circuit slug alone
(british-grand-prix-race.<hash>.bin), so a second year written into the same output
directory would overwrite the first circuit-for-circuit while the index still listed
both. The index records the year it was built from, and a differing --year is refused
unless --fresh says to start over.

Usage: python scripts/build_sim_data.py [--year 2026] [--prune]
                                        [--events "British Grand Prix" ...]
Default: builds the catalogue, fitted parameters, and British Grand Prix (Race +
Sprint) track model and replay packs -- the plan's chosen first dataset.
"""
from __future__ import annotations

import argparse
import concurrent.futures as _futures
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simdata.build_track import build_track_model, write_artifact as write_track
from simdata.catalogue import build_catalogue
from simdata.fit_params import build_params
from simdata.paths import (DEFAULT_YEAR, active_year, available_years, data_root,
                            layout_note, set_year)
from simdata.replay import build_replay_pack
from simdata.rules import default_event_rules, event_rules_to_mapping

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "sim"


def _json_safe(o):
    """Replace every non-finite float with null.

    Python's json.dumps happily writes bare NaN / Infinity, which are NOT valid JSON:
    the browser's JSON.parse rejects the whole document. A single NaN in one statistic
    (measured: "observedLateralStd":NaN in the Chinese GP track model) therefore broke
    the entire artifact for that circuit. null is honest -- the statistic genuinely has
    no value when its sample is empty -- and every consumer already handles null.
    """
    import math
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def write_json(obj: dict, out_dir: Path, name_prefix: str) -> str:
    import hashlib
    payload = json.dumps(_json_safe(obj), separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:10]
    fname = f"{name_prefix}.{digest}.json"
    (out_dir / fname).write_bytes(payload)
    return fname


def build_rules() -> dict:
    """The competition rule set, serialised exactly as the rule engine holds it.

    The UI needs the regulation limits (power envelope, store capacity, deploy and
    harvest budgets) to colour an energy readout against them. Those limits belong to
    scripts/simdata/rules.py and nowhere else -- section 32 forbids a second
    implementation, and typing them into TypeScript would be exactly that. So they are
    emitted here and READ by the frontend.

    Every one of them currently carries verified:false. That flag travels with the
    numbers so the UI can say the scale is provisional rather than implying the
    thresholds are regulation fact.
    """
    return event_rules_to_mapping(default_event_rules())


def discover_events(sessions: list[str]) -> list[str]:
    """Event directories that actually hold a buildable session.

    NOT a directory listing. A mirror also contains .git, .github, cache,
    cache_preseason and schemas, and 2026 holds a Spanish Grand Prix that only ever ran
    Practice -- 19 entries for 13 real circuits. A naive glob hands build_track_model
    ".git" and the whole rebuild dies on it. An event qualifies only if one of the
    REQUESTED sessions exists under it and that session has at least one driver
    directory carrying a lap file, which is the same evidence the builders need.
    """
    out = []
    root = data_root()
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or "Testing" in d.name:
            continue
        for session in sessions:
            sdir = d / session
            if not sdir.is_dir():
                continue
            if any(any(drv.glob("*_tel.json")) for drv in sdir.iterdir() if drv.is_dir()):
                out.append(d.name)
                break
    return out


def build_one_event(event: str, sessions: list[str]) -> dict:
    """Build one event's track model and replay packs, and WRITE its artifacts.

    Runs in a worker process. Events are completely independent -- separate raw
    directories, separate rings, separate packs -- and every artifact filename is a hash
    of its own content, so two workers can never choose the same name for different
    bytes, nor different names for identical bytes. The one piece of shared state is the
    INDEX, which is why this returns its entries instead of writing them: the parent
    merges and writes the index exactly once, after every worker has finished.

    Rebuilding serially took the sum of all 18 sessions; this makes the wall clock the
    slowest single EVENT instead.
    """
    from simdata.replay import build_replay_pack as _pack

    model = build_track_model(event)
    path = write_track(model, OUT_DIR)
    out = {"event": event, "slug": model["slug"], "track": path.name,
           "ring": model["ring"]["lengthMetres"], "sessions": {}, "errors": []}

    for session in sessions:
        session_dir = data_root() / event / session
        if not session_dir.exists():
            continue
        try:
            blob, man = _pack(event, session)
        except Exception as exc:                      # noqa: BLE001 - reported, not hidden
            out["errors"].append(f"{session}: {type(exc).__name__}: {exc}")
            continue
        digest_bin = hashlib.sha256(blob).hexdigest()[:10]
        base = f"{man['trackSlug']}-{session.lower().replace(' ', '-')}"
        bin_name = f"{base}.{digest_bin}.bin"
        (OUT_DIR / bin_name).write_bytes(blob)
        man["binFile"] = bin_name
        man_name = write_json(man, OUT_DIR, base)
        out["sessions"][session] = {"manifest": man_name, "bin": bin_name,
                                     "bytes": len(blob)}
    return out


def assert_year_matches(prev: dict, how: str) -> None:
    """Refuse to reuse an index built from a different season.

    Artifacts are named by circuit slug with no year (british-grand-prix-race.<hash>.bin),
    so one output directory holds exactly one season. Reusing another year's entries --
    by merging into them, or by keeping them across a catalogue-only rebuild -- produces
    an index that LOOKS complete while the files behind half its circuits came from a
    different season. There is no way to tell afterwards, so it is refused up front.
    """
    prev_year = str(prev.get("year") or "")
    if prev_year and prev_year != active_year():
        raise SystemExit(
            f"the existing index was built from {prev_year} and this run is "
            f"{active_year()}. Artifact names carry no year, so the two cannot share "
            f"{OUT_DIR}. Re-run a FULL build with --fresh to replace it, or build into "
            f"a different output directory. ({how})")
    if not prev_year:
        print("(the existing index predates year stamping; assuming it is "
              f"{active_year()} -- rebuild with --fresh if it is not)")


def referenced_files(manifest: dict, index_name: str) -> set:
    """Every artifact the freshly written index can reach, by filename."""
    live = {"index.json", index_name}
    for key in ("catalogue", "params", "rules"):
        if manifest.get(key):
            live.add(manifest[key])
    live.update(manifest.get("tracks", {}).values())
    for per_session in manifest.get("sessions", {}).values():
        for files in per_session.values():
            live.add(files["manifest"])
            live.add(files["bin"])
    return live


def prune(manifest: dict, index_name: str) -> tuple:
    """Delete artifacts no longer reachable from the index. Returns (count, bytes).

    Artifact names are content hashes and a build never overwrites: every rebuild of a
    circuit writes a NEW file and simply stops referencing the old one. Left alone the
    directory grows without bound -- measured at 199 files / 326 MB where the index
    reached only 54 files / 173 MB, so more than half was superseded output.

    Reachability is computed from the index that was just written, which is the same set
    the frontend can request, so anything removed here was already unreachable from the
    app. Superseded index.<hash>.json files go too: index.json names the current one and
    nothing reads the others.
    """
    live = referenced_files(manifest, index_name)
    removed, freed = 0, 0
    for path in sorted(OUT_DIR.iterdir()):
        if not path.is_file() or path.name in live:
            continue
        size = path.stat().st_size
        path.unlink()
        removed += 1
        freed += size
    return removed, freed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default=DEFAULT_YEAR,
                    help=f"which raw mirror to build from (default {DEFAULT_YEAR}). "
                          f"Years with a mirror on disk: "
                          f"{', '.join(available_years()) or 'none found'}")
    ap.add_argument("--events", nargs="*", default=["British Grand Prix"])
    ap.add_argument("--sessions", nargs="*", default=["Race", "Sprint"])
    ap.add_argument("--catalogue-only", action="store_true",
                    help="rebuild only the catalogue and re-point the existing index at it; "
                          "track models and replay packs are left alone (minutes -> a second)")
    ap.add_argument("--all", action="store_true",
                    help="build every event found in the year mirror")
    ap.add_argument("--jobs", type=int, default=1,
                    help="build this many EVENTS concurrently, in separate processes "
                          "(events are independent; the index is still written once, by "
                          "the parent). 0 means one per core, capped to leave headroom.")
    ap.add_argument("--prune", action="store_true",
                    help="after writing the index, delete artifacts it no longer "
                          "references (superseded rebuilds of the same circuit, and old "
                          "index files). Only ever removes files already unreachable "
                          "from index.json")
    ap.add_argument("--fresh", action="store_true",
                    help="start the index from empty instead of merging into the existing "
                          "one; use when removing an event, never for a routine subset build")
    args = ap.parse_args()

    # Before any worker is spawned: set_year writes the environment those workers
    # inherit, which is how a --jobs N build stays on one mirror (simdata/paths.py).
    set_year(args.year)
    root = data_root()
    print(f"raw mirror: {layout_note()}")
    if not root.is_dir():
        found = ", ".join(available_years()) or "none"
        ap.error(f"no raw mirror for {active_year()} at {root}. Years on disk: {found}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Merge into whatever the index already lists rather than starting empty. Building a
    # SUBSET used to silently drop every other event: `--events "British Grand Prix"`
    # rewrote the index with one track, and the twelve other circuits -- whose artifacts
    # were still sitting on disk -- simply disappeared from the app. The artifacts are
    # content-hashed, so re-listing them costs nothing and is always correct; --fresh is
    # there for the rare case where an event really should be removed.
    manifest = {"tracks": {}, "sessions": {}}
    if not args.fresh:
        pointer_path = OUT_DIR / "index.json"
        if pointer_path.exists():
            try:
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                prev = json.loads((OUT_DIR / pointer["latest"]).read_text(encoding="utf-8"))
                assert_year_matches(prev, "merging into the existing index")
                manifest["tracks"] = dict(prev.get("tracks") or {})
                manifest["sessions"] = {k: dict(v) for k, v in (prev.get("sessions") or {}).items()}
                kept = len(manifest["tracks"])
                if kept:
                    print(f"(merging into existing index: {kept} track(s) already listed)")
            except (OSError, KeyError, json.JSONDecodeError) as exc:
                print(f"(could not read existing index, starting fresh: {exc})")

    if args.catalogue_only:
        pointer = json.loads((OUT_DIR / "index.json").read_text(encoding="utf-8"))
        manifest = json.loads((OUT_DIR / pointer["latest"]).read_text(encoding="utf-8"))
        # Independently of the merge block above, which --fresh skips: this branch keeps
        # every existing track and session entry and only swaps the catalogue, so a
        # differing --year would relabel another season's artifacts as this one.
        assert_year_matches(manifest, "--catalogue-only reuses the existing track entries")
        print("== catalogue (only) ==")
        manifest["year"] = active_year()
        manifest["catalogue"] = write_json(build_catalogue(), OUT_DIR, "catalogue")
        print(f"  {manifest['catalogue']}")
        manifest["rules"] = write_json(build_rules(), OUT_DIR, "rules")
        print(f"  {manifest['rules']}")
        index_name = write_json(manifest, OUT_DIR, "index")
        (OUT_DIR / "index.json").write_bytes(
            json.dumps({"latest": index_name}, separators=(",", ":")).encode("utf-8"))
        print(f"\nindex: {index_name} (pointer: index.json)")
        if args.prune:
            removed, freed = prune(manifest, index_name)
            print(f"pruned {removed} unreferenced artifact(s), {freed / 1e6:.1f} MB")
        return

    manifest["year"] = active_year()

    print("== catalogue ==")
    cat = build_catalogue()
    manifest["catalogue"] = write_json(cat, OUT_DIR, "catalogue")
    print(f"  {manifest['catalogue']}")

    print("== rules ==")
    manifest["rules"] = write_json(build_rules(), OUT_DIR, "rules")
    print(f"  {manifest['rules']}")

    print("== fitted parameters ==")
    params = build_params()
    manifest["params"] = write_json(params, OUT_DIR, "params")
    print(f"  {manifest['params']}")

    events = list(args.events)
    if args.all:
        events = discover_events(list(args.sessions))
        print(f"discovered {len(events)} buildable event(s) for sessions "
              f"{', '.join(args.sessions)}")
    jobs = args.jobs if args.jobs > 0 else max(1, min(12, (os.cpu_count() or 4) - 2))
    jobs = max(1, min(jobs, len(events)))

    started = time.time()
    results = []
    if jobs == 1:
        for event in events:
            print(f"== {event} ==", flush=True)
            # Report a failed event and carry on, exactly as the process-pool branch
            # below already does. Without this the serial path -- which is the DEFAULT,
            # --jobs 1 -- aborts the whole run on the first event that cannot be built,
            # after every earlier event's artifacts have already been written to
            # OUT_DIR but BEFORE the index is written: measured with `--all`, which
            # sweeps in "Spanish Grand Prix" (Practice 1/2 only, no Race or Qualifying,
            # so build_track_model raises "no session with usable geometry"). That left
            # thirteen freshly built track models on disk with index.json still
            # pointing at the previous build.
            try:
                results.append(build_one_event(event, list(args.sessions)))
            except Exception as exc:              # noqa: BLE001 - surfaced, not hidden
                print(f"  FAILED {event}: {type(exc).__name__}: {exc}", flush=True)
    else:
        print(f"== building {len(events)} events on {jobs} processes ==", flush=True)
        with _futures.ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(build_one_event, ev, list(args.sessions)): ev
                       for ev in events}
            for fut in _futures.as_completed(futures):
                ev = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as exc:              # noqa: BLE001 - surfaced, not hidden
                    print(f"  FAILED {ev}: {type(exc).__name__}: {exc}", flush=True)

    for r in sorted(results, key=lambda r: r["slug"]):
        manifest["tracks"][r["slug"]] = r["track"]
        for session, files in r["sessions"].items():
            manifest["sessions"].setdefault(r["slug"], {})[session] = {
                "manifest": files["manifest"], "bin": files["bin"],
            }
        sess = ", ".join(f"{s} {files['bytes'] // 1024} KB"
                         for s, files in sorted(r["sessions"].items())) or "no sessions"
        print(f"  {r['slug']:<24} ring={r['ring']:>7.0f} m  {sess}")
        for err in r["errors"]:
            print(f"    ERROR {err}")

    print("")
    print(f"{len(results)} event(s) in {time.time() - started:.0f}s on {jobs} process(es)")

    index_name = write_json(manifest, OUT_DIR, "index")
    # a stable, un-hashed pointer so the app always knows where to start
    (OUT_DIR / "index.json").write_bytes(
        json.dumps({"latest": index_name}, separators=(",", ":")).encode("utf-8"))
    print(f"\nindex: {index_name} (pointer: index.json)")

    if args.prune:
        removed, freed = prune(manifest, index_name)
        print(f"pruned {removed} unreferenced artifact(s), {freed / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
