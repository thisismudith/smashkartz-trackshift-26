# TrackShift — Tanveer's Build Checkpoints

Execution plan for **Owner B (Tanveer)**: Chain R (rules), Chain P (pass probability), Chain E (energy/physics), and the shared foundations M01, M03, M04, M30, M33, plus M28 and M31.

- `TrackShift AGENTS.md` — engineering contract (what the system is). Section refs below are `§N` in that file.
- `MODELS.md` — ownership split, inventory IDs (M01–M34), interface contracts (C1–C10).
- `API.md` — the UI-facing surface every model must eventually expose.
- `CHECKPOINTS_RISHABH.md` — Owner A's plan (Chains S and V, foundations M02, M05, M06, M29).
- `CHECKPOINTS_INTEGRATION.md` — the joint integration plan, owned by Rishabh.
- **`CHECKPOINTS_TANVEER.md`** (this file) — the ordered checkpoints to actually build them.

**Scope: Owner B only.** Every checkpoint here is Tanveer's to build. Rishabh's items (M02, M05, M06, M08, M09, M22–M27, M29) appear only where they block or unblock you, and are labelled as such.

> ⚠️ **CP numbers are per-owner and they collide.** Each plan numbers its own
> checkpoints from CP-00, so the same number means different work in each file.
> Rishabh's CP-01 is race-context and the C7 contract; **this file's CP-01 is the
> local data audit.** They are unrelated.
>
> Never read a completed CP-N in `CHECKPOINTS_RISHABH.md` as progress against
> CP-N here. Always name the owner ("Rishabh's CP-04", "Tanveer's CP-04").
> Rishabh's work matters to this plan only where it delivers a named contract
> (C7, C8, C9) that a checkpoint here depends on — cite the **contract**, never
> his checkpoint number. Tick boxes only in the tracker below.

Written against `TrackShift AGENTS.md` at commit `7d2f5fd`.

---

## 0. How to use this file

Each checkpoint has the same shape:

| Field | Meaning |
|---|---|
| **Goal** | The one thing that must be true when it is done |
| **Depends on** | Checkpoints that must be complete first |
| **Dataset** | Exact scope: years, events, sessions, drivers, expected row counts |
| **Steps** | Concrete, runnable |
| **Models & parameters** | What to fit, with starting hyperparameters |
| **✅ Check** | Numeric acceptance gates — do not proceed until these pass |
| **⚠️ If output is bad** | Symptom → likely cause → fix |
| **Deliverables** | Files that must exist |

**Two rules that apply to every checkpoint:**

1. **Never touch the 2026 British Grand Prix during development.** It is the demo event *and* the final held-out test (§40, MODELS.md §7.6). Use `--exclude-event "British Grand Prix"` on every training run until the model and feature set are frozen. There is a guard script in CP-01 that fails the build if BGP rows appear in a training split.
2. **Every artifact writes a manifest** with git commit, source datasets, config, schema version, feature schema, seed, and `cpu_inference_verified` (§53, MODELS.md §6.4).

### Progress tracker

| CP | Item | IDs | Status |
|---|---|---|---|
| 00 | Environment and dependencies | — | ✅ |
| 01 | Local data audit | §51, §63 | ✅ |
| 02 | Registries scaffold | M31 | ✅ |
| 03 | Rule config skeleton + **speed-dependent power envelope**, all tracks | M18 | ✅ |
| 04 | Build the 20 m lake | Phase 2 | ✅ |
| 05 | **Track segmentation — freeze `segment_id`** | M03 | ✅ |
| 06 | Track-relative weather | M33 | ✅ |
| 07 | Practice lap classifier | M01 | ◐ |
| 08 | Tyre degradation and normalised pace | M30 | ◐ |
| 09 | Segment baselines | M04 | ✅ |
| 10 | Overtake state machine | M20 | ✅ |
| 11 | Rule engine (owns the envelope evaluator) | M19 | ✅ |
| 12 | Eligibility probability | M21 | ✅ |
| 13 | Overtake-opportunity dataset | M07 | ◐ |
| 14 | Pass-model benchmark | M10 | ☐ |
| 15 | Probability calibration | M11 | ☐ |
| 16 | Ensemble spread | M12 | ☐ |
| 17 | Regulation-era handling | M13 | ☐ |
| 18 | Energy twin | M14 | ✅ |
| 18b | **Override / ERS-mode discriminator** | M35 | ✅ |
| 19 | Fuel-load estimator | M34 | ✅ |
| 20 | Physics calibration hierarchy | M15 | ✅ |
| 21 | Segment-time model (ΔE→Δt) | M16 | ✅ |
| 22 | Physics uncertainty | M17 | ✅ |
| 23 | Ablation harness | M28 | ☐ |
| 24 | Service routes and replay bundle | API.md | ☐ |

✅ built, its outputs exist on disk, and its acceptance gates have been run
and recorded. ◐ code merged but the outputs do not exist yet. ☐ not started.
Chain E re-verified 2026-09-13; the rest 2026-09-12, against what is actually on
disk, not against what has been committed — four of these checkpoints had code on
`main` and no outputs at all, which reads as done until you look.

**A tick means measured, not that every gate is met.** Four Chain E checkpoints
carry a tick with gates still outstanding, and the outstanding gate is named in
each one's entry rather than left for a reader to find: **CP-20** has no
accepted calibrated rung and sits above its floor, **CP-21**'s `a_k` profile is
inverted against §30, **CP-22** inherits CP-20's fit, and **CP-18b** measures a
10.87% false-positive rate that follows from CP-20. They are complete as
components and honest about what they produce; the open work is upstream in the
fit, and is listed under **Next action** in the Chain E section.

**Owner drift, resolved for CP-07 and CP-08.** Rishabh took CP-06 through CP-10
from this plan onto his own branches (`rishabh/takeover-*`). Those branches are
merged into `main`: his `17e9bc3` practice-lap classifier core is the file the
section 9 widening edits, so the two are one lineage rather than rival
implementations. `git log -- src/trackshift/track/lap_classifier.py` shows both
commits and nothing else. The remaining takeovers are still Owner B contracts and
are still checked against the gates below; agree ownership before starting one.

---

## What is actually on your disk

Verified 2026-09-12, so you can size the work rather than guess.

```text
data/raw/tracinginsights/{2022,2023,2024,2025,2026}/    177,288 telemetry files
```

| Year | Telemetry files | Notes |
|---|---|---|
| 2022 | 31,854 | DRS era |
| 2023 | 35,473 | DRS era |
| 2024 | 37,959 | DRS era |
| 2025 | 37,666 | DRS era |
| 2026 | 34,336 | Overtake era, current regulations |

**2026 events on disk: 14 Grands Prix + 3 pre-season tests.** Sprint weekends (5 sessions) are British, Canadian, Chinese, Dutch, Miami. The rest have Practice 1 / Qualifying / Race. The Spanish Grand Prix directory has only Practice and no `corners.json` — treat it as incomplete.

Historical events (2022–2025) have **Qualifying and Race only** — no Practice. This matters: physics calibration (§9) can only use 2026 Practice 1.

**Per-session files:** `corners.json`, `drivers.json`, `rcm.json`, `weather.json`, `session_laptimes.json`, then one directory per driver holding `laptimes.json` and `<lap>_tel.json`.

### Raw field reference

`tel.json` — columnar arrays, one entry per sample:

```text
time distance rel_distance speed rpm gear throttle brake drs
x y z acc_x acc_y acc_z DriverAhead DistanceToDriverAhead dataKey
```

Sample lap (HAM, 2026 British GP, Race, lap 10): **715 rows, 5,819 m, median Δt 0.129 s** (min 0.008 s, max 1.011 s). At 20 m spacing that lap yields **291 rows**.

`laptimes.json` — per lap: `time lap sesT lST s1 s2 s3 vi1 vi2 vfl vst compound life fresh stint pos status pb pin pout iacc del delR drv dNum team` plus a weather snapshot `wT wAT wH wP wR wTT wWD wWS`.

`weather.json` — `wT` (session time), `wAT` (air temp), `wH` (humidity), `wP` (pressure), `wR` (rain bool), `wTT` (track temp), `wWD` (wind bearing), `wWS` (wind speed). 156 samples per race session.

`corners.json` — **columnar**, keys `CornerNumber X Y Angle Distance` as arrays plus a scalar `Rotation`. Silverstone: 18 corners at 463…5,674 m, `Rotation = 92.0`. Monaco 19 corners, `Rotation = 315.0`. Italy 11 corners, `Rotation = 95.0`.

`rcm.json` — **columnar**, keys `time cat msg status flag scope sector dNum lap`. 2026 British GP Race has 248 messages: `cat` ∈ {Flag 175, Other 65, SafetyCar 8}; `flag` ∈ {BLUE, CLEAR, YELLOW, DOUBLE YELLOW, BLACK AND WHITE, GREEN, CHEQUERED}.

### Three findings that shape the plan

**1. The 2026 `drs` channel is dead.** Across 60 sampled 2026 British GP laps, `drs` is `0` in every single sample. The same sampling on 2022 gives 11/40 laps with non-zero DRS. §20 already forbids inferring Overtake eligibility from this channel; the data confirms it is not merely wrong but empty. `drs_open_observed` (§11) must stay **unavailable** for 2026.

**2. `rcm.json` is your race-control Overtake source.** Every 2026 event carries `OVERTAKE ENABLED` / `OVERTAKE DISABLED` messages — 51 enable and 23 disable events across the season. The 2022 equivalent is `DRS ENABLED` / `DRS DISABLED`. This is where the rule engine's `race_control.overtake_disabled` state comes from, and it is genuinely `OBSERVED` → `RULE`.

**3. Detection and Activation line positions are nowhere in the raw data.** A regex over every 2026 `rcm.json` for `DETECTION|ACTIVATION|ZONE|MGU|ENERGY` returns nothing but 46 `SAFETY CAR DEPLOYED` hits. These values must come from FIA documents (§21). CP-03 handles this honestly with a two-tier provenance scheme.

### Track status codes

`laptimes.json.status` is a **string of concatenated single-digit status codes** that occurred during that lap, not one code. HAM's 2026 British GP race gives `'1'` on 41 laps and `'12'`, `'167'`, `'126'`, `'671'`, `'24'`, `'4'` on the rest — laps 49–52 are `'4'` (Safety Car), matching the two `SAFETY CAR DEPLOYED` messages in `rcm.json`.

| Digit | Meaning |
|---|---|
| 1 | Track clear (green) |
| 2 | Yellow flag |
| 4 | Safety Car |
| 5 | Red flag |
| 6 | Virtual Safety Car deployed |
| 7 | VSC ending |

Decode by iterating characters, not by parsing the integer. `'167'` means green, VSC deployed, and VSC ending all occurred within that lap. **Any lap whose status contains anything other than `1` is not `normal_race_model_eligible`** — this is your primary cross-check against Rishabh's M02 (C7).

---

# CP-00 — Environment and dependencies

**Goal:** a reproducible `.venv` that every later checkpoint runs in, with a `requirements.txt` committed so the A6000 and Rishabh's machine can rebuild it byte-for-byte.

**Depends on:** nothing. **This is a hard blocker** — `build_phase2_dataset.py` raises `SystemExit` without `pyarrow`, which is not installed.

Current state: system Python 3.13.14, no venv. Present: `pandas 3.0.1`, `numpy 2.4.3`, `pyyaml`, `pytest`, `fastapi`, `pydantic`, `uvicorn`. **Missing: `pyarrow`, `scipy`, `scikit-learn`, `lightgbm`, `xgboost`, `catboost`, `matplotlib`, `duckdb`.**

### Steps

