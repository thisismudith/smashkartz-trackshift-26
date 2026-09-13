# TrackShift Model API

The contract between the models in `MODELS.md` and the frontend UI. This is the **only** document the UI owner (Owner C) needs. It names every callable surface, its request and response shape, units, provenance, and failure behaviour.

- `TrackShift AGENTS.md` — engineering contract (what the system is)
- `MODELS.md` — who builds which model, on what compute
- `API.md` — this file: what the UI can call, and what comes back

Section references (§N) point to `TrackShift AGENTS.md`. Model IDs (M01–M35) and contract IDs (C1–C10) point to `MODELS.md`.

Written against `TrackShift AGENTS.md` including the strategic-state and causal-transition formalism (§1, §31) and the speed-dependent power envelope (§11–§13, §17, §19, §20.1, §20.2, §28.1).

---

## 1. Principles

1. **JSON in, JSON out.** Every payload is plain JSON. No pickles, no NumPy arrays, no DataFrames across the boundary.
2. **Every displayed number carries a provenance tag** (§43): `OBSERVED`, `DERIVED`, `INFERRED`, `SIMULATED`, or `RULE`. The UI must render the tag. It must never show a `SIMULATED` energy value as if it were measured (§58).
3. **Units are in field names.** `gap_s`, `distance_m`, `speed_kmh`, `energy_kj`, `time_s`. A field with no unit suffix is dimensionless or an enum.
4. **Uncertainty is never dropped.** Where a model produces a distribution, the API returns it (§42). The UI may collapse it for display but the payload keeps it.
5. **Missing means missing.** A quantity that is unavailable for a season or session is `null` with a `reason` string, never fabricated (§11).
6. **Two delivery modes, one schema.** A precomputed **replay bundle** (static JSON, §7) and a **live service** (HTTP, §5) return identical shapes. The UI is written once against the schema and works in both.
7. **Versioned.** Every response includes `api_version` and the `model_version` of each model that contributed.
8. **CPU-only backend is supported.** No route requires a GPU at inference time (MODELS.md §1.1).
9. **Illegal actions never appear** in any action list. The rule engine filters before scoring (§31).

---

## 2. Delivery modes

| Mode | What it is | When to use | Owner |
|---|---|---|---|
| **Replay** | Static JSON files under `artifacts/demo/<event>/<battle>/` generated once from the trained models | The hackathon demo, offline review, UI development before the service exists | Tanveer builds the bundle generator; both owners feed it |
| **Service** | HTTP server exposing the routes in §5, calling the `api.py` contracts from `MODELS.md §5` | Interactive what-if (change energy, change gap, re-plan), live-style playback | Per-route owner in §9 |

The UI **must** be built against the replay bundle first. The service is additive.

---

## 3. Common types

Used across all routes. Field names are exact.

### 3.1 `Provenance`

```text
"OBSERVED" | "DERIVED" | "INFERRED" | "SIMULATED" | "RULE"
```

These five are the whole vocabulary. There is no `MISSING` member and none is
added: a quantity with no value is `null` with a `reason` (principle 5, §3.2)
and keeps the provenance of the source that would have supplied it.
`provenance` says what kind of claim the number *is*, which does not change
when the number is absent. A tag from any other vocabulary in this field — in
particular a rule `value_source` (§3.1a) — is a contract violation, not a
richer answer.

### 3.1a `value_source` — what evidence fixed a rule value

Orthogonal to §3.1, and never a substitute for it. `provenance` says what kind
of statement a number is; `value_source` says what evidence fixed its value. A
rule-sourced number carries **both**, in two separate fields:

```text
"RULE_FIA" | "OBSERVED_RCM" | "OBSERVED" | "DERIVED_TELEMETRY" | "PROXY_HISTORICAL_DRS" | "UNVERIFIED"
```

```json
{ "value": 5789.3, "provenance": "RULE", "unit": "m", "value_source": "DERIVED_TELEMETRY",
  "source": "FIA 2026 British GP circuit map, Doc 6 p. 2, OVERTAKE DETECTION 115 m after T18; T18 at 5674.3 m from geometry.corner_distances_m" }
```

`RULE_FIA | OBSERVED_RCM | OBSERVED | DERIVED_TELEMETRY` are the **claimable**
tiers — a public claim may rest on them. `PROXY_HISTORICAL_DRS` is development
only and never claimable, because historical DRS is not 2026 Overtake (§41); a
bundle containing one is rejected (§7). `UNVERIFIED` means the number is present
but not yet traced to a source: it is what `unverified_keys` (5.3) lists and
what the badge-and-never-claim-legality obligation exists for (§57). An
`UNVERIFIED` value is still `provenance: "RULE"` — the tier is not a provenance
and never appears in the `provenance` field. A rule key with no value at all is
`value: null` plus a `reason` (principle 5), which is a different state again.

`verified` is exactly `value_source ∈ {RULE_FIA, OBSERVED_RCM, OBSERVED,
DERIVED_TELEMETRY}`; `unverified_keys` is the list of keys where it is false.

`OBSERVED` appears in both vocabularies with different meanings: in §3.1 it
means the number is a measurement in the timing/telemetry feed; here it means
the rule value was read off a non-regulatory raw source such as `corners.json`.
Read it against the field it is in. Collapsing the two vocabularies into one
field is what produces the drift this section exists to stop.

Source of truth for the tiers: `TIERS` and `CLAIMABLE_TIERS` in
`src/trackshift/rules/config.py`.

*The service does not yet match.* `GET /track/{event}` writes the `value_source`
into `provenance`, so a Detection Line arrives tagged `DERIVED_TELEMETRY` — a
value §3.1 does not define — and the field that should hold it is absent.
`GET /rules/{event}` has the opposite half of the same fault: it emits
`value_source` and omits `provenance`. Both routes must emit both fields.

### 3.2 `Quantity` — a single number with provenance

```json
{ "value": 1.83, "provenance": "DERIVED", "unit": "s" }
```

`value` may be `null`; then `reason` is present:

```json
{ "value": null, "provenance": "OBSERVED", "unit": "s", "reason": "field not published for 2023 season" }
```

### 3.3 `Uncertain` — a number with a range

```json
{
  "mean": 1420.0, "low": 1310.0, "high": 1535.0,
  "draws": [1398.2, 1451.7, "..."],
  "provenance": "SIMULATED", "unit": "kJ"
}
```

`low`/`high` are the 10th/90th percentiles unless `interval` says otherwise. `draws` is optional and may be omitted in replay bundles to save space.

### 3.4 `StateRef` — where on the track

```json
{
  "year": 2026, "event": "british_grand_prix", "session": "Race",
  "lap": 31, "segment_id": 22, "distance_m": 3140.0, "lap_fraction": 0.53
}
```

`event` is the snake_case key used in `config/rules/2026/<event>.yaml`. `segment_id` is stable per track (C1).

#### `StrategicState`

All rule, value, planner, and simulation routes consume the same decision-time state. The JSON object groups: `ref`; `energy` (Energy Store distribution, deployed/harvested energy, recharge budget); `tyre` (observed context plus inferred performance state and uncertainty); `gap` (time and distance gap, relative speed, relative acceleration, gap rate); `overtake_state`; `race_control`; `power_envelope`; `rival_state`; and `uncertainty`. A caller may omit a modelled block only when the route can derive it causally from the supplied `ref`; it must never backfill it from future telemetry.

### 3.5 `Action`

```json
{ "deploy_level": 0.75, "lift_amount": 0.0, "label": "ATTACK_DEPLOY" }
```

`deploy_level` ∈ {0, 0.25, 0.5, 0.75, 1.0}. `lift_amount` ∈ [0, 1]. `label` is a tactical name attached after optimisation (§31) and is display-only.

### 3.6 `Versions`

```json
{
  "api_version": "1.0.0",
  "git_commit": "3c5c3ac",
  "models": {
    "pass": "pass-2026.03", "rival": "rival-hsmm-2026.02",
    "twin": "twin-event-2026.01", "segment_time": "segtime-ridge-2026.01",
    "rules": "rules-2026-bgp-r3", "value": "dp-2026.01", "planner": "beam-2026.01"
  }
}
```

### 3.7 `Error`

```json
{ "error": { "code": "FEATURE_SCHEMA_MISMATCH", "message": "expected 41 features in schema pass-2026.03, got 39", "detail": {} } }
```

Codes:

| Code | HTTP | Meaning |
|---|---|---|
| `UNKNOWN_EVENT` | 404 | no rule config or track geometry for this event |
| `UNKNOWN_BATTLE` | 404 | `battle_id` not in the battle index |
| `RULE_KEY_MISSING` | 500 | rule config lacks a required key; never defaults to "enabled" (C3) |
| `FEATURE_SCHEMA_MISMATCH` | 422 | feature vector does not match the model's locked schema (§53) |
| `OFFLINE_ONLY_FEATURE` | 422 | a live route was given an `OFFLINE_ONLY` feature (§44) |
| `CHECKPOINT_VIOLATION` | 422 | a pass-prediction request for one decision checkpoint carried a feature that only exists at a later checkpoint (§19) |
| `NOT_MODEL_ELIGIBLE` | 422 | the state is not `normal_race_model_eligible` (SC / VSC / pit / unknown control); models are not defined there and the UI must show the gate instead (§12) |
| `ILLEGAL_STATE` | 422 | state is outside the energy/gap grid or violates accounting |
| `ENVELOPE_UNVERIFIED` | 200 + header `X-TrackShift-Unverified: power_envelope` | the response used envelope values whose `verified` flag is false; the UI must badge every derived number (§20.1) |
| `MODEL_NOT_LOADED` | 503 | artifact missing or failed CPU load |
| `STUB_RESPONSE` | 200 + header `X-TrackShift-Stub: true` | route is served by a stub; values are placeholders with correct shape |

### 3.8 `DecisionCheckpoint`

```text
"DETECTION" | "ACTIVATION" | "BRAKING"
```

