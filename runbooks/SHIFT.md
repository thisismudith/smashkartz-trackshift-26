# Working from Mudith's machine: CP-06 onward

Mudith owns the repo and the machine. This is the setup so Tanveer can continue
`CHECKPOINTS_TANVEER.md` there without re-deriving anything.

**The short version:** everything on GitHub is already on that machine. The only
thing missing is `data/`, which is gitignored and arrives on a pendrive from two
places — Rishabh (the lake and segments) and Tanveer's laptop (a 113 MB bundle of
session JSON). The 29 GB raw telemetry mirror does **not** need to travel.

---

## 1. What is already there

Nothing in this table needs to be copied. It is in the repo and comes down with a
`git pull`.

| What | Path | Note |
|---|---|---|
| Contracts and plans | `TrackShift AGENTS.md`, `MODELS.md`, `API.md`, `CHECKPOINTS_TANVEER.md` | |
| Dependency pins | `requirements.txt`, `requirements.lock.txt`, `requirements-gpu.txt` | |
| Phase 2 library | `src/trackshift/` | loader, validation, resampling, segmentation, progress |
| Registries (CP-02) | `config/data_registry.yaml`, `config/feature_registry.yaml` | |
| Rule config (CP-03) | `config/rules/2026/` — `common.yaml` + 14 event files | CP-10/CP-11 need only this plus segments |
| Race-control evidence (CP-03) | `artifacts/race_control/` — 17 files, 2.2 MB | Tier B |
| **Segment maps (CP-05)** | `config/geometry/*.yaml` — 4 circuits | **Looks like data, is not. It is in git.** |
| Scripts and runbooks | `scripts/`, `runbooks/` | |
| Tests | `tests/` — 312 passing | |

`config/geometry/*.yaml` is the trap worth naming: it is the static segment map,
it is small, and it must stay reviewable next to the `boundary_hash` its segment
table carries. It is committed. Do not put a second copy on the pendrive — a
stale copy overwriting the tracked one is exactly the failure `boundary_hash`
exists to catch.

### Get the branch

```powershell
cd <wherever the repo lives on Mudith's machine>
git fetch origin
git switch tanveer/rule-eng        # or: git switch -c tanveer/rule-eng origin/tanveer/rule-eng
git log --oneline -1
```

---

## 2. Working inside someone else's clone

This is the part that differs from a normal machine move, and it is worth five
minutes up front.

**Commit as yourself, not as Mudith.** The clone carries his `user.name` and
`user.email`. Set it per-repo so the history stays honest:

```powershell
git config user.name  "Tanveer Singh"
git config user.email "rdtraders.office@gmail.com"
git config user.name; git config user.email        # confirm
```

The push still authenticates as Mudith — unavoidable and fine; the authorship is
what matters in the log.

**Only push `tanveer/rule-eng`.** Never `git push` bare from a shared clone,
because whatever branch happens to be checked out is what goes:

```powershell
git push origin tanveer/rule-eng
```

**If Mudith also works in that same clone**, do not share a checkout — switching
branches under each other loses work. Use a worktree, so both of you get a
separate directory backed by one object store:

```powershell
git worktree add ../trackshift-tanveer tanveer/rule-eng
cd ../trackshift-tanveer
```

`data/` is gitignored, so a worktree starts with an empty `data/`. Restore the
pendrive contents into whichever directory you actually work in.

---

## 3. What comes by pendrive

Three items. About **1.3 GB**, from two sources.

### 3a. From Rishabh — outputs of his run of Tanveer's CP-04 and CP-05

| Copy this folder whole | Size | Restore to |
|---|---|---|
| `data/processed/telemetry_20m/` | ~1.1 GB for 2026 | same path |
| `data/processed/segments/` | ~50 MB | same path |

**Copy the manifests, not only the Parquet.** `run_manifest.json`,
`lap_manifest.csv`, `rejected_laps.csv` and `quality_summary.csv` sit in the lake
root and are under 2% of the transfer. Without them the lake cannot be audited or
reproduced: `run_manifest.json` records which git commit built it and whether the
tree was clean, and `rejected_laps.csv` is the only record of the ~7% of laps that
were dropped and why. Copying the folder whole gets all of it; cherry-picking
`.parquet` files does not.

If he also ran stage 4d (2022-2025), the lake grows to ~5.5 GB. Optional; only
CP-14's DRS-era priors want it.

### 3b. From Tanveer's laptop — raw session JSON

| What | Size | Why |
|---|---|---|
| Every raw `.json` **except** `*_tel.json` | **113 MB**, ~7,039 files | `weather.json`, `rcm.json`, `corners.json`, `drivers.json`, `laptimes.json` |
| `artifacts/schema_audit/` | 184 MB | CP-01 evidence. Optional — regenerable in ~90 min |

