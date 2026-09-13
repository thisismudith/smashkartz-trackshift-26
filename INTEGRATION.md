# TrackShift integration guide

**Audience:** whoever is wiring the frontend to the backend. Written for someone
who knows the UI and the API surface but has not seen the model artifacts.

The service has always answered. What changed is that some of those answers are
now real models rather than synthetic placeholders — and the rest still are not.
This document says which is which, what the real ones accept, what they refuse,
and what must stay labelled as synthetic.

**The one rule that matters.** A `SIMULATED` value must never be rendered as
though it were observed or modelled. Every response carries provenance and an
`is_stub` flag for exactly this reason. Read §6 before you decide a route is
ready to show as real.

## Quick start

```powershell
git pull
.venv/Scripts/python.exe scripts/serve/run_service.py --check   # readiness, no port
.venv/Scripts/python.exe scripts/serve/run_service.py           # serve on :8000
.venv/Scripts/python.exe -m pytest tests/test_pass_service.py -q
```

`--check` prints which checkpoints are backed by a real artifact and which would
fall back to the synthetic model. Run it first: a service that starts cleanly
and then serves placeholder probabilities is the failure worth catching before
the UI does.

---

## 1. What changed

`POST /api/v1/pass/predict` previously answered from a hard-coded logistic
curve. It now answers from the real CP-14 artifact when one is on disk, and
falls back to the synthetic curve only when it is not — recording that fallback
so nothing downstream can claim a real answer.

The loader, guards and response shape are already written. **Reuse
`src/trackshift/serve/pass_service.py`; do not write a second loader.** Two code
paths building the same JSON is the failure mode CP-24 explicitly warns about.

---

## 2. Artifact inventory

| Model | Path | Status |
|---|---|---|
| Pass model (M10, CP-14) | `artifacts/models/pass/v2/<checkpoint>/<family>/` | **Use this.** The 15–17 feature build |
| Pass model, older | `artifacts/models/pass/v1/` | Same feature count, kept for comparison. Prefer `v2` |
| Calibration (CP-15) | `artifacts/validation/calibration.json` | Comparison only; no calibrator artifact is wired yet |
| Ensemble (CP-16) | `artifacts/models/pass/v1/ensemble.json` | Spread only |
| Physics calibration (CP-20) | `artifacts/models/twin/<version>/`, `latest.txt` | **Partial** — do not promote |
| Segment time (CP-21) | `artifacts/models/segment_time/<version>/segment_responses.parquet` | **Partial** — `a_k` inverted |

Each pass artifact directory holds `model.pkl`, `feature_schema.json`,
`manifest.json`, `metrics.json`, plus a native model file.

**Every pass artifact is graded `INTERIM`, not `FULL`.** The opportunity table
holds 2026 only, so CP-14 could not run its documented 2022–24 / 2025 / 2026
split. The numbers are real and the split is leakage-safe; what they are not is
a passed acceptance gate. `manifest.run.evidence_grade` carries this and the
route already returns it. **Surface it in the UI** rather than dropping it.

---

## 3. The input contract

Send these field names. They are the model's own schema, not the UI's
vocabulary — if the frontend currently posts `gap_s`, it must map to
`gap_at_checkpoint`.

Shared by all three checkpoints:

```
numeric      gap_at_checkpoint, closing_rate_s_per_s, p_eligible,
             attacker_tyre_life_laps, defender_tyre_life_laps,
             tyre_life_delta_laps, wind_head_component_mps,
             wind_cross_component_mps, track_temperature
categorical  attacker_tyre_compound, defender_tyre_compound,
             tyre_compound_pair, corner_type, sector, wet_track_flag
```

Plus, by checkpoint:

| Checkpoint | Extra fields | Total |
|---|---|---|
| `DETECTION` | — | 15 |
| `ACTIVATION` | `speed_at_activation_kmh` | 16 |
| `BRAKING` | `speed_at_activation_kmh`, `speed_at_braking_kmh` | 17 |