Every pass probability is attached to exactly one checkpoint (§19). A value predicted at `DETECTION` used only information available at or before the Detection Line.

### 3.9 `RaceControl` — the eligibility gate

```json
{
  "pit_state": "ON_TRACK",
  "race_control_state": "GREEN",
  "safety_car_active": false, "virtual_safety_car_active": false,
  "race_control_transition_flag": false, "pit_transition_flag": false,
  "green_flag_elapsed_s": 218.4,
  "normal_race_model_eligible": true,
  "provenance": "DERIVED"
}
```

`pit_state` ∈ `ON_TRACK | PIT_IN | PIT_LANE | PIT_OUT | UNKNOWN`. `race_control_state` ∈ `GREEN | YELLOW | DOUBLE_YELLOW | VSC | SC | RED | UNKNOWN`. When `normal_race_model_eligible` is false, every model field in that step is `null` with `reason: "not normal-race eligible"` — the models are not defined there, and the UI shows the gate rather than a number.

### 3.10 `PowerEnvelope` — the speed-dependent cap

```json
{
  "normal":   { "breakpoints_kmh": [0, 290, 340], "max_power_kw": [350, 350, 0] },
  "override": { "breakpoints_kmh": [0, 337, 355], "max_power_kw": [350, 350, 0] },
  "separation_speed_kmh": 290.0,
  "provenance": "RULE",
  "verified": false,
  "source": "reported values, pending FIA document citation"
}
```

Maximum electrical deployment is a **function of speed**, not a constant (§20.1). Evaluate by linear interpolation between breakpoints, clamped outside the range. The two curves coincide at low speed and separate above `separation_speed_kmh` — below that speed, override confers no power advantage and mode is not observable (§20.2).

`verified: false` means the numbers are placeholders. Any UI element derived from them must carry an "unverified regulation" badge, and the page must not describe the system as legal by construction (§57).

Values shown are illustrative of the shape; the real curve comes from `config/rules/2026/<event>.yaml`.

### 3.11 `ErsState` — estimated electrical state

```json
{
  "ers_soc_est_mj":            { "mean": 3.42, "low": 3.05, "high": 3.80, "provenance": "SIMULATED", "unit": "MJ" },
  "ers_store_capacity_mj":     { "value": 4.00, "provenance": "RULE", "unit": "MJ" },
  "ers_deploy_budget_remaining_est_mj":  { "mean": 1.18, "low": 0.90, "high": 1.46, "provenance": "SIMULATED", "unit": "MJ" },
  "ers_harvest_budget_remaining_est_mj": { "mean": 0.55, "low": 0.30, "high": 0.80, "provenance": "SIMULATED", "unit": "MJ" },
  "ers_energy_used_est_mj":    { "mean": 2.82, "provenance": "SIMULATED", "unit": "MJ" },
  "ers_energy_harvested_est_mj": { "mean": 0.94, "provenance": "SIMULATED", "unit": "MJ" },
  "ers_deploy_power_est_kw":   { "mean": 210.0, "low": 150.0, "high": 260.0, "provenance": "SIMULATED", "unit": "kW" },
  "ers_harvest_power_est_kw":  { "mean": 0.0, "low": 0.0, "high": 20.0, "provenance": "SIMULATED", "unit": "kW" },
  "cap_kw":                    { "value": 312.0, "provenance": "RULE", "unit": "kW" },
  "applicable_mode":           "NORMAL",
  "headroom_kw":               { "value": 102.0, "provenance": "DERIVED", "unit": "kW" },
  "ers_mode_inferred":         "NORMAL",
  "override_active_inferred":  { "value": 0.08, "provenance": "INFERRED" },
  "discriminable":             true,
  "envelope_violation":        false
}
```

Every `ers_*_est_*` field is an estimate from the energy twin (M14), never a measurement. The `_est` suffix is part of the field name and must not be stripped for display. Label these "estimated", never "battery", "SOC", or "measured" (§58).

`cap_kw` is the regulatory maximum at the car's **current speed** under `applicable_mode` — it moves continuously as the car accelerates. `headroom_kw` is `cap_kw − ers_deploy_power_est_kw`; a small or negative headroom is the interesting case and is what the UI should draw attention to.

`discriminable` is false below `separation_speed_kmh`. When false, `ers_mode_inferred` is `UNKNOWN` and `override_active_inferred` is `null` — the UI must show "unknown", never "normal" (§20.2).

`envelope_violation: true` means the estimate exceeded the override cap, which indicates a **twin calibration fault**, not a rule breach by the car (§28.1). Surface it as a data-quality warning, not as a car behaviour.

### 3.12 `RegulationEra`

```json
{ "era": "2026", "historical_drs_eligible": null, "historical_drs_open": null, "overtake_eligible": true, "overtake_state": "ARMED" }
```

For 2022–2025 rows `historical_drs_*` are populated and `overtake_*` are `null`; for 2026 the reverse. `overtake_*` come only from the rule engine (§12, §20), never from the raw DRS channel. Raw `drs`, historical DRS fields, and `PROXY_HISTORICAL_DRS` are not accepted as 2026 request inputs; a route rejects them as a feature-schema mismatch rather than inferring a default Overtake state.

---

## 4. Resource map

What the UI shows → which route → which model.

| UI element | Route | Model / contract |
|---|---|---|
| Track map, segment boundaries, Detection/Activation lines | `GET /track/{event}` | M03 (C1), M18 |
| Rule panel (thresholds, envelopes, sources) | `GET /rules/{event}` | M18, M20 |
| Battle picker | `GET /battles` | M05 (C8) |
| Battle playback timeline (gap, speeds, context, everything per segment) | `GET /battles/{battle_id}/timeline` | M02, M06, C3, C4, C5, C10 |
| Energy gauge with band, tagged SIMULATED | inside timeline; `POST /twin/energy_state` | M14, M17 (C5) |
| Rival state bars | inside timeline; `POST /rival/state` | M09 (C10) |
| Eligibility indicator, margin, P(eligible) | inside timeline; `POST /rules/eligibility` | M20, M21 (C3) |
| Pass probability at an opportunity, with spread | inside timeline; `POST /pass/predict` | M10, M11 calibration, M12 spread (C4) |
| Residual vs driver / team / field baseline ("HAM is 0.08 s quicker than his own median here") | inside timeline | M04 (C2) |
| Opportunity-derived strategic features (eligibility margin, energy to unlock, distances) | inside timeline `eligibility` block | M21 (C6) |
| Shadow-price heat strip along the lap (headline visual) | `GET /value/{event}/shadow_price` | M22 |
| Recommended deploy plan, P(ahead), CVaR | `POST /plan` | M24, M22, M23 |
| Planner vs baselines comparison | `POST /plan` (`include_baselines`) | M25 |
| Legal action chips at a state | `POST /rules/legal_actions` | M19 (C3) |
| Simulated episodes playback | `POST /simulate` | M26, M27 |
| Rival policy picker | `GET /simulate/policies` | M27 |
| Race-control / pit / eligibility-gate badge (greys every model panel when false) | inside timeline `race_control` | M02 (C7) |
| Fuel-load estimate gauge, tagged INFERRED | inside timeline; `POST /twin/energy_state` | M34 (C5) |
| Power-envelope curve overlaid on the speed trace (cap vs actual, both modes) | `GET /rules/{event}/power_envelope` | M18, M19 |
| ERS store / remaining-allowance gauges in MJ, tagged SIMULATED | inside timeline `ers`; `POST /twin/energy_state` | M14 (C5) |
| Live cap and headroom readout (cap moves with speed) | inside timeline `ers.cap_kw` | M19 (C3) |
| Override-detected badge on the rival, with probability | inside timeline `ers.override_active_inferred` | M35 (C5) |
| ERS deployment / harvest estimate strip, tagged SIMULATED | inside timeline; `POST /twin/energy_state` | M14 (C5) |
| Decision-checkpoint chips on the pass panel (DETECTION / ACTIVATION / BRAKING) | inside timeline `pass` | M07, M10 (C4, C6) |
| Wind head / cross arrows on the map, corner type overlay | `GET /track/{event}` + timeline `weather` | M33, M03 (C1) |
| Regulation-era badge (DRS-era vs 2026 Overtake) | inside timeline `era` | M07, M13 |
| Model card / metrics panel | `GET /validation` | §55 reports |
| Version footer | `GET /meta` | §3.6 |

---

## 5. Routes

All routes are prefixed `/api/v1`. Every response body includes a top-level `versions` object (§3.6) — omitted below for brevity.

### 5.1 `GET /meta`

Returns `Versions` plus `mode: "replay" | "service"` and `stubs: [route, ...]` listing any routes currently served by stubs.

### 5.2 `GET /track/{event}`

Geometry for drawing the map. Source: M03 segments (C1) plus M18 lines.

```json
{
  "event": "british_grand_prix", "track": "silverstone", "lap_length_m": 5891.0,
  "centreline": [ { "distance_m": 0.0, "x_m": 0.0, "y_m": 0.0 }, "..." ],
  "segments": [
    {
      "segment_id": 22, "start_distance_m": 3080.0, "end_distance_m": 3260.0,
      "segment_length_m": 180.0, "kind": "BRAKING",
      "sector": 2, "zone": 1, "corner_id": 15, "corner_type": "MEDIUM_RIGHT", "corner_phase": "ENTRY",
      "track_heading_deg": 214.0,
      "brake_onset_m": 3210.0, "mean_gradient": -0.004,
      "geometry_version": "silverstone-geom-v1",
      "provenance": "DERIVED"
    }
  ],
  "lines": [
    { "kind": "DETECTION",  "zone": 1, "distance_m": 2890.0, "provenance": "RULE", "value_source": "DERIVED_TELEMETRY", "source": "FIA Event Notes 2026 BGP §4.2, OVERTAKE DETECTION 115 m after T18; T18 measured from geometry.corner_distances_m" },
    { "kind": "ACTIVATION", "zone": 1, "distance_m": 3020.0, "provenance": "RULE", "value_source": "DERIVED_TELEMETRY", "source": "FIA Event Notes 2026 BGP §4.2, OVERTAKE ACTIVATION 65 m after T18; T18 measured from geometry.corner_distances_m" }
  ],
  "zones": [ { "zone": 1, "name": "Hangar Straight", "start_distance_m": 3020.0, "end_distance_m": 3900.0 } ]
}
```

