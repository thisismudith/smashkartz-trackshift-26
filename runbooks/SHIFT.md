# Machine shift: moving CP-06 to CP-24 to the new laptop

Everything needed to continue Tanveer's plan on a different machine, split by
where each piece comes from. Nothing here changes the engineering plan in
`CHECKPOINTS_TANVEER.md`; the differences that *do* exist are listed in §6.

**The short version:** the code comes from GitHub, roughly **1.3 GB** of data
comes by pendrive, and the 29 GB of raw telemetry does not need to travel at all.

---

## 1. What comes from GitHub

Clone and switch. Everything below is already pushed.

```bash
git clone https://github.com/thisismudith/smashkartz-trackshift-26.git
cd smashkartz-trackshift-26
git switch tanveer/rule-eng
```

| What | Path |
|---|---|
| Contracts and plans | `TrackShift AGENTS.md`, `MODELS.md`, `API.md`, `CHECKPOINTS_*.md` |
| Dependency pins | `requirements.txt`, `requirements.lock.txt`, `requirements-gpu.txt` |
| Phase 2 library | `src/trackshift/` — loader, validation, resampling, progress |
| Registries (CP-02) | `config/data_registry.yaml`, `config/feature_registry.yaml` |
| Rule config (CP-03) | `config/rules/2026/` — `common.yaml` + 14 event files |
| Race-control evidence (CP-03) | `artifacts/race_control/` — 17 files, 2.2 MB, Tier B |
| Segment maps (CP-05) | `config/geometry/*.yaml` — **from Rishabh's push, not the pendrive** |
| Scripts | `scripts/data/`, `scripts/features/` |
| Runbooks | `runbooks/` |
| Tests | `tests/` — 312 passing |

`config/geometry/*.yaml` is the one that looks like data but is not. It is the
static segment map, it is small, and it must be reviewable alongside the
`boundary_hash` its segment table carries. **Rishabh commits it; you pull it.**

---

## 2. What comes by pendrive

Four items, about **1.3 GB** total. Two sources.

### 2a. From Rishabh — he builds these (his run of Tanveer's CP-04 and CP-05)

| # | Copy this folder whole | Size | Notes |
|---|---|---|---|
| 1 | `data/processed/telemetry_20m/` | ~1.1 GB for 2026 | The 20 m lake |
| 2 | `data/processed/segments/` | ~50 MB | Contract C1 |

**Copy the manifests, not only the Parquet.** `run_manifest.json`,
`lap_manifest.csv`, `rejected_laps.csv` and `quality_summary.csv` sit in the
lake root and are under 2% of the transfer. Without them the lake cannot be
audited or reproduced: `run_manifest.json` records which git commit built it and
whether the tree was clean, and `rejected_laps.csv` is the only record of the
~7% of laps that were dropped and why. Copying the folder whole gets all of this;
cherry-picking `.parquet` files does not.

If he also builds 4d (2022-2025), the lake grows to about 5.5 GB. That is
optional and only CP-14 needs it.

### 2b. From this laptop — raw session files

| # | What | Size | Why |
|---|---|---|---|
| 3 | Every raw `.json` **except** `*_tel.json` | **113 MB** | `weather.json`, `rcm.json`, `corners.json`, `drivers.json`, `laptimes.json` |
| 4 | `artifacts/schema_audit/` | 184 MB | CP-01 evidence. Optional — regenerable in ~90 min |

Item 3 is not optional and is easy to miss. **The lake carries no weather
columns at all** — I checked. CP-06 (M33, track-relative wind) reads
`weather.json` directly, and any future race-control or DRS work reads
`rcm.json` and `corners.json`. All of these are session-level, which is why they
fit in 113 MB instead of 29 GB.

To extract them without the telemetry, from Git Bash:

```bash
cd /t/Trackshift/Trackshift/data/raw/tracinginsights
mkdir -p /d/shift/raw_session_json
find . -name '*.json' ! -name '*_tel.json' -exec cp --parents {} /d/shift/raw_session_json/ \;
du -sh /d/shift/raw_session_json          # expect ~113 MB, ~7,039 files
```

Replace `/d/` with your pendrive letter. On the new machine, restore to
`data/raw/tracinginsights/` so the directory tree matches.

### What does NOT travel

| | Why |
|---|---|
| `data/raw/**/*_tel.json` — **29 GB** | Nothing in CP-06 to CP-24 reads raw telemetry. They read the lake and segments. |
| `.venv/` | Absolute paths are baked in. Rebuild from `requirements.lock.txt`. |
| `artifacts/drs_zones/` | Tier C, gitignored, regenerable, and development-only. |
| `__pycache__/` | Obviously. |

**The one case where you would need the 29 GB** is rebuilding the lake yourself.
If Rishabh is producing it, you do not. Decide before wiping this laptop, because
re-downloading is hours.

---

## 3. Setting up the new machine

```bash
# 1. Code
git clone https://github.com/thisismudith/smashkartz-trackshift-26.git
cd smashkartz-trackshift-26
git switch tanveer/rule-eng

# 2. Environment. lock, not requirements.txt: exact pins, so this machine and
#    Rishabh's cannot drift.
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows
# source .venv/bin/activate         # Linux
python -m pip install --upgrade pip
pip install -r requirements.lock.txt

# 3. Restore the pendrive folders to these exact paths
#    data/processed/telemetry_20m/
#    data/processed/segments/
#    data/raw/tracinginsights/          (session JSONs only)
#    artifacts/schema_audit/            (optional)
```

