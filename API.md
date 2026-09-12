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

For 2022–2025 rows `historical_drs_*` are populated and `overtake_*` are `null`; for 2026 the reverse. `overtake_*` come only from the rule engine (§12, §20), never from the raw DRS channel.

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
    { "kind": "DETECTION",  "zone": 1, "distance_m": 2890.0, "provenance": "RULE", "source": "FIA Event Notes 2026 BGP §4.2" },
    { "kind": "ACTIVATION", "zone": 1, "distance_m": 3020.0, "provenance": "RULE", "source": "FIA Event Notes 2026 BGP §4.2" }
  ],
  "zones": [ { "zone": 1, "name": "Hangar Straight", "start_distance_m": 3020.0, "end_distance_m": 3900.0 } ]
}
```

`centreline` is derived geometry (§12); raw `x`/`y` are never used directly across circuits. `kind` ∈ `STRAIGHT | BRAKING | CORNER | EXIT`. `corner_type` is the versioned geometry classification from §12 (hairpin / chicane / slow / medium / fast, with left / right / straight), never a free-text label; `geometry_version` changes whenever the classification or segment boundaries change, and every downstream artifact records it.

### 5.3 `GET /rules/{event}`

The rule configuration as loaded by M18, with sources, for the rule panel.

```json
{
  "event": "british_grand_prix", "year": 2026, "config_version": "rules-2026-bgp-r3",
  "regulation_snapshot": { "section_issues": ["..."], "effective_for_event": "...", "source_documents": ["..."], "retrieved_at": "..." },
  "overtake": {
    "enabled": true,
    "detection_gap_s": { "value": 1.0, "provenance": "RULE", "unit": "s", "source": "FIA Sporting Regulations 2026 Art. 22.7" },
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
      "minimum_gap_s": 0.41, "maximum_closing_rate_mps": 3.2,
      "detection_opportunities": 4, "pass_attempted": true, "pass_completed": true,
      "bounded_by": "PASS",
      "normal_race_only": true,
      "provenance": "DERIVED"
    }
  ]
}
```

`bounded_by` ∈ `PASS | PAIR_SWITCH | RACE_CONTROL_TRANSITION | PIT_TRANSITION | SESSION_END` says why the episode ended (§12: a race-control or pit transition is a hard boundary; no battle continues across it). `normal_race_only` is always true for battles in the model set; SC/VSC battles exist in the lake for audit but are not listed here.

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
- `pass.is_opportunity` is true only on segments that are rows in the overtake-opportunity dataset (M07). Elsewhere the whole `pass` block is `null`. When true, `checkpoints` holds one entry per decision checkpoint (§19); a checkpoint not yet reached at this step has `reached: false` and `null` probability. A DETECTION probability was computed from Detection-Line information only — it does not change when later checkpoints are reached; the UI shows all three side by side as they become available.
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
{ "decision_checkpoint": "DETECTION", "feature_schema_id": "pass-det-2026.03",
  "features": { "gap_at_checkpoint_s": 0.71, "delta_speed_checkpoint_kmh": 6.2, "delta_acceleration_checkpoint_mps2": 0.4,
                "closing_rate_mps": 1.9, "closing_rate_trend": 0.2, "recent_pace_delta_s": -0.11,
                "attacker_tyre_compound": "MEDIUM", "defender_tyre_compound": "HARD", "attacker_tyre_life": 12, "defender_tyre_life": 16,
                "attacker_tyre_degradation_proxy": 0.31, "defender_tyre_degradation_proxy": 0.27,
                "fuel_load_delta_kg_est": 0.6, "ers_energy_delta_kj_est": 240.0,
                "eligibility_margin_s": 0.29, "overtake_eligible": true, "overtake_state": "NOT_ARMED",
                "corner_type": "MEDIUM_RIGHT", "corner_phase": "ENTRY", "sector": 2, "regulation_era": "2026",
                "wind_head_component_mps": -2.1, "track_temperature": 41.0, "wet_track_flag": false, "...": "..." } }
```

Features are named, not positional; the server orders them against the locked schema **for that checkpoint** and rejects mismatches (§53). All must be `LIVE_SAFE` and must exist at or before the named checkpoint (§19): a `DETECTION` request carrying `gap_at_activation_s`, any braking-point speed, or a centred rolling statistic is refused with `CHECKPOINT_VIOLATION`. The state must be `normal_race_model_eligible`, else `NOT_MODEL_ELIGIBLE`.

Response:

```json
{ "decision_checkpoint": "DETECTION", "p_pass_by_outcome_horizon": 0.51, "outcome_horizon": "zone_exit_v1",
  "ensemble_spread": 0.08, "calibration": "isotonic", "era": "2026",
  "provenance": "INFERRED", "model_version": "pass-det-2026.03" }
```

`outcome_horizon` names the fixed, versioned definition of the training label `passed_by_outcome_horizon` (§19: e.g. completion before zone exit, or before the next Detection Line). The label itself is never returned by a live route and never accepted as a feature. `calibration` names the M11 method that produced the probability (`none | platt | isotonic`). Artifacts are checkpoint-specific (M10), so `model_version` differs per checkpoint.

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