The first is not optional and is the easiest thing to forget. **The lake carries
no weather columns at all** — verified, not assumed. CP-06 (M33, track-relative
wind) reads `weather.json` directly, and any further race-control or DRS work
reads `rcm.json` and `corners.json`. All of it is session-level, which is why it
fits in 113 MB instead of 29 GB.

Extract it on Tanveer's laptop, from Git Bash:

```bash
cd /t/Trackshift/Trackshift/data/raw/tracinginsights
mkdir -p /d/shift/raw_session_json
find . -name '*.json' ! -name '*_tel.json' -exec cp --parents {} /d/shift/raw_session_json/ \;
du -sh /d/shift/raw_session_json                          # expect ~113 MB
find /d/shift/raw_session_json -name '*.json' | wc -l     # expect ~7,039
```

Replace `/d/` with the pendrive letter. On Mudith's machine restore it to
`data/raw/tracinginsights/`, so the directory tree matches what the scripts
expect.

### What does not travel

| | Why |
|---|---|
| `data/raw/**/*_tel.json` — **29 GB** | Nothing in CP-06 to CP-24 reads raw telemetry. They read the lake and the segments. |
| `config/geometry/*.yaml` | Already in git (§1). Copying it risks overwriting the tracked map with a stale one. |
| `.venv/` | Absolute paths are baked in. Rebuild from `requirements.lock.txt`. |
| `artifacts/drs_zones/` | Tier C, gitignored, regenerable, development-only. |

**The one case that needs the 29 GB** is rebuilding the lake from scratch on
Mudith's machine. Rishabh is producing it, so that does not arise — but decide
before Tanveer's laptop is wiped or reused, because re-downloading is hours.

---

## 4. Setup on Mudith's machine

```powershell
# 1. Branch and identity  (see section 2)
git fetch origin
git switch tanveer/rule-eng
git config user.name  "Tanveer Singh"
git config user.email "rdtraders.office@gmail.com"

# 2. Environment. lock, not requirements.txt: exact pins, so this machine
#    cannot drift from Rishabh's.
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.lock.txt

# 3. Restore the pendrive folders to these exact paths
#    data/processed/telemetry_20m/
#    data/processed/segments/
#    data/raw/tracinginsights/        (session JSON only)
#    artifacts/schema_audit/          (optional)
```

Use a project-local `.venv`, not Mudith's global Python: it keeps this work off
his interpreter, and `.venv/` is already gitignored.

For the two optional neural candidates on his GPU:

```powershell
pip install -r requirements-gpu.txt
```

### Verify before doing any work

```powershell
python -m pytest -q
python scripts\data\verify_lake.py
python -c "import sys; sys.path.insert(0,'src'); from trackshift.data.registry import validate_registries; print(validate_registries() or 'registries valid')"
python -m pytest tests\test_segmentation.py -q
```

Expect **312 passed**, all `verify_lake` gates passing, `registries valid`, and
**68 passed** from the segmentation tests.

Two of these carry real information rather than being ceremony:

**`verify_lake.py` prints `built_by` with the commit that produced the lake.** If
that commit differs from `HEAD`, check whether the difference touches
`resample.py`, `raw_loader.py` or `validation.py`. If it does, the lake predates a
change to how it is built and should be rebuilt; if not, it is fine. This is the
check that makes a cross-machine handover safe, and the reason `git_commit` is
recorded at all.

**`tests/test_segmentation.py` cross-checks the maps in git against the Parquet
from the pendrive.** A failure means the geometry Rishabh built with is not the
geometry now in git — bump the version and rebuild rather than proceeding, because
every table downstream joins on `segment_id`.

A quick check that the transfer landed where the code looks:

```powershell
Get-ChildItem data\processed\telemetry_20m -Recurse -Filter *.parquet | Measure-Object
Get-ChildItem data\raw\tracinginsights -Recurse -Filter weather.json | Measure-Object
```

Both counts should be non-zero. The second is the CP-06 canary.

---

## 5. What Mudith's GPU actually buys

| Checkpoint | Compute |
|---|---|
| CP-06 weather, CP-07 lap classifier, CP-08 tyre, CP-09 baselines | CPU, minutes |
| CP-10 to CP-12 rule engine | CPU, seconds. Pure logic. |
| CP-13 opportunities | CPU, minutes |
| CP-14 pass model — LogReg, LightGBM, XGBoost, CatBoost | **CPU**, minutes on ~10k rows |
| CP-14 optional MLP | GPU helps |
| CP-18 to CP-20 energy twin, fuel, physics calibration | CPU, scipy least-squares |
| CP-21 segment-time — analytical, ridge, GBM | CPU |
| CP-21 optional neural regressor | GPU helps |
| CP-22 to CP-24 uncertainty, ablation, service | CPU |

