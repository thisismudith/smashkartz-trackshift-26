# Historical C1/C7 Spine: 2022–2025 Non-British Race/Sprint

## Overview

Built a portable, repeatable historical-data preparation pipeline for 2022–2025 non-British F1 Race and Sprint sessions. Every manifest records source year, event, schema, geometry/rule version, git commit, output rows, and explicit skip/failure reasons. British GP is excluded by default (leave-one-track-out via `DemoScope.TRACK`). Historical DRS is preserved as an OBSERVED covariate only — never named as 2026 Overtake state.

**Branch**: `teammate/historical-c1-c7-spine`, created from `origin/main` @ `df7665934d`.

---

## 1. Historical Availability Audit

Run via:
```bash
python scripts/data/materialize_historical_spine.py --audit-only
```

Raw mirrors are resolved per-season by their **declared** season (git remote → README → dir name), not by directory name. This unblocks machines with mirrors elsewhere or under mismatched directory names (e.g., `data/raw/tracinginsights/2027` that actually holds 2025).

| Year | Mirror used | Sessions on disk | Buildable | Race | Sprint | Telemetry laps | Skipped | Skip codes |
|---|---|---|---|---|---|---|---|---|
| 2022 | `data/raw/tracinginsights/2022` | 25 | 12 | 11 | 1 | 12,579 | 13 | `NO_GEOMETRY_CONFIG`×12, `EXCLUDED_EVENT`×1 |
| 2023 | `data/raw/tracinginsights/2023` | 28 | 13 | 11 | 2 | 13,577 | 15 | `NO_GEOMETRY_CONFIG`×14, `EXCLUDED_EVENT`×1 |
| 2024 | `data/raw/tracinginsights/2024` | 30 | 15 | 12 | 3 | 15,084 | 15 | `NO_GEOMETRY_CONFIG`×14, `EXCLUDED_EVENT`×1 |
| 2025 | `data/2025` | 24 | 12 | 12 | 0 | 13,746 | 18 | `NO_GEOMETRY_CONFIG`×11, `SESSION_ONLY_IN_UNUSABLE_MIRROR`×6, `EXCLUDED_EVENT`×1 |

### Key Findings

- **Geometry coverage is the bottleneck**: only 14 circuits (all 2026-calendar) have a `config/geometry/*.yaml` segment map. Historical-only events (Abu Dhabi, Azerbaijan, Bahrain, Emilia Romagna, French, Las Vegas, Mexico City, Qatar, Saudi Arabian, Singapore, São Paulo, United States) have none, so C1 cannot assign `segment_id` — recorded as `NO_GEOMETRY_CONFIG`.

- **`data/raw/tracinginsights/2027` is actually season 2025**: its git remote points at `TracingInsights/2025.git` and README says "2025 Public F1 telemetry files." It holds the only 2025 Sprint sessions (Belgium, China, Miami, Qatar, São Paulo, USA). Since its directory name disagrees with its declared season, it's refused by default (`--allow-directory-mismatch` overrides) — surfaced as `SESSION_ONLY_IN_UNUSABLE_MIRROR` with the migration path named in the reason.

- **Rules config**: only `config/rules/2026/` exists. C1/C7 don't consume event rules (AGENTS.md §21), so this doesn't block them.

- **Race-control metadata**: `rcm.json` is present for every in-scope historical session.

- **British GP**: present and complete in all four years; excluded by design.

---

## 2. Smoke Tests

### 2022 Australian GP (Race, 3 drivers × 30 laps)

```bash
python scripts/data/materialize_historical_spine.py \
  --years 2022 --events "Australian Grand Prix" --sessions Race \
  --drivers VER LEC PER --max-laps-per-driver 30 \
  --run-root data/processed/historical_spine_smoke_2022 \
  --lake-root data/processed/historical_spine_smoke_2022/telemetry_20m \
  --segments-root data/processed/historical_spine_smoke_2022/segments
```

**Result**: `status: COMPLETE`
- Lake: 21,896 rows / 84 accepted laps (6 rejected, structural)
- C1: 2,604 rows
- C7: 2,604 rows, 1,116 `normal_race_model_eligible`

### 2025 Japanese GP (Race, 3 drivers × 30 laps)

```bash
python scripts/data/materialize_historical_spine.py \
  --years 2025 --events "Japanese Grand Prix" --sessions Race \
  --drivers VER NOR PIA --max-laps-per-driver 30 \
  --run-root data/processed/historical_spine_smoke_2025 \
  --lake-root data/processed/historical_spine_smoke_2025/telemetry_20m \
  --segments-root data/processed/historical_spine_smoke_2025/segments
```

**Result**: `status: COMPLETE`
- Lake: 24,202 rows / 84 accepted
- C1: 2,688 rows
- C7: 2,688 rows, 2,304 eligible

Both manifests carry:
- Source year/event/schema/geometry-version/boundary-hash
- Git commit + dirty-file list
- Per-input sha256 hashes
- `race_control_fidelity` audit: C7's race-control signal here is GREEN/UNKNOWN only (no SC/VSC) — counted explicitly, never fabricated