Categorical values are strings: compounds `SOFT`/`MEDIUM`/`HARD`, `corner_type`
like `MEDIUM_RIGHT`, `tyre_compound_pair` like `SOFT|HARD`, `wet_track_flag`
boolean. `sector` is currently null in C1 for every row — send it or omit it,
it carries no signal yet.

**Partial payloads are allowed.** A missing feature stays missing (the trees
handle NaN natively) and the response lists it under `features_missing`. This
matters for you: wire the fields you have today and add the rest incrementally.
But show `features_missing` somewhere, because a probability built from 3 of 15
features is a weaker claim than one built from all 15, and the user cannot tell
otherwise.

---

## 4. The two refusals the frontend must handle

Both return **HTTP 422** with `{"detail": {"code": ..., "message": ...}}`.

**`CHECKPOINT_VIOLATION`** — the request carried a feature its checkpoint cannot
know. A `DETECTION` request carrying `speed_at_activation_kmh`,
`gap_at_activation_s`, `distance_activation_to_brake` or `speed_at_braking_kmh`
is refused. This is deliberate, and is the serving half of CP-13's leakage
guarantee: at the Detection Line the car has not reached the Activation Line, so
a caller supplying activation speed is either confused or leaking future data.

A `null` is not a violation — send the full shape with nulls freely. Only a
*value* is a claim.

**`NOT_MODEL_ELIGIBLE`** — `normal_race_model_eligible: false`. The model is
fitted on green-flag normal-race rows only, so under Safety Car, VSC or a pit
sequence its output would be extrapolation. Absent is treated as eligible.

Render both as an explanatory state, not a generic error toast. They are
answers, not failures.

---

## 5. Response shape

```json
{
  "p_pass_by_outcome_horizon": {
    "value": 0.1632,
    "provenance": "INFERRED",
    "model": "M10/lightgbm",
    "artifact_version": "v2/detection/lightgbm"
  },
  "checkpoint": "DETECTION",
  "calibration": "uncalibrated",
  "features_supplied": ["gap_at_checkpoint"],
  "features_missing": ["track_temperature"],
  "evidence_grade": "INTERIM",
  "is_stub": false,
  "versions": {}
}
```

`is_stub: true` means the synthetic curve answered. Never render a stub
probability as a model output.

---

## 6. Route inventory — what is real and what is not

All 17 routes respond. Only the first is backed by a trained model today.

| Route | Backing | Safe to show as real? |
|---|---|---|
| `POST /pass/predict` | **CP-14 artifact** | **Yes**, with the `INTERIM` grade shown |
| `GET /meta` | Version metadata | Yes |
| `GET /validation` | Synthetic report | No — labelled |
| `GET /track/{event}` | Synthetic centreline and segments | No |
| `GET /rules/{event}` | Real rule config (CP-03/CP-11) | Yes |
| `GET /rules/{event}/power_envelope` | Real rule engine | Yes |
| `POST /rules/legal_actions` | Real rule engine | Yes |
| `POST /rules/eligibility` | Real eligibility projection (CP-12) | Yes |
| `GET /battles`, `GET /battles/{id}/timeline` | Synthetic rows | No |
| `POST /rival/state` | Synthetic rival model | No |
| `POST /twin/segment_time` | Synthetic | **No** — see below |
| `POST /twin/energy_state` | Synthetic | **No** — see below |
| `GET /value/{event}/shadow_price` | Synthetic transition | No |
| `POST /plan` | Synthetic | No |
| `POST /simulate`, `GET /simulate/policies` | Synthetic | No |

### Why the twin routes stay synthetic

This is a deliberate hold, not unfinished wiring. CP-20's physics calibration
has no accepted rung — every fitted parameter sits on a bound, which means the
model is missing physics rather than that the bounds are wrong. CP-21's energy
sensitivity `a_k` is **inverted by segment type**: corners average 7.1 s/MJ
against straights at 0.63, where the physics says the opposite. Wiring that into
`/twin/segment_time` would hand the planner a transition pointing the wrong way,
and the planner would confidently recommend deploying energy where it helps
least.