If the GPU is to be used for the two optional neural candidates:

```bash
pip install -r requirements-gpu.txt
```

### Verify before doing any work

```bash
python -m pytest -q
python scripts/data/verify_lake.py
python -c "import sys; sys.path.insert(0,'src'); from trackshift.data.registry import validate_registries; print(validate_registries() or 'registries valid')"
```

Expect **312 passed**, all `verify_lake` gates passing, and `registries valid`.

`verify_lake.py` prints `built_by` with the commit that produced the lake. **If
that commit differs from your `HEAD`, check whether the difference touches
`resample.py`, `raw_loader.py` or `validation.py`.** If it does, the lake predates
a change to how it is built and should be rebuilt; if not, it is fine. This is
the check that makes a cross-machine handover safe, and it is the reason
`git_commit` is recorded at all.

Also confirm the segment maps and the segment table agree:

```bash
python -m pytest tests/test_segmentation.py -q     # 68 tests
```

A mismatch here means the geometry in git is not the geometry that built the
Parquet on the pendrive — bump and rebuild rather than proceeding.

---

## 4. What the new machine actually buys you

Honestly: **less than it looks, and not for the reason you might think.**

| Checkpoint | Compute |
|---|---|
| CP-06 weather, CP-07 lap classifier, CP-08 tyre, CP-09 baselines | CPU, minutes |
| CP-10 to CP-12 rules engine | CPU, seconds. Pure logic. |
| CP-13 opportunities | CPU, minutes |
| CP-14 pass model — LogReg, LightGBM, XGBoost, CatBoost | **CPU**, minutes on ~10k rows |
| CP-14 optional MLP | GPU helps |
| CP-18 to CP-20 energy twin, fuel, physics calibration | CPU, scipy least-squares |
| CP-21 segment-time — analytical, ridge, GBM | CPU |
| CP-21 optional neural regressor | GPU helps |
| CP-22 to CP-24 uncertainty, ablation, service | CPU |

Only **two optional benchmark candidates** use the GPU. Everything else is CPU.
The real gain from the new laptop is more cores and a faster disk, which matters
if you ever rebuild the lake — and that is the one job you are handing to
Rishabh.

This is not an argument against moving. It is an argument against waiting on the
GPU for anything.

---

## 5. Order of work after the shift

Dependencies from `CHECKPOINTS_TANVEER.md`. Everything below is unblocked the
moment the lake and segments land.

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

**Start with CP-10 and CP-11.** They need only `config/rules/2026/` (git) and the
segments, they are pure CPU logic, and CP-11 produces
`max_electrical_power_kw()`, which CP-18b and the DP both wait on. They are also
the cheapest way to confirm the new machine is working end to end.

**CP-06 is the one to check early**, because it is the only downstream checkpoint
that reads raw files. If you forgot the 113 MB bundle, CP-06 is where you find
out.

### Waiting on Rishabh

| You need | He produces | Status |
|---|---|---|
| C7 `normal_race_model_eligible` | his CP-01 | ✅ done, in git |
| C9 leakage-safe splitter | his CP-02 | ✅ done, in git |
| C8 battle episodes, `battle_id` | his CP-03 | ⏳ blocks your CP-13 |
| The lake and segments | your CP-04, CP-05 | ⏳ pendrive |

Your CP-13 can define opportunities from `driver_ahead_number` and
`gap_ahead_m` and swap to `battle_id` later — but **do not train before C9
lands**, because the split needs `battle_id` and a leaky split invalidates every
number CP-14 produces.

---

## 6. Changes to `CHECKPOINTS_TANVEER.md` worth knowing

The plan stands. Four things were corrected while building CP-00 to CP-05, and
they change what "good" looks like rather than what to do.

**Acceptance-rate and monotonicity gates were unachievable and are now
evidence-based.** Rejection is structural: lap 1 starts from a grid slot and pit
laps take a different path, so a correct lake lands near 93%, not 95%+, and
Qualifying near 76%. Judge the rejection *codes*.

**Lap length is telemetry-derived, not the published circuit length.** Telemetry
distance reads 0.6% to 2.7% short because it is integrated along the driven path.
CP-05 segments on telemetry distance, so everything downstream must use
`lap_length_m.value` from `config/rules/2026/`. The ±50 m gate became ±3%.

**CP-05 gained a fifth boundary source.** Long straights were single 757 m
segments, which hides the thing the project models: above ~290 km/h the power
envelope tapers, so a megajoule buys less time past that point. Splitting at the
taper crossing makes λ_E resolvable along a straight and took Silverstone to 33
segments.

**Every regulatory value is still `UNVERIFIED`.** `strict_mode` stays `false`
until the FIA 2026 Sporting and Technical Regulations and the per-event notes are
sourced. Nothing is blocked by this; only demo claims are (§57). This is the one
piece of genuinely parallel work available right now, and it needs no compute at
all — it is research.

---

## 7. Before wiping this laptop

- [ ] `git status` clean and `git push` done on `tanveer/rule-eng`
- [ ] Pendrive holds the 113 MB session-JSON bundle — **easy to forget, CP-06 needs it**
- [ ] Decide whether you will ever rebuild the lake here. If yes, the 29 GB raw mirror has to travel too, or be re-downloaded (hours)
- [ ] `artifacts/schema_audit/` copied if you want the CP-01 evidence without a 90-minute re-run
- [ ] Rishabh has pushed `config/geometry/*.yaml`, and his lake and segments are on the pendrive with their manifests