Each line carries both tags of §3.1a: `provenance: "RULE"` because a Detection
Line is a regulatory landmark, and `value_source` for the evidence that fixed
its distance — a landmark quoted in metres from a numbered corner is
`DERIVED_TELEMETRY`, because the corner's distance was measured. The service
currently writes the `value_source` into `provenance` and omits the field,
which is the drift §3.1a forbids.

`centreline` is derived geometry (§12); raw `x`/`y` are never used directly across circuits. `kind` ∈ `STRAIGHT | BRAKING | CORNER | EXIT`. `corner_type` is the versioned geometry classification from §12 (hairpin / chicane / slow / medium / fast, with left / right / straight), never a free-text label; `geometry_version` changes whenever the classification or segment boundaries change, and every downstream artifact records it.

### 5.3 `GET /rules/{event}`

The rule configuration as loaded by M18, with sources, for the rule panel.

```json
{
  "event": "british_grand_prix", "year": 2026, "config_version": "rules-2026-bgp-r3",
  "regulation_snapshot": { "section_issues": ["..."], "effective_for_event": "...", "source_documents": ["..."], "retrieved_at": "..." },
  "overtake": {
    "enabled": true,
    "detection_gap_s": { "value": 1.0, "provenance": "RULE", "unit": "s", "value_source": "RULE_FIA", "source": "FIA Sporting Regulations 2026 Art. 22.7" },
    "zones": [ { "zone": 1, "detection_line_m": 2890.0, "activation_line_m": 3020.0 } ]
  },
  "power_envelope": {
    "normal":   { "breakpoints_kmh": [0, 290, 340], "max_power_kw": [350, 350, 0], "source": "...", "verified": false },
    "override": { "breakpoints_kmh": [0, 337, 355], "max_power_kw": [350, 350, 0], "source": "...", "verified": false },
    "separation_speed_kmh": 290.0,
    "competition_adjustments": [],
    "provenance": "RULE"
  },
  "energy": {
    "deploy_limit_per_lap_mj":  { "value": 4.0, "provenance": "RULE", "unit": "MJ", "source": "...", "verified": false },
    "harvest_limit_per_lap_mj": { "value": 2.0, "provenance": "RULE", "unit": "MJ", "source": "...", "verified": false },
    "store_capacity_mj":        { "value": 4.0, "provenance": "RULE", "unit": "MJ", "source": "...", "verified": false },
    "accounting_window": "lap"
  },
  "race_control": { "overtake_disabled": false },
  "unverified_keys": ["power_envelope.normal", "power_envelope.override", "energy.deploy_limit_per_lap_mj", "energy.harvest_limit_per_lap_mj", "energy.store_capacity_mj"]
}
```

Numbers above are placeholders; real values come from the YAML with their sources. The UI must render the regulation snapshot, configuration version, and source for a selected rule on hover or in the panel (§21).

Every rule value block carries `value_source` and `source` (§3.1a) beside its
`provenance`, and they answer different questions: `provenance` is `RULE` for
all of them, `value_source` is what the number rests on. `verified` is exactly
`value_source ∈ {RULE_FIA, OBSERVED_RCM, OBSERVED, DERIVED_TELEMETRY}`.

**Power is not a single number** (§20.1). `power_envelope` is a pair of piecewise-linear curves, and the UI should draw them rather than print a peak figure — the shape is the point. `unverified_keys` lists every key whose `verified` is false; each must be badged, and the page must not claim the system is legal by construction while that list is non-empty (§57).

Energy budgets are in **MJ**, matching how the regulations state them. Per-segment deltas elsewhere in this API remain in kJ; the unit is always in the field name.

### 5.3a `GET /rules/{event}/power_envelope`

The envelope alone, sampled for plotting, so the UI does not re-implement interpolation.

Query: `mode` (`normal|override|both`, default `both`), `step_kmh` (default 5).

```json
{
  "event": "british_grand_prix",
  "separation_speed_kmh": 290.0,
  "curves": {
    "normal":   [ { "speed_kmh": 0.0, "max_power_kw": 350.0 }, { "speed_kmh": 5.0, "max_power_kw": 350.0 }, "..." ],
    "override": [ { "speed_kmh": 0.0, "max_power_kw": 350.0 }, "..." ]
  },
  "breakpoints": { "normal": [0, 290, 340], "override": [0, 337, 355] },
  "provenance": "RULE", "verified": false,
  "model_version": "rules-2026-bgp-r3"
}
```

Sampled values come from the same `max_electrical_power_kw` function the DP and simulator use (C3) — there is exactly one implementation of this curve in the system, so the plotted line is the line the optimiser actually saw.

### 5.4 `GET /battles`

Query: `year`, `event`, `session` (all optional). Source: M05 battle episodes (C8).

```json
{
  "battles": [
    {
      "battle_id": "2026_GBR_Race_HAM_ANT_Battle03",
      "year": 2026, "event": "british_grand_prix", "session": "Race",
      "attacker": "HAM", "defender": "ANT",
      "attacker_team": "Ferrari", "defender_team": "Mercedes",
      "start_lap": 29, "end_lap": 33,
      "duration_segments": 148, "duration_s": 312.4,
      "minimum_distance_gap_m": 61.5, "minimum_time_gap_s": null,
      "maximum_closing_rate_mps": 3.2,
      "detection_opportunities": 4, "pass_attempted": true, "pass_completed": true,
      "bounded_by": "PASS",
      "normal_race_only": true,
      "provenance": "DERIVED"
    }
  ]
}
```

`minimum_distance_gap_m` and `minimum_time_gap_s` are distinct quantities:
the time field is `null` unless an observed/derived C1 time-gap source exists;
the builder never converts a distance using speed. `bounded_by` ∈ `PASS |
PAIR_SWITCH | RACE_CONTROL_TRANSITION | PIT_TRANSITION | SESSION_END` says why
the episode ended (§12: a race-control or pit transition is a hard boundary; no
battle continues across it). `normal_race_only` is always true for battles in
the model set; SC/VSC battles exist in the lake for audit but are not listed here.

### 5.5 `GET /battles/{battle_id}/timeline`

**The main playback payload.** One entry per battle segment, joining every model's output at that segment. This is what the demo scrubs through.

Query: `include_draws=false` (set true to include uncertainty draws).