Leave them synthetic until CP-20 produces an accepted rung. The blocker is
documented in `CHECKPOINTS_TANVEER.md` §CP-20.

---

## 7. Testing

Existing, must stay green:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_pass_service.py -q
```

20 tests covering both refusals, provenance, missing-feature reporting, and that
a refusal never falls through to the stub.

Add for the integration:

1. **Contract test** — every field the frontend sends appears in
   `feature_schema.json` for that checkpoint. This catches a UI rename before it
   becomes a silently-missing feature and a quietly worse probability.
2. **Refusal rendering** — 422 `CHECKPOINT_VIOLATION` and `NOT_MODEL_ELIGIBLE`
   each produce the intended UI state.
3. **Stub detection** — with artifacts absent the response carries
   `is_stub: true` and the UI marks it. Monkeypatch `load_predictor` to raise
   `ModelUnavailable`; `tests/test_pass_service.py` has the pattern.
4. **Startup smoke** — `run_service.py --check` reports all three checkpoints
   backed, exit 0.
5. **Latency** — one prediction well under 50 ms once loaded. If it is not, the
   model is being unpickled per request.

---

## 8. Gotchas

- **Load once, at startup.** `app.state.pass_predictors` caches per checkpoint
  and a miss is remembered, not retried — unpickling on the hot path is the
  obvious latency bug.
- **`PASS_MODELS` resolves from the module file, not the cwd.** Deliberate: the
  replay bundle must be reproducible from any working directory.
- **Do not commit artifacts.** `.gitignore` excludes `artifacts/models/` and
  `artifacts/validation/`. A local edit once un-ignored them and staged roughly
  sixty model binaries.
- **`stubs_used` is measured, not asserted.** It was hard-coded empty while the
  route was synthetic — exactly what it existed to detect. `--final` now raises
  on a non-empty set.
- **Prefer `v2` over `v1`.** Same feature count; `v2` is the later build.

---

## 9. Integration checklist

Work top to bottom; each step is verifiable on its own.

- [ ] `run_service.py --check` reports all three checkpoints backed, exit 0
- [ ] Startup logs one line per checkpoint: artifact version, family, feature
      count, evidence grade — and a named warning on any that fail to load
- [ ] Frontend field names mapped to the model schema (§3); contract test added
- [ ] `CHECKPOINT_VIOLATION` renders as an explanatory state, not an error toast
- [ ] `NOT_MODEL_ELIGIBLE` renders as an explanatory state
- [ ] `evidence_grade` visible in the UI wherever a probability is shown
- [ ] `is_stub: true` visibly marked, never rendered as a model output
- [ ] `features_missing` surfaced somewhere the user can reach
- [ ] Twin, planner and simulate routes still labelled `SIMULATED`
- [ ] `pytest tests/test_pass_service.py -q` green
- [ ] One prediction under 50 ms once loaded

## 10. Where to look when something is wrong

| Symptom | Look at |
|---|---|
| Every probability is identical or suspiciously smooth | `is_stub` — the synthetic curve is answering. Check `run_service.py --check` |
| Probabilities barely move as the UI changes inputs | `features_missing` — the field names probably do not match §3 |
| 422 on a request that looks fine | `detail.code`. `CHECKPOINT_VIOLATION` means a future-scoped field was sent with a value; send `null` instead |
| First request slow, rest fast | Expected — the model unpickles once. If *every* request is slow, the cache is not being used |
| Service starts then 500s on predict | An artifact is present but unreadable. The startup log names which |

## 11. Related documents

- `CHECKPOINTS_TANVEER.md` — the completion register, per-checkpoint gate status,
  and why the partial ones are partial
- `runbooks/CLOSURE_LOOP.md` — the append-only evidence log for both owners
- `API.md` — the route contracts
- `MODELS.md` — artifact layout and versioning rules