Only **two optional benchmark candidates** touch the GPU. Everything else is CPU.
So never wait on GPU availability to make progress, and never block on Mudith
wanting his card back.

---

## 6. Order of work

Dependencies from `CHECKPOINTS_TANVEER.md`. Everything below unblocks the moment
the lake and segments are restored.

```
CP-06 weather  ──┐
CP-07 practice ──┼──▶ CP-08 tyre ──▶ (feeds CP-14)
                 └──▶ CP-18 twin ──▶ CP-19 fuel ──▶ CP-20 calibration ──▶ CP-21 ΔE→Δt ──▶ CP-22
CP-09 baselines

CP-10 state machine ──▶ CP-11 rule engine ──▶ CP-12 eligibility ──▶ CP-13 opportunities ──▶ CP-14 pass model
                                                                                              ├─▶ CP-15 calibration ──▶ CP-16 ensemble
                                                                                              └─▶ CP-17 era handling
CP-23 ablation  (needs CP-14 + CP-21)
CP-24 service   (needs CP-11 + CP-14 + CP-21)
```

**Start with CP-10 and CP-11.** They need only `config/rules/2026/` (already in
git) and the segments, they are pure CPU logic, and CP-11 produces
`max_electrical_power_kw()`, which CP-18b and the DP both wait on. They are also
the cheapest way to confirm the machine is set up correctly end to end.

**Run CP-06 early**, even out of dependency order, because it is the only
downstream checkpoint that reads raw files. If the 113 MB bundle was forgotten or
restored to the wrong path, CP-06 is where that surfaces — and finding out on day
one is much better than finding out at CP-18.

### Waiting on Rishabh

| Needed | From | Status |
|---|---|---|
| C7 `normal_race_model_eligible` | his CP-01 | ✅ in git |
| C9 leakage-safe splitter | his CP-02 | ✅ in git |
| C8 battle episodes, `battle_id` | his CP-03 | ⏳ blocks CP-13 |
| The lake and segments | Tanveer's CP-04 and CP-05, run by him | ⏳ pendrive |

CP-13 can define opportunities from `driver_ahead_number` and `gap_ahead_m` and
swap to `battle_id` later — but **do not train before C9 lands**, because the
split needs `battle_id`, and a leaky split invalidates every number CP-14
produces.

---

## 7. Four corrections to `CHECKPOINTS_TANVEER.md`

The plan stands. These four were corrected while building CP-00 to CP-05, and
they change what "good" looks like rather than what to do.

**Acceptance-rate and monotonicity gates were unachievable and are now
evidence-based.** Rejection is structural: lap 1 starts from a grid slot and pit
laps take a different path, so a correct lake lands near 93%, not 95%+, and
Qualifying near 76%. Judge the rejection *codes* — `NON_MONOTONIC_DISTANCE` is
expected, `MISSING_TEL_OBJECT` would mean damaged files.

**Lap length is telemetry-derived, not the published circuit length.** Telemetry
distance reads 0.6% to 2.7% short because it is integrated along the driven path.
CP-05 segments on telemetry distance, so everything downstream must use
`lap_length_m.value` from `config/rules/2026/`. The ±50 m gate became ±3%.

**CP-05 gained a fifth boundary source.** Long straights were single 757 m
segments, which hides the thing the project models: above ~290 km/h the power
envelope tapers, so a megajoule buys less time past that point. Splitting at the
taper crossing makes λ_E resolvable along a straight, and took Silverstone to 33
segments.

**Every regulatory value is still `UNVERIFIED`** and `strict_mode` stays `false`
until the FIA 2026 Sporting and Technical Regulations and the per-event notes are
sourced. Nothing is blocked by this; only demo claims are (§57). It is also the
one genuinely parallel task available right now, and it needs no compute at all —
it is research, and it can be done while the pendrive is still in transit.

---

## 8. Checklist

Before leaving Tanveer's laptop:

- [ ] `git status` clean, `git push origin tanveer/rule-eng` done
- [ ] Pendrive holds the 113 MB session-JSON bundle — **easy to forget, CP-06 needs it**
- [ ] `artifacts/schema_audit/` copied if the CP-01 evidence is wanted without a 90-minute re-run
- [ ] Decided whether the lake will ever be rebuilt there; if yes, the 29 GB raw mirror stays put

Before starting on Mudith's machine:

- [ ] On `tanveer/rule-eng`, with `user.name`/`user.email` set locally to Tanveer
- [ ] `.venv` built from `requirements.lock.txt`
- [ ] Rishabh's `telemetry_20m/` and `segments/` restored **with their manifests**
- [ ] Session JSON restored to `data/raw/tracinginsights/`
- [ ] 312 + 68 tests pass, `verify_lake.py` gates pass, `built_by` commit reconciled
- [ ] `weather.json` findable under `data/raw/` (the CP-06 canary)