```json
{
  "battle_id": "2026_GBR_Race_HAM_ANT_Battle03",
  "attacker": "HAM", "defender": "ANT",
  "steps": [
    {
      "ref": { "year": 2026, "event": "british_grand_prix", "session": "Race", "lap": 31, "segment_id": 22, "distance_m": 3140.0, "lap_fraction": 0.53 },
      "session_time_s": 4412.8,

      "race_context": { "label": "ATTACKING", "provenance": "INFERRED" },
      "race_control": {
        "pit_state": "ON_TRACK", "race_control_state": "GREEN",
        "safety_car_active": false, "virtual_safety_car_active": false,
        "race_control_transition_flag": false, "pit_transition_flag": false,
        "green_flag_elapsed_s": 218.4,
        "normal_race_model_eligible": true, "provenance": "DERIVED"
      },
      "era": { "era": "2026", "historical_drs_eligible": null, "historical_drs_open": null, "overtake_eligible": true, "overtake_state": "NOT_ARMED" },
      "geometry": { "sector": 2, "zone": 1, "corner_id": 15, "corner_type": "MEDIUM_RIGHT", "corner_phase": "ENTRY", "track_heading_deg": 214.0 },
      "weather": {
        "wind_head_component_mps":  { "value": -2.1, "provenance": "DERIVED", "unit": "m/s" },
        "wind_cross_component_mps": { "value":  3.4, "provenance": "DERIVED", "unit": "m/s" },
        "air_density_proxy":        { "value": 1.19, "provenance": "DERIVED", "unit": "kg/m3" },
        "track_temperature":        { "value": 41.0, "provenance": "OBSERVED", "unit": "C" },
        "wet_track_flag": false
      },

      "time_gap_s":          { "value": 0.78,  "provenance": "DERIVED", "unit": "s" },
      "distance_gap_m":      { "value": 61.5,  "provenance": "DERIVED", "unit": "m" },
      "closing_rate_mps":   { "value": 1.9,   "provenance": "DERIVED", "unit": "m/s" },
      "relative_speed_to_ahead_mps": { "value": 1.7, "provenance": "DERIVED", "unit": "m/s" },
      "relative_acceleration_to_ahead_mps2": { "value": 0.12, "provenance": "DERIVED", "unit": "m/s2" },
      "gap_rate_ahead_s_per_s":      { "value": -0.06, "provenance": "DERIVED", "unit": "s/s" },
      "delta_speed_kmh":    { "value": 6.2,   "provenance": "DERIVED", "unit": "km/h" },
      "delta_segment_time_s": { "value": -0.11, "provenance": "DERIVED", "unit": "s" },
      "tyre_age_delta_laps": { "value": -4,   "provenance": "OBSERVED", "unit": "laps" },
      "compound_pair": "MEDIUM_HARD",

      "baseline_residuals": {
        "attacker": {
          "segment_time_vs_driver_s": { "value": -0.08, "provenance": "DERIVED", "unit": "s", "n": 41 },
          "segment_time_vs_team_s":   { "value": -0.05, "provenance": "DERIVED", "unit": "s", "n": 88 },
          "segment_time_vs_field_s":  { "value": -0.14, "provenance": "DERIVED", "unit": "s", "n": 812 },
          "exit_speed_vs_driver_kmh": { "value": 2.1,   "provenance": "DERIVED", "unit": "km/h", "n": 41 }
        },
        "defender": { "...same keys": "..." },
        "baseline_valid": true
      },

      "attacker": {
        "speed_kmh": { "value": 287.0, "provenance": "OBSERVED", "unit": "km/h" },
        "throttle_pct": { "value": 100.0, "provenance": "OBSERVED", "unit": "%" },
        "brake_on": false,
        "braking_intensity_proxy": { "value": 0.0, "provenance": "DERIVED" },
        "tyre": { "compound": "MEDIUM", "life_laps": 12, "stint": 2, "degradation_proxy": { "value": 0.31, "provenance": "DERIVED" } },
        "ers": {
          "ers_soc_est_mj":           { "mean": 3.42, "low": 3.05, "high": 3.80, "provenance": "SIMULATED", "unit": "MJ" },
          "ers_deploy_budget_remaining_est_mj":  { "mean": 1.18, "low": 0.90, "high": 1.46, "provenance": "SIMULATED", "unit": "MJ" },
          "ers_deploy_power_est_kw":  { "mean": 210.0, "low": 150.0, "high": 260.0, "provenance": "SIMULATED", "unit": "kW" },
          "ers_harvest_power_est_kw": { "mean": 0.0, "low": 0.0, "high": 20.0, "provenance": "SIMULATED", "unit": "kW" },
          "cap_kw":                   { "value": 312.0, "provenance": "RULE", "unit": "kW" },
          "applicable_mode": "NORMAL", "headroom_kw": { "value": 102.0, "provenance": "DERIVED", "unit": "kW" },
          "ers_mode_inferred": "NORMAL", "override_active_inferred": { "value": 0.08, "provenance": "INFERRED" },
          "discriminable": true, "envelope_violation": false
        },
        "fuel_kg":          { "mean": 48.2,   "low": 45.0,  "high": 51.5,  "provenance": "INFERRED",  "unit": "kg" }
      },
      "defender": {
        "speed_kmh": { "value": 281.0, "provenance": "OBSERVED", "unit": "km/h" },
        "throttle_pct": { "value": 100.0, "provenance": "OBSERVED", "unit": "%" },
        "brake_on": false,
        "braking_intensity_proxy": { "value": 0.0, "provenance": "DERIVED" },
        "tyre": { "compound": "HARD", "life_laps": 16, "stint": 2, "degradation_proxy": { "value": 0.27, "provenance": "DERIVED" } },
        "ers": {
          "ers_soc_est_mj":           { "mean": 2.61, "low": 2.20, "high": 3.02, "provenance": "SIMULATED", "unit": "MJ" },
          "ers_deploy_budget_remaining_est_mj":  { "mean": 0.09, "low": 0.02, "high": 0.20, "provenance": "SIMULATED", "unit": "MJ" },
          "ers_deploy_power_est_kw":  { "mean": 318.0, "low": 270.0, "high": 360.0, "provenance": "SIMULATED", "unit": "kW" },
          "ers_harvest_power_est_kw": { "mean": 0.0, "low": 0.0, "high": 20.0, "provenance": "SIMULATED", "unit": "kW" },
          "cap_kw":                   { "value": 312.0, "provenance": "RULE", "unit": "kW" },
          "applicable_mode": "NORMAL", "headroom_kw": { "value": -6.0, "provenance": "DERIVED", "unit": "kW" },
          "ers_mode_inferred": "OVERRIDE", "override_active_inferred": { "value": 0.71, "provenance": "INFERRED" },
          "discriminable": true, "envelope_violation": false
        },
        "fuel_kg":          { "mean": 47.6,   "low": 44.4,  "high": 50.9,  "provenance": "INFERRED",  "unit": "kg" }
      },

      "rival_state": {
        "p": { "CONSERVING": 0.12, "BALANCED": 0.31, "DEPLOYING": 0.49, "DERATING": 0.08 },
        "merged": [], "provenance": "INFERRED", "model_version": "rival-hsmm-2026.02"
      },

      "eligibility": {
        "zone": 1, "armed": false,
        "p_eligible": { "value": 0.64, "provenance": "INFERRED" },
        "eligibility_margin_s": { "value": 0.22, "provenance": "DERIVED", "unit": "s" },
        "projected_gap_at_detection_s": { "mean": 0.78, "low": 0.61, "high": 0.97, "provenance": "INFERRED", "unit": "s" },
        "energy_required_to_unlock_kj": { "mean": 210.0, "low": 165.0, "high": 275.0, "provenance": "SIMULATED", "unit": "kJ" },
        "eligibility_fragility_per_kj": { "value": 0.0018, "provenance": "SIMULATED", "unit": "1/kJ" }
      },

      "pass": {
        "is_opportunity": true,
        "opportunity_id": "2026_GBR_Race_HAM_ANT_Battle03_L31_Z1",
        "outcome_horizon": "zone_exit_v1",
        "checkpoints": {
          "DETECTION":  { "reached": true,  "feature_cutoff_distance_m": 2890.0, "p_pass_by_outcome_horizon": 0.51, "ensemble_spread": 0.08, "model_version": "pass-det-2026.03" },
          "ACTIVATION": { "reached": true,  "feature_cutoff_distance_m": 3020.0, "p_pass_by_outcome_horizon": 0.58, "ensemble_spread": 0.07, "model_version": "pass-act-2026.03" },
          "BRAKING":    { "reached": false, "feature_cutoff_distance_m": 3210.0, "p_pass_by_outcome_horizon": null,  "ensemble_spread": null, "model_version": "pass-brk-2026.03" }
        },
        "provenance": "INFERRED"
      },

      "shadow_price_s_per_kj": { "value": 0.0031, "provenance": "DERIVED", "unit": "s/kJ" },
      "recommended_action": { "deploy_level": 0.5, "lift_amount": 0.0, "label": "HOLD_FOR_DETECTION" },
      "legal_actions": [ { "deploy_level": 0.0, "lift_amount": 0.0 }, { "deploy_level": 0.25, "lift_amount": 0.0 }, "..." ],

      "live_safe": true
    }
  ]
}
```

Notes for the UI:

- `baseline_residuals` comes from C2 (M04). `n` is the sample count behind the median; when `baseline_valid` is false the residuals are `null` and the UI greys the panel rather than hiding it.
- The `eligibility` block carries the C6 opportunity-derived features (M21): `eligibility_margin_s`, `projected_gap_at_detection_s`, `energy_required_to_unlock_kj`.
- `race_control` is the C7 gate (M02). When `normal_race_model_eligible` is false — SC, VSC, yellow/red restriction, any pit state, or unknown control state — every model block (`baseline_residuals`, `rival_state`, `eligibility`, `pass`, `shadow_price_s_per_kj`, `recommended_action`) is `null` with a `reason`, because those models are not defined there (§12). The UI shows the gate, not a number. `race_control_transition_flag` / `pit_transition_flag` mark hard boundaries: no rolling quantity is computed across them.
- `era` distinguishes DRS-era rows (2022–2025, `historical_drs_*`) from 2026 rows (`overtake_*`, from the rule engine only). Never present a historical DRS value as an Overtake state (§41, §58).
- `weather` is track-relative (M33): head/cross components are wind projected onto `geometry.track_heading_deg`. Raw wind direction is not in the payload because it is not comparable between segments.
- `fuel_kg` is `INFERRED` from a causal estimator (M34); every `ers.*_est_*` field is `SIMULATED` (M14). All carry uncertainty and are never labelled observed (§11).
- `ers.cap_kw` is the regulatory maximum **at this step's speed** under `applicable_mode` (§20.1) — it is not a constant, and it changes down a straight even when nothing else does. `headroom_kw` is the distance to that cap; near-zero or negative headroom is the interesting state to surface.
- `ers.override_active_inferred` (M35) is an inference from comparing estimated power against the normal-mode cap (§20.2), not an observation. In the defender block above, the estimate sits above the normal cap, which is what drives `ers_mode_inferred: "OVERRIDE"`. Show it as a probability with an "inferred" badge; never state that a rival *is* in override.
- `ers.discriminable: false` (below the envelope-separation speed) forces `ers_mode_inferred: "UNKNOWN"` and a `null` probability. Render "unknown" — not "normal" (§20.2).
- `ers.envelope_violation: true` is a **twin calibration warning**, not a regulatory breach by the car (§28.1). It belongs in a data-quality indicator, never in a "car exceeded the limit" message.
- `pass.is_opportunity` is true only on segments that are rows in the overtake-opportunity dataset (M07). Elsewhere the whole `pass` block is `null`. When true, `checkpoints` holds one entry per decision checkpoint (§19); a checkpoint not yet reached at this step has `reached: false` and `null` probability. A DETECTION probability was computed from Detection-Line information only — it does not change when later checkpoints are reached; the UI shows all three side by side as they become available. `p_pass_by_outcome_horizon` is a bare float **here** because `pass.provenance` tags the whole block and each checkpoint names its own `model_version` beside the number; 5.8 returns the same quantity on its own, where it must carry its own tag and is a `Quantity`. A client reading both routes handles both spellings.
- `rival_state.merged` lists any states merged per §25 (e.g. `["CONSERVING","DERATING"]`). If non-empty, `p` has fewer keys.
- `live_safe` is true when every value in the step was computable from information available at that timestamp (§44). A retrospective step (e.g. one using the known outcome for labelling) is `false` and must be visually distinguished.
- `energy_kj` is always `SIMULATED`. Label it "estimated electrical energy", never "battery" or "SOC" (§58).

### 5.6 `POST /rules/legal_actions`

C3. Which actions are legally available in a state.

Request:

```json
{ "state": { "ref": { "...StateRef" }, "energy": { "energy_kj": 1420.0, "deployed_kj": 0.0, "harvested_kj": 0.0, "recharge_budget_remaining": "..." }, "tyre": { "performance_state": "..." }, "gap": { "time_gap_s": 0.78, "distance_gap_m": 61.5, "relative_speed_mps": 1.7, "relative_acceleration_mps2": 0.12, "gap_rate_s_per_s": -0.06 }, "overtake_state": "NOT_ARMED", "race_control": { "overtake_disabled": false }, "power_envelope": { "regime": "NORMAL" } } }
```

