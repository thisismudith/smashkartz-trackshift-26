# Handover: CP-04 (build the 20 m lake) and CP-05 (track segmentation)

**For Rishabh. From Tanveer's branch `tanveer/rule-eng`.**

These are two checkpoints from `CHECKPOINTS_TANVEER.md`. Numbering is per-owner
and collides, so these are **Tanveer's CP-04 and CP-05**, not yours — your CP-04
is causal pairwise features and your CP-05 is the rival-state dataset.

Outputs come back to Tanveer on a pendrive.

---

## Before you start

**This is not GPU work.** The build parses 177k JSON files; it is bound by CPU
cores and disk speed. The RTX 4080 contributes nothing. What helps is `--jobs`
and an SSD.

**You need the raw mirror** at `data/raw/tracinginsights/<year>/<event>/<session>/<driver>/`.
Confirm with:

```bash
python scripts/data/inventory.py --raw-root data/raw/tracinginsights --output artifacts/schema_audit
```

Expect `177,288 lap files, 279 sessions, 27.19 GB` and
`years_missing_from_mirror: []`. If your layout is `data/raw/<year>/` instead,
pass `--raw-root data/raw` everywhere below.

**Environment** (CP-00, five minutes):

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
# source .venv/bin/activate         # Linux/macOS
pip install -r requirements.txt
python -m pytest -q                 # expect 244 passed
```

`pyarrow` is the one that matters: without it the builder exits rather than
writing Parquet.

---

## CP-04 — build the lake

### The short version

```bash
python scripts/data/build_lake.py --years 2026 --jobs 8
python scripts/data/verify_lake.py
```

On Windows there is a runbook that stages it and reports a completion
percentage:

```powershell
.\runbooks\CP-04.ps1 -Jobs 8
```

### Scope, in the order worth doing it

| Stage | Command | Laps | Time at `--jobs 8` |
|---|---|---|---|
| 4a smoke | `--years 2026 --events "British Grand Prix" --sessions Race --drivers HAM ANT` | 104 | ~1 min |
| 4b demo event | `--years 2026 --events "British Grand Prix"` | 2,644 | ~10 min |
| 4c 2026 domain | `--years 2026` | 34,336 | ~60 min |
| 4d DRS-era priors | `--years 2022,2023,2024,2025 --sessions Qualifying,Race` | 130,718 | ~3.8 h |

**Do 4a first.** It takes a minute and catches an environment problem before an
hour is spent on one.

**4d is optional and should probably wait.** It is 74% of the total build cost
and supplies DRS-era priors that only the pass-model benchmark needs, which is
several checkpoints away. 4a to 4c is what unblocks everything immediately.

Add `--dry-run` to any of them to see the session list without building.

### What "good" looks like

`verify_lake.py` checks nine gates and writes `verify_report.json`. All nine
should pass. Two numbers look wrong at first glance and are not:

**Acceptance rate is about 93%, not 95%+.** Rejection is structural. Lap 1
starts from a grid slot and pit in/out laps traverse a different path, so their
distance channel is legitimately non-monotonic and the validator rejects them by
design. On the 2026 British GP Race, 97 of 104 laps were accepted and all 7
rejections were lap 1 or a pit lap. **Judge the rejection codes, not the rate:**
`NON_MONOTONIC_DISTANCE` is expected, `MISSING_TEL_OBJECT` would mean damaged
files.

**Qualifying is worse, around 76% monotonic.** Every flying lap is bracketed by
an out-lap and an in-lap. Also correct, also by design.

### If a session fails

`run_manifest.json` has a `failures[]` array naming the session and the error.
One bad session does not stop the batch. Re-run just that scope; the builder is
idempotent and overwrites its own partition.

### Resuming

`build_lake.py` rebuilds whatever scope you give it, so to resume just narrow
the scope. The Windows runbook takes `-Stages 4b,4c` for the same purpose.

---

## CP-05 — track segmentation

**Read `CHECKPOINTS_TANVEER.md` CP-05 before starting.** It is marked critical
path for a reason: `segment_id` is the join key for every downstream table, so
changing it later invalidates baselines, pairwise features, opportunities and
the DP's state grid all at once.

The contract it produces is **C1** in `MODELS.md` section 5.1. Your own CP-03
and CP-04 consume it.

Two things that are easy to get wrong:

**Segment boundaries live in telemetry-distance coordinates**, not homologated
circuit distance. Telemetry distance reads 0.6% to 2.6% short of the published
lap length because it is integrated along the driven path. `config/rules/2026/`
records both numbers per circuit; use `lap_length_m.value`, and treat
`published_circuit_length_m` as a cross-check only.

**`geometry_version` must change whenever boundaries change.** Every artifact
that consumed the old segmentation records the version it used, so a bump is how
a stale downstream table becomes detectable instead of silently wrong.

---

## Handing the outputs back

Tanveer needs the built data on his own disk; the code travels by git, the data
does not.

| What | Path | Size |
|---|---|---|
| The lake | `data/processed/telemetry_20m/` | ~1.1 GB for 2026, ~5.5 GB for all seasons |
| Segments | `data/processed/segments/` | smaller |
| Manifests | `run_manifest.json`, `lap_manifest.csv`, `rejected_laps.csv`, `quality_summary.csv` | a few MB |

**Copy the manifests too, not just the Parquet.** They carry the rejection
reasons and the build configuration, and without them the lake cannot be audited
or reproduced.

Code changes go through git as normal:

```bash
git add -A && git commit -m "..." && git push
```

### Checking the transfer worked

The build is deterministic: identical inputs and the same commit produce
byte-identical Parquet. After copying, Tanveer can confirm nothing was corrupted
in transit with:

```bash
python scripts/data/verify_lake.py
```

If the row counts and gates match what your `run_manifest.json` reported, the
copy is sound.

---

## Questions worth asking before you start

1. Is your raw mirror at `data/raw/tracinginsights/` or `data/raw/`? It changes
   every `--raw-root` below.
2. Do you want 4d? It is nearly four hours and nothing needs it yet.
3. Are you on Windows or Linux? The `.ps1` runbooks are Windows-only; the Python
   scripts work anywhere.
