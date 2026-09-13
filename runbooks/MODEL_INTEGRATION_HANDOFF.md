# Model integration handoff — swap telemetry overrides for real models

**Audience:** the session that built the frontend/backend integration against
synthetic telemetry overrides. Written for someone who knows the UI and the API
surface but has not seen the model artifacts.

The models now exist. This is what they are, where they live, what they accept,
what they refuse, and what is still synthetic and must stay labelled that way.

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

## 6. Routes that are still synthetic

Leave them synthetic and keep them labelled. Promoting a `SIMULATED` value to
look observed is the one thing this project treats as unrecoverable.

| Route | Status |
|---|---|
| `POST /pass/predict` | **Real model** |
| `POST /twin/segment_time` | Synthetic. CP-21's `a_k` is inverted by segment type, so wiring it would hand the planner a transition pointing the wrong way |
| `POST /twin/energy_state` | Synthetic. CP-20 has no accepted rung |
| `GET /value/{event}/shadow_price`, `POST /plan`, `POST /simulate` | Synthetic, and depend on the twin above |
| `GET /track`, `/rules`, `/battles`, `POST /rival/state` | Rishabh's half |

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
