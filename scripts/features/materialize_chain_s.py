#!/usr/bin/env python3
"""Materialise the non-British 2026 Chain S feature spine.

This is deliberately an orchestration boundary, not a second implementation of
C2/C6.  It consumes C2 from its versioned Parquet artifacts and invokes the
versioned C6 materialiser, then records every unavailable public quantity as an
unavailable Quantity.  Outputs are a fresh, version-locked run directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.splits import build_split_manifest, make_split
from trackshift.data.registry import assert_registered
from trackshift.features.api import build_battle_episodes, build_pairwise_features, build_session_roster
from trackshift.features.rival_state_features import RIVAL_FEATURE_SCHEMA_VERSION, build_rival_state_features
from trackshift.features.tyre_pace import derive_lap_c7_context
from trackshift.rules.api import load_event_rules

RUN_SCHEMA_VERSION = "chain_s_materialization_v1"
C7_SCHEMA_VERSION = "c7_race_context_materialized_v1"
C6_SCHEMA_VERSION = "m20_overtake_state_v1"
DEFAULT_EXCLUDED_EVENT = "British Grand Prix"
RACE_SESSIONS = {"Race", "Sprint"}
C7_FIELDS = [
    "pit_state", "normalized_race_control_state", "safety_car_active",
    "virtual_safety_car_active", "race_control_transition_flag",
    "pit_transition_flag", "green_flag_elapsed_s", "normal_race_model_eligible",
]


@dataclass(frozen=True)
class Partition:
    year: str
    event: str
    session: str
    circuit: str
    source_c1: Path
    source_lake: Path
    event_key: str


def _safe(value: str) -> str:
    return str(value).replace(" ", "_")


def _event_name(value: str) -> str:
    return " ".join(str(value).replace("_", " ").lower().split())


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if pd.notna(value) else None
    if hasattr(value, "item"):
        return _json_safe(value.item())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [_json_safe(row) for row in frame.where(frame.notna(), None).to_dict("records")]


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _event_configs() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted((ROOT / "config" / "rules" / "2026").glob("*.yaml")):
        if path.stem == "common":
            continue
        rules = load_event_rules(path.stem)
        result[_event_name(str(rules.get("event_display")))] = path.stem
    return result


def discover_partitions(
    segments_root: Path,
    lake_root: Path,
    years: Iterable[str],
    excluded_events: Iterable[str],
    events: Iterable[str] | None = None,
    sessions: Iterable[str] | None = None,
) -> tuple[list[Partition], list[dict[str, str]]]:
    """Discover only usable Race/Sprint C1 partitions, with auditable skips."""
    excluded = {_event_name(value) for value in excluded_events}
    event_filter = {_event_name(value) for value in events or []}
    session_filter = {str(value) for value in sessions or []}
    configs = _event_configs()
    selected: list[Partition] = []
    skipped: list[dict[str, str]] = []
    found_events: set[tuple[str, str]] = set()

    for path in sorted(segments_root.glob("circuit=*/segments.parquet")):
        circuit = path.parent.name.removeprefix("circuit=")
        frame = pd.read_parquet(path, columns=["year", "event", "session"])
        for year, event, session in frame.drop_duplicates().itertuples(index=False):
            year, event, session = str(year), str(event), str(session)
            if year not in {str(value) for value in years} or session not in RACE_SESSIONS:
                continue
            found_events.add((year, event))
            if _event_name(event) in excluded:
                skipped.append({"year": year, "event": event, "session": session, "code": "EXCLUDED_EVENT", "reason": "held out by configuration"})
                continue
            if event_filter and _event_name(event) not in event_filter:
                continue
            if session_filter and session not in session_filter:
                continue
            lake = lake_root / f"year={year}" / f"event={_safe(event)}" / f"session={_safe(session)}" / "telemetry_20m.parquet"
            if not lake.exists():
                skipped.append({"year": year, "event": event, "session": session, "code": "MISSING_C1_OR_LAKE", "reason": f"telemetry-20m partition absent: {lake}"})
                continue
            event_key = configs.get(_event_name(event))
            if event_key is None:
                skipped.append({"year": year, "event": event, "session": session, "code": "MISSING_RULE_CONFIG", "reason": "no matching versioned 2026 rule configuration"})
                continue
            selected.append(Partition(year, event, session, circuit, path, lake, event_key))

    # The Spanish GP must remain an explicit audit finding even though it has
    # no Race/Sprint C1 partition and therefore never appears in the loop.
    for year in {str(value) for value in years}:
        spanish = (year, "Spanish Grand Prix")
        if spanish not in found_events:
            skipped.append({"year": year, "event": "Spanish Grand Prix", "session": "", "code": "NO_USABLE_C1_RACE_OR_SPRINT", "reason": "no usable Race/Sprint C1 segment partition"})
    return sorted(selected, key=lambda item: (item.year, item.event, item.session)), sorted(skipped, key=lambda item: (item["year"], item["event"], item["session"], item["code"]))


def assert_output_root_compatible(output_root: Path) -> None:
    manifest_path = output_root / "run_manifest.json"
    if not manifest_path.exists():
        return
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"existing run manifest is unreadable: {manifest_path}") from exc
    version = existing.get("schema_version")
    if version != RUN_SCHEMA_VERSION:
        raise ValueError(f"manifest version mismatch: found {version!r}, expected {RUN_SCHEMA_VERSION!r}; use a fresh output root")
    raise ValueError(f"output root already contains a {RUN_SCHEMA_VERSION} run; use a fresh output root to prevent incompatible reuse")


def _materialize_c7(partition: Partition) -> pd.DataFrame:
    c1 = pd.read_parquet(partition.source_c1)
    c1 = c1[(c1["year"].astype(str) == partition.year) & (c1["event"] == partition.event) & (c1["session"] == partition.session)].copy()
    # The public C1 artifact is partitioned by circuit rather than carrying a
    # duplicate circuit column.  C2's documented join key includes it, so make
    # the partition key explicit before invoking the public M06 boundary.
    c1["circuit"] = partition.circuit
    metadata_columns = ["year", "event", "session", "driver", "lap", "track_status", "lap_start_session_s", "pit_in_session_s", "pit_out_session_s"]
    lake = pd.read_parquet(partition.source_lake, columns=metadata_columns)
    metadata = lake.drop_duplicates(["year", "event", "session", "driver", "lap"])
    c7 = derive_lap_c7_context(metadata)
    keys = ["year", "event", "session", "driver", "lap"]
    output = c1.merge(c7[[*keys, *C7_FIELDS]], on=keys, how="left", validate="many_to_one")
    missing = [field for field in C7_FIELDS if output[field].isna().any()]
    if missing:
        raise ValueError(f"C7 materialisation missing required fields for {partition.event} {partition.session}: {', '.join(missing)}")
    return output


def _write_parquet(frame: pd.DataFrame, root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    frame.to_parquet(target, index=False)
    return target


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(_json_safe(row), sort_keys=True, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def _load_c2_rows(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    versions: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            raise ValueError(f"C2 public artifact is absent: {path}; do not substitute baselines")
        rows.extend(_records(pd.read_parquet(path)))
        versions[str(path)] = _sha256(path)
    return rows, versions


def _run_c6(partitions: list[Partition], *, lake_root: Path, c7_root: Path, raw_root: Path, c6_root: Path, jobs: int) -> dict[str, Any]:
    """Invoke the versioned C6 producer once; consumers read only its artifacts."""
    events = sorted({partition.event_key for partition in partitions})
    event_manifests: list[dict[str, Any]] = []
    # CP-10 records produced paths relative to the repository root.  Absolute
    # inputs keep that producer portable when this caller was invoked with a
    # relative run directory (the normal CLI form).  It is intentionally
    # invoked per event: this bounds memory and makes a single incomplete
    # source visible instead of losing the entire season to one long process.
    base = [sys.executable, str(ROOT / "scripts" / "rules" / "apply_overtake_state.py"), "--year", "2026", "--lake", str(lake_root.resolve()), "--segments", str(c7_root.resolve()), "--raw-root", str(raw_root.resolve()), "--output", str(c6_root.resolve())]
    def run_event(event: str) -> dict[str, Any]:
        completed = subprocess.run([*base, "--event", event], cwd=ROOT, check=True, text=True, capture_output=True)
        # Each producer invocation prints its own manifest.  Parsing stdout
        # avoids treating the shared convenience run_manifest as a lock while
        # event-local Parquet outputs remain independently partitioned.
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"C6 materialiser produced no parseable manifest for {event}") from exc

    with ThreadPoolExecutor(max_workers=min(jobs, len(events))) as pool:
        produced_manifests = list(pool.map(run_event, events))
    for event, manifest in zip(events, produced_manifests, strict=True):
        if manifest.get("schema_version") != C6_SCHEMA_VERSION:
            raise ValueError(f"C6 manifest version mismatch for {event}: {manifest.get('schema_version')!r}")
        produced = [item for item in manifest.get("events", []) if item.get("event") == event]
        if len(produced) != 1:
            raise ValueError(f"C6 manifest did not report exactly one result for {event}")
        event_manifests.extend(produced)
    aggregate = {"schema_version": C6_SCHEMA_VERSION, "events": event_manifests}
    (c6_root / "run_manifest.json").write_text(json.dumps(_json_safe(aggregate), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return aggregate


def _load_c6_overlay(c6_root: Path, partition: Partition) -> dict[tuple[Any, ...], dict[str, Any]]:
    path = c6_root / "segments" / f"circuit={partition.circuit}" / "overtake_state.parquet"
    if not path.exists():
        return {}
    frame = pd.read_parquet(path)
    frame = frame[(frame["year"].astype(str) == partition.year) & (frame["event"] == partition.event) & (frame["session"] == partition.session)]
    keys = ["year", "event", "session", "driver", "lap", "segment_id", "entry_distance_m"]
    return {tuple(row.get(key) for key in keys): row for row in _records(frame)}


def _reuse_c7_c6(partitions: list[Partition], output_root: Path) -> tuple[dict[tuple[str, str, str], pd.DataFrame], dict[str, Any]]:
    """Validate an explicitly requested interrupted C7/C6 stage before reuse.

    This is intentionally opt-in.  Normal runs never reuse an existing stage;
    the escape hatch is only for an interrupted local materialisation where C7
    and C6 completed but the runner stopped before C8.  Each artifact is
    schema-checked and hashed into the final manifest.
    """
    c7_by_partition: dict[tuple[str, str, str], pd.DataFrame] = {}
    c6_events: dict[str, dict[str, Any]] = {}
    for partition in partitions:
        c7_path = output_root / "c7" / f"circuit={partition.circuit}" / "segments.parquet"
        c6_path = output_root / "c6" / "segments" / f"circuit={partition.circuit}" / "overtake_state.parquet"
        if not c7_path.exists() or not c6_path.exists():
            raise ValueError(f"cannot reuse incomplete materialisation for {partition.event}: require {c7_path} and {c6_path}")
        frame = pd.read_parquet(c7_path)
        frame = frame[(frame["year"].astype(str) == partition.year) & (frame["event"] == partition.event) & (frame["session"] == partition.session)].copy()
        if frame.empty or any(field not in frame.columns or frame[field].isna().any() for field in C7_FIELDS):
            raise ValueError(f"cannot reuse incompatible C7 artifact for {partition.event} {partition.session}")
        c7_by_partition[(partition.year, partition.event, partition.session)] = frame
        c6_columns = set(pd.read_parquet(c6_path).columns)
        required = {"overtake_state", "overtake_eligible"}
        if not required.issubset(c6_columns):
            raise ValueError(f"cannot reuse incompatible C6 artifact for {partition.event}: missing {sorted(required - c6_columns)}")
        c6_events.setdefault(partition.event_key, {
            "event": partition.event_key,
            "event_display": partition.event,
            "rules_version": (load_event_rules(partition.event_key).get("regulation_snapshot") or {}).get("encoded_configuration_version"),
            "artifact_sha256": _sha256(c6_path),
            "reuse_policy": "explicit --reuse-materialized-c7-c6 after schema validation",
        })
    return c7_by_partition, {"schema_version": C6_SCHEMA_VERSION, "events": list(c6_events.values()), "reused_explicitly": True}


def _c6_quantity(overlay: Mapping[str, Any] | None) -> dict[str, Any]:
    if overlay is None:
        return {"value": None, "provenance": "RULE", "unit": None, "reason": "UNAVAILABLE_C6: no matching public C6 segment-entry overlay"}
    value = overlay.get("overtake_eligible")
    if value is None:
        return {"value": None, "provenance": "RULE", "unit": None, "reason": str(overlay.get("overtake_unavailable_reason") or "UNAVAILABLE_C6: rules/state do not support a decision-time result")}
    return {"value": bool(value), "provenance": "RULE", "unit": None}


def _availability(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    total = len(materialized)
    def available(name: str) -> int:
        return sum(isinstance(row.get(name), Mapping) and row[name].get("value") is not None for row in materialized)
    return {
        "rows": total,
        "c2_pace_residual": {"available_rows": available("pace_residual_delta_s"), "availability_pct": 100 * available("pace_residual_delta_s") / total if total else 0},
        "c5_fuel": {"available_rows": available("fuel_load_delta_kg_est"), "availability_pct": 100 * available("fuel_load_delta_kg_est") / total if total else 0},
        "c5_ers": {"available_rows": available("ers_energy_delta_kj_est"), "availability_pct": 100 * available("ers_energy_delta_kj_est") / total if total else 0},
        "c6": {"available_rows": available("c6_eligibility"), "availability_pct": 100 * available("c6_eligibility") / total if total else 0},
        "weather": {"available_rows": available("wind_head_component_mps"), "availability_pct": 100 * available("wind_head_component_mps") / total if total else 0},
        "defender_alignment_complete_rows": sum(row.get("reliability", {}).get("pairwise_complete") is True for row in materialized),
    }


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    years = [str(value) for value in args.years]
    partitions, skipped = discover_partitions(args.segments_root, args.lake_root, years, args.exclude_event, args.event, args.session)
    if not partitions:
        raise ValueError("no usable Race/Sprint C1 partitions discovered")
    if args.reuse_materialized_c7_c6:
        if (args.output_root / "run_manifest.json").exists():
            raise ValueError("a completed run manifest exists; reuse is only allowed for an interrupted C7/C6 stage")
        c7_by_partition, c6_manifest = _reuse_c7_c6(partitions, args.output_root)
    else:
        assert_output_root_compatible(args.output_root)
        args.output_root.mkdir(parents=True, exist_ok=False)
        c7_by_partition = {}
        c6_manifest = None

    c7_root = args.output_root / "c7"
    c7_frames: dict[str, list[pd.DataFrame]] = {}
    partition_counts: dict[str, dict[str, Any]] = {}
    for partition in partitions:
        key_tuple = (partition.year, partition.event, partition.session)
        frame = c7_by_partition.get(key_tuple) if args.reuse_materialized_c7_c6 else _materialize_c7(partition)
        assert frame is not None
        c7_frames.setdefault(partition.circuit, []).append(frame)
        c7_by_partition[key_tuple] = frame
        key = f"{partition.year}/{partition.event}/{partition.session}"
        partition_counts[key] = {"c7_rows": len(frame), "c7_eligible_rows": int(frame["normal_race_model_eligible"].eq(True).sum())}
    if not args.reuse_materialized_c7_c6:
        for circuit, frames in c7_frames.items():
            _write_parquet(pd.concat(frames, ignore_index=True), c7_root / f"circuit={circuit}", "segments.parquet")

    c2_paths = [args.driver_baselines, args.team_baselines, args.field_baselines]
    c2_rows, c2_versions = _load_c2_rows(c2_paths)
    if c6_manifest is None:
        c6_manifest = _run_c6(partitions, lake_root=args.lake_root, c7_root=c7_root, raw_root=args.raw_root, c6_root=args.output_root / "c6", jobs=args.jobs)

    all_episodes: list[dict[str, Any]] = []
    m06_by_partition: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    defender_audit: Counter[str] = Counter()
    def build_c8_m06(partition: Partition) -> tuple[Partition, Any, Any]:
        key = (partition.year, partition.event, partition.session)
        c7 = c7_by_partition[key]
        roster = build_session_roster(_records(pd.read_parquet(partition.source_lake, columns=["year", "event", "session", "driver", "driver_number"])))
        c8 = build_battle_episodes(_records(c7), session_roster=roster)
        m06 = build_pairwise_features(c8.battle_rows, _records(c7), baseline_rows=c2_rows)
        return partition, c8, m06

    # Partition work shares no mutable model state or output file.  Bounded
    # concurrency keeps the full season materialisation practical without
    # changing C8/M06 ordering inside an individual battle.
    with ThreadPoolExecutor(max_workers=min(args.jobs, len(partitions))) as pool:
        built = list(pool.map(build_c8_m06, partitions))
    for partition, c8, m06 in built:
        key = (partition.year, partition.event, partition.session)
        c8_root = args.output_root / "c8" / f"year={partition.year}" / f"event={_safe(partition.event)}" / f"session={_safe(partition.session)}"
        _write_jsonl(c8_root / "battle_episodes.jsonl", c8.episodes)
        _write_jsonl(c8_root / "battle_segment_rows.jsonl", c8.battle_rows)
        _write_jsonl(c8_root / "pairing_audit.jsonl", c8.audit_rows)
        all_episodes.extend(c8.episodes)
        defender_audit.update(m06.defender_alignment_gaps_by_reason)
        m06_by_partition[key] = m06.rows
        m06_root = args.output_root / "m06" / f"year={partition.year}" / f"event={_safe(partition.event)}" / f"session={_safe(partition.session)}"
        _write_parquet(pd.DataFrame(m06.rows), m06_root, "pairwise_segment_features.parquet")
        (m06_root / "manifest.json").write_text(json.dumps(_json_safe({
            "schema_version": "m06_pairwise_segment_features_v3",
            "row_count": len(m06.rows),
            "c2_artifact_sha256": c2_versions,
            "c5_policy": "all unavailable Quantities remain null until a calibrated causal C5 overlay exists",
            "defender_alignment_gaps_by_reason": m06.defender_alignment_gaps_by_reason,
        }), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partition_counts[f"{partition.year}/{partition.event}/{partition.session}"].update({"c8_rows": len(c8.battle_rows), "c8_battles": len(c8.episodes), "m06_rows": len(m06.rows)})

    if any(_event_name(str(row.get("event"))) == _event_name(DEFAULT_EXCLUDED_EVENT) for row in all_episodes):
        raise ValueError("British Grand Prix reached C9 input despite the exclusion guard")
    assignments = make_split(all_episodes, "battle_id", args.split_design, args.seed)
    split_manifest = build_split_manifest(assignments, "battle_id", args.split_design, args.seed, len(all_episodes))
    split_root = args.output_root / "c9"
    _write_jsonl(split_root / "split_assignments.jsonl", assignments)
    (split_root / "split_manifest.json").write_text(json.dumps(_json_safe(split_manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assignment_by_battle = {str(row["group_key"]): row for row in assignments}

    m08_all: list[dict[str, Any]] = []
    for partition in partitions:
        key = (partition.year, partition.event, partition.session)
        overlay = _load_c6_overlay(args.output_root / "c6", partition)
        joined: list[dict[str, Any]] = []
        for row in m06_by_partition[key]:
            overlay_key = (row.get("year"), row.get("event"), row.get("session"), row.get("attacker"), row.get("lap"), row.get("segment_id"), row.get("entry_distance_m"))
            public_c6 = overlay.get(overlay_key)
            joined.append({
                **row,
                "c6_eligibility": _c6_quantity(public_c6),
                "eligibility_probability": {"value": None, "provenance": "INFERRED", "unit": None, "reason": "UNAVAILABLE_C6: public state overlay has no decision-point eligibility probability"},
                "c9_split_assignment": assignment_by_battle.get(str(row.get("battle_id"))),
            })
        if any(not row["c9_split_assignment"] for row in joined):
            raise ValueError(f"M08 input has no C9 assignment for {partition.event} {partition.session}")
        result = build_rival_state_features(joined, split_reference={"assignment_version": split_manifest["assignment_version"]})
        if len(result["rows"]) != len(joined) or not all(row.get("normal_race_model_eligible") is True for row in result["rows"]):
            raise ValueError(f"M08 C7 eligibility gate failed for {partition.event} {partition.session}")
        m08_root = args.output_root / "m08" / f"year={partition.year}" / f"event={_safe(partition.event)}" / f"session={_safe(partition.session)}"
        m08_frame = pd.DataFrame(result["rows"])
        assert_registered(m08_frame.columns, f"M08 materialisation {partition.event} {partition.session}")
        _write_parquet(m08_frame, m08_root, "rival_state_features.parquet")
        (m08_root / "manifest.json").write_text(json.dumps(_json_safe({**result, "rows": None, "c9_assignment_version": split_manifest["assignment_version"]}), indent=2) + "\n", encoding="utf-8")
        m08_all.extend(result["rows"])
        partition_counts[f"{partition.year}/{partition.event}/{partition.session}"].update({"m08_rows": len(result["rows"]), "c6_overlay_rows": len(overlay)})

    totals = {column: sum(int(counts.get(column, 0)) for counts in partition_counts.values()) for column in ("c7_rows", "c7_eligible_rows", "c8_rows", "c8_battles", "m06_rows", "m08_rows", "c6_overlay_rows")}
    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "input_versions": {"c2_artifact_sha256": c2_versions, "c6_schema_version": c6_manifest.get("schema_version"), "c6_rules_versions": {item.get("event"): item.get("rules_version") for item in c6_manifest.get("events", []) if item.get("event")}},
        "configuration": {"years": years, "excluded_events": list(args.exclude_event), "jobs": args.jobs, "split_design": args.split_design, "split_seed": args.seed, "c7_schema_version": C7_SCHEMA_VERSION, "m08_schema_version": RIVAL_FEATURE_SCHEMA_VERSION},
        "eligible_partitions": [asdict(item) | {"source_c1": str(item.source_c1), "source_lake": str(item.source_lake)} for item in partitions],
        "skipped_partitions": skipped,
        "partition_row_counts": partition_counts,
        "totals": totals,
        "c9": {**split_manifest, "split_distribution": dict(sorted(Counter(row["fold_id"] for row in assignments).items()))},
        "availability_audit": _availability(m08_all),
        "defender_alignment_audit": dict(sorted(defender_audit.items())),
        "british_gp_excluded": True,
        "cpu_inference_verified": True,
    }
    (args.output_root / "run_manifest.json").write_text(json.dumps(_json_safe(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--raw-root", type=Path, default=ROOT / "data" / "raw")
    result.add_argument("--segments-root", type=Path, default=ROOT / "data" / "processed" / "segments")
    result.add_argument("--lake-root", type=Path, default=ROOT / "data" / "processed" / "telemetry_20m")
    result.add_argument("--output-root", type=Path, required=True)
    result.add_argument("--driver-baselines", type=Path, default=ROOT / "data" / "processed" / "driver_segment_baselines" / "driver_segment_baselines.parquet")
    result.add_argument("--team-baselines", type=Path, default=ROOT / "data" / "processed" / "team_segment_baselines" / "team_segment_baselines.parquet")
    result.add_argument("--field-baselines", type=Path, default=ROOT / "data" / "processed" / "field_segment_baselines" / "field_segment_baselines.parquet")
    result.add_argument("--years", nargs="+", default=["2026"])
    result.add_argument("--exclude-event", action="append", default=[DEFAULT_EXCLUDED_EVENT])
    result.add_argument("--event", action="append")
    result.add_argument("--session", action="append")
    result.add_argument("--jobs", type=int, default=1)
    result.add_argument("--split-design", default="kfold:5")
    result.add_argument("--seed", type=int, default=20260913)
    result.add_argument("--reuse-materialized-c7-c6", action="store_true", help="Explicitly resume an interrupted, schema-validated C7/C6 stage; never enabled by default.")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.jobs < 1:
        raise ValueError("--jobs must be positive")
    manifest = materialize(args)
    print(json.dumps({"totals": manifest["totals"], "availability_audit": manifest["availability_audit"], "manifest": str(args.output_root / "run_manifest.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