Response:

```json
{ "actions": [ { "deploy_level": 0.0, "lift_amount": 0.0 }, { "deploy_level": 0.25, "lift_amount": 0.0 }, "..." ],
  "excluded": [ { "action": { "deploy_level": 1.0, "lift_amount": 0.0 }, "rule": "energy.deploy_limit_per_lap_kj", "source": "..." } ],
  "provenance": "RULE" }
```

`excluded` explains why each illegal action was removed, for the UI to show on hover. `eligibility` ∈ `NOT_ARMED | ARMED | ACTIVE | DISABLED`.

### 5.7 `POST /rules/eligibility`

C3. Evaluate the Overtake state machine at a state.

Request: the same `StrategicState` as 5.6 plus optional causal `projected_gap_s: Uncertain` and a declared short-horizon action sequence when requesting `energy_required_to_unlock`.

Response: the `eligibility` object from the timeline step (5.5).

### 5.8 `POST /pass/predict`

C4. Pass probability at an opportunity, **at one decision checkpoint**.

Request:

```json
{ "decision_checkpoint": "DETECTION", "feature_schema_id": "m10_pass_features_v2",
  "features": { "gap_at_checkpoint_s": 0.71, "closing_rate_s_per_s": 0.04, "p_eligible": 0.64,
                "attacker_tyre_life_laps": 12, "defender_tyre_life_laps": 16, "tyre_life_delta_laps": -4,
                "wind_head_component_mps": -2.1, "wind_cross_component_mps": 3.4, "track_temperature_c": 41.0,
                "attacker_tyre_compound": "MEDIUM", "defender_tyre_compound": "HARD",
                "tyre_compound_pair": "MEDIUM|HARD", "corner_type": "MEDIUM_RIGHT", "sector": 2,
                "wet_track_flag": false } }
```

Features are named, not positional; the server orders them against the locked schema **for that checkpoint** and rejects mismatches (§53). All must be `LIVE_SAFE` and must exist at or before the named checkpoint (§19): a `DETECTION` request carrying `gap_at_activation_s`, any braking-point speed, or a centred rolling statistic is refused with `CHECKPOINT_VIOLATION`. A `null` is not a violation — only a *value* is a claim. The state must be `normal_race_model_eligible`, else `NOT_MODEL_ELIGIBLE`.

**The locked schema is the vocabulary.** The feature names below are the ones
fitted into the artifact (`artifacts/models/pass/v2/<checkpoint>/<family>/feature_schema.json`,
`schema_version: m10_pass_features_v2`), not the UI's. A name the schema does
not hold is accepted, ignored, and absent from `features_supplied` — so a
request written to any other vocabulary is scored on zero features and returns
the model's unconditional prior, which looks exactly like a prediction and moves
for no input. Fifteen features are shared by all three checkpoints:

| Wire name | Unit | Schema name | Notes |
|---|---|---|---|
| `gap_at_checkpoint_s` | s | `gap_at_checkpoint` | attacker-to-defender time gap at this checkpoint |
| `closing_rate_s_per_s` | s/s | `closing_rate_s_per_s` | **positive while closing** — the opposite sign to 5.5's `gap_rate_ahead_s_per_s`. The two are never aliased |
| `p_eligible` | probability | `p_eligible` | from 5.7, not an `ARMED`/`NOT_ARMED` toggle mapped to 1.0/0.0 |
| `attacker_tyre_life_laps` | laps | `attacker_tyre_life_laps` | |
| `defender_tyre_life_laps` | laps | `defender_tyre_life_laps` | |
| `tyre_life_delta_laps` | laps | `tyre_life_delta_laps` | attacker − defender |
| `wind_head_component_mps` | m/s | `wind_head_component_mps` | track-relative (§12, §39) |
| `wind_cross_component_mps` | m/s | `wind_cross_component_mps` | track-relative |
| `track_temperature_c` | °C | `track_temperature` | |
| `attacker_tyre_compound` | enum | `attacker_tyre_compound` | `SOFT` / `MEDIUM` / `HARD` |
| `defender_tyre_compound` | enum | `defender_tyre_compound` | |
| `tyre_compound_pair` | enum | `tyre_compound_pair` | e.g. `"MEDIUM\|HARD"` |
| `corner_type` | enum | `corner_type` | the §12 classification, e.g. `MEDIUM_RIGHT` |
| `sector` | enum | `sector` | `null` in C1 for every 2026 row today; send it or omit it, it carries no signal yet |
| `wet_track_flag` | bool | `wet_track_flag` | |

Plus, by checkpoint:

| Checkpoint | Extra | Total |
|---|---|---|
| `DETECTION` | — | 15 |
| `ACTIVATION` | `speed_at_activation_kmh` (km/h) | 16 |
| `BRAKING` | `speed_at_activation_kmh`, `speed_at_braking_kmh` (km/h) | 17 |

Where the two columns differ, the schema spelling drops the unit suffix that
principle 3 requires on the wire. The wire keeps the suffix and the server maps it: the
authoritative map is `FEATURE_ALIASES` in `src/trackshift/serve/pass_service.py`
(`gap_s`, `time_gap_s`, `gap_at_checkpoint_s` → `gap_at_checkpoint`;
`track_temperature_c` → `track_temperature`; `speed_at_*_kph` →
`speed_at_*_kmh`). Schema spellings are accepted directly as well. **The aliases
are renames only and never convert a unit** — a field whose unit differs from the
schema's is deliberately left unmapped so that it surfaces in `features_missing`
rather than being silently rescaled.

Both request shapes are accepted: the `features` envelope above, and the same
names at the top level of the body (INTEGRATION.md §3). On a clash the envelope
wins, being the more specific statement of intent. `decision_checkpoint` is the
contract name for the checkpoint; `checkpoint` is accepted as a legacy alias.
`feature_schema_id` is optional and is not yet verified by the service, so a
wrong id does not raise `FEATURE_SCHEMA_MISMATCH` today.

**Partial payloads are allowed.** A missing feature stays missing — the trees
handle NaN natively — and comes back under `features_missing`. Wire the fields
you have and add the rest incrementally, but show `features_missing`: a
probability built from 3 of 15 features is a weaker claim than one built from
15, and the number alone does not say which it is.