Re-running into the same `--run-root` correctly refuses: `SpineError: ... already contains a completed ... run`.

---

## 3. Full Materialisation Command

```bash
# Full 2022-2025 non-British Race+Sprint spine, 8-way parallel
python scripts/data/materialize_historical_spine.py \
  --years 2022,2023,2024,2025 \
  --run-root data/processed/historical_spine \
  --jobs 8

# Audit only (no writes)
python scripts/data/materialize_historical_spine.py --audit-only
```

**Defaults**:
- Sessions: `Race,Sprint`
- British GP excluded (additive — cannot be removed via `--exclude-event`, independently backstopped by `assert_demo_held_out(..., scope=DemoScope.TRACK)`)
- Lake root: `data/processed/telemetry_20m` (additive across years)
- Segments root: `data/processed/segments` (additive across years)

---

## 4. Changed Files

### New Files
- **`config/raw_sources.yaml`** — portable per-season raw-mirror search config
- **`src/trackshift/data/raw_sources.py`** — mirror resolution by declared season (git remote → README → dir name)
- **`scripts/data/materialize_historical_spine.py`** — orchestrator (raw → lake → C1 → C7)
- **`tests/test_raw_sources.py`** (15 tests) — portability, resolution, selection
- **`tests/test_materialize_historical_spine.py`** (18 tests) — year filtering, skip reasons, British exclusion

### Modified Files
- **`scripts/features/build_segments.py`** — added `--lake` flag (backward-compatible; default unchanged) so the orchestrator can target a non-canonical lake for smoke tests

---

## 5. Test Results

```
.venv/bin/python -m pytest -q
...
14 failed, 1193 passed in 522.84s
```

All 14 failures are in `scripts/simdata/test_build_track.py` / `test_glb_surface.py` — a pre-existing `SurfaceBake.__init__()` signature mismatch in the teammate's in-progress simulator work, already broken before this session and entirely outside this task's scope.

**My 33 new tests** (test_raw_sources.py + test_materialize_historical_spine.py) and every existing test touching C1/C7/guards/registry/build_lake **all pass**.

---

## 6. Implementation Notes

### Portability (AGENTS.md §5)

- No absolute paths; `ROOT = Path(__file__).resolve().parents[1]`
- All machine-specific settings via CLI flags or environment variables
- Raw mirror search is config-file-driven, not hard-coded
- Tests use synthetic temp directories; no assumptions about real-data location

### Raw Data Immutability (AGENTS.md §7)

- A mirror's season is read from metadata (git remote, README), never inferred or repaired
- Directory/season disagreements are reported with actionable skip reasons, never silently fixed
- The 2025-in-2027 case is surfaced as `SESSION_ONLY_IN_UNUSABLE_MIRROR` with migration path

### British GP Exclusion

- Configured via `--exclude-event` (additive to default, never a replacement)
- Independent backstop: `assert_demo_held_out(..., scope=DemoScope.TRACK)` unconditionally guards every output, even if the flag somehow bypassed

### Historical DRS Era vs 2026 Overtake

- `drs_open` column is OBSERVED and carried through the lake unchanged
- No `overtake_state`, `overtake_eligible`, or `overtake_unavailable_reason` columns are written for historical partitions
- DRS era classification is explicit in the manifest: `"regulation_version": "historical_drs_era"`

### Session Policy

- Race and Sprint only (AGENTS.md §8.1)
- No Practice sessions for 2022–2025
- Portable 14-circuit geometry: australian, austrian, belgian, canadian, chinese, dutch, hungarian, italian, japanese, miami, monaco, spanish, (+ silverstone for 2026 British, held out here)

### Manifest Reproducibility

- `git_provenance`: commit, branch, dirty (bool), dirty_files[:20], python_version, platform, hostname
- Input artifacts hashed via sha256 (1MB blocks)
- Per-unit and per-circuit summaries with fidelity counters
- Run-root freshness guard prevents silent mixing of two builds

---

## 7. Unblocks

This spine unblocks **CP-08: Historical M08 materialisation** — the 2022–2025 race context and eligibility gates for past-season tyre-pace models.

---

## 8. Known Limitations / Out of Scope

- **`config/data_registry.yaml` is stale**: segments and race_context entries show `status: planned` / `schema_version: null` despite 14 built artifacts and known partitioning. This predates historical work and belongs to Rishabh's CP-08 ownership; left untouched.
- **M08/M07/C4/C5 models**: out of scope; handled by downstream CP-08 work
- **Training/calibration**: out of scope; this is data prep only
- **2026 Overtake state**: out of scope; handled by `scripts/rules/apply_overtake_state.py` (CP-20)

---

## Commit & Merge

All work is on branch `teammate/historical-c1-c7-spine`. Ready to merge to `main`.

```bash
git log --oneline origin/main..HEAD
# Shows the historical spine commits

git merge --no-ff main
# Merges back to main with a merge commit for history
```