Query: `energy_kj`, `time_gap_s`, `eligibility` (`NOT_ARMED|ARMED`), optional `tyre_state`, `relative_speed_mps`, `gap_rate_s_per_s`, `rival_state`, and `lap_index` (0 or 1 within the two-lap horizon). Omitted state dimensions use the declared replay state or grid cell and are returned in the response metadata.

```json
{
  "event": "british_grand_prix", "energy_kj": 1420.0, "gap_s": 0.78, "eligibility": "NOT_ARMED",
  "profile": [
    { "segment_id": 1,  "lambda_s_per_kj": 0.0012 },
    { "segment_id": 21, "lambda_s_per_kj": 0.0087, "note": "before DETECTION zone 1" },
    "..."
  ],
  "spikes": [ { "segment_id": 21, "cause": "DETECTION_LINE", "zone": 1 } ],
  "provenance": "DERIVED", "model_version": "dp-2026.01",
  "grid": { "energy_kj": [0, 250, "...", 4000], "gap_s": [0.0, 0.25, "...", 3.0] }
}
```

`spikes` marks where λ_E jumps because of an upcoming Detection Line (§20). `grid` tells the UI the valid ranges for sliders.

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
  "plan": [
    { "segment_id": 22, "action": { "deploy_level": 0.5, "lift_amount": 0.0, "label": "HOLD_FOR_DETECTION" },
      "expected_gap_s": 0.71, "expected_energy_kj": 1330.0, "lambda_s_per_kj": 0.0031 },
    "..."
  ],
  "p_ahead_at_horizon": { "mean": 0.63, "low": 0.51, "high": 0.74, "provenance": "INFERRED" },
  "p_pass_now": { "value": 0.58, "provenance": "INFERRED" },
  "p_repass_within_horizon": { "value": 0.21, "provenance": "INFERRED" },
  "final_energy_kj": { "mean": 410.0, "low": 320.0, "high": 505.0, "provenance": "SIMULATED", "unit": "kJ" },
  "cvar_p_ahead": 0.44,
  "rule_violations": 0,
  "latency_ms": 38,
  "decision": { "dominant_mechanism": "ELIGIBILITY_UNLOCK", "primary_constraint": "NEXT_DETECTION_LINE", "decision_stability": 0.86,
                "alternatives": [{ "action": { "deploy_level": 0.75, "lift_amount": 0.0 }, "expected_value": 0.59, "regret": 0.04 }] },
  "baselines": [
    { "name": "greedy_attack",       "p_ahead_at_horizon": 0.51, "final_energy_kj": 120.0, "rule_violations": 0 },
    { "name": "longest_straight",    "p_ahead_at_horizon": 0.55, "final_energy_kj": 380.0, "rule_violations": 0 },
    { "name": "lap_time_only",       "p_ahead_at_horizon": 0.48, "final_energy_kj": 290.0, "rule_violations": 0 },
    { "name": "dp",                  "p_ahead_at_horizon": 0.61, "final_energy_kj": 400.0, "rule_violations": 0 },
    { "name": "beam_dp",             "p_ahead_at_horizon": 0.63, "final_energy_kj": 410.0, "rule_violations": 0 },
    { "name": "oracle_rival_state",  "p_ahead_at_horizon": 0.69, "final_energy_kj": 430.0, "rule_violations": 0 }
  ],
  "model_version": "beam-2026.01"
}
```

`rule_violations` must always be `0` for `plan`; it is reported so the UI can assert it (§31). `baselines` names match §33 exactly. `oracle_rival_state` is an upper bound, not a deployable policy — label it so.

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
  "summary": { "p_ahead_at_horizon": 0.62, "mean_final_energy_kj": 405.0, "rule_violations": 0, "cvar_p_ahead": 0.43, "n_episodes": 200, "seed": 7 },
  "episodes": [
    { "episode": 0, "outcome": "AHEAD", "pass_lap": 31, "pass_segment_id": 27, "repassed": false,
      "trace": [ { "segment_id": 22, "gap_s": 0.78, "energy_kj": 1420.0, "action": {"deploy_level": 0.5, "lift_amount": 0.0} }, "..." ] }
  ],
  "assumptions": [ "rival policy is a fixed explicit policy, not a learned agent", "energy is SIMULATED from public telemetry", "..." ],
  "provenance": "SIMULATED", "model_version": "sim-2026.01"
}
```

`assumptions` is mandatory and must be shown in the UI (§56: "Its assumptions must be disclosed"). `episodes[].trace` may be truncated to the first N episodes in replay bundles.

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
               "p_ahead": 0.63, "final_energy_kj": 410.0, "decision_regret": 0.04, "decision_stability": 0.86, "rule_violations": 0, "cvar_p_ahead": 0.44, "latency_ms_p95": 61 },
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