**1. Create the venv** (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If activation is blocked: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for that session only, or call `.\.venv\Scripts\python.exe` directly everywhere.

**2. Write `requirements.txt`** at the repo root:

```text
# Core data
pandas>=2.2,<4
numpy>=1.26,<3
pyarrow>=16
duckdb>=1.0

# Physics and stats
scipy>=1.13
scikit-learn>=1.5
statsmodels>=0.14

# Tree models (Chain P)
lightgbm>=4.3
xgboost>=2.1
catboost>=1.2

# Config, service, testing
PyYAML>=6
pydantic>=2.7
fastapi>=0.110
uvicorn>=0.29
pytest>=8

# Plots for validation reports
matplotlib>=3.8
```

`torch` is deliberately **not** here — it is only needed for the optional MLP (M10) and neural regressor (M16), and only on the A6000. Keep it in `requirements-gpu.txt`:

```text
--extra-index-url https://download.pytorch.org/whl/cu124
torch>=2.3
```

**3. Install and freeze:**

```powershell
pip install -r requirements.txt
pip freeze > requirements.lock.txt
```

Commit `requirements.txt` and `requirements.lock.txt`. The lock file is what the A6000 installs from, so the two environments cannot drift.

**4. Add `.venv/` to `.gitignore`.**

### ✅ Check

```powershell
python -c "import pandas, pyarrow, scipy, sklearn, lightgbm, xgboost, catboost; print('all import OK')"
python -m pytest -q          # expect: 6 passed
python -c "import pandas as pd; pd.DataFrame({'a':[1]}).to_parquet('_t.parquet'); print(pd.read_parquet('_t.parquet').shape)"; rm _t.parquet
```

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| `lightgbm`/`catboost` wheel fails on 3.13 | Wheels lag new Python releases | Build the venv on Python 3.12: `py -3.12 -m venv .venv`. Pin `python_requires` in the README so the team matches. |
| `pyarrow` install is very slow | Building from source | Force a wheel: `pip install --only-binary :all: pyarrow` |
| `pandas 3.0` API breaks the existing scripts | Pandas 3 removed some 2.x behaviour | Pin `pandas>=2.2,<3` in `requirements.txt` and reinstall. The Phase 2 script only uses `DataFrame` + `to_parquet`, so this is unlikely, but the tests will tell you. |

### Deliverables

`requirements.txt`, `requirements.lock.txt`, `requirements-gpu.txt`, `.gitignore` updated, `.venv/` working.

### ✅ Completed

All wheels resolved on **Python 3.13.14** with no source build, so the 3.12 fallback in the table above was not needed and that warning is now stale. 50 packages pinned in `requirements.lock.txt`.

Verified: `all import OK`; `17 passed` (6 before merging `origin/main`, plus Rishabh's `test_race_context.py` and `test_splits.py`); Parquet round-trip `(1, 1)`; and the real blocker cleared — `build_phase2_dataset.py` now writes Parquet (2026 British GP Race, HAM+ANT, 3 laps each: 6 discovered, 4 accepted, **1162 rows**).

Two defects found and fixed while closing this checkpoint:

- `pip freeze >` under PowerShell wrote `requirements.lock.txt` as **UTF-16LE**, which git stored as binary (`Bin 0 -> 1784 bytes`, zero insertions) — a lock file with no reviewable diff. Converted to UTF-8/LF and pinned via `.gitattributes`.
- `data/processed/` was **not** gitignored. One full session is 300,809 rows; CP-04 builds the whole lake. Added `data/processed/` and `data/interim/`.

---

# CP-01 — Local data audit

**Goal:** a machine-readable inventory of what is on disk, and the guard that keeps the demo event out of training. §51 and §63 both make this the prerequisite for everything.

**Depends on:** CP-00.

**Dataset:** all five years, all events, all sessions — read-only.

### Steps

**1. Run the existing audit** (it already exists and is correct):

```powershell
python scripts/audit_raw_data.py --raw-root data/raw/tracinginsights --output artifacts/schema_audit
```

Note the `--raw-root`: the script defaults to `data/raw`, but your mirror is at `data/raw/tracinginsights` per §7.

**2. Extend it** — the current script covers telemetry schema but not the session-level inventory you need for scoping. Add `scripts/data/inventory.py` producing `artifacts/schema_audit/inventory.csv` with one row per (year, event, session): driver count, lap-file count, presence of each session file, total bytes, and for 2026 the count of `OVERTAKE ENABLED`/`DISABLED` messages.

**3. Write the demo-event guard** at `src/trackshift/data/guards.py`:

```python
DEMO = {"year": "2026", "event": "British Grand Prix"}

def assert_demo_held_out(df, context: str) -> None:
    """Raise if the frozen demo event leaked into a training or calibration split."""
    hit = df[(df["year"].astype(str) == DEMO["year"]) & (df["event"] == DEMO["event"])]
    if len(hit):
        raise RuntimeError(
            f"{context}: {len(hit)} rows from the held-out demo event "
            f"({DEMO['year']} {DEMO['event']}). See AGENTS.md section 40."
        )
```

Call it at the top of every `scripts/train/*.py`. A test in CP-14 asserts it fires.

### ✅ Check

File names below are what the script actually writes; the earlier draft of this
checkpoint listed four names that do not exist (`field_inventory.csv`,
`events.csv`, `lap_quality.csv`, `summary.json`).

- `artifacts/schema_audit/` contains `field_availability.csv`, `events_sessions.csv`, `data_quality_summary.csv`, `repository_summary.json`, `schema_differences.json`, `canonical_schema.json`, plus `inventory.csv` and `inventory.json` from `inventory.py`
- `repository_summary.json` shows `partial_audit: false`, five years `present`, and `malformed: []`
- `data_quality_summary.csv` row count = **177,288** (cross-checks against the download manifests, which report the same total)
- `distance_monotonic` is true for **>99%** of laps — anything worse means the validator will reject at scale
- `inventory.csv` lists **13 complete 2026 GPs**, not 14. The mirror holds 17 2026 event directories: 13 complete Grands Prix, 3 Pre-Season Testing directories (not races), and the Spanish Grand Prix, which has Practice 1 only and no `corners.json` (open item T3). So "14 tracks" elsewhere in this file means 14 GP-named events, of which one is Practice-only.

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Parse errors on some `_tel.json` | Interrupted sparse checkout | `git -C data/raw/tracinginsights/<year> status`, then re-run the downloader for that year. Do **not** delete the mirror. |
| `distance_monotonic` false on many laps | Real upstream artefact at lap boundaries | Do not repair raw data (§7). The validator already rejects these; record the rate in the audit and move on. Investigate only if >2%. |
| Audit takes very long | 177k files, single-threaded | Run per year: `--raw-root data/raw/tracinginsights` is fine, but add a `--years 2026` flag and run overnight for the rest. 2026 alone unblocks CP-04. |

### Deliverables

`artifacts/schema_audit/*`, `scripts/data/inventory.py`, `src/trackshift/data/guards.py`, `tests/test_guards.py`.

### ✅ Completed

`.
unbooks\CP-01.ps1 -SkipAudit` reports **8 of 8 requirements met**. Full mirror audited: five seasons, `partial_audit: false`, **177,288 lap files**, **zero malformed**. The lap total cross-checks exactly against the download manifests and the inventory.

Audit runtime was **1h30m** at 26 laps/s for 2022–2025 — slower than the 81/s measured on a warm subset, so budget ~90 minutes rather than 40 for a cold full run.

`distance_monotonic` by session type, all above the provisional floors:

| session | monotonic | rejected |
|---|---|---|
| Race | 95.05% | 6,375 |
| Sprint | 93.97% | — |
| Sprint Shootout | 83.17% | — |
| Practice 1 | 83.51% | 1,874 |
| Sprint Qualifying | 82.80% | — |
| Qualifying | **76.09%** | 9,014 |

**Qualifying loses nearly a quarter of its laps**, because every flying lap is bracketed by an out-lap and an in-lap. That is correct behaviour, not a defect, but it means any qualifying-derived feature has materially less data than the raw lap count suggests — relevant at CP-13.

Two defects found and fixed while closing this checkpoint:

- The audit reported **11 malformed files** (2023 Qatar Sprint Shootout lap 2). They are not malformed: the JSON parses and the first two `distance` samples are simply missing, which the source writes as the string `"None"`. `audit_tel` compared `b >= a` filtering only on `is not None`, so the sentinel reached the comparison and raised `TypeError`. `validation.py` already handled this correctly, so the lake was never affected. Now coerced through `numeric()`, with `distance_nonnumeric_count` recorded per lap.
- `--merge` was added so the remaining seasons could be audited without discarding a finished one. The audit rewrites its whole output directory, so auditing 2022–2025 after 2026 would have destroyed 34,336 already-audited laps and a 37 MB CSV.

---

# CP-02 — Registries scaffold (M31)

**Goal:** `config/data_registry.yaml` and `config/feature_registry.yaml` exist with a loader and a schema test, so every later checkpoint registers what it produces in the same PR.

**Depends on:** CP-00.

Per §45, §46 and the MODELS.md update, each feature records the base fields **plus** `availability_scope`, `source_gated`, `decision_checkpoint`, `uncertainty_field`, `causal_status`, `counterfactual_safe`, `regulation_version`, `regulation_source`, and `interaction_group`.

### Steps

**1. `config/feature_registry.yaml`** — one entry per feature:

```yaml
schema_version: 1
features:
  - name: gap_at_checkpoint_s
    definition: Time gap to the car ahead, sampled at a named decision checkpoint.
    unit: s
    source: derived from DistanceToDriverAhead and speed
    derivation: gap_m / speed_mps at the checkpoint distance
    allowed_sessions: [Race, Sprint]
    supported_years: [2022, 2023, 2024, 2025, 2026]
    live_safe: true
    provenance: DERIVED
    availability_scope: all
    source_gated: false
    decision_checkpoint: [DETECTION, ACTIVATION, BRAKING]
    uncertainty_field: null
    causal_status: CAUSAL
    counterfactual_safe: false
    regulation_version: null
    regulation_source: null
    interaction_group: [gap]
    consuming_models: [M07, M10]

  - name: tyre_temperature
    definition: Tyre surface temperature.
    unit: C
    source: NOT AVAILABLE - no documented continuous source in TracingInsights
    live_safe: null
    provenance: OBSERVED
    availability_scope: none
    source_gated: true          # absent until a documented source exists (section 11)
    decision_checkpoint: null
    uncertainty_field: null
    causal_status: null
    counterfactual_safe: false
    regulation_version: null
    regulation_source: null
    interaction_group: []
    consuming_models: []
```

Pre-register all seven §11 source-gated channels with `availability_scope: none` so nobody silently zero-fills them: `brake_pressure`, `steering_angle`, `tyre_temperature`, `tyre_pressure`, `brake_temperature`, `damage`, `fuel_consumption`.

**2. `config/data_registry.yaml`** — one entry per processed dataset (name, description, source, schema_version, partitioning, primary key, producer script, consuming components, live/offline status). Seed it with `telemetry_20m`.

**3. `src/trackshift/data/registry.py`** — `load_feature_registry()`, `load_data_registry()`, `feature(name)`, and `assert_registered(columns, context)` which raises listing any column absent from the registry.

### ✅ Check

- `pytest tests/test_registry.py` passes: every entry has all required keys; `live_safe` is not null unless `availability_scope: none`; no duplicate names
- `assert_registered` raises on an unknown column
- All seven source-gated channels present with `availability_scope: none`

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Registry drifts from real Parquet columns | Registration treated as an afterthought | Make `assert_registered` a hard call at the end of every builder script, not a lint. A dataset that writes an unregistered column should fail the build. |
| Two features with near-identical names | Chain P and Chain E naming independently | The registry is the single namespace. Search before adding; `test_registry.py` should also flag names differing only by suffix. |

### Deliverables

`config/data_registry.yaml`, `config/feature_registry.yaml`, `src/trackshift/data/registry.py`, `tests/test_registry.py`.

### ✅ Completed

30 registry tests pass; `validate_registries()` reports no problems. 55 features and 13 datasets registered (3 built, 10 planned).

Seeded from the **real** 48-column `telemetry_20m` Parquet rather than an aspirational list, since drift between what is registered and what is written is the failure this registry exists to prevent.

`assert_registered` is wired into `build_phase2_dataset.py` as a hard build failure, not a lint. Verified by temporarily unregistering `speed_kmh`: the build stopped and named the column. `--allow-unregistered` remains for local experiments only.

`validate_registries()` also enforces invariants YAML cannot express: `live_safe` may be null only when `availability_scope: none`; an `ACAUSAL` feature can never be `live_safe`; `availability_scope: none` implies `source_gated`; and names differing only by a unit suffix are flagged as near-duplicates.

**Found while seeding:** every lap-metadata column in the lake was silently null. `laptimes.json` is columnar, `load_lap_metadata` expected records, and the type check returned all-`None` with no error. Fixed before the registry could be written against a false schema — see the commit `fix: lap metadata was silently null for every lap`. Parquet went from 41 to 48 columns.

---

# CP-03 — Rule config skeleton, all 14 tracks (M18)

**Goal:** `config/rules/2026/` holds `common.yaml` plus one file per 2026 event, every value carrying a provenance tier and a source string. Per your decision: **all 2026 tracks, not just Silverstone**, sourced from public FIA material.

**Depends on:** CP-01.

### The honesty problem, and how to handle it

Three tiers of value, and the config must distinguish them, because §57/§58 govern what the demo may claim:

| Tier | `value_source` | Meaning | Usable for |
|---|---|---|---|
| **A** | `RULE_FIA` | Cited to an FIA Sporting/Technical Regulation article or event note | Everything. "Legal by construction" claims. |
| **B** | `OBSERVED_RCM` | Derived from `rcm.json` race-control messages in the raw mirror | Race-control state. Genuinely observed. |
| **C** | `PROXY_HISTORICAL_DRS` | Inferred from where DRS was active at the same circuit in 2022–2025 | Versioned development fixture/DP-shape work only. It is rejected from final C3/C4/C5, planner, simulator, route, replay, calibration, and demo claims. |

Every key gets `value_source` and `source`. The rule engine (CP-11) refuses to start if any Tier-C value is used while `strict_mode: true`.

### Steps

**1. `config/rules/2026/common.yaml`** — season-wide values:

```yaml
schema_version: 1
season: 2026
regulation_snapshot:
  section_issues: []
  publication_dates: []
  effective_date: null
  source_documents: []
  retrieved_at: null
  encoded_configuration_version: rules-2026-common-v1
overtake:
  detection_gap_s:
    value: 1.0
    value_source: RULE_FIA          # replace with UNVERIFIED until cited
    source: "TODO: FIA 2026 Sporting Regulations, Overtake article"
    note: "Gap threshold at the Detection Line for Overtake to arm."
# Maximum electrical deployment is a FUNCTION OF SPEED, not a constant (§20.1).
# Piecewise linear between breakpoints; clamped outside the range.
power_envelope:
  normal:
    breakpoints_kmh: [0, 290, 340]
    max_power_kw:    [350, 350, 0]
    value_source: UNVERIFIED
    source: "TODO: FIA 2026 Technical Regulations, MGU-K deployment vs speed"
    note: "Reported shape: full power to ~290 km/h, tapering to zero by ~340 km/h."
  override:
    breakpoints_kmh: [0, 337, 355]
    max_power_kw:    [350, 350, 0]
    value_source: UNVERIFIED
    source: "TODO: FIA 2026 Sporting/Technical Regulations, Overtake override"
    note: "Reported shape: ~350 kW to ~337 km/h, available to ~355 km/h."
  separation_speed_kmh: 290          # below this the curves coincide; mode is unobservable (§20.2)
  competition_adjustments: []        # per-event overrides on top of the season curve, if any are sourced
energy:
  deploy_limit_per_lap_mj:
    value: null
    value_source: UNVERIFIED
    source: "TODO: FIA 2026 Technical Regulations, energy flow limits"
  harvest_limit_per_lap_mj:
    value: null
    value_source: UNVERIFIED
    source: "TODO: FIA 2026 Technical Regulations, recovery limits"
  store_capacity_mj:                 # physical bound on ers_soc_est_mj — separate from the per-lap flow limits above
    value: null
    value_source: UNVERIFIED
    source: "TODO: FIA 2026 Technical Regulations, Energy Store capacity"
  accounting_window: lap
strict_mode: false                   # set true before any demo claim
```

**Units:** stored and per-lap energy in **MJ** (matching how the regulations state it); power in **kW**; per-segment deltas elsewhere stay in kJ. The unit is always in the field name (§11).

**The envelope numbers above are the reported shape, not verified regulation.** They are usable for modelling immediately — that is the point of encoding them — but `value_source: UNVERIFIED` must propagate to every consumer, and no demo may claim legality by construction until each is traced to an FIA article (§20.1, §57). Getting these three curves right matters more than almost any other config value, because the DP's entire action space is built on them.

**2. One file per event** — 14 of them, named from the directory (`british_grand_prix.yaml`, `monaco_grand_prix.yaml`, …):

```yaml
schema_version: 1
event: british_grand_prix
event_display: British Grand Prix
circuit: silverstone
year: 2026
lap_length_m:
  value: 5819.0
  value_source: DERIVED_TELEMETRY
  source: "median max(distance) over accepted 2026 Race laps"
geometry:
  corner_count: 18
  rotation_deg: 92.0
  source: "data/raw/tracinginsights/2026/British Grand Prix/Race/corners.json"
overtake:
  enabled: true
  zones:
    - zone: 1
      name: "TODO"
      detection_line_m:
        value: null
        value_source: UNVERIFIED
        source: "TODO: FIA event notes 2026 British Grand Prix"
      activation_line_m:
        value: null
        value_source: UNVERIFIED
        source: "TODO: FIA event notes 2026 British Grand Prix"
race_control:
  # generated by scripts/data/extract_race_control.py from rcm.json
  overtake_windows_source: "artifacts/race_control/2026_british_grand_prix.json"
  value_source: OBSERVED_RCM
```

**3. `scripts/data/extract_race_control.py`** — parse every 2026 `rcm.json`, emit per event/session a timeline of `OVERTAKE ENABLED` / `OVERTAKE DISABLED` windows with session times, plus SC/VSC/flag windows. This is Tier B and needs no FIA document. Write to `artifacts/race_control/<year>_<event>.json`.

**4. `scripts/data/derive_drs_zones.py`** — Tier C fallback. For each circuit, take 2022–2025 Race laps at that track, find distance ranges where `drs != 0` for a meaningful share of laps, and emit candidate activation zones. Mark every output `PROXY_HISTORICAL_DRS`. This may seed an explicitly development-only geometry fixture while FIA values are `UNVERIFIED`; it must not reach final C3/C4/C5, calibration, route, replay, or demo paths. **Silverstone's zones from 2022 DRS are a historical proxy, not 2026 Overtake zones.**

**5. Sourcing task** (do this in parallel, it is research not code): FIA 2026 Sporting Regulations and Technical Regulations from `fia.com`; per-event "Event Notes" from the race director, which historically carry the DRS/Overtake zone definitions. Record the document title, date, and article number in each `source` field. Convert `UNVERIFIED` → `RULE_FIA` one key at a time.

**6. `src/trackshift/rules/config.py`** — loader with a pydantic schema, plus `resolve(event, key)` returning `(value, value_source, source)` and raising in strict mode on `UNVERIFIED` or `PROXY_*`.

### ✅ Check

- 14 event files exist, one per complete 2026 GP directory (the incomplete Spanish GP is explicitly excluded with a comment)
- `pytest tests/test_rules_config.py`: every file validates; every leaf value has both `value_source` and `source`; no `value_source: RULE_FIA` with a `source` containing "TODO"
- `artifacts/race_control/*.json` covers all 14 events; totals reconcile to **51 enable / 23 disable** messages across 2026
- `resolve()` raises in strict mode for every key still `UNVERIFIED`
- Lap lengths derived from telemetry are within ±50 m of published circuit lengths

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| FIA event notes not findable for 2026 | Documents not published or moved | Keep the key `UNVERIFIED`, use the Tier-C proxy, and record the search in the `source` field ("searched fia.com/documents 2026-09-12, not found"). The plan does not block on this — only demo claims do. |
| DRS-proxy zones look wrong (too many, too long) | Threshold too loose, or DRS-in-traffic noise | Require the zone to be active on ≥25% of laps and ≥300 m long; drop zones that overlap a braking region from CP-05. |
| Two events share a circuit with different values | Layout changed between years | Key rule files by **event**, not circuit, exactly as the directory names are. Record `circuit` as metadata only. |
| Rule value changes later | Regulations updated | Bump `schema_version` in the event file and record it in every artifact manifest that consumed it. Never edit silently. |

### Deliverables

`config/rules/2026/common.yaml` + 14 event files, `scripts/data/extract_race_control.py`, `scripts/data/derive_drs_zones.py`, `artifacts/race_control/*.json`, `src/trackshift/rules/config.py`, `tests/test_rules_config.py`.

### ✅ Completed

All six acceptance gates pass; 83 CP-03 tests, 244 in the suite.

| Gate | Result |
|---|---|
| 14 event files | ✅ 13 complete GPs + Spanish GP (Practice-only, flagged) |
| Every file validates | ✅ no problems |
| No `RULE_FIA` with a TODO source | ✅ none |
| Race control covers every event | ✅ 17 files; totals reconcile to **51 enable / 23 disable** |
| `resolve()` raises in strict mode on `UNVERIFIED` | ✅ |
| Lap lengths sane vs published | ✅ all within 3%, worst −2.71% |

**Measured, not assumed.** Lap length is `DERIVED_TELEMETRY` (median `max(distance)` over sampled Race laps) and corner geometry is `OBSERVED` from `corners.json`. Every *regulatory* value — power envelope, energy budgets, detection gap, line positions — stays `UNVERIFIED` pending an FIA citation.

Four findings worth carrying forward:

- **The ±50 m lap-length gate was wrong and is now ±3%.** Telemetry distance reads short of the homologated length on *every* circuit, from −0.61% at Monza to −2.71% at the Hungaroring, tracking corner density: distance is integrated along the driven path and straight-line integration between samples undershoots the arc through a corner. **Use the measured value** — CP-05 segments on telemetry distance, so boundaries must share that coordinate system. Each file records the published length and delta as a cross-check only.
- **Race control writes `VSC DEPLOYED`, not the expanded form.** Matching only the long spelling found all 26 endings and none of the 31 deployments, which would have left VSC periods invisible to the `normal_race_model_eligible` gate.
- **The DRS-proxy threshold is calibrated, not guessed.** The channel thins from 5.2% of samples in 2022 to 1.0% in 2025, so the initial 25% lap-share found both Silverstone zones in 2022 and nothing after. At 8% all four seasons independently recover the same two zones within 40 m — that agreement is the evidence they are circuit features rather than one race's traffic.
- **Corner data is not clean everywhere.** The Hungaroring repeats three distances and lists them out of order. The files sort them and record the anomaly rather than smoothing it away.

**Still open (research, not code):** every regulatory value is `UNVERIFIED`. `strict_mode` stays `false` until the FIA 2026 Sporting and Technical Regulations and the per-event notes are sourced. Nothing is blocked by this — only demo claims are (§57).

---

# CP-04 — Build the 20 m lake

**Goal:** `data/processed/telemetry_20m/` populated for the scope you need, deterministically, with manifests.

**Depends on:** CP-00 (needs `pyarrow`).

**Dataset — build in this order:**

| Stage | Scope | Purpose | Approx rows |
|---|---|---|---|
| 4a | 2026 British GP, Race, HAM + ANT | Smoke test on the demo battle | ~30 k |
| 4b | 2026 British GP, all sessions, all drivers | Demo event complete | ~1.5 M |
| 4c | 2026 all 14 GPs, Practice 1 + Qualifying + Race (+ Sprints) | Chain E and Chain P 2026 domain | ~20 M |
| 4d | 2022–2025 all events, Qualifying + Race | DRS-era priors for Chain P | ~35 M |

Estimate basis: Silverstone lap = 5,819 m ÷ 20 m = 291 rows per lap; a 22-driver race with ~52 laps ≈ 333 k rows.

### Steps

**1. Smoke test:**

```powershell
python scripts/build_phase2_dataset.py `
  --raw-root "data/raw/tracinginsights" `
  --output-root "data/processed/telemetry_20m" `
  --year 2026 --event "British Grand Prix" --session Race `
  --drivers HAM ANT --dry-run
```

Expect `discovered_laps: 104, accepted_laps: ~104, rejected_laps: small`. Then drop `--dry-run`.

**2. Add a batch driver** — `scripts/data/build_lake.py` looping over year/event/session with `--years`, `--events`, `--sessions`, `--exclude-event`, `--jobs`, calling the existing builder per session. Keep `build_phase2_dataset.py` unchanged; it works and is tested (§3: do not rewrite working Phase 2 components).

**3. Verify determinism** (MODELS.md §6.3 depends on it — you and Rishabh each build locally and the outputs must match): build one session twice into different roots and compare content hashes of the Parquet, ignoring file mtime.

### Models & parameters

None — this is deterministic resampling. Policy already fixed in `resample.py`: continuous channels linearly interpolated onto the grid, discrete channels zero-order hold from the preceding sample, grid at exact multiples of `spacing_m` from the first sample at or above `distance[0]`.

### ✅ Check

Run `python scripts/data/verify_lake.py`, which checks all nine gates and writes
`verify_report.json`. Two gates from the earlier draft measured the *scope built*
rather than the data, and failed on a correct lake:

- ~~Acceptance rate >95%~~ — **rejection is structural.** Lap 1 starts from a grid slot and pit in/out laps traverse a different path, so the validator rejects them by design. Measured on 2026 British GP Race (HAM + ANT): 104 laps, 97 accepted = **93.27%**, and all 7 rejections were lap 1 or a pit lap. Judge the **codes**, not the rate.
- ~~`gap_ahead_m` non-null >95% in Race~~ — **the leader has no car ahead.** The same build scored 90.7%, but every null belonged to ANT while running P1, and non-leader rows were **100.00%** non-null. With two drivers one leads a large share; with 22 cars only ~1/22 of rows are leader rows and identical data scores ~95.5%. The gate was measuring field size. The exact property is checked instead.

The nine gates actually enforced:

- `run_manifest.json` present with `schema_version: phase2_20m_v1`, and zero failed sessions
- No broken-file rejection codes (`MISSING_TEL_OBJECT`, `MALFORMED_JSON`, `MISSING_REQUIRED_FIELD`, `ARRAY_LENGTH_MISMATCH`) — these would mean damaged files, unlike the structural codes
- Every rejection code is recognised, so a new failure mode cannot slip through unnoticed
- Row count per lap = `lap_length_m / spacing` ± 2 for >99% of laps
- **`gap_ahead_m` present for every non-leader row**, and null only where the driver leads
- Lap metadata is joined and not silently null — pins the CP-02 regression
- Every written column is in the feature registry
- `distance_m` lies on exact multiples of the spacing
- Two builds of the same session produce identical content hashes — **verified: same 48 columns, same 28,140 rows, identical SHA-256.** MODELS.md §6.3 depends on this, since both owners build locally and compare.

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Many `NON_MONOTONIC_DISTANCE` rejections | Lap-boundary artefacts upstream | Expected at some rate. Do not repair raw data. If >5%, consider a validator option to trim a trailing non-monotonic tail — but implement it as an explicit, recorded policy with a test, not a silent fix. |
| `drs_open` all zero for 2026 | **Not a bug** — the channel is genuinely dead | Leave it. Mark `drs_open_observed` unavailable for 2026 in the registry (§11). Overtake state comes from CP-10, never from here. |
| Output files are tiny and numerous | One Parquet per session | §10 warns against thousands of tiny files. If a session file is <5 MB, consider partitioning at event level instead of session for the historical years. |
| Build is very slow | 177k JSON files, single process | Parallelise at the session level in `build_lake.py` (`--jobs 8`). Do not parallelise inside a session — determinism first. |
| Disk fills up | ~55 M rows total | Parquet with compression should be a few GB. Check with `du -sh data/processed`. Build 4a–4c first; 4d only when Chain P needs it. |

### Deliverables

`data/processed/telemetry_20m/**`, `scripts/data/build_lake.py`, per-root `run_manifest.json`, `lap_manifest.csv`, `rejected_laps.csv`, `quality_summary.csv`.

---

# CP-05 — Track segmentation (M03) ⭐ critical path

**Goal:** a **static, versioned** segment map per circuit, and `data/processed/segments/` built from it. `segment_id` becomes the join key for every downstream table, so it is frozen after this checkpoint.

**Depends on:** CP-04 (needs the lake), CP-03 (needs FIA line positions where available).

This is the single most important checkpoint you own. M05, M07, M08 and everything downstream join on `segment_id`; if it moves later, every dataset must be rebuilt.

### The key design decision

**Segment boundaries are a property of the circuit, not of a lap.** Derive them once from a reference lap set, store them in `config/geometry/<circuit>.yaml`, then apply them to every lap. If you instead detect brake points per lap, `segment_id` means a different piece of track on every lap and all baselines are meaningless.

### Steps

**1. Build the reference lap set** per circuit: 2026 Race + Qualifying laps that are `iacc: true`, status `'1'` only, not in/out laps, and accepted by the validator. Silverstone gives roughly 22 drivers × 40 clean laps ≈ 880 laps.

**2. Derive candidate boundaries** from the median profile across reference laps:

| Boundary type | Rule |
|---|---|
| Brake onset | Distance where median `brake_on` crosses 0→1 |
| Throttle return | Distance where median `throttle_pct` first exceeds 95 and stays above for ≥60 m |
| Corner apex | `Distance` from `corners.json` (18 for Silverstone) |
| FIA lines | `detection_line_m`, `activation_line_m` from CP-03 where available |
| Zone edges | Start/end of activation zones |

**3. Merge and clean:**
- Merge any segment shorter than **40 m** into its neighbour
- Target **30–40 segments per lap** (§13). Silverstone with 18 corners should land near 36
- Number sequentially by distance from the start/finish line
- Classify each: `STRAIGHT | BRAKING | CORNER | EXIT`

**4. Derive static geometry per segment** (§12, §13): `sector`, `zone`, `corner_id`, `corner_type`, `corner_phase`, `track_heading_deg`.

`corner_type` must be a **versioned, geometry-based classification**, not a free-text label. Compute a curvature proxy from the median `x`/`y` centreline, then bin:

| `corner_type` | Rule (median apex speed and curvature) |
|---|---|
| `STRAIGHT` | curvature below threshold |
| `HAIRPIN` | apex speed < 100 km/h |
| `SLOW` | 100–160 km/h |
| `MEDIUM` | 160–220 km/h |
| `FAST` | > 220 km/h |
| `CHICANE` | two curvature sign changes within 150 m |

Suffix `_LEFT`/`_RIGHT` from the curvature sign. Store the thresholds in the geometry file and stamp `geometry_version` (for example `silverstone-geom-v1`) — API.md requires it in `/track`, and every downstream artifact records it.

`track_heading_deg` = `atan2(dy, dx)` along the smoothed centreline, corrected by the `Rotation` scalar from `corners.json` (92.0 at Silverstone). M33 depends on this being right.

**5. Write `config/geometry/<circuit>.yaml`** and `scripts/features/build_segments.py` which applies it to the lake and emits `data/processed/segments/` per C1.

**6. Emit the C1 columns**, live fields aligned to **segment entry**, full-segment summaries in separate `OFFLINE_ONLY` columns.

### ✅ Check

- Segment count per lap in **[30, 40]** for every circuit; Silverstone ≈ 36
- No segment shorter than 40 m
- `segment_id` for a given `(circuit, geometry_version)` is **identical across every lap, driver, session and year** — assert by hashing the boundary array
- Boundary stability: brake-onset boundary distance has a standard deviation across reference laps of **< 25 m**
- `sum(segment_length_m) == lap_length_m` ± 20 m
- `corner_id` present on every `CORNER` segment and matches the count in `corners.json`
- `track_heading_deg` is continuous mod 360 — no jumps >45° between adjacent segments
- Rejection manifest exists; no lap silently dropped (§49)

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| 60+ segments per lap | Merge threshold too low; noisy brake detection | Raise merge threshold to 60 m; require brake_on true for ≥3 consecutive 20 m rows before calling it an onset |
| < 25 segments | Over-merging, or a circuit with few corners (Monza has 11) | Accept a lower count on low-corner circuits but record it. Add throttle-return boundaries on long straights to split them. |
| Boundary std > 25 m across laps | Using per-lap detection instead of the median profile | Re-read the design decision above. Derive once from the reference set. |
| `corner_type` unstable between years | Thresholds tuned to one season's speeds | Bin on curvature primarily, speed secondarily. Bump `geometry_version` if you retune, and rebuild everything downstream. |
| `track_heading_deg` jumps wildly | Coordinate frame not rotation-corrected, or `x`/`y` noise | Apply `Rotation` from `corners.json`; smooth the centreline over 5 points before differencing. |
| Segment sum ≠ lap length | Grid starts above 0 m (resampler skips samples below `distance[0]`) | Expected — the first segment starts at the first grid point. Record the offset rather than forcing it to zero. |

### Deliverables

`config/geometry/*.yaml`, `src/trackshift/track/segmentation.py`, `scripts/features/build_segments.py`, `data/processed/segments/**`, `tests/test_segmentation.py`, registry entries.

---

## Chain E status, CP-18 to CP-22

Every script in the twin chain is built, runs end to end, and reports its own
gates. Sprint is out of scope throughout. Verified 2026-09-13 against the
manifests on disk.

| CP | Script | Verdict |
|---|---|---|
| 18 | `scripts/twin/build_energy_twin.py` | ✅ violation rate **1.1%**, all at 254 km/h or above, none below 150 |
| 19 | `scripts/twin/build_fuel_curves.py` | ✅ median final fuel **1.00 kg**, **100%** of finishers in the 0-3 kg band |
| 18b | `scripts/twin/apply_override_discriminator.py` | ✅ false-positive rate now **measured**: **10.87%** on the 2024 control |
| 20 | `scripts/train/calibrate_physics.py` | ✅ no calibrated rung accepted; best **0.1918 s** against a 0.1631 s floor |
| 21 | `scripts/train/train_segment_time.py` | ✅ 214 segments fitted, all monotone, but `a_k` inverted by segment type |
| 22 | `scripts/twin/build_uncertainty.py` | ✅ coverage **84.7%** vs nominal 80%, but inherits CP-20's fit |

**CP-18 and CP-19 pass their own gates.** The twin's envelope violations sit
where they should -- median 266 km/h, none below 150, which is the test for the
balance being wrong rather than the taper. Fuel now covers Race, Practice 1 and
Qualifying; the burn is measured from the twin's ICE work and the level is a
per-car calibration for races and a documented per-session prior elsewhere,
flagged row by row.

### CP-18b: the false-positive rate is measured, not inferred

The 2022-2025 control called for in the check list has been run on 2024, which
turns the component's headline weakness from a suspicion into a number:

| | |
|---|---|
| samples | 379,473 |
| discriminable (above the separation speed) | 35,744 |
| OVERRIDE detections | 3,885 |
| **false-positive rate** | **10.87%** |

Every one of those detections is wrong by construction: no override mechanism
existed in 2024. Against the **23.2%** detection rate on 2026 discriminable rows,
roughly **half the 2026 detections are the twin over-estimating power** and the
remainder are plausibly real. Before this run the only honest statement was
"23% looks high".

Two things the control needed, and both were silent failures worth recording.
`build_segments.py` had no year filter and ran serially, so one historical season
meant a 19-minute full rebuild; it now takes `--years` and `--jobs` and is
additive, so 2024 contributed 380,299 segment rows across 13 circuits --
Barcelona has no 2024 lake data -- while 2026's 744,655 stayed untouched. The twin and
the discriminator loaded rules by *data* year, so 2024 found no
`config/rules/2024/` and reported `violation=0.0%` -- a zero that read as "no
violations" and meant "never measured", exactly where a calibration bug would
hide. Both now take `--rules-year`, defaulting to 2026, because the question a
control asks is whether the *2026* component fires on historical telemetry.

**Caveat recorded with the result:** 2024 segments are built against 2026
geometry maps. Circuits get resurfaced between seasons. That is fine for
measuring a power over-estimate and is not a basis for any 2024 claim about
track geometry.

Two check-list gates remain unmeasured: detections concentrating after
Activation Lines, and the sensitivity check that detection rate must **not** rise
when the twin is deliberately mis-calibrated by raising mass 3%.

### CP-20 is the blocker for the rest, and no rung is accepted

| Rung | Held-out MAE | Accepted |
|---|---|---|
| 1. Pure analytical | 0.3266 s | ✅ (priors only, nothing fitted) |
| 2. Global calibrated | **0.1918 s** | ❌ `P_ICE` pinned at its 500 kW bound |
| 3. Team-specific | 0.1982 s | ❌ does not improve on rung 2 (§34) |
| 4. Event-specific | 0.1918 s | ❌ no improvement over rung 2 |
| 5. Physics + ML residual | 0.1945 s | ❌ worse than rung 2 |

Reference points on the same rows: the **best constant per segment** scores
**0.1631 s** and the **C2 baseline** scores **0.1774 s**. So the only accepted
rung is twice the floor, and the best-scoring rung is still *worse than simply
using the C2 baseline*. The section 29 target of 0.15 s sits **below** the floor,
so no model predicting from segment identity plus physics can reach it. Reaching
it needs within-segment lap-to-lap explanation, and on clean-air laps that
variation is dominated by driver and traffic rather than fuel, wind or density.
The largest missing control is fuel: CP-19 covers Race, while section 29 trains
on Practice 1 and Qualifying, so mass is effectively constant across every
training row.

Three of four parameters pin at the bounds that minimise the physics correction,
which is the optimiser saying the correction does not earn its place.

**Section 55 is now enforced as a rejection clause.** Each rung scores an
envelope-violation rate and a rung that raises it by more than one point over the
baseline is refused with a reason. The first version read 0.00% everywhere
because it passed zero acceleration, at which the ICE covers demand and nothing
deploys; with real acceleration derived from `exit_speed_kmh_offline` and
`segment_time_s_offline` it separates the rungs -- analytical **0.51%**, global
**0.06%**. The fitted rung passes that gate and is still correctly rejected: it
lowers violations by pushing `P_ICE` to 500 kW, above the reported 2026 figure of
400. It is buying a better violation rate with an engine the regulations do not
allow, and the at-bound check catches it.

**The fit has only ever seen 6,000 of 107,807 clean rows** (4,444 train, 1,556
test, held out by event: Australian, Italian and Japanese). That cap was kept
small while the forward model was being iterated and has never been lifted, so
the bound-pinning could still be an artefact of a thin fit.

The forward model went through five revisions on the way, and the failures were
the useful part: endpoint-average traversal (0.489 s) assumed a linear speed
change through segments that run 300-100-300; a steady-state speed cap (0.334 s)
bound on nearly every segment because a car accelerating is legitimately below
terminal speed and one braking legitimately above; power as a correction to the
C2 baseline was the right structure. Feeding fuel into mass changed nothing
measurable, which is itself informative -- 50 kg moves rolling resistance by
about 6 kW against 370 kW of drag -- so an inertial term was added, because mass
bites through acceleration.

**CP-21 fits but its sensitivity profile is inverted.** Corners average 7.1 s/MJ
across 187 segments against straights at 0.63 across 20, where section 30 expects
the opposite. Deployment barely varies inside a corner, so a large time variance
over a tiny energy variance produces an enormous slope -- one corner reaches 462
s/MJ. That is confounding, not sensitivity. All 214 fitted segments are monotone
and every non-positive `a_k` is refused rather than published (12 refused on that
ground, 128 more for no energy variation at all), but the profile says the
transition is not yet safe for the DP, and in-sample MAE of 0.187 s is far from
the 0.06 s target.

**CP-22 passes its coverage gate at 84.7%**, after a real methodological fix: the
first version derived sigma from MAE under a normal assumption and covered 63.3%.
Held-out error is skewed, so it now inflates by measured residual quantiles
estimated on a disjoint half of the sample. It still inherits CP-20's fit, and
its manifest says so in `inherits_note`.

### Next action

1. **Run CP-20 at full scale.** `--max-rows` defaults to 40,000, about 7x the
   current fit, and it settles whether the bound-pinning is real or thin-fit
   noise. This is the cheapest remaining experiment and it gates 21, 22 and 18b.
2. **Add within-lap controls** or revise the section 29 target in writing. The
   0.15 s target is below the 0.1631 s floor; one of the two has to move, and
   changing the target silently is the thing section 34 exists to prevent.
3. **Re-run CP-21, CP-22 and CP-18b off whichever fit survives** -- all three
   inherit it, and CP-18b's 10.87% control is the measurement that will say
   whether the new fit is actually better.

**Lake and segment coverage, for whoever picks up CP-14.** The 20 m lake now
holds 2022, 2023, 2024 and 2026. Segments are built for 2024 (13 circuits,
380,299 rows; Barcelona has no 2024 lake data) and 2026 (14 circuits, 744,655
rows) -- 2022 and 2023 are in the lake but have no segment table yet, and **2025
is absent entirely**, so CP-14's Train 2022-2024 / Validation 2025 split is still
blocked on the validation half. Building a season's segments is now cheap:
`--years` is additive and `--jobs` parallelises across circuits.

---

# CP-06 — Track-relative weather (M33)

**Goal:** wind projected onto the track, not a compass bearing. §12 and §39.

**Depends on:** CP-05 (needs `track_heading_deg`).

**Dataset:** `weather.json` per session (156 samples in a race — roughly one per 40 s, so interpolate onto lap time), joined to segments by session time.

### Steps

**1. Time-align** weather samples to segment entry by interpolating on `wT` (session time) against the segment's `session_time_s`.

**2. Derive** (§12):

```python
# wWD is the bearing the wind comes FROM; track_heading_deg is the direction of travel
rel = radians(wWD - track_heading_deg)
wind_head_component_mps  = wWS * cos(rel)    # positive = headwind
wind_cross_component_mps = wWS * sin(rel)    # positive = from the right
air_density_proxy        = (wP * 100) / (287.05 * (wAT + 273.15))   # kg/m3
wet_track_flag           = bool(wR)
```

**3. Keep raw** `wAT`, `wTT`, `wH`, `wP`, `wWS`, `wWD` in the lake (§39) but mark `wWD` **not live-safe for models** in the registry — only the derived components go to models.

### ✅ Check

- `air_density_proxy` in **[1.0, 1.35] kg/m³** for realistic conditions; Silverstone in July with ~1013 hPa and 25 °C gives ≈1.18
- `wind_head_component_mps² + wind_cross_component_mps² ≈ wWS²` to within 1e-6
- Sign test: on a segment whose `track_heading_deg` equals `wWD`, head component ≈ `+wWS` and cross ≈ 0
- Head component changes sign roughly twice per lap on a typical circuit (the car goes out and comes back)
- `wet_track_flag` false for all 156 samples at 2026 British GP — matches `wR` in the raw file

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Head component never changes sign | Heading not varying — segmentation geometry is wrong | Go back to CP-05; check the `Rotation` correction |
| Sign inverted | `wWD` is "from", not "towards" | The formula above assumes "from". Verify against a session with strong known wind; the test in `tests/test_weather.py` pins the convention |
| Weather join produces NaN at session start | First segment precedes the first weather sample | Forward-fill from the first sample and flag `weather_extrapolated: true`; do not interpolate backwards from nothing |

### Deliverables

`src/trackshift/track/weather.py`, `scripts/features/build_weather_overlay.py`,
`tests/test_weather.py`, versioned C1-keyed weather overlay, registry entries.

### ✅ Completed

Revalidated 2026 Australian Grand Prix Race with
`m33_segment_weather_overlay_v1` at `c681dad002fa0c932465db84dba8b2295d760351`:
**29,508** C1-keyed overlay rows, **0** pre-first extrapolations and **0**
post-final holds; one C1 row without an entry clock is explicitly
`MISSING_SEGMENT_ENTRY_TIME`, never filled. Air-density proxy was 1.180692 to
1.187100 kg/m³ and the wind-vector identity error was 8.9e-16. British GP Race
was used only for final deterministic feature validation (34,173 rows; 0/0
extrapolation/hold; all available rows dry; density 1.171139 to 1.178409
kg/m³). Both circuits have positive and negative headwind components over the
lap; raw wind bearing remains non-model/live-safe. Full suite: **618 passed,
7 skipped** (`.venv/bin/python -m pytest -q`).

---

# CP-07 — Practice lap classifier (M01)

**Goal:** every Practice lap labelled `PUSH | LONG_RUN | COOLDOWN | OUT_LAP | IN_LAP | INTERRUPTED | INVALID | UNKNOWN` (§9), so physics calibration uses clean-air laps only.

**Depends on:** CP-04.

**Dataset:** 2026 Practice 1 only — historical years have no Practice on disk. 2026 British GP P1 alone has **613 tel laps across 22 drivers**; the full season gives roughly 8,500 P1 laps.

### Steps

Start **deterministic** (§9 allows deterministic, inferred or hybrid). A rule-based classifier is interpretable, needs no labels, and is sufficient. Only escalate to a model if the rules leave a large `UNKNOWN` share.

| Label | Rule |
|---|---|
| `OUT_LAP` | `pout` set on this lap, or previous lap had `pin` |
| `IN_LAP` | `pin` set on this lap |
| `INVALID` | `del: true`, or `iacc: false` |
| `INTERRUPTED` | lap `status` contains any digit other than `1` |
| `PUSH` | lap time within **107%** of the driver's session best, tyre `life` ≤ 5, not out/in |
| `LONG_RUN` | part of a run of ≥4 consecutive green laps with lap-time spread < 3% and increasing tyre `life` |
| `COOLDOWN` | lap time > 115% of session best, not in/out, following a `PUSH` |
| `UNKNOWN` | anything else |

Record `provenance: DERIVED` for all of these, and store the thresholds in `config/lap_classification.yaml` so they are versioned.

### ✅ Check

- `UNKNOWN` share **< 15%** across 2026 P1
- `PUSH` laps are **5–20%** of the session — P1 has few genuine qualifying simulations
- `LONG_RUN` laps 20–50%
- Every `OUT_LAP`/`IN_LAP` reconciles with a `pin`/`pout` in `laptimes.json`
- Spot-check five `PUSH` laps by hand against the session best

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| `UNKNOWN` > 30% | Thresholds too tight, or a red-flagged session | Loosen the `PUSH` window to 108%; check whether the session had a long stoppage (`status` digits) |
| Almost everything is `PUSH` | Session best is itself slow (wet, or a short session) | Use the *field* best for the session as a secondary reference, and never classify laps from a session whose best is >110% of the Qualifying best |
| `LONG_RUN` never fires | Run detection requires consecutive laps; in/out laps break runs | Allow a run to continue across a single `UNKNOWN`, but not across an `IN_LAP` |

### Deliverables

`src/trackshift/track/lap_classifier.py`, `config/lap_classification.yaml`, `tests/test_lap_classifier.py`, `practice_lap_class` column, registry entries.

### ◐ Not complete — one gate fails, and the earlier pass was not honest

17 partitions, 9,488 laps. Two of the three written gates pass. The third does
not, and the record below replaces an earlier entry that reported it as passing.

| Gate | Measured | |
|---|---|---|
| UNKNOWN < 15% | **12.67%** (1,202) | ✅ |
| PUSH 5–20% | **11.58%** (1,099) | ✅ |
| LONG_RUN 20–50% | **9.27%** (880) | ❌ |
| Pit reconciliation | 1,430/1,430 IN_LAP, 1,607/1,607 OUT_LAP | ✅ |

Full distribution: RACE_PACE 2,074 (21.86%), OUT_LAP 1,607, IN_LAP 1,430,
UNKNOWN 1,202, PUSH 1,099, LONG_RUN 880 (9.27%), INVALID 519, COOLDOWN 388,
INTERRUPTED 289.

#### What the earlier revision claimed, and what measuring it showed

The gate had been restated as LONG_RUN **plus RACE_PACE** at 20–50%, which
reads 31.13% and passes. The argument was that causal labelling cannot reach
the first three laps of a run, so the sustained-running population is split
across the two labels and the band belongs on their union.

`scripts/features/audit_practice_lap_classes.py` reconstructs every candidate
run and locates each RACE_PACE lap inside it. The argument does not survive:

| Where the 2,074 RACE_PACE laps sit | Laps | |
|---|---|---|
| Inside a run that reached four laps | 722 | 34.8% — the argument covers these |
| Inside a run of two or three laps | 454 | 21.9% |
| **Isolated single laps, in no run at all** | **898** | **43.3%** |

So the union's numerator is 43% laps that are not sustained running by any
reading. Counted honestly, the population the gate is about — LONG_RUN plus
RACE_PACE laps inside a completed run — is **1,602 laps, 16.88%**, below the
band. Even stretching it to include two- and three-lap runs reaches 21.67%,
and a two-lap run is not sustained running.

Two figures in the earlier entry were also overstated. The season has **273**
runs that reach four laps, not 690, and they cost **819** unlabellable head
laps, not "roughly 2,000".

The gate in `build_practice_lap_classes.py` is now the written one. The union
is still reported in `gate_detail` as a diagnostic, labelled as not the
acceptance criterion.

#### What stands

**`RACE_PACE` itself is sound and stays.** 2,074 laps sat inside the PUSH time
band on a tyre older than the PUSH life limit. That is ordinary race-pace
running, and the label set had nowhere to put it — too old for PUSH, not
necessarily inside a four-lap run, not slow enough for COOLDOWN. Calling it
UNKNOWN claimed the lap's character could not be determined when the lap time
and the tyre life say plainly what it is. It is causal: lap time, tyre life and
the running best are all known at lap completion. It sits below LONG_RUN in
precedence, so a lap inside a sustained run still reads as the stronger
evidence. **The UNKNOWN gate passes on its own merit** — 34.53% to 12.67% —
because those laps really are race-pace laps, not because they were moved.
Only the use of RACE_PACE in the LONG_RUN numerator was wrong.

#### Why LONG_RUN is genuinely low, and what would move it

Two hypotheses were tested and rejected. Tyre life is not too strict: 98.9% of
adjacent pairs increment by exactly 1. Whole-run spread does not penalise long
runs: rolling-4 spread is 0.3586 against whole-run 0.3586.

The binding constraint is measured. Of 3,799 candidate runs, **3,212 are a
single lap** and only 273 reach four. Runs die overwhelmingly from lap-time
spread — 2,153 endings against 1,513 at a structural boundary — because 2026 P1
alternates push and cooldown laps rather than running race simulations. The
causal ceiling is real but small: 819 head laps, 8.6% of the session.

Three things could move the gate, and none may be done silently:

1. **Relax `long_run_lap_time_spread_ratio_max_exclusive` above 3%.** Over a
   ten-lap run, tyre degradation alone can exceed 3%, so the current value may
   be mis-specified for long runs rather than correct-but-strict. This is a
   specification decision for the owner, not a retune, and it changes M01's
   contract.
2. **Widen the session scope beyond Practice 1.** Race simulations that P1 does
   not contain may live in P2/P3. This changes the stated scope and needs the
   same explicit decision.
3. **Accept that 2026 P1 does not contain 20% sustained running** and revise the
   band against measured evidence, which is a change to the gate and must be
   argued on the data rather than applied to obtain a pass.

Until one of those is decided, CP-07 stays open. The classifier is correct and
causal; the session simply does not contain what the gate asks for.

No source-quality exclusion was applied. All 17 partitions carry laps, and the
missing-input rate among UNKNOWN is 0.42% — 5 laps of 1,202 lack a tyre life.
The remaining 1,197 are genuine: 1,043 slow laps that did not follow a PUSH and
154 in the 108–115% dead band that belongs to no rule. Excluding sessions after
seeing the revised shares would not have been a pre-registered exclusion, which
is the condition CP-07 sets for one.

# CP-08 — Tyre degradation and normalised pace (M30)

**Goal:** a **causal** `tyre_degradation_proxy` and tyre-normalised pace, normalised relative to driver, car, compound, stint, track and session, as evidence-based inputs to a future tyre-performance state rather than a claim to reconstruct physical tyre sensors (§38).

**Depends on:** CP-05, CP-07.

**Dataset:** 2026 Race + Sprint (stints with `compound`, `life`, `stint` from `laptimes.json`), all 14 events. HAM at 2026 British GP: HARD 25 laps, MEDIUM 23, SOFT 4, pit in on laps 23 and 48.

### Steps

**1. Reconstruct stints** from `stint`, `compound`, `life`, `pin`, `pout`. A stint is bounded by pit events; `life` includes laps from earlier sessions on used sets, so use `fresh` to distinguish.

**2. Fit a per-(driver, compound, stint) pace trend** on green-flag, non-in/out laps only. Causal means: at lap *k*, the proxy may only use laps ≤ *k*.

```text
tyre_degradation_proxy(k) = (rolling median lap time over laps [k-4, k])
                          - (median of the first 3 green laps of the stint)
```

Normalise by the driver's clean-air reference for the circuit so it is comparable across cars.

**3. Tyre-normalised pace**: residual of segment time against the driver's own median for that segment on the same compound at similar tyre life, so it does not double-count what M04 baselines already remove.

### ✅ Check

- Proxy resets to ~0 at every stint start, subject to an explicit `reason` for insufficient clean history
- Report the within-stint trend and compound comparison with confidence intervals; do not hard-code a universal monotonicity or compound ordering
- Compare tyre age only, the degradation proxy, and the latent tyre-performance state when it becomes available; retain the more complex state only if it improves held-out transition or decision metrics
- **Causality test**: computing the proxy on a truncated stint (first *k* laps) gives the identical value at lap *k* as computing it on the full stint. This is the test that catches accidental lookahead — put it in `tests/test_tyre.py`
- Fuel burn confound is visible: pace usually improves early in a stint even as tyres age. Document that the proxy mixes the two until M34 provides the fuel estimate, then re-derive.

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Proxy decreases through the stint | Fuel burn, traffic, weather, or measurement noise dominates the local trend | This does not by itself invalidate the proxy. Record the uncertainty, control for causal fuel/context estimates when available, and retain the simpler representation if a latent state has no held-out benefit. |
| Wild values on short stints | Fewer than 3 green laps to baseline against | Emit `null` with `reason: "stint too short"`, never 0 |
| Proxy differs between two builds | Rolling window crossing a race-control transition | §12: transitions are hard boundaries. Reset the rolling window at any `race_control_transition_flag` (C7 from Rishabh). |

### Deliverables

`src/trackshift/features/tyre_pace.py`, `tests/test_tyre.py`, `tyre_degradation_proxy` on `segments`, registry entries.

### Validation — remains partial

Revalidated `m30_tyre_pace_overlay_v1` at
`c681dad002fa0c932465db84dba8b2295d760351` over 2026 Race/Sprint C1 rows,
with British GP excluded: 408,078 segment rows, 15,038 laps, 5,663 reconstructed
stints, 873 usable stints, and 7,258 laps with normalised pace available.
There are 2,101 explicit insufficient-causal-history rows; rolling resets are
recorded for session start, C7 race-control transition, lap gap, pit metadata,
pit transition, and invalid timing metadata. Fuel context remains
`UNAVAILABLE_C5`: the public C5 module exposes a pure inferred fuel curve but
does not yet materialise a C1-keyed, decision-point-valid fuel input for M30.
No fuel correction was added. CP-08 remains partial because CP-07 remains
incomplete, despite its causal, C7-aware core and passing tests.


### ◐ Every CP-08 gate passes; the checkpoint stays open because CP-07 does

| | |
|---|---|
| C1 rows / laps | 408,078 / 15,038 |
| Stints reconstructed | 5,663, of which **873 usable** |
| Retained laps | 10,232 |
| Insufficient history | 2,101 rows, null with an explicit reason |
| Stint resets | race control 3,441, pit 1,365, session start 346, lap gap 376, invalid timing 135 |
| Proxy distribution | n 8,131, median 0.0 s, p05 −1.02 s, p95 +1.02 s |
| Fuel context | **`UNAVAILABLE_C5`** |
| British GP | excluded (`EXCLUDED_EVENT`) |
| CPU-only | verified |

Every gate has test coverage in `tests/test_tyre.py`: causal truncated-stint
invariance, stint reset at pit and C7 race-control boundaries, insufficient
history as null plus reason rather than zero, one-to-one output with C1 with
all unavailable reasons declared, and CPU-only operation.

**CP-08 does not read CP-07's output.** It is keyed on C1 Race segments;
CP-07 labels Practice 1 laps, and no CP-08 module references
`practice_lap_class`. The dependency is a plan-level one, so CP-08 is held open
by the CP-07 sequencing rule rather than by any measurable defect of its own.
The dependency does run the other way: CP-19 uses `LONG_RUN`/`RACE_PACE` to
choose a fuel-level prior for practice stints.

**`UNAVAILABLE_C5` is retained, and the reason has changed.** M34 now
materialises `fuel_load_kg_est` per (event, session, driver, lap) across 14
circuits, so "no fuel estimate exists" is no longer true. The estimate is still
unusable here for a narrower reason: `build_fuel_curves.py` derives each race's
start fuel from the **completed** race — the finisher set and the median total
burn. The curve's *shape* is causal, since lap *k* subtracts only laps up to
*k*, but its *level* is fixed by how the race turned out. Subtracting it from a
lap's pace would import the race's outcome into a decision-point feature.
Practice and Qualifying levels are priors rather than measurements and fail on
the same ground. The proxy therefore continues to mix tyre ageing with fuel
burn, and the manifest declares the mixture instead of removing it with a value
that is not decision-point valid.

No universal compound ordering is claimed: compound summaries are descriptive
only, as the manifest records.

---

# CP-09 — Segment baselines (M04)

**Goal:** driver, team and field median baselines per segment (§14, §15), the C2 contract, computed on `normal_race_model_eligible` rows only.

**Depends on:** CP-05, and Rishabh's C7 gate (M02). If C7 is not ready, use the track-status decoding from this file's reference section as a stand-in and swap it for C7 later — record which one was used in the manifest.

**Dataset:** all years, Race + Sprint + Qualifying, grouped by `(circuit, segment_id)`.

### Steps

Compute, for each of `segment_time_s`, `exit_speed_kmh`, `brake_onset_m`, `full_throttle_fraction`, `max_speed_kmh`:

```text
driver baseline: median over (same driver, same circuit, same segment_id)
team   baseline: median over (same team,   same circuit, same segment_id)
field  baseline: median over (all cars,    same circuit, same segment_id)
```

Emit median **and** sample count `n`. Rows with `n` below a declared minimum carry `baseline_valid = false` rather than being omitted (C2).

Minimum `n`: **driver 15, team 25, field 100.** Store in `config/baselines.yaml`.

Residuals are `x_current - baseline`, computed at consumption time, not stored per row.

### ✅ Check

- Field baseline `n` ≥ 100 for every segment at every circuit with a full race
- Driver baselines exist for all 22 drivers at circuits they raced
- Residual distributions are roughly centred: median driver residual within ±0.02 s of zero by construction
- `baseline_valid: false` rows are present, not silently dropped — count them
- A known-fast driver shows negative segment-time residuals against the field at most segments; sanity-check one

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Baselines dominated by Safety Car laps | The eligibility gate is not applied | This is the exact failure §37 warns about. Verify the filter before anything else. |
| Team baseline `n` tiny | Driver changes mid-season, or a team with one entry | Fall back to field baseline and set `baseline_valid: false` for the team level only |
| Residuals huge at one segment | Segment spans a pit entry, or a boundary sits mid-corner | Re-check CP-05 boundaries at that `segment_id`; exclude pit-lane rows via `pit_state` |
| Baselines differ between years at the same circuit | Regulation change (2022–2025 vs 2026), resurfacing | Key baselines by `(circuit, year_group, segment_id)` where `year_group` ∈ {`drs_era`, `2026`}. Do not pool across the era boundary (§41). |

### Deliverables

`src/trackshift/track/baselines.py`, `scripts/features/build_baselines.py`, `data/processed/{driver,team,field}_segment_baselines/`, `config/baselines.yaml`, `tests/test_baselines.py`.

### ✅ Completed

Built on the 2026 lake: **8,440 driver, 3,957 team, 355 field** groups over 13
circuits. British GP is absent by design — 75,672 rows held out as the frozen
final test.

| Gate | Measured |
|---|---|
| Field `n` >= 100 for every segment | **min 325**, none below |
| Driver baselines across circuits | 32 drivers, 13 circuits, 22+ per circuit |
| Driver residuals centred on field | **+0.0008 s** (gate: +/- 0.02) |
| `baseline_valid: false` retained, not dropped | 534 driver, 42 team |
| A fast driver is negative vs field | ANT **-0.035 s**, negative at 79.4% of 355 segments |

Gate 5 is the one that carries information: the five fastest by residual came out
ANT, RUS, VER, NOR, PIA. A plausible pace order rather than noise is what tells
you the residuals mean something.

**`brake_onset_m` was missing entirely** — 0 of 8,440 non-null. CP-05 computed
`brake_fraction` but never recorded *where* braking began, so one of the five
required metrics had no source column at all. Fixed in
`scripts/features/build_segments.py`, which now emits `brake_onset_m_offline`.
Verified additive before rebuilding all 14 circuits: identical row count, all 42
pre-existing columns byte-identical, one column added. Braking is now detected on
**45.5% of 744,655** segment rows, and the per-circuit spread is the physical
ordering you would want — hungarian 73.8% highest, italian 35.3% among the
lowest. Flat across circuits would have meant the detection was wrong.

**Completing C1 then broke the Parquet write.** `unavailable_metrics` is keyed by
whichever metrics were missing, so with nothing missing it became a struct with no
child fields, which Arrow cannot represent. The failure was triggered by the data
getting *better*, and would have fired for whoever completed C1. Fixed at the
write boundary — the dict shape is right in memory — by JSON-encoding to one
stable column.

Two honest limits to carry forward:

- **2026 only.** The spec says all years and the code implements the section 41
  `year_group` split correctly, but every group came back `year_group: 2026`
  because the lake is 2026-only. The `drs_era` half fills in when the historical
  lake is built for CP-14; no code change needed.
- **C7 is not available yet**, so this ran on the track-status bridge this plan
  prescribes as the stand-in. `c7_source: legacy_track_status_bridge_v1` is in the
  manifest, so it is auditable and swappable when M02 lands. The filter did bite:
  **145,296 rows** excluded as `NOT_NORMAL_RACE_MODEL_ELIGIBLE`, which is the
  section 37 failure — baselines dominated by Safety Car laps — not happening.

---

# CP-10 — Overtake state machine (M20)

**Goal:** the 2026 Overtake state machine as an explicit, deterministic transition system (§20). **Never** inferred from the `drs` channel — which for 2026 is all zeros anyway.

**Depends on:** CP-03, CP-05.

### The state machine

```text
NOT_ARMED ──(cross Detection Line with gap < detection_gap_s)──> ARMED
ARMED ─────(cross Activation Line)──────────────────────────────> ACTIVE
ACTIVE ────(leave zone / lift / end of zone)────────────────────> NOT_ARMED
any ───────(race control OVERTAKE DISABLED)─────────────────────> DISABLED
DISABLED ──(race control OVERTAKE ENABLED)──────────────────────> NOT_ARMED
```

`DISABLED` comes from CP-03's `artifacts/race_control/*.json` windows (Tier B, genuinely observed). The other transitions need the line positions (Tier A or C).

### Steps

**1. `src/trackshift/rules/state_machine.py`** — a pure function `step(state, position_m, gap_s, race_control, event_rules) -> state`, no I/O, no data frames.

**2. Enumerate transitions in a table** and drive the implementation from it, so the tests can enumerate the same table.

**3. Apply across the lake** to produce `overtake_state` and `overtake_eligible` per segment, `RULE` provenance.

**4. For 2022–2025**, produce `historical_drs_eligible` and `historical_drs_open` instead, from the `drs` channel plus `DRS ENABLED/DISABLED` messages. These are **historical covariates only** (§41) — never named `overtake_*`.

### ✅ Check

- `pytest tests/test_rules.py` covers **below, at, and above** every threshold (§52) — gap at `detection_gap_s - ε`, exactly `detection_gap_s`, and `+ ε`
- Detection, Activation, and disabled behaviour each have a test
- No state transition happens without crossing a line or a race-control message
- Applied to 2026 British GP: `ARMED` occurs on a plausible number of segments; `ACTIVE` is a subset of `ARMED`; `DISABLED` windows match the 3 disable messages in that event
- `overtake_state` is never non-null for 2022–2025, and `historical_drs_*` is never non-null for 2026

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Nothing ever arms | Line positions still `null` from CP-03 | Use Tier-C DRS-proxy zones only in an explicitly development-only fixture; test the state-machine logic independently of the values and fail final mode |
| Everything arms | `detection_gap_s` too large, or gap computed in metres not seconds | `DistanceToDriverAhead` is **metres**. Convert: `gap_s = gap_m / speed_mps`. This is a very easy mistake and it will silently ruin Chain P. |
| `ACTIVE` without `ARMED` | Transition order wrong when both lines fall in one segment | Evaluate at 20 m resolution inside the segment, not once per segment |
| Disabled windows do not line up | Session-time vs lap-time confusion | `rcm.json` `time` is a timestamp; `laptimes.json` `sesT`/`lST` are session-relative seconds. Align on session time, and unit-test the conversion. |

### Deliverables

`src/trackshift/rules/state_machine.py`, `tests/test_rules.py` (threshold triples), `overtake_state`/`overtake_eligible`/`historical_drs_*` columns.

### ✅ Completed

Verified on Monaco, where the state distribution matches the zone geometry
rather than merely being non-empty:

| State | Rows | Share | Geometry predicts |
|---|---|---|---|
| `ARMED` | 4,599 | **2.0%** | detection 2920 to activation 3000 = 80 m of 3,284 = 2.4% |
| `ACTIVE` | 16,817 | **7.2%** | activation to lap end = 284 m of 3,284 = 8.6% |
| `DISABLED` | 34,771 | 14.8% | race-control windows |
| `NOT_ARMED` | 178,576 | 76.1% | |

Both sit just under their geometric ceiling, which is what you want -- not every
lap arms. British GP is absent because it is held out as the frozen final test.

Getting here needed four fixes, three of them bugs that each independently
produced a plausible-looking empty result:

**Detection Lines were unsourced.** The FIA Race Director's Competition Notes
state that the Article B 7.2.1 detection line is at Safety Car Line 1, and that
Safety Car Line 1 is the pit-entry bollard. Pit entry is measurable: the row
where `lap_start_session_s + lap_elapsed_s` meets `pit_in_session_s`. Most
circuits pin to a single 20 m bin over dozens of in-laps. Evidence in
`artifacts/detection_lines/harvest_2026.json`; 11 circuits configured as
`DERIVED_TELEMETRY`, Dutch and Hungarian left null because both measured past
their own lap length.

**One Detection Line per lap, not one per zone.** The notes say "the detection
line" in the singular at one location. Our per-zone model was a DRS-shaped
assumption of our own, marked UNVERIFIED, and was never evidence. Three
independent circuit descriptions agree -- Silverstone's detection at Vale,
Suzuka's before the final chicane, Monza's before Parabolica -- all pit-entry
locations. `resolved_zones()` now shares the lap's line across every zone, and a
zone-level value would still win if one is ever documented.

**`detection_gap_s` was silently missing from every event.** `load_event_rules`
merged event files over the season defaults with a shallow replace, so an event
that defined its own `overtake` block for zones wiped out
`overtake.detection_gap_s` from common.yaml. The state machine had no arming
threshold and nothing armed anywhere -- and the config still validated, because
the key existed in common.yaml. This was the real blocker; the missing Detection
Lines had been masking it.

**`ACTIVE` survived the lap boundary.** Two Tier-C proxy zones end beyond the
telemetry lap length (Monaco 3300 m of 3284, Australian zone 4), so `ZONE_EXIT`
could never fire and the car stayed ACTIVE for the rest of the session -- 70% of
rows on the first corrected run. A zone lives inside a lap, so crossing the line
now ends any armed or active state.

---

# CP-11 — Rule engine (M19)

**Goal:** `legal_actions(state, event_rules) -> ActionSet` over the shared `StrategicState` contract C3. **Illegal actions are absent from the set, never low-scored** (§31, §32).

**Depends on:** CP-10.

### Steps

**1. The envelope evaluator — build this first.** This is the single most reused function you will write, and §32 makes you its sole owner:

```python
def max_electrical_power_kw(speed_kmh: float, mode: str, event_rules: dict) -> float:
    """Regulatory cap at this speed. The ONLY implementation in the system."""
    curve = event_rules["power_envelope"][mode.lower()]   # "normal" | "override"
    return float(np.interp(speed_kmh, curve["breakpoints_kmh"], curve["max_power_kw"]))
```

`np.interp` clamps at both ends, which is exactly the required behaviour. Everything that needs a cap — the DP, the simulator, the twin's diagnostics (CP-18), the override discriminator (CP-18b) — imports this one function. **No envelope number may appear anywhere else in the codebase.** Grep for it in CI if you like; a stray `350` in a physics module is the failure this rule exists to prevent.

**2. Action space** (§31): `deploy_level ∈ {0, 0.25, 0.5, 0.75, 1.0}` × `lift_amount ∈ {0, 0.25, 0.5}` = 15 candidates before filtering.

**`deploy_level` is a fraction of the cap at the current speed, not of a fixed power:**

```text
requested_kw = deploy_level * max_electrical_power_kw(speed_kmh, applicable_mode, rules)
```

So `legal_actions` now **requires `speed_kmh` in the state**, and returns `cap_kw`, `applicable_mode`, and `delivered_power_kw` per action so the DP can account energy against what is actually deliverable rather than against the request (§31). Tell Rishabh the moment you change this signature — it changes his DP state.

**3. Filters, each with its own rule key and test:**

| Filter | Removes |
|---|---|
| Power envelope | Any action whose `delivered_power_kw` would exceed `max_electrical_power_kw(speed, applicable_mode)`. The cap moves with speed, so this filter's effect differs along a single straight. |
| Deploy budget | Any action whose `delta_e_mj` would exceed `deploy_limit_per_lap_mj`, tracked as `ers_deploy_budget_remaining_est_mj` for the lap so far |
| Harvest budget | Any lift/coast choice whose implied recovery would exceed `ers_harvest_budget_remaining_est_mj` — a separate regulatory constraint from the deploy budget, not the same number under a different name |
| Store capacity | Any deployment exceeding the current estimated `ers_soc_est_mj`, or that would push it above `ers_store_capacity_mj` under harvest |
| Race control | All deployment above baseline when `overtake_disabled` |
| Eligibility | Override-envelope actions when state is not `ACTIVE` — the mode selects which curve applies |

**3. Return `excluded`** alongside `actions` — each entry naming the rule and its source, so the UI can explain the exclusion on hover (API.md §5.6).

**4. Strict mode**: refuse to start if any consumed rule value is `UNVERIFIED` or `PROXY_*` while `strict_mode: true`.

**5. Stub mode** (`--stub`): return a fixed legal set with the correct schema so Rishabh's DP can develop against C3 before the real values land (MODELS.md §6.1).

### ✅ Check

- **Zero illegal actions** in any returned set, asserted over a sweep of 10,000 random states **spanning 0–360 km/h** — the speed sweep is the point, since the cap is a function of it
- `actions` is never empty — coasting (`deploy_level: 0, lift_amount: 0`) must always be legal, at every speed
- Every excluded action names a rule key that exists in the config
- Threshold triples tested for every numeric limit
- **Envelope evaluator tested immediately below, exactly at, and immediately above every breakpoint** of both curves, plus both clamped regions, plus the separation speed (§20.1, §32)
- Envelope is monotone non-increasing above the taper start, and never negative at any speed
- At a speed above the taper, `deploy_level: 1.0` yields **less** power than at a speed below it — assert this directly; it is the behaviour the whole change exists to produce
- `grep -rn` finds no numeric power constant outside `config/`
- Strict mode raises on the current `UNVERIFIED` config, and passes once values are `RULE_FIA`
- Unknown event raises `UNKNOWN_EVENT`; missing rule key raises `RULE_KEY_MISSING` and **never defaults to enabled** (C3)

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Empty action set | Energy filter too aggressive at low SOC | Coasting must never be filtered. Add an explicit assertion, not a fallback. |
| DP produces illegal plans | DP scoring illegal actions low instead of the engine removing them | §31 is explicit: they must not enter the candidate set. Fix in the engine, tell Rishabh. |
| Engine is slow inside DP | Rebuilding config per call | Load config once, cache per event; the function must stay pure but the config can be a bound argument |
| Envelope evaluator is the DP hot loop | `np.interp` per call inside a nested loop | Precompute a lookup table over a 1 km/h speed grid per mode at config load; the curve is piecewise linear so a table is exact at breakpoints and negligibly off between them. Verify the table against the function before using it. |
| Cap looks constant in the output | `speed_kmh` not plumbed into the state, so a default is being used | The signature change is the fix. A constant cap silently reverts the entire §20.1 behaviour and will not otherwise announce itself — assert cap varies across a straight in a test. |

### Deliverables

`src/trackshift/rules/engine.py`, `src/trackshift/rules/api.py` (C3), `tests/test_rules.py` extended, stub mode.

### ✅ Completed

`src/trackshift/rules/engine.py` and `src/trackshift/rules/api.py` (the C3
boundary did not exist), with `tests/test_rules.py` at **27 tests**. Suite 496
passed.

| Gate | Measured |
|---|---|
| Zero illegal actions | 10,000 seeded states across 0-360 km/h, every action re-checked against a freshly evaluated cap |
| `actions` never empty; coasting always legal | asserted at every speed; the engine **raises** if coast is ever filtered |
| Threshold triples | below/at/above every breakpoint of both curves, plus both clamped regions and the separation speed |
| Deploy buys less above the taper | asserted directly |
| Cap varies along a straight | asserted — a constant cap silently reverts section 20.1 and will not otherwise announce itself |
| Every exclusion names a real rule key | each fed back through `resolve()`, which raises if the key does not exist |
| Strict mode refuses `UNVERIFIED` | raises on the current config |

**Null limits are reported, not assumed.** All three energy limits are
`null`/`UNVERIFIED`, so those filters cannot run. Instead of behaving as "no
limit", the result carries `filters_not_applied` naming each skipped filter and
why. They activate with no code change when the FIA values land.

**The section 32 check is AST-based, not grep**, so prose mentions of 350 kW in
docstrings do not cry wolf — a check that cries wolf gets muted. It immediately
caught a hardcoded `350.0` in this checkpoint's own stub mode, now removed: the
stub short-circuits the *filters*, never the envelope. Three pre-existing
violations are allowlisted with the reason each is not a free fix, so anything new
fails the build:

| Location | Constant | Why it is not free |
|---|---|---|
| `src/trackshift/track/segmentation.py:108` | `envelope_taper_kmh: 290.0` | duplicates `power_envelope.separation_speed_kmh`; closing it bumps `geometry_version` |
| `scripts/simdata/twin.py:64-65` | `350.0` twice | Rishabh's simulator; raise it with him rather than editing his module |

**Tell Rishabh:** `legal_actions` requires `speed_kmh` in the state and returns
`cap_kw`, `applicable_mode` and `delivered_power_kw` per action. That changes his
DP state, which is why section 31 says to tell him the moment the signature moves.

---

# CP-12 — Eligibility probability (M21)

**Goal:** `P(gap at Detection Line < threshold)` and the §22 derived features — not a deterministic boolean when uncertainty is material.

**Depends on:** CP-10, CP-11.

### Steps

**1. Project the gap forward** to the Detection Line from the current time and distance gap, relative speed, relative acceleration, gap rate, tyre state, selected feasible energy action, track geometry, and causal rival-response assumption. The baseline projection may begin with closing rate and trailing variance, but it must expose which terms it actually used.

**2. Model the projected gap as a distribution**, not a point:

```text
projected_gap_at_detection ~ Normal(mu, sigma)
mu    = gap_now - closing_rate * time_to_detection_line
sigma = std of closing rate over the trailing 5 segments, propagated
p_eligible = P(projected_gap < detection_gap_s) = Phi((threshold - mu) / sigma)
```

Use a **trailing** window only — §12 forbids centred windows for live features.

**3. Derive the rest of §22:** `eligibility_margin = threshold - gap_at_detection`, `energy_required_to_unlock_kj` with uncertainty (from CP-21's causal energy-to-time and energy-to-gap response under a declared target probability), `eligibility_fragility_per_kj` where supported, `distance_detection_to_activation`, `distance_activation_to_brake`, `delta_v_at_activation`.

`energy_required_to_unlock_kj` depends on CP-21 — emit `null` until then, then backfill.

### ✅ Check

- `p_eligible ∈ [0,1]`, and → 1 as the gap goes well below threshold, → 0 well above
- Calibration: bin `p_eligible` into deciles over historical opportunities and compare against the observed arming rate. Expected calibration error **< 0.10**
- `sigma` grows with distance to the Detection Line — a projection 2 km out must be less certain than one 200 m out
- `eligibility_margin` sign convention: positive means eligible
- No centred-window leakage — the truncation test from CP-08 applies here too
- Any counterfactual feature is reproducible from the decision-time state and declared action, without observed future gap or pass outcome

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| `p_eligible` always 0 or 1 | `sigma` collapsing to ~0 | Floor `sigma` at a physically sensible minimum (0.05 s); a perfectly certain projection is not real |
| Badly calibrated | Normal assumption wrong; closing rate is skewed | Try a Student-t, or an empirical quantile approach over historical projections at the same distance-to-line bucket |
| Feature is null everywhere | No Detection Line in config for that event | Expected while CP-03 is `UNVERIFIED`. Use the Tier-C proxy for development and record which tier produced each value. |

### Deliverables

`src/trackshift/rules/eligibility.py`, `src/trackshift/features/overtake_features.py`, C6 columns, `tests/test_eligibility.py`.

---

# CP-13 — Overtake-opportunity dataset (M07)

**Goal:** the §19 dataset — **one row per opportunity per decision checkpoint**, with a strict feature-cutoff rule. This is the biggest change from the original plan and the foundation of Chain P.

**Depends on:** CP-12, and Rishabh's C8 (battle episodes, M05). If C8 is late, you can define opportunities directly from `DriverAhead`/`DistanceToDriverAhead` and swap to `battle_id` later — but the split (C9) needs `battle_id`, so do not train before it lands.

### Definition of an opportunity

An attacker–defender pair approaching a Detection Line where the gap is plausibly close enough to matter. Concretely: `gap_s < detection_gap_s * 2` at the Detection Line, both cars `normal_race_model_eligible`, in a Race or Sprint session.

### The three checkpoints

Each opportunity emits **three rows**, one per checkpoint, sharing an `opportunity_id`:

| `decision_checkpoint` | `feature_cutoff_distance_m` | May contain |
|---|---|---|
| `DETECTION` | Detection Line distance | Everything at or before the Detection Line |
| `ACTIVATION` | Activation Line distance | Also activation-time quantities |
| `BRAKING` | Braking-point distance | Also braking-point quantities |

**A `DETECTION` row must never contain `gap_at_activation`, activation speed, braking speed, or any centred/future rolling statistic.** Enforce this in code, not by convention: the builder holds a per-checkpoint allowlist from the registry's `decision_checkpoint` field, and raises if a disallowed column is populated.

### The label

`passed_by_outcome_horizon` with a **fixed, versioned** definition. Start with `zone_exit_v1`: the attacker is ahead at the exit of the activation zone in which the opportunity occurred. Store the definition string in every row and in the artifact manifest.

`pass_attempted` and the outcome distance are stored **for audit only** and must never be used as features (§19).

### Row key and features

```text
opportunity_id  decision_checkpoint  feature_cutoff_distance_m  outcome_horizon
year event session lap zone battle_id attacker defender attacker_team defender_team
regulation_era
gap_at_checkpoint  eligibility_margin  delta_speed_checkpoint  delta_acceleration_checkpoint
closing_rate  closing_rate_trend  recent_pace_delta
attacker_tyre_compound defender_tyre_compound attacker_tyre_life defender_tyre_life
attacker_tyre_degradation_proxy defender_tyre_degradation_proxy
fuel_load_delta_kg_est  ers_energy_delta_kj_est          # null until CP-18/CP-19
historical_drs_eligible historical_drs_open              # 2022-2025 only
overtake_eligible overtake_state                          # 2026 only, rule engine
sector corner_id corner_type corner_phase
wind_head_component_mps wind_cross_component_mps track_temperature wet_track_flag
distance_detection_to_activation distance_activation_to_brake distance_remaining_in_zone
position_context
passed_by_outcome_horizon                                 # label
pass_attempted outcome_distance_m                         # audit only, never features
```

**Only `normal_race_model_eligible`, green-flag opportunities enter the training set.** SC/VSC/pit/unknown rows are built and retained for audit with a flag, but excluded from the model set.

### Expected volume

Rough estimate: a race has 20–60 close approaches to a Detection Line. Across 2026's 14 events plus sprints, expect **~1,500–4,000 opportunities → 4,500–12,000 rows**. Across 2022–2025 (DRS era, more events, more DRS passes), expect **~15,000–40,000 opportunities**. If your numbers are an order of magnitude off, the gap threshold or the zone definition is wrong.

### ✅ Check

- Exactly 3 rows per `opportunity_id`, no more, no fewer
- **Leakage test** (`tests/test_opportunities.py`): for every `DETECTION` row, every activation- and braking-scoped column is null. This test is non-negotiable.
- `feature_cutoff_distance_m` strictly increases across the three checkpoints of one opportunity
- Label base rate is plausible: 10–35% passes. Below 5% or above 60% means the opportunity definition is wrong.
- Every row's `battle_id` exists in C8
- Zero rows with `normal_race_model_eligible: false` in the model set
- 2026 rows have `overtake_*` populated and `historical_drs_*` null; 2022–2025 the reverse

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Far too many opportunities | Gap threshold too loose, or counting every segment | One opportunity per (battle, zone, lap), not per segment |
| Base rate ~2% | Counting hopeless approaches as opportunities | Tighten to `gap_s < detection_gap_s * 1.5`; require a positive closing rate |
| Base rate ~70% | Only counting cases that were already passes | Check that opportunities are defined at the Detection Line, before the outcome is known |
| Leakage test fails | Builder populates all columns then filters | Build per checkpoint from a checkpoint-scoped view of the telemetry, truncated at `feature_cutoff_distance_m`. Never build the full row and blank fields afterwards. |
| Label ambiguous on multi-car battles | Attacker passes one car, another repasses | `zone_exit_v1` is about *this* pair only. Document it; the counterattack case is M23 (Rishabh's), not the label. |

### Deliverables

`src/trackshift/features/opportunities.py`, `scripts/features/build_opportunities.py`, `data/processed/overtake_opportunities/`, `tests/test_opportunities.py`, registry entries with `decision_checkpoint` on every feature.

### ✅ Completed

**4,970 opportunities, 14,910 rows** across 8 events, every gate measured on the
produced data rather than on fixtures:

| Gate | Measured |
|---|---|
| Exactly 3 rows per `opportunity_id` | **PASS**, 14,910 = 4,970 x 3 |
| Checkpoints strictly ordered | **PASS** on distance since detection |
| **Leakage** | **PASS** -- no activation or braking column populated in any DETECTION row |
| Label base rate 10-35% | **15.4%** |

### Contract repair status

CP-13 is partial pending the M07 v2 contract repair. The initial generated
opportunities exposed a constant projected_gap_sigma_s, duplicate gap
representations, deterministic eligibility margin, and circuit-identifying
geometry in the CP-14 matrix. Those fields are now metadata or unavailable
Quantities and the pre-training audit rejects recurrence. CP-13 and CP-14
remain unchecked for completion until the rebuilt non-British dataset passes
the repaired contract.

### A2 — the causal C8 `battle_id` join

M07 previously wrote `battle_id=None` on every row, which left CP-14 with no
split unit. The join is now made in `trackshift.features.battle_join`, and the
part worth recording is why matching on the lap is not enough.

C8 emits mostly **single-lap** episodes — 25,462 of 25,877 Race/Sprint episodes
— and routinely emits *several* for one pair on one lap, because it closes an
episode when the pair separates and opens a new one when it re-forms. Matching
on `(pair, lap)` alone therefore left **28.4%** of 2026 opportunities ambiguous:

| Key | JOINED | AMBIGUOUS | No episode |
|---|---|---|---|
| pair + lap | 68.4% | 28.4% | 3.2% |
| pair + lap + **distance** | **92.5%** | **0%** | 7.4% |

The disambiguator is distance. `battle_segment_rows.jsonl` carries each
episode's within-lap extent, and on the real 2026 data **all 5,745
multi-episode (pair, lap) cells have disjoint distance spans — zero overlaps**.
An opportunity anchored at its Detection Line distance therefore resolves to
exactly one episode, exactly, with no grouping and no guessing.

Two rules hold the join honest:

- **No `battle_id` is ever invented.** Every opportunity resolves to one episode
  or records why it did not (`battle_join_status`: `NO_EPISODE_FOR_LAP`,
  `NO_EPISODE_FOR_PAIR`, `NO_DEFENDER_CODE`, `AMBIGUOUS_EPISODE`). A fabricated
  id would place unrelated opportunities in one C9 split group, which is the
  precise leak the unit exists to prevent.
- **Unjoined rows are excluded from training, never relabelled.** The ~7.5%
  with no C8 episode are dropped by the trainer with their count and reasons
  recorded in the manifest. C9 refuses a partially populated unit outright, so
  the alternatives were to drop them or to lower the unit, and lowering the unit
  is what CP-14 forbids.

C8 names both cars by driver code; M07 knows its defender only by car number,
because that is what the telemetry reports. The number-to-code map is built per
`(year, event, session)` from the lake, which carries both — per session because
car numbers are reused between seasons, and a cross-season map would attach an
opportunity to another year's driver.

**The persistent C9 assignment** is built once by
`scripts/features/build_split_assignments.py`, hashed, and read back by CP-14
rather than re-derived per run. Two runs that re-derive can disagree with
nothing in the artifacts to say why. The planner refuses a missing assignment,
one built over the wrong unit, and a stale one that predates an M07 rebuild.

The base rate is the gate that carries information. A definition that counted
hopeless approaches would sit near 2%, and one that only counted completed
passes near 70%. Landing mid-band is what says the opportunity is being defined
at the Detection Line, before the outcome is known.

**An opportunity usually crosses the start line, and this was the hard part.**
The Detection Line is at Safety Car Line 1, near the lap end, so the activation
zone is normally early on the *following* lap -- **90% of opportunities** wrap,
100% at every circuit except Canada and Monaco. Three things followed:

- Ordering by raw lap distance rejects every wrapping opportunity, because the
  activation distance is numerically smaller than the detection distance. Rows
  now carry `feature_cutoff_offset_m`, the distance travelled since detection,
  which is strictly increasing; `feature_cutoff_distance_m` still holds the true
  lap distance. `lap_length_m` must be supplied when an opportunity wraps, and
  is refused rather than assumed.
- The builder works on a continuous per-driver distance axis spanning laps. A
  per-lap frame cannot express the opportunity at all.
- CP-10's lap-boundary reset was right for `ACTIVE` and wrong for `ARMED`: a car
  detected at the end of a lap must stay armed into the next one, which is the
  whole point of a detection line placed there.

Before this, 9 of 11 configured circuits produced nothing and Canada produced
436 from its one non-wrapping zone -- a result that looked like a threshold
problem and was really a coordinate-system problem.

---

# CP-14 — Pass-model benchmark (M10)

**Goal:** the §26 benchmark set trained **per decision checkpoint** — 3 checkpoints × 5 families = **15 fits** — selected on calibration quality, not accuracy.

**Depends on:** CP-13, and C9 (Rishabh's leakage-safe splitter).

### Splits (§40)

Never random. Use C9 with `unit="battle_id"`.

| Split | Content |
|---|---|
| Train | 2022–2024, all events |
| Validation | 2025, all events |
| Test | 2026, **excluding British Grand Prix** |
| Frozen final test | 2026 British Grand Prix — untouched until model and features are frozen |

Also run leave-one-track-out on 2026 as a secondary generalisation check.

### Models and starting parameters

Train each on the same feature matrix per checkpoint. Fix `random_state=42` everywhere and record it.

**1. Logistic Regression** — the interpretable baseline (§26). Every other model must beat it or lose.

```python
LogisticRegression(penalty="l2", C=1.0, solver="lbfgs", max_iter=2000,
                   class_weight=None, random_state=42)
# Standardise numeric features; one-hot the categoricals; keep the pipeline in the artifact.
```

**2. LightGBM**

```python
LGBMClassifier(n_estimators=2000, learning_rate=0.03, num_leaves=31,
               max_depth=-1, min_child_samples=40, subsample=0.8,
               subsample_freq=1, colsample_bytree=0.8,
               reg_alpha=0.0, reg_lambda=1.0, random_state=42)
# early_stopping_rounds=100 on the validation split, metric="binary_logloss"
```

**3. XGBoost**

```python
XGBClassifier(n_estimators=2000, learning_rate=0.03, max_depth=5,
              min_child_weight=5, subsample=0.8, colsample_bytree=0.8,
              reg_lambda=1.0, eval_metric="logloss",
              early_stopping_rounds=100, random_state=42)
```

**4. CatBoost** — handles the categorical pairs (`compound_pair`, `corner_type`, teams) natively.

```python
CatBoostClassifier(iterations=3000, learning_rate=0.03, depth=6,
                   l2_leaf_reg=3.0, loss_function="Logloss",
                   eval_metric="Logloss", od_type="Iter", od_wait=100,
                   random_seed=42, verbose=200)
```

**5. Small MLP** — optional (§26). At ~40 k rows a single fit is under a minute on CPU; the A6000 is only worth it for a large hyperparameter sweep. See the A6000 decision table below.

```python
MLPClassifier(hidden_layer_sizes=(64, 32), activation="relu", alpha=1e-3,
              learning_rate_init=1e-3, max_iter=500, early_stopping=True,
              n_iter_no_change=25, random_state=42)
```

### Feature groups (for CP-23 ablation)

Declare these in the registry so the ablation harness can toggle them: `geometry`, `tyre`, `weather`, `team_identity`, `driver_identity`, `rolling_battle_trends`, `race_context`, `fuel_ers` (null until CP-19).

### Metrics (§26, §55)

Report all, with **N**:

```text
Brier score      <- primary
log loss
calibration ECE and a reliability plot
ROC-AUC
PR-AUC           <- matters, the classes are imbalanced
```

**Selection rule (§26): a model with slightly lower ROC-AUC but materially better calibration wins.** The DP consumes probabilities, not classifications.

### ✅ Check

- All 15 fits complete and produce artifacts with locked feature schemas
- Every model beats a constant-base-rate predictor on Brier
- LightGBM/XGBoost/CatBoost beat Logistic Regression on log loss — if not, the features are weak, not the models
- `ACTIVATION` and `BRAKING` models outperform `DETECTION` (they see more) — if `DETECTION` wins, suspect leakage
- The demo-event guard fires when you deliberately include BGP — test it
- Leave-one-track-out variance is modest: Brier std across folds < 0.05. High variance means the model is memorising circuits.
- Identity features (§17): train with and without driver/team identity. If identity alone carries most of the signal, the model is memorising drivers rather than learning racecraft.

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| ROC-AUC > 0.95 | Leakage — almost certainly a post-outcome feature | Check the CP-13 leakage test; check `pass_attempted` and `outcome_distance_m` are excluded; check the split is by `battle_id` |
| All models ≈ base rate | Features carry no signal, or the label is noise | Verify the label by hand on 20 opportunities. Then check `gap_at_checkpoint` alone — it should have real signal on its own. |
| Trees hugely beat logistic | Strong non-linearity, or one dominant feature | Inspect feature importance; if one feature dominates, confirm it is legitimately available at that checkpoint |
| Validation good, 2026 test poor | Regulation-era shift — this is exactly what §41 predicts | Not a bug. Record the gap; it motivates the deferred §41 era handling in `pass_model/era.py`. |
| Calibration poor but AUC fine | Tree probabilities are typically miscalibrated | Expected — that is what CP-15 exists for. Do not tune it away here. |

### Deliverables

`src/trackshift/pass_model/candidates.py`, `scripts/train/train_pass_model.py`, `artifacts/models/pass/<version>/` per checkpoint with `feature_schema.json` and `manifest.json`, `artifacts/validation/pass_model_report.md`.

### Split methodology and evidence grades

CP-14's acceptance gate is not "a benchmark ran". It is a benchmark run on the
documented split. Three conditions must hold, and they fail **independently**:

1. the split unit is `battle_id`,
2. the design is the `year_table`,
3. the years that table names are actually present.

A run can be perfectly leakage-safe and still not be a CP-14 pass, because the
historical seasons do not exist yet. Conflating those two states is the failure
this section exists to prevent, so `grade_evidence()` classifies every run and
the grade travels into the manifest, the gate list and the report header:

| Grade | Condition | May be quoted as a CP-14 pass |
|---|---|---|
| `FULL` | Documented split: `battle_id` unit, `year_table` design, all of 2022–2025 present | **Yes** |
| `INTERIM` | `battle_id` unit, but the year table was unavailable (or missing training years) | No |
| `REDUCED` | Split unit coarser than `battle_id` (`--allow-event-split`) | No |

An `INTERIM` run is worth doing — it exercises the whole pipeline before the
historical lake lands, and its numbers are real measurements on a leakage-safe
split. What it does not do is answer CP-14's question about generalising across
regulation eras, because with one season there is no era axis to generalise
over.

**Two-stage plan, and the second stage is gated on Owner A's C1/C7 spine:**

*Stage 1 — now (2026 only).* Design resolves to `leave_one_event_out`, unit
stays `battle_id`. Grades `INTERIM`. Records the pipeline, the feature audit,
the identity ablation and the per-event variance.

*Stage 2 — once 2022–2025 opportunities exist.* Re-run with
`--require-documented-split`, which refuses to start unless the run would grade
`FULL`. That flag exists so a run intended as the acceptance gate cannot quietly
degrade to `INTERIM` when an upstream partition is missing, which is exactly how
a blocked checkpoint gets written up as a passing one.

**The split unit never degrades.** `--allow-event-split` exists for callers who
genuinely want the coarser unit, and stamps `REDUCED` on everything it touches.
CP-14 itself always runs with `require_unit="battle_id"` against a *persisted*
C9 assignment, so two runs cannot silently disagree about which battle went
where.

### Minimum fold support

An event needs at least `MIN_FOLD_POSITIVES` (10) positive labels before it may
serve as a test or validation fold. Below that it is **kept in training** and
excluded from the fold rotation, with the exclusion recorded in the plan notes.

This is not a tuning knob, it is a correctness guard, and the real data shows
why. Monaco 2026 yields **63 opportunities with 1 positive**. Scored as a test
fold it produces a ROC-AUC and a PR-AUC computed against a single positive —
noise presented as measurement. Used as a validation fold, it early-stops
LightGBM and XGBoost against one positive, which halts essentially at random.
Either would then feed CP-14's "Brier std across folds < 0.05" gate, letting one
unmeasurable fold decide whether the model looks like it memorised circuits.

Keeping the thin event in training rather than dropping it is the deliberate
half: its rows are perfectly good training data, and only its *metrics* are
meaningless. On the 2026 data this takes the rotation from 7 folds to 6.

If fewer than three scorable events remain, the design raises rather than
building a rotation whose numbers could not be read.

### DRS is not a modelling parameter

`historical_drs_*` are refused from every feature matrix, independently of the
registry's `metadata_only` tag, by interaction group and by name token, with the
pre-training audit as a backstop. DRS exists only in 2022–2025 and has no 2026
counterpart; the 2026 Overtake mechanism it would stand in for works
differently. A model that leans on it learns the DRS era and then carries that
lesson into a season where the mechanism does not exist.

---

# CP-15 — Probability calibration (M11)

**Goal:** compare uncalibrated vs Platt/sigmoid vs isotonic (§27), fitted on data **separate from the final test**.

**Depends on:** CP-14.

### Steps

**1. Calibration data must not be the test data** (§27: "Do not calibrate and evaluate on the same event"). Use the 2025 validation split to fit calibrators, evaluate on 2026-excluding-BGP.

**2. Fit all three variants** per checkpoint per model family:

```python
CalibratedClassifierCV(base_estimator, method="sigmoid", cv="prefit")   # Platt
CalibratedClassifierCV(base_estimator, method="isotonic", cv="prefit")  # isotonic
```

Isotonic needs data — with fewer than ~1,000 calibration samples it overfits; prefer Platt below that. Record which you used and why.

**3. Reliability diagrams** into `artifacts/validation/` — 10 equal-count bins, predicted vs observed, with counts per bin.

### ✅ Check

- ECE improves after calibration; target **< 0.05**
- Reliability curve within the diagonal's confidence band in every bin with n ≥ 50
- Brier improves or holds; log loss improves
- Calibration does not materially hurt ROC-AUC (ranking is monotone-preserved by both methods, so any change means a bug)
- Calibrator fitted on 2025, evaluated on 2026 — assert the disjointness in code

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Isotonic makes it worse | Too few calibration samples; step function overfits | Switch to Platt; report both |
| Calibration good on validation, bad on test | Era shift again | Fit the calibrator on 2026 data (excluding BGP) instead, and note that historical calibration does not transfer |
| Predictions cluster at 0 and 1 | Overconfident trees | Reduce depth/`num_leaves`, increase `min_child_samples`, then recalibrate |

### Deliverables

`src/trackshift/pass_model/calibration.py`, reliability plots, calibrated artifacts, `tests/test_calibration.py` asserting bounds and disjointness.

---

# CP-16 — Ensemble spread (M12)

**Goal:** `ensemble_spread` for API.md and §42 — the uncertainty the UI shows next to `p_pass`.

**Depends on:** CP-15.

### Steps

Train **5 seeds** of the selected family per checkpoint (`random_state` 42, 43, 44, 45, 46), calibrate each on the same calibration split, and report:

```text
p_pass          = mean of member probabilities
ensemble_spread = standard deviation across members
```

Keep members in the artifact so the API can recompute; they are small.

### ✅ Check

- Spread is larger in sparse regions of feature space — plot spread against `gap_at_checkpoint`; it should widen where data is thin
- Mean of the ensemble is at least as well calibrated as any single member
- Spread is not ~0 everywhere (that means the seeds are not actually different — check that subsampling is on)
- Spread is not enormous (> 0.25 typical) — that means the model is unstable and needs regularisation

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Spread ≈ 0 everywhere | Members are identical — the seed is not reaching the sampler | Confirm `subsample < 1.0` and `colsample_bytree < 1.0`; a fully deterministic learner needs bagged data (different bootstrap per member), not just a different seed |
| Spread huge (> 0.3) in dense regions | Model genuinely unstable, not uncertain | Regularise: fewer leaves, higher `min_child_samples`. Large spread where data is plentiful is a defect, not information. |
| Spread uncorrelated with data density | Members overfitting the same noise | Increase member diversity: vary `num_leaves` as well as the seed, or bag the training rows |
| Ensemble mean worse calibrated than a single member | Averaging before calibration | Calibrate each member on the same split, then average — not the reverse |

### Deliverables

`src/trackshift/pass_model/ensemble.py`, 5 member artifacts per checkpoint, spread column in C4.

---

# CP-17 — Pass-model fine-tuning (M13)

**Goal:** take whichever model CP-14/CP-15/CP-16 selected and tune it on the
2026 dataset, then report honestly whether the tuning bought anything.

**Depends on:** CP-14, and CP-15/CP-16 where a calibrated or ensembled form is
the one being tuned.

> **Scope change.** CP-17 previously specified the §41 regulation-era
> comparison — five strategies for making 2022–2025 DRS-era data inform the
> 2026 model. That comparison needs two eras and the opportunity table holds
> one, so it was unrunnable and is deferred as a §41 follow-up rather than a
> checkpoint. The implementation survives in
> `src/trackshift/pass_model/era.py` and `scripts/train/compare_eras.py`, which
> report five of six strategies blocked with reasons; pick it up when the
> historical seasons land.

### The search

One family per checkpoint, chosen from CP-14's benchmark on Brier (§26) unless
`--family` overrides it. Grids are deliberately small — a few thousand
opportunities over six folds gives a per-configuration standard error
comparable to the gap between neighbouring settings, so a large grid mostly
selects noise and reports it as a discovery.

| Family | Tuned |
|---|---|
| LightGBM | `num_leaves`, `learning_rate`, `min_child_samples`, `reg_lambda` |
| XGBoost | `max_depth`, `learning_rate`, `min_child_weight`, `reg_lambda` |
| CatBoost | `depth`, `learning_rate`, `l2_leaf_reg` |
| Logistic | `C` |
| MLP | `hidden_layer_sizes`, `alpha`, `learning_rate_init` |

Trial 0 is always CP-14's untuned block, so every search has a floor to beat.

### Three rules that keep the result honest

**The test split is never tuned on.** 2026-excluding-BGP is CP-14's test set.
Selecting hyperparameters against it turns the reported test score into a
training score. Selection happens on each fold's *validation* split; the
baseline and the winner are scored on the test split exactly once, afterwards.

**A gain inside fold-to-fold noise is not a gain.** A search over two dozen
configurations always produces a leader. Whether that leader is distinguishable
from the untuned model is a separate question, so the winner must beat the
baseline by more than the baseline's own fold-to-fold standard deviation before
the verdict is `ADOPT_TUNED` rather than `KEEP_BASELINE`.

**ROC-AUC does not select alone.** §26 is explicit: the DP consumes
probabilities, not classifications, so a model with slightly lower ROC-AUC and
materially better calibration wins. `--objective` defaults to `roc_auc` because
that is often what is asked for, but every metric travels with every result and
a calibration regression is flagged in the report.

### ✅ Check

- Trial 0 (CP-14's parameters) is present in every ranking
- The winner's test score is computed once, after selection, never during it
- `gain_is_real` is reported against the baseline's fold spread, not asserted
- Every objective's value is shown for baseline and winner, not just the tuned one
- A calibration regression under a ranking objective raises a visible warning
- Reruns with the same seed reproduce the same trials

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Every verdict is `KEEP_BASELINE` | CP-14's block is already reasonable for a small tabular problem, which is the common case | A legitimate finding. Record it; do not widen the grid until it finds something. |
| ROC-AUC rises, Brier and log loss worsen | Chasing ranking at the cost of probability quality — exactly what §26 warns about | The planner reads probabilities. Re-run with `--objective brier` and compare. |
| Winner differs on every rerun | The gains are inside noise and the search is selecting variance | Raise the fold count or accept the baseline |

### Measured on the first INTERIM run

DETECTION, LightGBM, 12 trials over 6 folds, `--objective roc_auc`:

| | ROC-AUC | PR-AUC | Brier | Log loss | ECE |
|---|---:|---:|---:|---:|---:|
| baseline | 0.72716 | 0.36686 | **0.13266** | **0.62747** | **0.10655** |
| best | **0.73818** | **0.39273** | 0.14156 | 0.98993 | 0.11932 |

Verdict `KEEP_BASELINE`: ROC-AUC rose by 0.011 against a fold-to-fold spread of
0.041, so the gain is not distinguishable from noise — while log loss worsened
by 58% and Brier and ECE both regressed. This is §26's argument as a
measurement rather than a principle: tuning for ranking bought an unmeasurable
ordering gain and paid for it in the probabilities the planner actually reads.

### Deliverables

`src/trackshift/pass_model/tuning.py`, `scripts/train/tune_pass_model.py`,
`artifacts/validation/tuning_report.md`, selected configuration recorded in the
manifest.

---

# CP-18 — Energy twin (M14)

**Goal:** the longitudinal power balance producing `ers_deploy_power_est_kw`, `ers_harvest_power_est_kw`, `ers_energy_used_est_mj`, `ers_energy_harvested_est_mj`, `ers_soc_est_mj`, `ers_soc_uncertainty_mj`, `ers_deploy_budget_remaining_est_mj`, `ers_harvest_budget_remaining_est_mj` — all tagged `SIMULATED`, never `OBSERVED` (§28, §58). The `_est` suffix is part of the name and never stripped.

**Depends on:** CP-05, CP-06, CP-07.

### The physics (§28)

```text
P_wheel = m·a·v + P_drag + P_rolling + P_gradient

P_drag     = 0.5 · rho · CdA · v^3           rho from air_density_proxy (CP-06)
P_rolling  = Crr · m · g · v
P_gradient = m · g · sin(theta) · v          theta from mean_gradient (CP-05)
m·a·v      from acc_x and speed

P_K ≈ P_wheel / eta - P_ICE
```

Wind matters: use `v_air = v + wind_head_component_mps` in the drag term. That is why CP-06 comes first.

### Parameters and starting values

| Parameter | Start | Source / note |
|---|---|---|
| `mass_kg` | 800 (2026 minimum) + fuel | Fuel from M34 (CP-19); until then use a linear lap-based proxy, explicitly labelled a proxy (§38) |
| `CdA_m2` | 1.0 low-drag … 1.4 high-downforce | Per circuit; calibrated in CP-20 |
| `Crr` | 0.012 | Literature; calibrate |
| `eta_drivetrain` | 0.92 | Calibrate |
| `P_ICE_max_kw` | 400 | 2026 regulations pending CP-03 |
| `g` | 9.81 | Constant |

Store all in `config/physics/priors.yaml` with a `source` per key.

### Steps

**1. Implement the balance** in `src/trackshift/twin/power_balance.py` as a pure function over a segment.

**2. Integrate** `P_K` over distance to obtain deployment and harvest flows per segment, then update the modelled Energy Store explicitly: `E_next = E_current + eta_h * harvest - deploy / eta_d`. Track Energy Store state, energy deployed, energy harvested, and remaining recharge budget separately.

**3. Apply the event/session power envelope** at the current speed and mode before a deployment is admitted. Do not hard-code a universal 2026 power or recharge limit; CP-03 supplies the configured values.

**4. Tag everything `SIMULATED`** and carry uncertainty (CP-22 formalises it).

**5. Causality**: the estimate at distance *d* uses only telemetry at or before *d* (§12). This is what `causal_cutoff_distance_m` in API.md C5 records.

**5. Envelope diagnostic — do not clamp** (§28.1). At every sample, compare the estimate against the cap from CP-11:

```python
cap = max_electrical_power_kw(speed_kmh, "override", rules)   # the loosest legal cap
violation = ers_deploy_power_est_kw > cap
```

Record `envelope_violation` and `violation_margin_kw` alongside the **raw, unmodified** estimate. An estimate above the override cap is not a discovery about the car — it is proof your `CdA`, mass, `Crr`, `eta`, or ICE map is wrong.

Truncating to the cap would hide exactly the error you need to see, and would manufacture a series that looks plausible while being wrong. Keep the raw number.

The violation **rate** then becomes a first-class calibration metric in CP-20: a calibration that lowers RMSE while raising the violation rate has not improved (§29, §55).

### ✅ Check

- On a **2026 Practice 1 `PUSH` lap in clean air**, estimated `P_wheel` peaks in a plausible range (roughly 700–1,000 kW at full deployment on a long straight)
- Harvest is negative deployment under braking, and its magnitude is bounded
- Integrated deployment per lap does not exceed the regulatory per-lap limit by more than the model's stated uncertainty — if it does by a wide margin, a parameter is wrong
- **Envelope violation rate is low and concentrated at high speed.** A few percent near the taper is tolerable pre-calibration; violations at 150 km/h mean the balance itself is wrong, not the taper
- Estimated power at the end of a long straight is **not** flat at 350 kW — if it is, you are reading a constant somewhere instead of calling the CP-11 evaluator
- `ers_soc_est_mj`, `ers_deploy_budget_remaining_est_mj`, and `ers_harvest_budget_remaining_est_mj` remain separately auditable and do not drift monotonically to absurd values over a full stint — plot each and report clipping rate
- Sign conventions: positive `acc_x` under acceleration; positive gradient uphill
- **Causality test**: truncating the lap at distance *d* gives the identical estimate at *d*

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Power an order of magnitude off | Unit error — `speed` is **km/h**, must be m/s | Convert once, at the boundary, and unit-test it |
| Energy state diverges over a race | No harvest model, or harvest efficiency too low | Bound the state to a physically sensible window and report clipping rate; then calibrate in CP-20 |
| Huge noise segment-to-segment | `acc_x` is noisy at 20 m resolution | Smooth `acc_x` over 3 segments with a **trailing** window (never centred), or derive acceleration from the speed trace instead and compare |
| Gradient term dominates | `z` is noisy or in the wrong units | Silverstone is nearly flat — the gradient term should be small there. Test on a flat circuit first, then Spa. |
| Estimates differ between drivers implausibly | Team-level aero differences | Expected. That is why CP-20 has a team calibration rung. |
| Violation rate above ~10% | `CdA` too low, so drag is under-counted and the balance attributes the shortfall to electrical power | Raise `CdA` first — it is the parameter the drag term is most sensitive to at high speed, which is precisely where violations appear |
| Violations cluster on one circuit | Gradient or `CdA` wrong for that track's aero configuration | Per-event calibration rung in CP-20; do not fix by clamping |
| Tempted to clip the estimate to the cap | It makes the plot look right | Don't. §28.1 forbids it. The plot looking wrong is the signal. Clipping converts a visible calibration bug into an invisible one. |

### Deliverables

`src/trackshift/twin/power_balance.py`, `config/physics/priors.yaml`, `tests/test_twin.py` (units, signs, causality, causal Energy Store/deploy/harvest-budget accounting, power-envelope boundaries, `SIMULATED` tagging, envelope-violation accounting without clamping).

---

# CP-18b — Override / ERS-mode discriminator (M35)

**Goal:** `override_active_inferred` (a probability) and `ers_mode_inferred` — turning part of a rival's latent energy decision into a partially observed one (§20.2).

**Depends on:** CP-11 (envelope evaluator), CP-18 (power estimate), and realistically **CP-20** before you trust the output.

**Measured 2026-09-13:** ✅ the 2024 control gives a **10.87%** false-positive rate (3,885 OVERRIDE of 35,744 discriminable, 379,473 samples). Against 23.2% on 2026, roughly half the 2026 detections are the twin over-estimating power. The discriminability gate is confirmed on real data -- Monaco reports 0% discriminable because it never exceeds 290 km/h. Still unmeasured: detection concentration after Activation Lines, and the mass +3% sensitivity check. See **Chain E status, CP-18 to CP-22** for the full table.

### Why this exists

The normal and override envelopes coincide at low speed and separate above roughly the taper start. Above that speed, a car deploying more than the normal cap allows **cannot be in normal mode**. That is a rare thing in this project: public telemetry constraining a rival's energy decision rather than merely hinting at it.

```text
excess = ers_deploy_power_est_kw - max_electrical_power_kw(v, "NORMAL", rules)
if v <= separation_speed_kmh:  mode = UNKNOWN         # no information here
elif excess > k * sigma:       mode = OVERRIDE        # evidence
else:                          mode = NORMAL          # weak evidence
```

### Steps

**1. Gate on discriminability first.** Below `separation_speed_kmh` the test has no power. Return `discriminable: false` and `UNKNOWN`. Returning `NORMAL` there would be a fabricated claim — it is the single most likely way to make this component dishonest.

**2. Scale the margin by the twin's own uncertainty**, not by a fixed kW figure. `sigma` comes from CP-22. A tight threshold on a poorly calibrated twin produces confident nonsense.

**3. Return a probability, not a label.** Use the twin's uncertainty to convert `excess` into `P(override)`; keep `ers_mode_inferred` as a convenience label derived from it, clearly `INFERRED`.

**4. Do not use it as a training label** (§24). It is a feature and a prior for Rishabh's rival-state model (M09), never ground truth. Tell him that explicitly when you hand it over — the temptation to treat it as a label is strong precisely because it feels observational.

### ✅ Check

- **False-positive rate measured before use**: run the discriminator on 2022–2025 races, where no override mechanism exists. Every detection there is a false positive by construction. This is the cleanest validation available to you and costs nothing — use it.
- Detections concentrate where they should: after Activation Lines, on cars that were within the detection gap at the Detection Line
- `discriminable: false` for every sample below the separation speed, with `UNKNOWN` mode and `null` probability
- Detection rate does **not** rise when the twin is deliberately mis-calibrated in a harmless-looking way (raise mass 3%) — if it does, the margin is too tight
- Never emits `OBSERVED` provenance

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Override detected constantly | Twin over-estimates power; `CdA` too low | Fix the twin (CP-20), not the threshold. Chasing this with a wider margin hides a calibration bug. |
| False positives on 2022–2025 | Same — the pre-2026 seasons are your control group | Tune until the historical false-positive rate is acceptable, then apply the same settings to 2026 |
| Never detects anything | Margin too wide, or the estimate never reaches the normal cap | Check that the cap is actually varying with speed; a constant cap makes the excess meaningless |
| Detections below 290 km/h | Discriminability gate missing | The gate is the first check in the function, not the last |

### Deliverables

`src/trackshift/twin/override.py`, `override_state()` in C5, `tests/test_override.py` (gate, uncertainty scaling, historical false-positive bound, provenance).

---

# CP-19 — Fuel-load estimator (M34)

**Goal:** `fuel_load_kg_est` + `fuel_load_uncertainty_kg`, **causal**, tagged `INFERRED` (§11, §38). The only permitted source of fuel context for any live feature.

**Depends on:** CP-18.

### Steps

Fuel is not observable. Build a causal estimator, not a lookahead:

```text
fuel_kg(lap k) = start_fuel_kg - sum(consumption per lap up to k)
```

- `start_fuel_kg`: race distance × a per-circuit consumption rate, bounded by the regulatory maximum
- Per-lap consumption: from the energy twin's ICE work, or a circuit constant calibrated so that fuel reaches ~1 kg at the end of a green-flag race
- Uncertainty grows with laps since the last anchor point

**Explicitly label it a proxy** (§38). Never present it as observed. The registry entry must say so, and the API returns `provenance: INFERRED`.

### ✅ Check

- Monotonically decreasing within a stint, resetting only at the race start (no refuelling in F1)
- Ends a full race between 0 and 3 kg
- Uncertainty widens with distance from the start
- Correlates with the known lap-time fuel effect: roughly 0.03 s per kg per lap — regress lap time against the estimate on green laps and check the coefficient is in a plausible range
- Causality test as before

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Ends far from zero | Consumption rate wrong for that circuit | Calibrate per circuit against total race distance; store per-circuit in `config/physics/priors.yaml` |
| Lap-time coefficient wrong sign | Confounded with tyre degradation (CP-08 flagged this) | Fit fuel and tyre effects jointly on green laps, or use Qualifying (low fuel) vs Race (high fuel) as the contrast |

### Deliverables

`src/trackshift/twin/fuel.py`, `tests/test_fuel.py`, `fuel_load_kg_est` and `fuel_load_uncertainty_kg` columns, registry entries. Then **revisit CP-08** to subtract the fuel effect from the tyre proxy.

---

# CP-20 — Physics calibration hierarchy (M15)

**Goal:** all five rungs of §29 fitted and compared, with the required controls.

**Depends on:** CP-18, CP-19.

**Measured 2026-09-13:** ✅ built, run and measured. **No calibrated rung is accepted:** Rung 1 (analytical, nothing fitted) is the only one that passes at 0.3266 s; rung 2 reaches 0.1918 s but pins `P_ICE` at its 500 kW bound. Both sit above the C2 baseline's 0.1774 s and the 0.1631 s best-constant floor, and the §29 target of 0.15 s is below that floor. Fitted on 6,000 of 107,807 clean rows -- the cap has never been lifted. See **Chain E status, CP-18 to CP-22** for the full table.

### The five rungs

| Rung | What is fitted |
|---|---|
| 1. Pure analytical | Nothing — priors only |
| 2. Global calibrated | One `(CdA, Crr, eta, P_ICE)` set across all cars and circuits |
| 3. Team-specific | Per-team parameters |
| 4. Event-specific | Per-team, per-circuit |
| 5. Physics + ML residual | Rung 4 plus a learned residual: `prediction = physics(x) + MLResidual(x)` |

### Required controls (§29 update)

The calibration and the residual model must control for: entry speed, segment geometry, aero/Overtake state, tyre compound and life, fuel-load estimate, wind head/cross components, track temperature, wetness, and `normal_race_model_eligible`.

### Fitting

Target: observed segment time. Loss: weighted least squares over segments. Data: 2026 Practice 1 `PUSH` and `LONG_RUN` laps (clean air, §9) plus Qualifying for the high-performance envelope.

```python
scipy.optimize.least_squares(
    residual_fn, x0=[CdA, Crr, eta, P_ICE_max],
    bounds=([0.6, 0.005, 0.85, 300], [1.8, 0.025, 0.98, 500]),
    loss="soft_l1",   # robust to outlier segments
    f_scale=0.05,
)
```

Bounds are physical constraints — a fit that runs to a bound is telling you the model is wrong, not that the bound should move.

Residual model (rung 5): LightGBM with `n_estimators=500, learning_rate=0.05, num_leaves=15, min_child_samples=50` — deliberately small, because a large residual model just memorises the physics error.

### ✅ Check

- Each rung improves held-out MAE over the previous, or is rejected (§34)
- MAE targets: rung 2 < 0.15 s/segment, rung 4 < 0.08 s, rung 5 < 0.06 s
- **Zero physical constraint violations** — no negative drag, no efficiency > 1, no fitted parameter at a bound
- Error broken down by speed regime and by segment type (§29) — a model good on straights and bad in corners is not usable for the DP
- Residual model does not exceed ~30% of total predicted variation; if it does, the physics is doing too little work
- Team-specific parameters differ plausibly (§15: never assume similar PU means similar aero)

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Fit hits a bound | Missing physics (downforce-induced drag, DRS/Overtake aero state) | Add an aero-state term rather than widening bounds |
| Event calibration overfits | Too few clean laps per event | Require ≥30 clean laps per (team, event); otherwise fall back to the team rung and record the fallback |
| Residual model does all the work | Physics parameters poorly initialised | Fix rungs 2–4 first; do not let rung 5 paper over them |
| Good MAE, bad ΔE→Δt behaviour | The model fits time but not the energy *sensitivity* | That is CP-21's check, and it is the one that matters for the DP |

### Deliverables

`src/trackshift/twin/calibration.py`, `scripts/train/calibrate_physics.py`, `artifacts/models/twin/<version>/` per rung, `artifacts/validation/twin_report.md`.

---

# CP-21 — Segment-time model, ΔE→Δt (M16)

**Goal:** the causal transition the DP consumes: how energy and tyre state change segment time and, where needed, projected Detection-Line gap (§30). Rishabh's DP cannot produce a meaningful shadow price without this.

**Depends on:** CP-20.

**Measured 2026-09-13:** ✅ 214 segments fitted, all monotone, every non-positive `a_k` refused. But `a_k` is **inverted**: corners average 7.1 s/MJ against straights at 0.63, where §30 expects the opposite, so the transition is not yet safe for the DP. See **Chain E status, CP-18 to CP-22** for the full table.

### The form (§30)

```text
t_k(d, L) = t_base,k - a_k · ΔE + c_k · L
```

`a_k` is the **energy sensitivity** of segment *k* — the local slope that contributes to λ_E. The model must also test whether tyre state changes this response and whether the action changes projected future gap, not just immediate segment time.

### Candidates to benchmark (§30)

| Candidate | Parameters |
|---|---|
| Analytical physics | From CP-20 rung 4 |
| Linear / ridge | `Ridge(alpha=1.0)` per segment, or pooled with segment interactions |
| Gradient boosting | `LGBMRegressor(n_estimators=1500, learning_rate=0.03, num_leaves=31, min_child_samples=40, subsample=0.8, colsample_bytree=0.8, random_state=42)` |
| Physics + residual | CP-20 rung 5 |
| Small neural regressor | 2×64 MLP — **the one genuinely GPU-worthy model you own**; 1.24 M rows (2026) to 6.4 M (all years). See the A6000 section for the export recipe. |

**The winner must remain physically plausible** (§30) — this constraint overrides raw MAE.

### ✅ Check

- `a_k > 0` for **every** segment — deploying energy must never make a segment slower
- `a_k` is largest on long straights and near zero in slow corners. Plot `a_k` against segment type; if it is flat, the model has not learned the physics and the DP will produce nonsense.
- `c_k > 0` — lifting costs time
- Monotonicity: `t_k` strictly decreasing in ΔE across the full action range, checked on a dense sweep for every segment
- MAE < 0.06 s/segment held out
- Extrapolation sanity: at ΔE beyond the observed range, time does not go negative or invert
- **Silverstone sanity**: the Hangar Straight segment should have among the highest `a_k` on that circuit
- Report MAE/RMSE for next relative speed, next time gap, and projected Detection-Line gap where those outputs are consumed by CP-12
- Compare tyre age only, degradation proxy, and latent tyre-performance state by held-out benefit; do not retain an interaction solely because it is physically plausible

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| `a_k` negative somewhere | Confounding — high deployment correlates with defending or traffic | Restrict fitting to clean-air segments; add the race-context control. If it persists, constrain the sign in the model (monotone constraints in LightGBM: `monotone_constraints`). |
| `a_k` flat across segment types | Model learned an average, not the geometry | Fit per segment or add explicit segment×energy interactions |
| Tree model wins on MAE but violates monotonicity | Trees do not respect physics by default | Use `monotone_constraints=[-1]` on the energy feature, or prefer physics+residual. §30: physical plausibility outranks MAE. |
| DP produces a flat shadow price | `a_k` has no variation across the lap, or the gap transition ignores action | Re-check geometry and the causal energy-to-gap response before changing the planner. |

### Deliverables

`src/trackshift/twin/segment_time.py`, `scripts/train/train_segment_time.py`, `artifacts/models/segment_time/<version>/`, monotonicity test in `tests/test_twin.py`.

---

# CP-22 — Physics uncertainty (M17)

**Goal:** parameter draws and confidence ranges so C5 can return `Uncertain` rather than point estimates, while retaining separate energy-state, physics-transition, tyre-state, gap/eligibility, and rival-state uncertainty where each is produced (§42).

**Depends on:** CP-20, CP-21.

**Measured 2026-09-13:** ✅ coverage **84.7%** against a nominal 80%, so its own gate passes. It inherits CP-20's unaccepted fit and its manifest says so. See **Chain E status, CP-18 to CP-22** for the full table.

### Steps

**1. Parameter covariance** from the least-squares Jacobian at the optimum (`scipy.optimize.least_squares` returns `jac`; covariance ≈ `inv(J.T @ J) * residual_variance`).

**2. Draw N=200 parameter sets** from that covariance, propagate each through the twin and segment-time model, and report mean plus 10th/90th percentiles. Preserve correlation between coupled energy, time, and gap outputs instead of independently sampling incompatible values.

**3. Cache draws per (event, team)** — recomputing 200 draws inside the DP loop will be too slow.

### ✅ Check

- Intervals contain the observed value about 80% of the time on held-out segments (that is what an 80% interval means)
- Intervals widen for extrapolated conditions (wet, unusual temperature, unseen team)
- Draws respect physical bounds — no negative drag in any draw
- Latency: 200 draws for one segment in < 10 ms so the API stays responsive

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Coverage far below 80% | Underestimating uncertainty — Jacobian covariance ignores model misspecification | Add a residual-variance term from held-out error, not just parameter uncertainty |
| Intervals absurdly wide | Poorly constrained parameters (correlated CdA and Crr) | Fix one from literature and fit the other, or reparameterise |

### Deliverables

`src/trackshift/twin/uncertainty.py`, `t_draws_s` and `low`/`high` in C5, coverage test.

---

# CP-23 — Ablation harness (M28)

**Goal:** one implementation both owners run, so §35 feature-group decisions are evidence-based.

**Depends on:** CP-14, CP-21.

### Steps

`src/trackshift/eval/ablation.py` reads feature groups from the registry and runs leave-one-group-out, reporting the delta in the primary metric with the same splits and seeds.

Groups: `geometry`, `tyre`, `weather`, `team_identity`, `driver_identity`, `rolling_battle_trends`, `gap_dynamics`, `race_context`, `fuel_ers`, `rule_state`, `rival_belief`, `future_eligibility`.

**Keep a group only if it improves held-out metrics or provides necessary causal context without materially degrading others** (§35).

### ✅ Check

- Every group has a measured delta with a confidence interval, not a point estimate
- Identity groups are scrutinised hardest (§17) — a large identity gain with a small racecraft gain means memorisation
- Results are reproducible across runs with the same seed
- The harness runs on both Chain P and Chain E models
- Strategic levels report terminal `P(ahead)`, decision regret, rule violations, latency, and decision stability, not only component error

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Every group looks useless (deltas ≈ 0) | Groups are correlated, so removing one leaves the signal in another | Report leave-one-out **and** add-one-in against a minimal baseline; a group that is redundant is a different finding from a group that is useless |
| Deltas larger than the run-to-run noise floor cannot be distinguished | No repeats | Run each configuration over ≥3 seeds and report the interval. §35 decisions must survive noise. |
| A group helps one checkpoint and hurts another | Genuine — features differ in value at DETECTION vs BRAKING | Keep the decision per checkpoint. The registry's `decision_checkpoint` field already allows this. |
| Identity groups dominate everything | Memorisation, exactly the §17 risk | Report the identity-only and identity-free models side by side and let the numbers make the case for dropping identity |

### Deliverables

`src/trackshift/eval/ablation.py`, `scripts/evaluate/run_ablation.py`, `artifacts/validation/ablation_report.md`.

---

# CP-24 — Service routes and replay bundle

**Goal:** your half of API.md live, plus the replay bundle generator (which is yours for the whole team).

**Depends on:** CP-11, CP-14, CP-21.

### Your routes (API.md §9)

`GET /meta`, `GET /validation`, `GET /track/{event}`, `GET /rules/{event}`, `POST /rules/legal_actions`, `POST /rules/eligibility`, `POST /pass/predict`, `POST /twin/segment_time`, `POST /twin/energy_state`, plus `src/trackshift/serve/app.py` (error model, stub middleware) and `scripts/serve/build_replay_bundle.py`.

FastAPI, pydantic and uvicorn are already installed.

### ✅ Check

- Every example JSON in API.md validates against `schemas.py` (`tests/serve/test_schemas.py`)
- `CHECKPOINT_VIOLATION` raised when a `DETECTION` request carries an activation feature
- `NOT_MODEL_ELIGIBLE` raised outside normal-race rows
- Every number in every response carries a provenance tag
- Rule, twin, eligibility, and planner requests validate the shared `StrategicState` shape, including causal status for any supplied counterfactual field
- Replay bundle files are byte-identical in shape to live responses
- `bundle_manifest.json` records `stubs_used: []` for the final demo
- All routes respond on CPU only

### ⚠️ If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Replay file and live response differ in shape | Two code paths building JSON | Generate the bundle **by calling the routes**, not by re-serialising models. `build_replay_bundle.py` should invoke the app in-process. |
| A number reaches the UI without provenance | Serialiser drops the wrapper for plain floats | Make `Quantity`/`Uncertain` the only permitted numeric types in the response models; let pydantic reject bare floats |
| `/plan` too slow when a slider moves | DP recomputing per request | Precomputed `plan/segNNN.json` covers scrubbing; for live what-if, cache the value table per (event, energy grid) |
| Bundle contains stub output | A model artifact was missing at build time | `bundle_manifest.json` records `stubs_used`; fail the build if it is non-empty when `--final` is passed |
| Routes work locally, fail for the UI owner | Machine-specific paths or a running model server assumed | The bundle must be self-contained: no absolute paths, no service dependency (§5) |

### Deliverables

`src/trackshift/serve/*`, `scripts/serve/run_service.py`, `scripts/serve/build_replay_bundle.py`, `artifacts/demo/2026_british_grand_prix/**`.

---

# A6000 remote workflow — 38 GB budget

Remote disk is **38 GB**. That is the binding constraint, and it rules out the obvious approach: the raw mirror alone is **27.6 GB measured**, and a CUDA PyTorch install is ~6.5 GB, so raw data plus torch does not fit. It never needs to.

**The rule: features are built locally on CPU. Only a pruned training matrix crosses the wire.** Everything the A6000 trains on is tens of megabytes, not gigabytes.

## Measured data volumes

| Data | Volume | Ships to remote? |
|---|---|---|
| Raw telemetry, 5 years | **27.6 GB** (measured, 177,288 laps at ~150 KB) | **Never** |
| 20 m lake, all years | 41.0 M rows, est. 5–6.5 GB | **Never** |
| 20 m lake, 2026 only | 7.9 M rows, est. ~1 GB | **Never** |
| Segments, all years | 6.38 M rows, est. ~1.3 GB | Only pruned |
| Segments, 2026 only | 1.24 M rows, est. ~250 MB | Yes, pruned to ~50–80 MB |
| Overtake opportunities | tens of thousands of rows | Trivially, single-digit MB |

Basis: measured mean `tel.json` size per year, real median lap length 4,629 m → 231 rows per lap at 20 m spacing, 36 segments per lap.

## Which models actually go remote

| Model | Rows | GPU? | Why |
|---|---|---|---|
| M10 Logistic | ~40 k | **No** | Seconds on CPU |
| M10 LightGBM / XGBoost / CatBoost | ~40 k × ~40 feat | **No** | CPU histogram builds take seconds to a minute at this size. GPU tree training only pays off past a few million rows. |
| M10 MLP, single fit | ~40 k | **No** | 2×64 net, under a minute on CPU |
| M10 MLP, hyperparameter sweep | ~40 k × 100+ configs | **Optional** | Only if the grid is large; otherwise run it locally overnight |
| **M16 neural segment-time regressor** | **1.24 M (2026) – 6.4 M (all years)** | **Yes** | The one genuinely GPU-shaped model you own |
| M15 physics calibration | — | **No** | `scipy.optimize.least_squares`; a GPU cannot help a Levenberg–Marquardt fit |
| M17 uncertainty draws | 200 draws | **No** | Vectorised NumPy, milliseconds |
| M14 / M34 energy twin, fuel | — | **No** | Deterministic physics over Parquet |
| M03 / M04 / M33 / M30 foundations | — | **No** | Median and threshold work |
| M18–M21 rules | — | **No** | Config, state machine, deterministic logic |
| M28 ablation | many refits | **Only if** the underlying model is the GPU one | For tree ablations, more CPU cores beat a GPU |

**So: one model needs the A6000 (M16 neural), one optionally (the M10 MLP sweep).** Everything else in your three chains is CPU by design (§54: do not use GPU merely because it is available).

If the remote were unavailable entirely you would lose exactly one benchmark candidate out of five in CP-21 — and that checkpoint selects on physical plausibility over raw MAE (§30), which favours physics+residual anyway. **The A6000 is a convenience here, not a dependency.** Do not let it block the critical path.

## The 38 GB budget

| Item | Size | Note |
|---|---|---|
| Base venv (pandas, numpy, pyarrow, sklearn, lightgbm) | ~1.2 GB | from `requirements.lock.txt` |
| `torch` + CUDA 12.4 `nvidia-*` wheels | ~6.5 GB | the single biggest item |
| pip cache **if not disabled** | up to 3 GB | always `--no-cache-dir` |
| Pruned training matrix, one model | 50–300 MB | see below |
| Model artifact, best checkpoint only | ~50 MB | not one per epoch |
| Logs | ~100 MB | |
| **Working total** | **~8–9 GB** | |
| **Headroom** | **~29 GB** | stays that way only if raw never lands there |

Comfortable — *provided* you never rsync `data/raw/` or `data/processed/telemetry_20m/`. Put both in an rsync exclude file so it cannot happen by reflex:

```text
# .rsync-exclude  (committed)
data/raw/
data/processed/telemetry_20m/
.venv/
artifacts/demo/
```

## Shrinking the export — three techniques, ~10× together

Applied in `scripts/data/export_training_set.py`:

**1. Column pruning.** Ship only the features the registry declares for that model, plus the label and the split key. A segment row has ~60 columns; M16 needs about 25.

**2. `float32`, not `float64`.** Halves the numeric footprint. Telemetry-derived features carry nowhere near 15 significant digits.

**3. `zstd` compression, not snappy.** Roughly 30% smaller on smooth numeric columns.

```python
numeric = df[cols].select_dtypes("number").columns
df[cols].astype({c: "float32" for c in numeric}).to_parquet(
    out, compression="zstd", compression_level=9, index=False
)
```

Result for M16, all years: 6.38 M rows × 25 float32 columns ≈ 640 MB uncompressed → **roughly 200–300 MB on disk**. Restricted to 2026: **50–80 MB**. The M10 opportunity table is a few MB either way.

> **Verify at CP-04.** Once the first Parquet exists, `du -sh` it, divide by row count, and write the measured bytes-per-row into the export script docstring. The figures above are derived from measured raw sizes and real lap lengths, but a measured number beats a derived one.

## The rotation protocol — one model at a time

**1. Preflight locally.** The export script refuses to write a file that would not fit:

```powershell
python scripts/data/export_training_set.py `
  --model segment_time --split train,val --years 2026 `
  --float32 --compression zstd --max-export-mb 500 `
  --out artifacts/export/segment_time_v1.parquet
```

It writes a sidecar `.json` with git commit, feature schema, split definition, row count and exact file size, so the remote run is reproducible and the returned artifact is traceable.

**2. Check remote free space before uploading:**

```bash
ssh USER@A6000 'df -h ~ | tail -1'
```

Abort below 10 GB free. A transfer that fills the disk mid-copy is worse than not starting.

**3. One-time remote setup.** `--no-cache-dir` is not optional at 38 GB:

```bash
ssh USER@A6000
mkdir -p ~/trackshift/{data,artifacts} && cd ~/trackshift
python -m venv .venv && source .venv/bin/activate
pip install --no-cache-dir -r requirements.lock.txt
pip install --no-cache-dir -r requirements-gpu.txt
pip cache purge
du -sh .venv                     # expect ~7.7 GB
```

**4. Upload, train, pull back:**

```powershell
scp artifacts/export/segment_time_v1.parquet USER@A6000:~/trackshift/data/
ssh USER@A6000 "cd ~/trackshift && source .venv/bin/activate && python scripts/train/train_segment_time.py --input data/segment_time_v1.parquet --candidate neural --device cuda --keep-best-only --out artifacts/segment_time_neural_v1"
scp -r USER@A6000:~/trackshift/artifacts/segment_time_neural_v1 artifacts/models/segment_time/
```

`--keep-best-only` matters: a checkpoint per epoch over 200 epochs turns a 50 MB artifact into 10 GB.

**5. Clear the remote before the next model:**

```bash
ssh USER@A6000 'rm -rf ~/trackshift/data/* ~/trackshift/artifacts/*; df -h ~ | tail -1'
```

The venv stays; datasets and artifacts do not. This is what makes "one model at a time" a storage strategy rather than a scheduling preference.

**6. Verify on CPU locally, then commit:**

```powershell
python scripts/evaluate/verify_cpu_inference.py --artifact artifacts/models/segment_time/neural_v1
```

## ⚠️ If storage becomes a problem

| Symptom | Cause | Fix |
|---|---|---|
| Remote disk fills during `pip install` | pip cache | `pip install --no-cache-dir`, then `pip cache purge`. Recovers up to 3 GB. |
| Disk fills during training | Per-epoch checkpoints | `--keep-best-only`. Also check the logger is not writing per-step tensors. |
| Export is larger than expected | No column pruning, or float64 | Confirm the registry filter is applied and the dtype cast happened before `to_parquet`, not after |
| Transfer is slow | Shipping the lake instead of the training matrix | You should be moving tens of MB. If it takes minutes, you are moving the wrong file. |
| A future dataset genuinely will not fit | Multi-year full-resolution training | Shard by year and train incrementally, deleting each shard after its epoch group; or stratified-subsample to 30% for the benchmark and only train the winner on everything; or mount over sshfs and stream (slower per epoch, zero remote storage). Do not just compress harder — first re-check whether the model needs to be remote at all. |
| Two models needed at once | Scheduling pressure | Do not. Sequential is the whole point at 38 GB. Queue them. |

## Non-negotiable rules

- Every artifact must load and run on CPU (`map_location="cpu"`), verified before merge (MODELS.md §1.1)
- Save `state_dict`, never a pickled module
- `--device` is always a CLI argument; every script runs end-to-end on CPU
- `requirements.lock.txt` is the single source of truth for both environments
- Remote host, user and paths live in ignored local config — never committed (§5)
- The manifest records `device_trained_on` and `cpu_inference_verified: true`
- **`data/raw/` and `data/processed/telemetry_20m/` never leave this machine**

# Cross-checkpoint invariants

Check these at every milestone, not just once.

| Invariant | Where it breaks first | §Ref |
|---|---|---|
| No BGP rows in any training or calibration split | CP-14, CP-15 | §40 |
| `segment_id` stable for a given `geometry_version` | CP-05 | §13 |
| No centred or future windows in any live feature | CP-08, CP-12, CP-18 | §12, §44 |
| Every number carries a provenance tag | all | §43 |
| Energy and fuel never labelled `OBSERVED` | CP-18, CP-19 | §11, §58 |
| Illegal actions absent, never low-scored | CP-11 | §31 |
| `normal_race_model_eligible` filter applied | CP-09, CP-13, CP-20 | §12 |
| Checkpoint features never leak backwards | CP-13, CP-14 | §19 |
| Every produced column is in the registry | all | §46 |
| Counterfactual fields are generated from a decision-time state and declared intervention, never observed future trajectory | CP-12, CP-21, CP-24 | §40 |
| Rule snapshot/configuration version is recorded in every rule, twin, and planner artifact | CP-03, CP-11, CP-18, CP-24 | §6, §21 |
| Every artifact has a manifest with git commit and seed | all | §53 |

### The truncation test

The single most valuable test you can write, and it applies to CP-08, CP-12, CP-18 and CP-19:

```python
def test_causal(builder, lap_df):
    """A causal feature computed on a truncated lap must equal the full-lap value at
    the truncation point. Any lookahead breaks this."""
    full = builder(lap_df)
    for k in (10, 25, 50):
        truncated = builder(lap_df.iloc[:k])
        assert truncated.iloc[k-1] == pytest.approx(full.iloc[k-1]), (
            f"lookahead detected at row {k}"
        )
```

---

# Open items

## Regulation-era boundary closure — 2026-09-13

- `config/feature_registry.yaml` marks historical DRS fields
  `historical_prior_only`; `src/trackshift/data/registry.py` enforces that
  policy at consumer and final-mode boundaries. Raw/historical DRS is accepted
  only for explicitly labelled 2022–2025 audit/prior work and never for 2026
  strategy inputs. `PROXY_HISTORICAL_DRS` is development-fixture provenance
  only.
- `src/trackshift/pass_model/features.py` and
  `scripts/train/calibrate_pass_model.py --final-mode` reject proxy/DRS C4
  inputs; final mode also requires only non-British 2026 rows. No C4 retraining
  was run because the required final rule inputs and accepted C5 callback are
  unavailable. British Grand Prix was not used for training or calibration.
- Official source ledger: `config/rules/sources_2026.yaml`; common rule
  snapshot: `config/rules/2026/common.yaml`; British event configuration:
  `config/rules/2026/british_grand_prix.yaml`. The FIA power curves are sourced;
  Detection Gap, generic deployment budget, physical store capacity, and
  event-specific conditions remain unresolved and keep final mode blocked.
- Focused validation command:
  `.venv/bin/python -m pytest -q tests/test_strategic_state.py
  tests/test_registry.py tests/test_rules_config.py tests/test_rules.py
  tests/test_pass_model.py tests/test_ensemble.py tests/test_rival_chain.py
  tests/test_rival_cp07.py tests/test_twin.py` → `309 passed in 5.14s`.
- Generated C4/C5 artifacts were not rebuilt or staged. Existing C4 candidates
  under `artifacts/models/pass/cp14_2026_v1/` remain development evidence only;
  no accepted public decision-time C5 transition exists.

### Rishabh synthetic closure hand-off — 2026-09-13

Rishabh's M13/M22–M27 development paths and shared API/replay hand-off now have
an executable synthetic fixture. This does not upgrade Tanveer's C4/C5 gates:
the accepted public decision-time callbacks remain unavailable, and British GP
remains excluded from training and calibration.

```bash
.venv/bin/python -m pytest -q tests/test_serve.py tests/test_registry.py tests/test_chain_v_cores.py tests/test_rival_cp07.py
.venv/bin/python scripts/simulate/run_closure_synthetic.py --out /tmp/trackshift-closure-9Mhknx --episodes 8 --seed 17 > /tmp/trackshift-closure-synthetic-run.json
```

The focused route/registry/Chain V command returned `58 passed in 1.13s`.
The synthetic closure run returned `SYNTHETIC_DEVELOPMENT_COMPLETE`, planner
p95 `4.80098300249665 ms`, and zero aggregate rule violations. Generated
evidence remains local under `/tmp/trackshift-closure-9Mhknx/`; it is not a
replacement for a real C4 calibration manifest or C5 acceptance artifact.
The required full suite completed with `.venv/bin/python -m pytest -q` →
`1222 passed, 23 skipped, 2 warnings in 79.66s (0:01:19)`.

| # | Item | Status |
|---|---|---|
| T1 | FIA 2026 Sporting/Technical Regulations and per-event notes for all 14 tracks | Research task in CP-03; Tier-C proxy unblocks development meanwhile |
| T2 | Whether `detection_gap_s` differs per event in 2026 | Assume season-wide in `common.yaml`; override per event when sourced |
| T3 | Spanish Grand Prix has Practice only and no `corners.json` | Excluded from the rule config; revisit if the mirror is completed |
| T4 | C7 (`normal_race_model_eligible`) from Rishabh | CP-09 and CP-13 depend on it; track-status decoding in this file is the interim stand-in |
| T5 | C8 (`battle_id`) and C9 (splitter) from Rishabh | CP-13 can build without them, CP-14 cannot train without C9 |
| T6 | `outcome_horizon` definition beyond `zone_exit_v1` | Alternative: "before the next Detection Line". Compare both in CP-13. |
| T7 | Integration of the two halves is **not** in this file | `CHECKPOINTS_INTEGRATION.md` is the joint runbook, owned by Rishabh. CP-24 completes Tanveer's 9 routes, app skeleton, and bundle generator; Rishabh's 7 routes, including `GET /battles/{id}/timeline` joining C3/C4/C5, and the zero-stub demo bundle land through that plan. |