Names from earlier editions of this section — `delta_speed_checkpoint_kmh`,
`delta_acceleration_checkpoint_mps2`, `closing_rate_mps`, `closing_rate_trend`,
`recent_pace_delta_s`, `attacker_tyre_life`, `defender_tyre_life`,
`*_tyre_degradation_proxy`, `fuel_load_delta_kg_est`, `ers_energy_delta_kj_est`,
`eligibility_margin_s`, `overtake_eligible`, `overtake_state`, `corner_phase`,
`regulation_era` — are **not** features of any checkpoint. Several are excluded
by the schema on purpose and say so in its `excluded` block (`eligibility_margin`,
`gap_at_activation_s` and the `distance_*` fields are "rule/display/audit
metadata; never a trainable feature"). They remain valid display quantities in
5.5; they are not request inputs here.

Response:

```json
{ "decision_checkpoint": "DETECTION", "checkpoint": "DETECTION",
  "p_pass_by_outcome_horizon": { "value": 0.1632, "unit": null, "provenance": "INFERRED",
                                 "model": "M10/lightgbm", "artifact_version": "v2/detection/lightgbm" },
  "outcome_horizon": "zone_exit_v1",
  "ensemble_spread": { "value": null, "provenance": "INFERRED",
                       "reason": "M12 spread not wired; artifacts/models/pass/v1/ensemble.json exists and is not read" },
  "calibration": "uncalibrated", "era": "2026",
  "features_supplied": ["gap_at_checkpoint", "track_temperature"],
  "features_missing": ["p_eligible", "closing_rate_s_per_s", "..."],
  "evidence_grade": "INTERIM",
  "is_stub": false,
  "empirical": {
    "p_pass_by_outcome_horizon": { "value": 0.076023, "provenance": "DERIVED", "model": "empirical-rate-table" },
    "interval": { "low": 0.060093, "high": 0.095747, "method": "wilson", "level": 0.95 },
    "support": { "n": 855, "passes": 65, "gap_bucket_s": [0.5, 1.0] }
  } }
```

`p_pass_by_outcome_horizon` is a `Quantity` (§3.2), not a bare float. Principle 2
requires the provenance to travel with the number, and this one is returned on
its own with no enclosing block to carry a tag — so the tag, the model and the
artifact that produced it ride inside it. The same quantity appears in 5.5 under
`pass.checkpoints.<CHECKPOINT>` as a bare float; there it is covered by the
block-level `pass.provenance` and the per-checkpoint `model_version` beside it,
so both spellings are correct in their own place and a client reading both
routes must handle both. The artifact identity inside the `Quantity` is what
satisfies principle 7 here: it names the exact build
(`v2/detection/lightgbm`), which a single `model_version` string did not.

`outcome_horizon` names the fixed, versioned definition of the training label `passed_by_outcome_horizon` (§19: e.g. completion before zone exit, or before the next Detection Line). The label itself is never returned by a live route and never accepted as a feature.

`calibration` names the M11 method that produced the probability and is exactly
`uncalibrated | platt | isotonic` — the `METHODS` tuple in
`src/trackshift/pass_model/calibration.py`. `uncalibrated` means no calibrator
was applied and the number is the raw model score: nothing downstream may
describe it as calibrated. A counted-frequency answer is `uncalibrated` too —
there is no calibrator to name — and says who answered through `is_stub` and the
`empirical` block instead.

`features_supplied` / `features_missing` name, per the locked schema, what the
prediction was actually built from. `evidence_grade` is the artifact's own grade
from `manifest.run.evidence_grade`: every pass artifact is `INTERIM`, not `FULL`,
because the opportunity table holds 2026 only and CP-14 could not run its
documented 2022–24 / 2025 / 2026 split. The numbers are real and the split is
leakage-safe; what they are not is a passed acceptance gate, and the UI shows the
grade wherever it shows the probability. `is_stub: true` means the synthetic
curve answered and the number is not a model output (§3.7).

`empirical` is a **different quantity from the headline**: the counted 2026 pass
rate in the request's gap bucket, with its Wilson interval and support. Its
`interval` is the interval of that counted rate, never of the model's
probability — rendering the model's `value` inside the rate table's band
attaches a `DERIVED` uncertainty to an `INFERRED` number, which is the
provenance failure principle 2 exists to prevent. Show them as two rows, each
with its own tag, or show only the headline.

*The service does not yet match on three points.* It omits `era` and
`ensemble_spread` entirely rather than returning the latter as an absent
quantity; and it spells `calibration` three ways — `uncalibrated` on the artifact
branch, `"none"` on the counted-rate branch and `"synthetic-development"` on the
stub branch. The last two are outside the enum above: both are `uncalibrated`,
and which component answered is already carried by `empirical` and `is_stub`.

### 5.9 `POST /rival/state`

C10. Rival state distribution from a window of battle segments.

Request: `{ "battle_id": "...", "up_to_segment_index": 87 }` **or** `{ "segments": [ { "...rival-state feature row" }, "..." ] }`.

Response: the `rival_state` object from 5.5.

### 5.10 `POST /twin/segment_time`

C5. ΔE → Δt for a segment and action.

Request: `{ "state": {"...StrategicState"}, "action": {"...Action"}, "context": { "entry_speed_kmh": 287.0, "tyre_compound": "MEDIUM", "tyre_life_laps": 12, "tyre_performance_state": "...", "fuel_proxy_lap": 31, "aero_state": "NORMAL", "applicable_mode": "NORMAL" } }`

Response:

```json
{ "t_s": { "mean": 9.42, "low": 9.31, "high": 9.55, "provenance": "SIMULATED", "unit": "s" },
  "delta_e_kj": { "mean": 185.0, "low": 160.0, "high": 205.0, "provenance": "SIMULATED", "unit": "kJ" },
  "harvested_e_kj": { "mean": 0.0, "low": 0.0, "high": 12.0, "provenance": "SIMULATED", "unit": "kJ" },
  "projected_gap_delta_s": { "mean": -0.03, "low": -0.06, "high": -0.01, "provenance": "SIMULATED", "unit": "s" },
  "calibration_level": "event", "model_version": "twin-event-2026.01" }
```

`calibration_level` ∈ `analytical | global | team | event | residual` (§29) tells the UI which rung of the hierarchy answered.

### 5.11 `POST /twin/energy_state`

C5. Estimated ERS and fuel state from a causal telemetry window (M14, M34).

Request: `{ "battle_id": "...", "driver": "HAM", "up_to_segment_index": 87 }`

Response:

```json
{
  "energy_kj":         { "mean": 1420.0, "low": 1310.0, "high": 1535.0, "provenance": "SIMULATED", "unit": "kJ" },
  "ers_deployment_kw": { "mean": 210.0,  "low": 150.0,  "high": 260.0,  "provenance": "SIMULATED", "unit": "kW" },
  "ers_harvest_kw":    { "mean": 0.0,    "low": 0.0,    "high": 20.0,   "provenance": "SIMULATED", "unit": "kW" },
  "energy_deployed_kj": { "mean": 185.0, "low": 160.0, "high": 205.0, "provenance": "SIMULATED", "unit": "kJ" },
  "energy_harvested_kj": { "mean": 0.0, "low": 0.0, "high": 12.0, "provenance": "SIMULATED", "unit": "kJ" },
  "recharge_budget_remaining": { "value": null, "provenance": "RULE", "unit": "kJ", "reason": "not yet encoded for this configuration" },
  "power_envelope": { "regime": "NORMAL", "power_limit_kw": { "value": null, "provenance": "RULE", "unit": "kW", "reason": "speed-dependent event configuration required" } },
  "fuel_kg":           { "mean": 48.2,   "low": 45.0,   "high": 51.5,   "provenance": "INFERRED",  "unit": "kg" },
  "causal_cutoff_distance_m": 3140.0,
  "model_version": { "twin": "twin-event-2026.01", "fuel": "fuel-causal-2026.01" }
}
```

Uses only telemetry at or before `causal_cutoff_distance_m` (§12). These four are the only fuel/ERS quantities any live feature may consume; none is ever `OBSERVED` (§11, §58).

### 5.12 `GET /value/{event}/shadow_price`

M22. λ_E along the lap for a given energy, gap, and eligibility — **the headline visual**: the same kJ has different value at different places.

Query: `energy_kj`, `time_gap_s`, `eligibility` (`NOT_ARMED|ARMED`), optional `tyre_state`, `relative_speed_mps`, `gap_rate_s_per_s`, `rival_state`, and `lap_index` (0 or 1 within the two-lap horizon). Omitted state dimensions use the declared replay state or grid cell and are returned under `resolved_state`.

The profile comes straight from M22. **This is the response today**, and it is
the correct one until the dynamic programme is built (CP-10):

```json
{
  "event": "british_grand_prix", "energy_kj": 1420.0, "gap_s": 0.78, "eligibility": "NOT_ARMED",
  "status": "UNAVAILABLE",
  "reason": "M22's per-position dynamic programme (CP-10) is not built; the development DP returns one state-level marginal value, which is not a function of lap position",
  "profile": [],
  "spikes": null,
  "spikes_reason": "a spike is a feature of the profile; with no profile there is nothing to locate",
  "provenance": "DERIVED", "model_version": "m22_dp_development_v2",
  "grid": { "energy_kj": [0, 100, "...", 4000], "gap_s": [-3.0, -2.9, "...", 3.0] },
  "resolved_state": { "tyre_state": { "value": null, "reason": "the development DP does not condition on tyre state" }, "...": "..." }
}
```

An empty `profile` with a `reason` is the honest answer and the required one. A
single marginal value repeated once per segment is not a profile — it is flat by
construction, which is the exact opposite of the claim this visual makes — and
principle 5 forbids standing a pooled value in for a specific one.

When CP-10 lands, each entry arrives in this shape, `status` becomes
`"COMPLETE"` and `reason` becomes `null`:

```json
{ "segment_id": 21,
  "lambda_s_per_kj": { "value": 0.0087, "unit": "s/kJ", "provenance": "DERIVED", "note": "before DETECTION zone 1" } }
```

`lambda_s_per_kj` in **s/kJ** is the contract quantity, and it is the same
quantity 5.5 carries per step as `shadow_price_s_per_kj`. It is a `Quantity`
(§3.2), so an unreachable cell of the value table is `value: null` plus a
`reason` rather than a hole in the array or a zero.

`spikes` marks where λ_E jumps because of an upcoming Detection Line (§20).
`grid` tells the UI the valid ranges for sliders, in the same unit as the query
parameter — the DP grids in MJ internally (0–4.0 MJ in 0.1 MJ steps, gap −3.0 to
3.0 s in 0.1 s steps) and the route converts ×1000 so the slider cannot send
4.0 where 4000 was meant. `resolved_state` reports every query dimension that
was defaulted and to what, so an ignored dimension is visible instead of silent.

*The service does not yet match.* It emits a `profile` of one repeated scalar
under the key `lambda_utility_per_mj`, and no `spikes`, `grid`, `status` or
`resolved_state` at the top level. That key is a **different quantity**, not a
renamed one: the development DP's terminal utility is abstract, so the number is
dimensionless utility per MJ, and the response says so itself
(`marginal_value_units: "abstract_utility_per_mj"`, with
`time_based_shadow_price: {"value": null, "unit": "s/MJ", "reason": "not emitted:
development terminal utility is abstract and C4/C5 are not verified calibrated
time inputs"}`). It may be returned under its own name in the development
`shadow_price` block, labelled with its own unit; it must never be printed in a
seconds slot, and no client may treat it as `lambda_s_per_kj` scaled by 1000 —
the difference is a change of kind, not of prefix.

### 5.13 `POST /plan`

M24 planner. Given a state, return the recommended action sequence over the horizon.

Request:

```json
{ "state": {"...StrategicState"},
  "rival_state": { "p": { "CONSERVING": 0.12, "BALANCED": 0.31, "DEPLOYING": 0.49, "DERATING": 0.08 } },
  "horizon_laps": 2, "include_baselines": true, "risk": { "cvar_alpha": 0.2 } }
```

`rival_state` is optional; if omitted the server calls C10 itself.

Response:

```json
{
  "status": "COMPLETE", "reason": null, "provenance": "DERIVED",
  "recommended_action": { "deploy_level": 1.0, "lift_amount": 0.0, "label": "HOLD_FOR_DETECTION",
                          "applicable_mode": "normal", "cap_kw": 350.0, "delivered_power_kw": 350.0, "delta_e_mj": 0.63 },
  "expected_value": { "value": -3.936, "provenance": "DERIVED", "unit": "abstract_utility" },
  "plan": [],
  "plan_reason": "the development planner returns one action for the current state; the horizon rollout is not emitted (M24)",
  "p_ahead_at_horizon": { "mean": null, "low": null, "high": null, "provenance": "INFERRED", "reason": "not computed by the development planner (M24/M23)" },
  "p_pass_now": { "value": null, "provenance": "INFERRED", "reason": "not computed; call 5.8 for a checkpoint probability" },
  "p_repass_within_horizon": { "value": null, "provenance": "INFERRED", "reason": "not computed by the development planner (§23)" },
  "final_energy_mj": { "mean": null, "low": null, "high": null, "provenance": "SIMULATED", "unit": "MJ", "reason": "no horizon rollout, so no terminal energy" },
  "cvar_p_ahead": { "value": null, "provenance": "INFERRED", "reason": "the development DP optimises expected value only; CVaR is not computed" },
  "risk": { "criterion": "expected_value", "cvar_alpha": 0.2, "applied": false,
            "reason": "the development DP optimises expected value only" },
  "rule_violations": 0,
  "legal_actions": [ { "deploy_level": 0.0, "lift_amount": 0.0, "applicable_mode": "normal", "cap_kw": 350.0, "delivered_power_kw": 0.0 }, "..." ],
  "excluded_actions": [],
  "input_provenance": { "energy": "SIMULATED", "gap": "SIMULATED", "eligibility": "RULE" },
  "latency_ms": 38,
  "decision": { "dominant_mechanism": "ENERGY_POSITION_VALUE", "primary_constraint": "C3_LEGAL_ACTION_SET", "decision_stability": 1.0,
                "alternatives": [{ "action": { "deploy_level": 0.75, "lift_amount": 0.0 }, "expected_value": -3.95, "regret": 0.02 }] },
  "baselines": [
    { "name": "greedy_attack", "actions": [ { "deploy_level": 1.0, "lift_amount": 0.0 }, "..." ],
      "p_ahead_at_horizon": null, "final_energy_mj": null, "rule_violations": 0,
      "reason": "M25 does not score the baselines yet; the action sequence each would take is real" },
    "..."
  ],
  "rule_configuration_version": "rules-2026-common-v2-fia-iss08-iss20",
  "model_version": "m24_beam_development_v1"
}
```

`rule_violations` must always be `0` for `plan`; it is reported so the UI can assert it (§31). `baselines` names match §33 exactly — `greedy_attack`, `longest_straight`, `lap_time_only`, `dp`, `beam_dp`, `oracle_rival_state`. `oracle_rival_state` is an upper bound, not a deployable policy — label it so.

`status` is `COMPLETE` or `UNAVAILABLE`, and `reason` says which input was
missing when it is `UNAVAILABLE`. Every field above appears on **both** paths: a
refusal that simply omits the headline fields is indistinguishable from a
success the UI failed to read, and `UNAVAILABLE` is the branch a
contract-shaped request most often hits.

The uncomputed quantities are `null` with a `reason`, not absent (principle 5), so the
UI can render "unavailable, because —" rather than a blank. Three of them are
not permanently absent: `latency_ms` is measurable today in the serving layer
around the `plan(...)` call, `model_version` is known to the planner
(`PLANNER_SCHEMA_VERSION`), and `rule_configuration_version` is known to the
rule engine. None of those three may be emitted as `null`.

`expected_value`, `decision.alternatives[].expected_value` and `regret` are in
the development DP's **abstract utility**, not seconds and not a probability —
the same abstract terminal utility 5.12 describes. They are ordering
information: a comparison between actions is meaningful, the magnitude is not,
and neither may be rendered in a unit slot. The entries under `decision` are
bare floats because the block's own `provenance` and the unit named on
`expected_value` cover them, the way `pass.provenance` covers 5.5's checkpoint
ladder.

`final_energy_mj` is in **MJ**: it is a stored-energy quantity, and §11 and 5.3
put stored energy in MJ while per-segment deltas stay in kJ. It was
`final_energy_kj` in earlier editions; the quantity is unchanged and the
illustrative figures restate as 0.41 / 0.32 / 0.505 MJ.

*The service does not yet match on four points.* It omits `plan`,
`p_ahead_at_horizon`, `p_pass_now`, `p_repass_within_horizon`, `final_energy_mj`,
`cvar_p_ahead` and `latency_ms` rather than returning them null-with-reason; it
spells the version `model_versions` and returns it empty; it returns `baselines`
as an object keyed by baseline name holding action lists, which is not the
scored comparison §33 asks for; and it attaches the whole DP solve under `dp`
(policy table, value table, grid). That `dp` block is a development diagnostic,
not part of this contract, and no UI element may depend on it.

### 5.14 `POST /simulate`

M26, M27. Run seeded episodes from a state under an explicit rival policy.

Request:

```json
{ "state": {"...StrategicState"},
  "our_policy": "beam_dp", "rival_policy": "DEFEND_CONSERVE", "n_episodes": 200, "seed": 7, "horizon_laps": 2 }
```

`rival_policy` is one of the explicit policies defined in M27 (returned by `GET /simulate/policies`).

Response:

```json
{
  "status": "COMPLETE",
  "summary": { "p_ahead_at_horizon": 0.62, "mean_final_energy_mj": 0.405, "rule_violations": 0, "n_episodes": 200, "seed": 7 },
  "episodes": [
    { "episode": 0, "outcome": "AHEAD", "pass_lap": 31, "pass_segment_id": 27, "repassed": false, "environment_seed": 647892279,
      "trace": [ { "segment_id": 22, "gap_s": 0.78, "energy_mj": 1.42, "action": {"deploy_level": 0.5, "lift_amount": 0.0}, "provenance": "SIMULATED" }, "..." ] }
  ],
  "assumptions": [ "rival policy is a fixed explicit policy, not a learned agent", "energy is SIMULATED from public telemetry", "..." ],
  "provenance": "SIMULATED", "model_version": "m26_simulator_development_v1"
}
```

`assumptions` is mandatory and must be shown in the UI (§56: "Its assumptions must be disclosed"). `episodes[].trace` may be truncated to the first N episodes in replay bundles.

`mean_final_energy_mj` and `trace[].energy_mj` are in **MJ**. Both are the ERS
store level, which §11 and 5.3 put in MJ — only per-segment deltas are in kJ —
and the DP and simulator grid in MJ internally, so MJ is what the server has.
They were `mean_final_energy_kj` and `energy_kj` in earlier editions; the
quantities are unchanged and the illustrative figures restate as 0.405 MJ and
1.42 MJ. The `_kj` spellings were the error, not the numbers.

`cvar_p_ahead` is **not** a field of this response. CVaR over the per-episode
ahead indicator is degenerate — the indicator is Bernoulli, so the worst-α tail
mean collapses to 0 or 1 and carries no information about the risk the UI wants
to show. It stays on 5.15, where it comes from an offline planner artifact over
a continuous value distribution at a stated α, and on 5.13, where the request
names the α. Restoring it here requires a written definition first, not an
estimator.

*The service does not yet match on two points.* `episodes[].pass_segment_id` is
omitted although the simulator knows the segment when it sets `pass_lap`; and
the version is spelled `model_versions`, an empty map, beside a
`schema_version` — principle 7 wants the contributing model's version under
`model_version`.

### 5.15 `GET /validation`

Per-component metrics for a model-card panel (§55). Sample size is always present.

```json
{
  "pass":    { "model_version": "pass-2026.03", "split": "train 2022-2024 / val 2025 / test 2026-excl-BGP", "n_test": 1834,
               "brier": 0.171, "log_loss": 0.503, "roc_auc": 0.79, "pr_auc": 0.61, "calibration_ece": 0.021, "calibration": "isotonic" },
  "rival":   { "model_version": "rival-hsmm-2026.02", "n_battles_test": 212,
               "synthetic_state_recovery": 0.81, "predictive_log_likelihood": -0.92, "next_segment_accuracy": 0.66, "stability": 0.93, "merged_states": [] },
  "twin":    { "model_version": "twin-event-2026.01", "n_segments_test": 41200,
               "mae_s": 0.041, "rmse_s": 0.058, "by_speed_regime": { "low": 0.05, "mid": 0.04, "high": 0.03 }, "constraint_violations": 0 },
  "planner": { "model_version": "beam-2026.01", "n_episodes": 5000,
               "p_ahead": 0.63, "final_energy_mj": 0.41, "decision_regret": 0.04, "decision_stability": 0.86, "rule_violations": 0, "cvar_p_ahead": 0.44, "latency_ms_p95": 61 },
  "strategic_ablation": { "energy_state": "...", "tyre_state": "...", "gap_dynamics": "...", "rule_state": "...", "rival_belief": "...", "future_eligibility": "..." }
}
```

Numbers are illustrative placeholders. Real values come from `artifacts/validation/*.md` companion JSON. `split` is the leakage-safe design used (C9, §40); the demo battle's event is always in the held-out test set and is named in `split`.

### 5.16 `GET /simulate/policies`

M27. The explicit rival policies the simulator supports, for the `/simulate` request's `rival_policy` field and for the UI's policy picker.

```json
{ "policies": [
    { "name": "DEFEND_CONSERVE", "description": "holds energy, deploys only on Activation Line", "parameters": { "deploy_level_default": 0.25 } },
    { "name": "DEFEND_MIRROR",   "description": "matches attacker deployment one segment late", "parameters": {} },
    { "name": "ATTACK_GREEDY",   "description": "counterattack policy after being passed (§23)", "parameters": {} }
  ],
  "provenance": "RULE", "model_version": "sim-2026.01" }
```

Names above are placeholders; the real list is whatever M27 defines. Every policy has a one-line `description` the UI shows verbatim, since the simulator's assumptions must be disclosed (§56).

---

## 6. Feature exposure rules

What the UI may and may not send to live routes.

| Feature class | Allowed in `POST` requests | Reason |
|---|---|---|
| `LIVE_SAFE` (registry flag) | yes | available at prediction time (§44) |
| `OFFLINE_ONLY` | no → `OFFLINE_ONLY_FEATURE` | uses future information |
| identity (`attacker_driver`, `defender_team`) | yes, but the registry records whether the model uses them | §17 identity caution |
| raw `x_m`, `y_m`, driver number, timestamps | no | §36 |
| raw `drs`, `historical_drs_*`, `PROXY_HISTORICAL_DRS` on a 2026 request | no → `FEATURE_SCHEMA_MISMATCH` | historical-era audit/prior data cannot define 2026 Overtake, power, or legality |
| a feature from a later decision checkpoint than the one requested | no → `CHECKPOINT_VIOLATION` | §19 |
| source-gated channels (`brake_pressure`, `steering_angle`, `tyre_temperature`, `tyre_pressure`, `brake_temperature`, `damage`, `fuel_consumption`) | only when the registry marks a documented continuous source; never zero-filled | §11 |
| `pit_stop_duration_s_offline`, `pass_attempted`, outcome distance | no — audit / label only | §11, §19 |
| raw `wind_direction_deg` | no — send the track-relative head/cross components | §12, §39 |
| a hardcoded power cap from the client | no — the cap is server-side, from the one evaluator in C3 | §20.1, §32 |

The feature registry (`config/feature_registry.yaml`, M31) is the source of truth for `live_safe`, `decision checkpoint`, `source-gated`, `availability scope`, causal status, counterfactual safety, uncertainty field, regulation version, and interaction group, and is exported at `GET /meta` under `feature_registry_version`.

---

## 7. Replay bundle

Static JSON generated for the demo battle so the UI works without a running backend. Layout under `artifacts/demo/`:

```text
artifacts/demo/
    2026_british_grand_prix/
        meta.json                        GET /meta
        track.json                       GET /track/british_grand_prix
        rules.json                       GET /rules/british_grand_prix
        battles.json                     GET /battles?year=2026&event=british_grand_prix
        validation.json                  GET /validation
        shadow_price/
            index.json                   list of (energy_kj, gap_s, eligibility) grid points available
            e1420_g0.78_NOT_ARMED.json   GET /value/.../shadow_price for that point
            ...
        battles/
            2026_GBR_Race_HAM_ANT_Battle03/
                timeline.json            GET /battles/{id}/timeline
                plan/
                    seg022.json          POST /plan evaluated at each segment of the timeline
                    ...
                simulate/
                    beam_dp__DEFEND_CONSERVE__seed7.json
                    ...
            2026_GBR_Race_ANT_HAM_Battle04/    (counterattack direction, §23)
                ...
        bundle_manifest.json             git commit, model versions, generated_utc, file list, sha256 per file
```

Rules:

- File contents are **byte-identical in shape** to the corresponding service response.
- `shadow_price/index.json` enumerates the precomputed grid so the UI slider snaps to available points.
- `plan/segNNN.json` is precomputed for every segment in the timeline so scrubbing shows a plan instantly.
- `bundle_manifest.json` follows §53 (git commit, source datasets, model versions, seeds).
- Any file whose state or rule snapshot contains `PROXY_HISTORICAL_DRS` is ineligible for a final bundle; the generator must fail rather than serialize a development proxy.
- Generator: `scripts/serve/build_replay_bundle.py --event british_grand_prix --battle 2026_GBR_Race_HAM_ANT_Battle03 --out artifacts/demo`.

---

## 8. Display obligations

The UI is part of what makes claims true or false (§57, §58). These are not optional.

| Must do | Because |
|---|---|
| Show the provenance tag next to every number | §43 |
| Label energy as "estimated" / "SIMULATED", never "battery" or "SOC" | §28, §58 |
| Show `rival_state` as a distribution (bars), not a single label | §42 |
| Show `p_eligible` as a probability when `armed` is false and margin is small | §22 |
| Render `assumptions` from `/simulate` on screen | §56 |
| Render `source` for every rule value on hover or in a panel | §21 |
| Mark `live_safe: false` steps visually | §44 |
| Label `oracle_rival_state` as an upper bound | §33 |
| Show `n` / sample size on every metric | §55 |
| Never show one "overall accuracy" number | §55 |
| Display `X-TrackShift-Stub` responses with a visible "stub data" badge | §3.7 |
| When `normal_race_model_eligible` is false, grey out every model panel and show the race-control / pit state instead of any number | §12 |
| Label `fuel_kg` as "estimated fuel (inferred)"; never as fuel load or a measured value | §11, §38 |
| Show which decision checkpoint a pass probability belongs to; never merge DETECTION / ACTIVATION / BRAKING into one number | §19 |
| Show DRS-era rows (2022–2025) with a "historical DRS" badge, never the 2026 Overtake iconography | §41, §58 |
| Render wind as head / cross components relative to the track, not a compass direction | §12, §39 |
| Draw the power envelope as a curve against speed; never present peak kW as "the" power limit | §20.1 |
| Badge every number derived from an envelope key whose `verified` is false, and never claim legality by construction while `unverified_keys` is non-empty | §20.1, §57 |
| Label ERS fields "estimated"; keep the `_est` suffix meaning visible; never write "battery", "SOC", or "measured" | §11, §58 |
| Show `override_active_inferred` as a probability with an "inferred" badge; show "unknown" when `discriminable` is false | §20.2 |
| Present `envelope_violation` as a data-quality warning about our model, never as the car breaking a limit | §28.1 |
| Use MJ for stored/accumulated energy and kW for power, matching the field names | §11 |
| Show the applicable power-envelope regime and regulation snapshot (source documents, retrieval date) beside any action constraint | §21, §28 |
| Label energy-to-unlock, eligibility fragility, and alternate-policy results as modelled or simulated rather than observed race facts | §22, §40, §56 |

---

## 9. Route ownership

| Route | Implements | Owner |
|---|---|---|
| `GET /meta`, `GET /validation` | aggregation | Tanveer |
| `GET /track/{event}` | C1 + M18 | Tanveer |
| `GET /rules/{event}` | M18 | Tanveer |
| `GET /rules/{event}/power_envelope` | M18, M19 | Tanveer |
| `POST /rules/legal_actions`, `POST /rules/eligibility` | C3 | Tanveer |
| `POST /pass/predict` | C4 | Tanveer |
| `POST /twin/segment_time`, `POST /twin/energy_state` | C5 | Tanveer |
| `GET /battles` | C8 | Rishabh |
| `GET /battles/{id}/timeline` | joins C3, C4, C5, C7, C8, C10, M22 | Rishabh |
| `POST /rival/state` | C10 | Rishabh |
| `GET /value/{event}/shadow_price` | M22 | Rishabh |
| `POST /plan` | M24, M25 | Rishabh |
| `POST /simulate`, `GET /simulate/policies` | M26, M27 | Rishabh |
| `scripts/serve/build_replay_bundle.py` | calls every route in replay mode | Tanveer |
| `src/trackshift/serve/` app skeleton, error model, stub middleware | §3.7 | Tanveer |
| UI | consumes all of the above | Owner C |

Source layout:

```text
src/trackshift/serve/
    app.py              app factory, /api/v1 prefix, error handlers, stub middleware   (T)
    schemas.py          the JSON shapes in §3 and §5 as typed models                    (T scaffolds, both extend)
    routes_track.py     (T)
    routes_rules.py     (T)  includes /power_envelope
    routes_pass.py      (T)
    routes_twin.py      (T)
    routes_battles.py   (R)
    routes_rival.py     (R)
    routes_value.py     (R)
    routes_plan.py      (R)
    routes_simulate.py  (R)
    replay.py           serves a bundle directory with the same routes                 (T)
scripts/serve/
    run_service.py      --bundle artifacts/demo/... | --live                            (T)
    build_replay_bundle.py                                                              (T)
tests/serve/
    test_schemas.py     every example in this file validates against schemas.py         (T)
    test_replay_equivalence.py   bundle file == service response for the demo battle    (R)
```

---

## 10. Versioning and change policy

- `api_version` follows semver. A field removal or rename is a major bump. Adding an optional field is a minor bump.
- Every route response carries the `model_version` of each contributing model. The UI shows them in the footer from `/meta`.
- A change to any shape in §3 or §5 is its own PR, reviewed by the other model owner **and** Owner C.
- Stubs are allowed at any time as long as they set `X-TrackShift-Stub: true`. The replay bundle for the final demo must contain **zero** stub-generated files; `bundle_manifest.json` records `stubs_used: []`.

---

## 11. Readiness checklist

A model is "ready for UI" when every box is ticked. Tick per model.

| Check | pass (T) | rival (R) | twin (T) | rules (T) | value (R) | planner (R) | sim (R) |
|---|---|---|---|---|---|---|---|
| `api.py` contract implemented (MODELS.md §5) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Route(s) in §5 return the documented shape | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Every number carries provenance | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Uncertainty returned where §42 requires it | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| CPU inference verified, artifact manifest present | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `FEATURE_SCHEMA_MISMATCH` raised on bad input | ☐ | ☐ | ☐ | — | — | — | — |
| No `OFFLINE_ONLY` feature accepted on live route | ☐ | ☐ | ☐ | — | — | — | — |
| Zero illegal actions in any returned action list | — | — | — | ☐ | ☐ | ☐ | ☐ |
| Metrics with `n` in `/validation` | ☐ | ☐ | ☐ | — | — | ☐ | ☐ |
| Demo battle files present in replay bundle | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Not stub (`stubs_used` excludes it) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `CHECKPOINT_VIOLATION` raised for later-checkpoint features | ☐ | — | — | — | — | — | — |
| `NOT_MODEL_ELIGIBLE` raised / `null` returned outside normal-race rows | ☐ | ☐ | ☐ | — | ☐ | ☐ | ☐ |
| Causal cutoff recorded (`causal_cutoff_distance_m`, `feature_cutoff_distance_m`) | ☐ | ☐ | ☐ | — | — | — | — |
| Envelope evaluated only via C3 `max_electrical_power_kw`; no local constant | — | — | ☐ | ☐ | ☐ | ☐ | ☐ |
| `cap_kw` / `applicable_mode` returned wherever a deploy level appears | — | — | ☐ | ☐ | ☐ | ☐ | ☐ |
| `verified: false` propagated to every derived response | — | — | ☐ | ☐ | ☐ | ☐ | ☐ |

---

## 12. Open questions for Owner C

| # | Question | Assumption until answered |
|---|---|---|
| U1 | UI stack (web/JS, Streamlit, desktop)? Affects nothing in the schema, only how the service is hosted. | Web frontend reading JSON; service is a local HTTP server on the demo machine. |
| U2 | Is the demo replay-only, or does it need live what-if (sliders re-planning)? | Replay-first; `/plan` and `/simulate` served live from the same machine if time allows. |
| U3 | Latency budget for `/plan` when a slider moves? | ≤ 100 ms p95 on CPU; the precomputed `plan/segNNN.json` covers the scrub case regardless. |
| U4 | Does the UI need per-segment centreline polylines or is per-segment start/end enough? | Full centreline at 20 m resolution is included in `/track`. |
