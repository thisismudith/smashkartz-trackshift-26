# TrackShift Model Ownership and Integration Plan

Companion to `TrackShift AGENTS.md`. That file is the engineering contract and is not modified by this plan. This file records **who builds which model, on what compute, and how the two halves join**. Section references (§N) point to `TrackShift AGENTS.md`.

Training procedures are deliberately out of scope here. Each model gets its own design note when its phase starts.

The UI-facing surface of every model listed here is specified in `API.md`. `MODELS.md` says who builds what; `API.md` says what the frontend can call. Per-owner build plans live in their own files: `CHECKPOINTS_TANVEER.md` covers Owner B's rules, pass, energy, and foundations; `CHECKPOINTS_RISHABH.md` covers Owner A's rival-state, value/decision, and foundation work; `CHECKPOINTS_INTEGRATION.md` defines the final unified API, artifact, replay, and release process.

Written against `TrackShift AGENTS.md` including the strategic-state formalism (§1, §31) and the speed-dependent power envelope (§20.1, §20.2, §28.1). If that file changes, re-run the coverage checkpoint in §9 before building against this plan.

---

## 1. Team and compute

| | Owner A | Owner B | Owner C |
|---|---|---|---|
| Person | Rishabh | Tanveer | UI / demo owner |
| Role | models (Chains S, V) | models (Chains R, P, E) | frontend, integration, demo |
| Local compute | RTX 4080 (16 GB VRAM) | CPU only | — |
| Remote compute | — | RTX A6000 (48 GB VRAM), persistent SSH access | — |
| Default stack | CPU first; local GPU for temporal neural models and vectorised simulation | CPU for trees, physics, rules; A6000 by default for the neural candidates (MLP in M10, neural regressor in M16) and for sweeps | consumes `API.md` only |

Owner C never imports model internals. The only surface Owner C sees is `API.md`.

Governing rule (§54): *"Do not use GPU compute merely because it is available."* Most of TrackShift is tree models, HMMs, least-squares physics, and dynamic programming. All of that is CPU work. GPU is justified only for the neural benchmark candidates and for batched simulation.

### 1.1 Compute rules that both owners follow

1. **Every model artifact must load and run inference on CPU.** The planner, simulator, and demo UI may run on any team machine. PyTorch models are saved as `state_dict` and loaded with `map_location="cpu"`. Never pickle a whole module.
2. **Device is a CLI argument** (`--device cpu|cuda`), never hardcoded (§5). Scripts must run end-to-end on CPU, even if slowly.
3. **Remote A6000 sessions produce the same artifact layout** as local runs. Artifacts are copied back into `artifacts/models/<component>/` and carry the same manifest fields (§53).
4. **No machine-specific paths in code or docs** (§5). Remote host names, mount points, and SSH configs live in ignored local config.

---

## 2. Complete inventory

Everything in `TrackShift AGENTS.md` that is learned, fitted, benchmarked, or is a compute component the models plug into. Items marked *(not ML)* are included because the models are meaningless without them and someone has to own them.

| ID | Component | §Ref | Phase | Kind |
|---|---|---|---|---|
| M01 | Practice lap classifier (PUSH / LONG_RUN / COOLDOWN / OUT_LAP / IN_LAP / INTERRUPTED / INVALID / UNKNOWN) | §9 | 3 | deterministic or hybrid classifier |
| M02 | Race-context labeller (CLEAN_AIR / FOLLOWING / CLOSE_FOLLOWING / ATTACKING / DEFENDING / TRAFFIC / SAFETY_CAR / VSC / YELLOW / PIT_IN / PIT_OUT / WET / DRY) **plus** race-control and pit-state normalisation: `pit_state` (ON_TRACK / PIT_IN / PIT_LANE / PIT_OUT / UNKNOWN), `normalized_race_control_state`, `safety_car_active`, `virtual_safety_car_active`, `race_control_transition_flag`, `pit_transition_flag`, `green_flag_elapsed_s`, and the **`normal_race_model_eligible` gate** that every downstream model dataset filters on. Transitions are hard sequence boundaries. | §11, §12, §37 | 3 | deterministic, inferred, or hybrid |
| M03 | Track segmentation (braking onset, throttle return, FIA lines, zone boundaries; 30–40 segments/lap) plus static per-track geometry: `sector`, `zone`, `corner_id`, versioned geometry-based `corner_type` (hairpin / chicane / slow / medium / fast / left / right / straight), `corner_phase`, `track_heading_deg`, `braking_intensity_proxy`. Full-segment summaries are `OFFLINE_ONLY`; live fields are time-aligned to segment entry. | §12, §13 | 4 | deterministic algorithm *(not ML)* |
| M04 | Driver / team / field segment baselines (median residuals), computed on `normal_race_model_eligible` rows only | §12, §14, §15 | 5 | statistical |
| M05 | Dynamic pair assignment and battle-episode extraction; an episode never continues across a race-control or pit-state transition, and only `normal_race_model_eligible` segments enter the initial battle set | §12, §16, §18 | 6 | deterministic *(not ML)* |
| M06 | Pairwise feature derivation: `gap_to_ahead_s` / `gap_to_behind_s`, `relative_speed_to_ahead_mps`, `relative_speed_to_behind_mps`, `closing_rate_ahead_mps`, `closing_rate_behind_mps`, `relative_acceleration_to_ahead_mps2`, `gap_trend_ahead_s_per_s`, `gap_trend_behind_s_per_s`, `brake_point_delta`, `braking_intensity_delta`, attacker/defender tyre compound, life and degradation proxies, `fuel_load_delta_kg_est` and `ers_energy_delta_kj_est` (with uncertainty), track-relative wind, `corner_type` / `corner_phase`. Aligned contemporaneous or strictly trailing observations only; no centred windows. | §12, §17 | 6 | feature engineering |
| M07 | Overtake-opportunity dataset: **one row per opportunity per decision checkpoint** — `DETECTION`, `ACTIVATION`, `BRAKING` — keyed by `opportunity_id`, `decision_checkpoint`, `feature_cutoff_distance_m`, `outcome_horizon`. A row carries only features whose checkpoint has already occurred (a DETECTION row never holds `gap_at_activation`). Label is `passed_by_outcome_horizon` with a fixed, versioned definition; `pass_attempted` and the outcome distance are stored for audit and never used as features. Only `normal_race_model_eligible`, green-flag opportunities enter the training set. Includes `regulation_era`, `historical_drs_eligible` / `historical_drs_open` (2022–2025) and `overtake_eligible` / `overtake_state` (2026, rule engine only). | §19 | 7 | dataset producer |
| M08 | Rival-state feature dataset (one row per `normal_race_model_eligible` battle segment): residuals, `braking_intensity_proxy`, `relative_speed_to_ahead_mps`, `tyre_degradation_proxy`, fuel and ERS estimates with uncertainty, track-relative wind, `track_temperature`, `wet_track_flag`, `corner_type` / `corner_phase`. Never turns an unobserved channel into an assumed tactical state. | §24 | 8 | dataset producer |
| M09 | Rival-state model benchmark: HMM, HSMM, gradient-boosted rolling-window classifier, GRU, TCN, small temporal Transformer | §25 | 10 | learned, benchmark track |
| M09b | Synthetic labelled-trajectory generator for rival state-recovery evaluation | §25, §55, §57 | 10 | simulation for evaluation |
| M10 | Pass-probability benchmark: Logistic Regression, CatBoost or LightGBM, XGBoost, optional small MLP — trained as **checkpoint-specific models or with strictly checkpoint-compatible feature sets** (one per DETECTION / ACTIVATION / BRAKING), never one model fed later-checkpoint features | §19, §26 | 9 | learned, benchmark track |
| M11 | Probability calibration: uncalibrated vs Platt/sigmoid vs isotonic | §27 | 9 | post-hoc fit |
| M12 | Pass-model uncertainty: ensemble spread | §42 | 9 | ensemble |
| M13 | Regulation-era handling: regulation-era feature, historical pretraining/prior, 2026 recalibration, domain weighting, separate historical and 2026 models followed by comparison | §41 | 9, 10 | cross-cutting on M09 and M10 |
| M14 | Energy twin (longitudinal power balance → P_K, tagged SIMULATED). Produces the lake's causal, uncertainty-tagged ERS estimates: `ers_deploy_power_est_kw`, `ers_harvest_power_est_kw`, `ers_energy_used_est_mj`, `ers_energy_harvested_est_mj`, `ers_soc_est_mj`, `ers_soc_uncertainty_mj`, `ers_deploy_budget_remaining_est_mj`, `ers_harvest_budget_remaining_est_mj` — `INFERRED` or `SIMULATED`, never stored or trained on as observed telemetry. Uses the §20.1 envelope as a **calibration diagnostic**, counting cap violations as a fit-quality metric, and never as a silent clamp (§28.1) | §11, §12, §28, §28.1 | 11 | physics model |
| M15 | Physics calibration hierarchy: pure analytical physics → global calibrated physics → team-specific calibrated physics → event-specific calibrated physics → physics + ML residual correction. Calibration and residual models control at minimum for entry speed, segment geometry, aero/Overtake state, tyre compound and life, fuel-load estimate, wind head/cross components, track temperature, wetness, and normal-race eligibility | §29 | 11 | fitted physics + learned residual |
| M16 | Segment-time / energy model (ΔE → Δt): analytical, linear/ridge, gradient boosting, physics + residual, small neural regressor | §30 | 11 | learned, benchmark track |
| M17 | Physics uncertainty: parameter draws, confidence ranges | §42 | 11 | uncertainty propagation |
| M18 | Event rule configuration (`config/rules/2026/*.yaml`, provenance RULE), covering **all 2026 events on disk**. Holds the speed-dependent electrical power envelope as piecewise-linear curves (`normal` and `override`: `breakpoints_kmh` + `max_power_kw`), the per-lap energy budget in MJ, and the Overtake line geometry. Every key carries `source` and `verified`; unverified values are usable but must surface as unverified everywhere they are displayed (§20.1) | §20, §20.1, §21 | 12 | configuration *(not ML)* |
| M19 | Deterministic rule engine (legal action set, envelopes, limits, race-control state). **Sole owner** of `max_electrical_power_kw(speed_kmh, mode, event_rules)` — the one implementation of the §20.1 curve in the system. Every consumer (DP, simulator, twin diagnostics, override discriminator) calls it; no envelope constant may appear in any other module | §20.1, §32 | 12 | deterministic *(not ML)* |
| M20 | 2026 Overtake state machine (Detection Line → armed → Activation Line → envelope). Resolves eligibility into **which envelope applies** (`normal` vs `override`), which is what the DP action set is built against | §20, §20.1 | 12 | state machine *(not ML)* |
| M21 | Eligibility probability and Overtake-derived features (eligibility_margin, P(g_det < g_thr), energy_required_to_unlock, …) | §22 | 12–13 | probabilistic derived features |
| M22 | Dynamic programming and energy shadow price λ_E(k, e, g, ε). `deploy_level` is a fraction of the **speed-dependent cap**, not of a fixed power, so the same action differs physically by location; energy accounting uses delivered energy, never the request (§31). λ_E now has two structural sources of variation: the Detection-Line discontinuity and the track's speed profile against the taper | §1, §31, §20.1 | 13 | optimisation *(not ML)* |
| M23 | Counterattack / repass valuation inside the two-lap value function | §23 | 13 | part of DP value |
| M24 | Planner (beam search for near-term uncertainty + DP terminal value) | §33 | 14 | optimisation |
| M25 | Planner baselines: greedy attack, longest-straight deployment, lap-time-only, DP, beam + DP, oracle rival-state policy | §33 | 15 | heuristic policies |
| M26 | Simulator (same segments, same rule engine, same pass API, seeded episodes, uncertainty draws) | §56 | 15 | simulation |
| M27 | Explicit rival policies for the simulator | §56 | 15 | policy definitions |
| M28 | Feature-group ablation harness (tyre, weather, team id, driver id, rolling trends, geometry, race context) | §35 | 9–11 | evaluation tooling |
| M29 | Leakage-safe splitter (by event / battle_id / weekend / track / year; year-forward and leave-one-out designs) | §40 | 6+ | evaluation tooling |
| M30 | Tyre context: a causal `tyre_degradation_proxy` and tyre-normalised pace, normalised relative to driver, car, compound, stint, track and session (not one global curve); attacker and defender kept separate before deltas. Tyre temperature / pressure remain source-gated (`*_delta_to_stint_baseline` if a documented source is added) | §38 | 5–7 | feature engineering |
| M31 | Dataset registry and feature registry (`config/data_registry.yaml`, `config/feature_registry.yaml`). Every feature records `availability scope`, `source-gated`, `decision checkpoint`, and `uncertainty field` in addition to the §46 base fields; the registry is also where the §11 source-gated channel policy (`brake_pressure`, `steering_angle`, `tyre_temperature`, `tyre_pressure`, `brake_temperature`, `damage`, `fuel_consumption`) is enforced — a channel is absent until a documented continuous source exists, and missing is never numeric zero | §11, §45, §46 | all | configuration |
| M32 | UI, integration, validation, demo | §59 Phase 16 | 16 | integration |
| M33 | Track-relative weather derivation: `wind_head_component_mps`, `wind_cross_component_mps` (wind projected onto `track_heading_deg`), `air_density_proxy`, `wet_track_flag`; raw `humidity_pct`, `air_pressure_hpa`, `wind_speed_mps`, `wind_direction_deg` retained in the lake | §12, §39 | 4–5 | feature engineering |
| M34 | Causal fuel-load estimator: `fuel_load_kg_est` + `fuel_load_uncertainty_kg`, tagged `INFERRED` or `SIMULATED`, uses no future information; the only permitted source of fuel context for live features | §11, §38 | 11 | estimator |
| M35 | Override / ERS-mode discriminator: compares the twin's `ers_deploy_power_est_kw` against the normal-mode cap at the observed speed to produce `override_active_inferred` (probability) and `ers_mode_inferred`. Has power **only above the speed where the two envelopes separate**; returns UNKNOWN below it. Must be evaluated for false-positive rate against twin uncertainty before use, and is a feature/prior — never a tactical-state label (§20.2, §24) | §20.2, §11 | 11–12 | probabilistic discriminator |

---

## 3. Ownership split

Models are grouped into **chains**. A chain is a sequence where each item is a direct follow-up of the previous one (its input is the previous item's output, or it post-processes it). Chains are never split between owners.

### 3.1 Owner B — Tanveer (CPU + remote A6000)

| Chain | Items | Compute |
|---|---|---|
| **Chain R — Rules** | M18 → M20 → M19 → M21 | CPU. Pure config, state machine, and deterministic logic. |
| **Chain P — Pass probability** | M07 → M10 → M11 → M12 → M13 (pass side) | CPU for LogReg / LightGBM / CatBoost / XGBoost / isotonic / Platt. Optional MLP: A6000 (persistent access), artifact verified on CPU before merge. |
| **Chain E — Energy / physics** | M14 → M34 → M15 → M35 → M16 → M17 | CPU for analytical physics, least-squares calibration, ridge, gradient boosting, residual fits. A6000 for the small neural regressor candidate in M16 and for large parameter-draw ensembles; every artifact verified on CPU before merge. |
| **Foundations owned** | M01, M03, M04, M30, M33 | CPU. Segmentation, baselines, and weather projection are median/threshold/geometry work over Parquet. |

Why this grouping:

- Chain R is the prerequisite for Chain P (opportunities need Detection and Activation Lines) and for Chain E's ΔE→Δt (legal envelopes bound the action set). One owner keeps line definitions, envelopes, and eligibility semantics consistent.
- Chain P is tree-model work. CPU is the natural home; §26 puts calibration quality above raw accuracy, and calibration is a CPU fit.
- Chain E is physics first, ML second (§29). Fitting `mass`, `CdA`, rolling resistance, efficiency, and an ICE map is scipy work.
- M01 (Practice lap classification) belongs with Chain E because Practice is the physics-calibration session (§9). M03 segmentation is physics-derived (braking onset, throttle return). M04 baselines are a per-segment follow-up of M03. M33 needs `track_heading_deg` from M03, so it follows it. M34 (fuel estimator) is a sibling of the energy twin and its output is a required control in M15.
- M35 (override discriminator) sits **after** M15 deliberately. It compares an estimate against a regulatory cap, so it is only trustworthy once the twin is calibrated — an uncalibrated `CdA` or mass manufactures false override detections (§20.2). It spans Chain R and Chain E (it needs M19's envelope function and M14's power estimate), which is a further reason both chains sit with one owner.

### 3.2 Owner A — Rishabh (RTX 4080)

| Chain | Items | Compute |
|---|---|---|
| **Chain S — Rival state** | M08 → M09 → M09b → M13 (rival side) | HMM / HSMM / GBM: CPU. GRU / TCN / Transformer: RTX 4080. Synthetic trajectory generation: CPU. |
| **Chain V — Value and decision** | M22 → M23 → M24 → M25 → M26 → M27 | DP: CPU (NumPy) by default; torch on 4080 if the state grid grows. Simulator: vectorised episodes on 4080 when sweeping seeds and uncertainty draws. |
| **Foundations owned** | M02, M05, M06 | CPU. |

Why this grouping:

- Chain S is the only place where GPU is likely to be required, because the benchmark set (§25) includes GRU, TCN, and Transformer. Keeping HMM/HSMM in the same hands as the neural candidates is what makes the benchmark comparable — same features, same splits, same synthetic evaluation.
- Chain V consumes every other output. DP → planner → simulator is one tight loop (§33: beam + DP; §56: simulator uses the same rule engine and pass API). Splitting it would put the integration burden inside the loop.
- M02 race-context labels are a direct input to Chain S: §37 warns that a slow yellow-flag segment must not be read as energy conservation. M05/M06 produce battle episodes and pairwise rows, which are the unit of Chain S.

### 3.3 Shared and cross-cutting

| Item | Primary owner | Note |
|---|---|---|
| M28 ablation harness | Tanveer builds the harness; each owner runs it on their own models | One implementation in `src/trackshift/eval/ablation.py`; feature groups declared in the feature registry. |
| M29 leakage-safe splitter | Rishabh | `battle_id` is produced by M05, so the splitter lives beside it. Tanveer's Chain P must use it, never a private split. |
| M31 registries | Tanveer scaffolds; both append | Every dataset and feature either owner produces gets a registry entry in the same PR. |
| M32 UI / demo | Owner C | Consumes the model outputs strictly through `API.md`. Both model owners deliver their endpoints and the replay bundle defined there. |

---

## 4. Dependency graph

```text
                       Phase 2 lake (exists)
                              |
             +----------------+----------------+
             |                                 |
        M03 segmentation (T)              M02 race context (R)
             |                                 |
        M04 baselines (T)                      |
        M33 track-rel. weather (T)             |
             |                                 |
             +---------------+-----------------+
                             |
                 M05 pairs + battles (R)
                 M06 pairwise features (R)
                 M29 splitter (R)
                             |
            +----------------+------------------+
            |                                   |
   M18/M20/M19/M21 rules (T)             M08 rival features (R)
            |                                   |
   M07 opportunities (T)                 M09 rival models (R)
            |                            M09b synthetic eval (R)
   M10/M11/M12 pass model (T)                   |
            |                                   |
   M14/M34/M15/M16/M17 energy (T)               |
            |                                   |
            +----------------+------------------+
                             |
                    M22/M23 DP + lambda_E (R)
                             |
                    M24 planner (R)
                             |
                    M25 baselines + M26/M27 simulator (R)
                             |
                    M32 UI / demo (C, via API.md)

(T) = Tanveer   (R) = Rishabh   (C) = UI / demo owner
```

Critical path: **M03 → M05 → M08 → M09** on the rival side and **M18 → M07 → M10** on the pass side both need M03 first. Segmentation is the first thing to freeze, because `segment_id` is the join key for every downstream table.

---

## 5. Interface contracts

Per §60, each hand-off is defined by input schema, output schema, units, provenance, and failure behaviour **before** implementation. Each owned package exposes its contract in a single `api.py`. Consumers import only from `api.py`, never from internals.

### 5.1 Tanveer → Rishabh

**C1. Segments table** — `data/processed/segments/` (Parquet, partitioned year/event/session)

| Field | Unit | Provenance |
|---|---|---|
| `segment_id` (stable per track, int) | — | DERIVED |
| `start_distance_m`, `end_distance_m`, `segment_length_m` | m | DERIVED |
| `segment_time_s`, `entry_speed_kmh`, `exit_speed_kmh`, `max_speed_kmh`, `mean_speed_kmh` | s, km/h | DERIVED |
| `brake_onset_m`, `brake_fraction`, `full_throttle_fraction`, `lift_fraction`, `coast_fraction` | m, ratio | DERIVED |
| `mean_gradient`, `elevation_change` | ratio, m | DERIVED |
| `gap_entry`, `gap_exit` | s | DERIVED |
| `distance_gap_entry_m`, `time_gap_entry_s`, `relative_speed_to_ahead_mps`, `relative_acceleration_to_ahead_mps2`, `gap_rate_ahead_s_per_s` | m, s, m/s, m/s², s/s | DERIVED |
| `tyre_compound`, `tyre_life`, `stint` | —, laps, — | OBSERVED |
| `tyre_degradation_proxy` | ratio | DERIVED |
| `tyre_state_est`, `tyre_state_uncertainty` | model-defined | INFERRED |
| `sector`, `zone`, `corner_id`, `corner_type`, `corner_phase` (static per track map, versioned) | — | DERIVED |
| `track_heading_deg` | deg | DERIVED |
| `wind_head_component_mps`, `wind_cross_component_mps`, `wet_track_flag` (time-aligned to segment entry) | m/s, m/s, bool | DERIVED |
| `normal_race_model_eligible` | bool | DERIVED (from C7) |

Live fields are aligned to segment **entry**. Any summary that uses the full segment is a separate `OFFLINE_ONLY` column (§13).

Failure: a lap that cannot be segmented is written to a rejection manifest, never silently dropped (§49).

**C2. Baselines** — `data/processed/driver_segment_baselines/`, `data/processed/team_segment_baselines/`, `data/processed/field_segment_baselines/`

Keyed by `(track, segment_id, [driver|team])`; one column per baselined quantity (segment time, exit speed, brake point, throttle commitment, top speed) holding the median and sample count `n`. Rows with `n` below a declared minimum carry `baseline_valid = false` rather than being omitted.

**C3. Rule engine API** — `src/trackshift/rules/api.py`

```text
max_electrical_power_kw(speed_kmh, mode, event_rules) -> float
    mode:        NORMAL | OVERRIDE
    returns:     the regulatory cap at that speed, by interpolation on the
                 piecewise-linear curve in event_rules (§20.1)
    guarantee:   clamped outside the breakpoint range; never negative
    provenance:  RULE
    note:        the ONLY implementation of the envelope in the system —
                 this is P_ERS^max(v, mode, competition) in §28's notation.
                 No other module may hold an envelope constant.

legal_actions(state: StrategicState, event_rules) -> ActionSet
    state:       segment_id; speed_kmh (required to resolve the applicable
                 power-envelope regime); Energy Store state (ers_soc_est_mj) and
                 uncertainty; deploy and harvest budgets remaining; tyre state;
                 time/distance gap; relative speed; gap rate; Overtake eligibility;
                 race-control state
    returns:     permitted (deploy_level, lift_amount) pairs plus exclusions with
                 rule sources, where deploy_level is a fraction of
                 max_electrical_power_kw at this state's speed and applicable
                 mode — not of a fixed power (§31) — and each permitted action
                 also carries applicable_mode, cap_kw, and delivered_power_kw so
                 the DP accounts energy against what is deliverable, not the request
    guarantee:   illegal actions are absent from the set, never low-scored (§31)

eligibility(state, event_rules) -> EligibilityResult
    returns:     armed: bool, projected-gap distribution, p_eligible: float in [0,1],
                 eligibility_margin_s: float, applicable_mode: NORMAL | OVERRIDE,
                 and optional energy-to-unlock estimate with uncertainty
    provenance:  RULE for thresholds, INFERRED or SIMULATED for causal projections
```

Failure: unknown event or missing rule key raises; it never defaults to "Overtake enabled" and never substitutes a default envelope. A config whose envelope keys carry `verified: false` is usable, and the flag propagates to every consumer so the UI can mark it (§20.1).

**C4. Pass probability API** — `src/trackshift/pass_model/api.py`

```text
predict_pass(features: OpportunityFeatures, decision_checkpoint) -> PassPrediction
    decision_checkpoint:      DETECTION | ACTIVATION | BRAKING  (required)
    p_pass_by_outcome_horizon: calibrated float in [0,1]
    outcome_horizon:          str, versioned definition (e.g. "zone_exit_v1")
    ensemble_spread:          float (std across ensemble members)
    model_version:            str  (checkpoint-specific artifact)
    feature_schema_id:        str  (rejects a differently ordered matrix, §53)
    provenance:               INFERRED
```

Input features are `LIVE_SAFE` only (§44) **and must belong to the given checkpoint or an earlier one** (§19): a DETECTION call carrying `gap_at_activation` or any braking-point quantity raises `CheckpointViolation`. The model ships with `feature_schema.json` per checkpoint; a mismatch raises.

**C5. Energy twin / segment-time API** — `src/trackshift/twin/api.py`

```text
segment_time(segment_id, action, strategic_context) -> SegmentTimeEstimate
    strategic_context: entry speed; tyre state; fuel; aero; weather; regulation/power state;
                       current gap dynamics and a causal rival-response assumption
    t_s, t_draws_s:    segment-time distribution
    delta_e_kj:        segment deployment energy, SIMULATED
    harvested_e_kj:    segment harvested energy, SIMULATED
    projected_gap_delta_s: causal downstream gap effect where modelled
    provenance:         SIMULATED

energy_state(telemetry_window, event_rules) -> EnergyEstimate
    ers_deploy_power_est_kw, ers_harvest_power_est_kw
    ers_energy_used_est_mj, ers_energy_harvested_est_mj
    ers_soc_est_mj, ers_soc_uncertainty_mj
    ers_deploy_budget_remaining_est_mj, ers_harvest_budget_remaining_est_mj
    ers_store_capacity_mj                              (RULE, the physical bound on ers_soc_est_mj)
    cap_kw, applicable_mode                             (RULE; the envelope regime at the current speed)
    fuel_load_kg_est, fuel_load_uncertainty_kg          (from M34)
    envelope_violation: bool, violation_margin_kw: float   (§28.1 diagnostic)
    provenance: SIMULATED (ERS estimates), INFERRED (fuel), RULE (capacity and cap)

override_state(telemetry_window, event_rules) -> OverrideInference   (M35)
    override_active_inferred: float in [0,1]
    ers_mode_inferred:        NORMAL | OVERRIDE | UNKNOWN
    discriminable:            bool  (false below the envelope separation speed)
    provenance: INFERRED
```

Causal: uses only telemetry at or before the window end (§12). Never labelled OBSERVED (§11, §28, §58). These are the only permitted sources of ERS and fuel context for any live feature.

`envelope_violation` is a **calibration signal, not a clamp** (§28.1): an estimate above the override cap means the twin's parameters are wrong, and the raw estimate is preserved so that the calibration can be fixed rather than hidden. `discriminable` is false below the speed where the two envelopes separate, and the UI must show "unknown", never "normal" (§20.2).

**C6. Opportunity and rule-derived features** — appended to `overtake_opportunities` and available to Chain S, each tagged with the checkpoint at which it becomes available: `gap_at_checkpoint`, time/distance gap, relative speed, relative acceleration, gap rate, `eligibility_margin`, causal `projected_gap_at_detection`, `probability_eligible`, `energy_required_to_unlock` with uncertainty, `eligibility_fragility_per_kj` where supported, `delta_speed_checkpoint`, `delta_acceleration_checkpoint`, `distance_detection_to_activation`, `distance_activation_to_brake`, `overtake_eligible`, `overtake_state` (2026, rule engine only), `historical_drs_eligible`, `historical_drs_open` (2022–2025 covariates only). The registry's `decision checkpoint`, causal status, and counterfactual-safe fields are authoritative for which rows may carry which feature.

### 5.2 Rishabh → Tanveer

**C7. Race-context labels** — column set on `telemetry_20m` and `segments`: `race_context` (enum from §37), `race_context_provenance` ∈ {DERIVED, INFERRED}, `pit_state` (ON_TRACK / PIT_IN / PIT_LANE / PIT_OUT / UNKNOWN), `normalized_race_control_state`, `safety_car_active`, `virtual_safety_car_active`, `race_control_transition_flag`, `pit_transition_flag`, `green_flag_elapsed_s`, and **`normal_race_model_eligible`**. The gate is true only for on-track, green-flag racing with no SC/VSC/red/yellow restriction, pit state, or unknown control state. M04, M05, M07, M08 and M15 all filter on it; SC/VSC rows stay in the lake for audit. `pit_stop_duration_s_offline` is `OFFLINE_ONLY` and never a live feature.

**C8. Battle episodes and pairwise rows** — `data/processed/battle_episodes/`, `data/processed/pairwise_segment_features/`

Battle key `battle_id` (format `YYYY_EVT_Session_ATT_DEF_BattleNN`), `attacker`, `defender`, `start_lap`, `end_lap`, `duration_segments`, `duration_s`, `minimum_gap`, `maximum_closing_rate`, `detection_opportunities`, `pass_attempted`, `pass_completed`, `bounded_by` (why the episode ended: PASS / PAIR_SWITCH / RACE_CONTROL_TRANSITION / PIT_TRANSITION / SESSION_END). Pairwise rows retain time and distance gap, relative speed, relative acceleration, and gap rate as separate causal fields. An episode never spans a race-control or pit-state transition, and no rolling feature is computed across one. Chain P builds M07 by joining opportunities onto these.

**C9. Splitter** — `src/trackshift/data/splits.py`

```text
make_split(rows, unit="battle_id"|"event"|"track"|"year", design=...) -> SplitAssignment
```

Guarantee: no `battle_id` appears in more than one fold. Chain P and Chain S both call this; neither writes its own.

**C10. Rival-state API** — `src/trackshift/rival/api.py`

```text
rival_state(battle_segments) -> StateDistribution
    p: full state distribution over {CONSERVING, BALANCED, DEPLOYING, DERATING}  (sums to 1; merged states allowed per §25)
    provenance: INFERRED
```

Consumed by the planner (same owner) and exposed so Chain P can test rival-state as a candidate feature group in M28.

### 5.3 Consumed by both, produced once

| Contract | Producer | Notes |
|---|---|---|
| `config/feature_registry.yaml` | both append | `live_safe`, `provenance`, `consuming_models` filled in for every feature at the PR that introduces it |
| `config/data_registry.yaml` | both append | producer script, schema version, partitioning |
| `config/rules/2026/*.yaml` | Tanveer | every key has a `source` |

---

## 6. Integration workflow

### 6.1 Order of work

| Milestone | Tanveer | Rishabh | Unblocks |
|---|---|---|---|
| **I0** | Scaffold registries (M31), rules config skeleton (M18) | Splitter (M29), race-context labels **incl. the `normal_race_model_eligible` gate and transition flags** (M02) | contracts C7, C9 — every later dataset filters on the gate |
| **I1** | Segmentation (M03) → freeze `segment_id` | — | everything |
| **I2** | Baselines (M04), practice classifier (M01), track-relative weather (M33) | Pairs + battles (M05), pairwise features (M06) | C1, C2, C8 |
| **I3** | Rule engine + state machine + eligibility (M19, M20, M21) | Rival-state features (M08) | C3, C6 |
| **I4** | Opportunities (M07) → pass benchmark (M10, M11, M12, M13) | Rival benchmark (M09, M09b, M13) | C4, C10 |
| **I5** | Energy twin → fuel estimator → calibration → ΔE→Δt (M14, M34, M15–M17) | DP + λ_E (M22, M23) using stub C4/C5 | C5 |
| **I6** | Ablations (M28) on Chains P and E | Planner (M24), baselines (M25), simulator (M26, M27) | end-to-end run |
| **I7** | replay bundle for the demo event (API.md §7) | `/plan`, `/simulate`, `/timeline` routes (API.md) | Owner C wires the UI |

The two columns run in parallel from I2 onward. Chain V (I5) starts against **stub implementations** of C3, C4, C5 that return fixed values with the correct schema, so DP can be developed before the real models exist. Stubs live in each `api.py` behind `--stub` and are replaced when the artifact lands.

### 6.2 Branch and merge

- Rishabh integrates through `main` (current practice, plus `codex/*` branches).
- Tanveer works on branch `Tanveer`, fast-forwarded to `main` before each milestone starts, merged to `main` by PR at each milestone end.
- Contract files (`api.py`, registry entries, rule configs) are merged **first** and separately from implementations, so the other owner can code against them.
- A change to any contract in §5 is its own PR, reviewed by the other owner.

### 6.3 Data sharing

Each model owner holds a complete raw mirror locally and builds the processed lake locally with the same scripts and the same scope. Determinism of `build_phase2_dataset.py` and the feature builders is therefore a requirement: identical inputs and identical git commit must produce identical Parquet, and every build writes its manifest so mismatches are detectable by comparing `schema_version` and row counts. Raw mirrors are large (one 2022 event-session measured at ~243 MB; the default 2022–2026 scope extrapolates to >100 GB) and are never synced between machines.

Artifacts under `artifacts/models/` are small and are committed or attached to PRs with their manifests.

### 6.4 Artifact manifest (both owners, §53)

Every model artifact directory contains `manifest.json` with: `git_commit`, `source_datasets`, `years_events`, `config`, `schema_version`, `feature_schema` (ordered list), `model_params`, `seed`, `created_utc`, `device_trained_on`, `cpu_inference_verified: true`.

---

## 7. Final deliverables

Grouped by the target tree in §48. Marked with owner.

### 7.1 Source modules

```text
src/trackshift/
    data/
        splits.py                       (R)  M29
        registry.py                     (T)  M31 loaders
    track/
        segmentation.py                 (T)  M03
        baselines.py                    (T)  M04
        weather.py                      (T)  M33
        lap_classifier.py               (T)  M01
        race_context.py                 (R)  M02
    features/
        pairing.py                      (R)  M05
        battles.py                      (R)  M05
        pairwise.py                     (R)  M06
        tyre_pace.py                    (T)  M30
        opportunities.py                (T)  M07
        rival_state_features.py         (R)  M08
        overtake_features.py            (T)  M21
    rules/
        api.py                          (T)  C3
        config.py                       (T)  M18 loader
        state_machine.py                (T)  M20
        engine.py                       (T)  M19
        eligibility.py                  (T)  M21
    pass_model/
        api.py                          (T)  C4
        candidates.py                   (T)  M10  (logreg, lightgbm/catboost, xgboost, mlp)
        calibration.py                  (T)  M11
        ensemble.py                     (T)  M12
        era.py                          (T)  M13 pass side
    rival/
        api.py                          (R)  C10
        hmm.py, hsmm.py                 (R)  M09
        gbm_window.py                   (R)  M09
        neural.py                       (R)  M09 (GRU, TCN, Transformer)
        synthetic.py                    (R)  M09b
        era.py                          (R)  M13 rival side
    twin/
        api.py                          (T)  C5
        power_balance.py                (T)  M14
        fuel.py                         (T)  M34
        calibration.py                  (T)  M15
        segment_time.py                 (T)  M16
        uncertainty.py                  (T)  M17
    value/
        dp.py                           (R)  M22
        shadow_price.py                 (R)  M22
        counterattack.py                (R)  M23
    planner/
        beam.py                         (R)  M24
        baselines.py                    (R)  M25
    sim/
        simulator.py                    (R)  M26
        rival_policies.py               (R)  M27
    eval/
        ablation.py                     (T)  M28
        metrics.py                      (both)  §55 per-component metrics
    serve/                              (both)  HTTP + replay bindings of API.md, see API.md §9 for per-route owner
    ui/                                 (C)     M32, outside this plan
```

### 7.2 Scripts

```text
scripts/
    features/
        build_segments.py               (T)
        build_baselines.py              (T)
        build_battles.py                (R)
        build_opportunities.py          (T)
        build_rival_features.py         (R)
    train/
        train_pass_model.py             (T)
        train_rival_model.py            (R)
        calibrate_physics.py            (T)
        train_segment_time.py           (T)
    evaluate/
        evaluate_pass_model.py          (T)
        evaluate_rival_model.py         (R)
        evaluate_twin.py                (T)
        evaluate_planner.py             (R)
        run_ablation.py                 (T harness, both run)
    simulate/
        run_dp.py                       (R)
        run_simulator.py                (R)
```

### 7.3 Configuration

```text
config/
    data_registry.yaml                  (both)
    feature_registry.yaml               (both)
    rules/2026/common.yaml              (T)
    rules/2026/<event>.yaml             (T)
    physics/priors.yaml                 (T)  analytical starting parameters, with source
```

### 7.4 Artifacts

```text
artifacts/
    models/
        pass/<version>/                 (T)  model, calibrator, ensemble members, feature_schema.json, manifest.json, metrics.json
        rival/<version>/                (R)  per-candidate artifacts, chosen model, manifest.json, metrics.json
        twin/<version>/                 (T)  calibrated params per level (global/team/event), residual model, manifest.json
        segment_time/<version>/         (T)
    validation/
        pass_model_report.md            (T)  Brier, log loss, calibration, ROC-AUC, PR-AUC, N
        rival_model_report.md           (R)  synthetic state recovery, predictive log likelihood, next-segment prediction, stability, calibration, latency
        twin_report.md                  (T)  MAE, RMSE, by speed regime, by segment type, constraint violations
        planner_report.md               (R)  P(ahead), final energy, decision regret, rule violations, CVaR, decision stability, latency
        ablation_report.md              (both)  component and strategic ablations
    dp/
        value_tables/<event>/           (R)  V(strategic_state) and lambda_E
```

### 7.5 Tests (§52)

```text
tests/
    test_segmentation.py                (T)
    test_baselines.py                   (T)
    test_lap_classifier.py              (T)
    test_race_context.py                (R)  eligibility gate; transitions are hard boundaries
    test_weather.py                     (T)  head/cross projection sign vs track heading
    test_opportunities.py               (T)  a DETECTION row never carries an ACTIVATION/BRAKING feature
    test_pairing.py                     (R)  driver-ahead, battle start/end, switching after pass, no self-pair
    test_splits.py                      (R)  no battle in two folds
    test_rules.py                       (T)  below / at / above every threshold; Detection, Activation, disabled, power-envelope and recharge-budget boundaries
    test_pass_api.py                    (T)  feature-order rejection, calibration bounds
    test_twin.py                        (T)  physical constraints, causal Energy Store accounting, SIMULATED tagging
    test_rival_api.py                   (R)  distribution sums to 1, merged-state path
    test_dp.py                          (R)  zero illegal actions, energy accounting, boundary conditions, uncertainty-stable policy checks
    test_simulator.py                   (R)  seeded determinism
```

---

## 7.6 Demo target

The demo event is fixed so that rule configuration, replay bundles, and validation splits have a concrete first target.

```text
year:      2026
event:     British Grand Prix (Silverstone)
session:   Race (Sprint as secondary if available)
battle:    HAM vs ANT  (Hamilton, Antonelli)
```

Both directions are needed because of counterattack modelling (§23): HAM attacking ANT and ANT attacking HAM after a pass. `config/rules/2026/british_grand_prix.yaml` is the first rule file. The replay bundle in `API.md §7` is generated for this battle first. The demo event is also a **held-out test event** for Chains P and S — it must not enter training or calibration folds (§40).

---

## 8. Open questions

Answers change the plan in the stated way. Until answered, the assumption in the third column applies.

| # | Question | Assumption if unanswered |
|---|---|---|
| Q1 | ~~How is the processed lake shared?~~ **Resolved:** each owner holds the full raw dataset and builds locally. See §6.3. | — |
| Q2 | ~~Is A6000 access persistent?~~ **Resolved:** persistent. Neural candidates in Chains P and E default to the A6000. See §1. | — |
| Q3 | ~~Which 2026 event is the demo event?~~ **Resolved:** British Grand Prix, Silverstone, HAM vs ANT. See §7.6. | — |
| Q4 | Who owns Phases 0–2 going forward (currently on `main` via `codex/*` branches)? | Rishabh, unchanged. |
| Q5 | ~~Who owns UI / demo?~~ **Resolved:** a third contributor (Owner C), integrating through `API.md` only. | — |
| Q6 | Hackathon date (§61 mentions one)? | Unknown; milestone table in §6.1 is ordered but undated. |
| Q7 | ~~Should DP move to Tanveer?~~ **Resolved:** stays with Rishabh. Chain V is self-contained and develops against stubs of C3/C4/C5, so it does not block on Tanveer's chains. | — |
| Q8 | ~~Who writes the per-owner and integration build plans?~~ **Resolved:** Tanveer's is `CHECKPOINTS_TANVEER.md`; Rishabh's is `CHECKPOINTS_RISHABH.md` for M02, M05, M06, M08, M09, M09b, M13 rival side, and M22–M29; the joint plan is `CHECKPOINTS_INTEGRATION.md`. **Integration is owned by Rishabh.** | — |

---

## 9. Coverage checkpoint

Every section of `TrackShift AGENTS.md` that names a model, fitted component, or the compute the models plug into, mapped to an inventory ID and an owner. Use this table to confirm nothing was dropped.

| §Ref | Topic | Inventory | Owner |
|---|---|---|---|
| §1 | Energy shadow price λ_E | M22 | R |
| §9 | Practice lap labels | M01 | T |
| §11 | Canonical telemetry fields (race-control, pit state, weather raw channels, fuel/ERS estimates, source-gated extensions) | M02, M14, M33, M34, M31 | R, T, T, T, T |
| §12 | Derived features (geometry, track-relative weather, race-context flags, pair-aware, regulated aero/energy context) | M03, M33, M02, M06, M07, M14 | T, T, R, R, T, T |
| §13 | Segment representation (incl. sector / corner geometry, weather at entry, eligibility) | M03, M33 | T |
| §14 | Baseline hierarchy | M04 | T |
| §15 | Combining teams (global → team → driver → track → session) | M04, M13 | T / both |
| §16 | Dynamic pair definition | M05 | R |
| §17 | Pairwise features (relative speed, gap trends, tyre/fuel/ERS deltas, weather) | M06 | R |
| §18 | Battle episodes | M05, M29 | R |
| §19 | Overtake-opportunity dataset (DETECTION / ACTIVATION / BRAKING checkpoints, `passed_by_outcome_horizon`) | M07, M10 | T |
| §20 | 2026 Overtake state machine | M20 | T |
| §20.1 | Speed-dependent electrical power envelope (curve in config, single evaluator, deploy_level semantics, λ_E consequences) | M18, M19, M22 | T, T, R |
| §20.2 | Override partially observable (envelope-separation discriminator) | M35 | T |
| §21 | Event rule configuration | M18 | T |
| §22 | Overtake-derived strategic features, eligibility probability | M21 | T |
| §23 | Counterattack modelling | M23 | R |
| §24 | Rival-state dataset (normal-race-eligible, expanded features) | M08 | R |
| §25 | Rival model benchmark (HMM, HSMM, gradient-boosted rolling-window classifier, GRU, TCN, Transformer) | M09, M09b | R |
| §26 | Pass-probability benchmark (LogReg, CatBoost/LightGBM, XGBoost, MLP) | M10 | T |
| §27 | Probability calibration (Platt, isotonic) | M11 | T |
| §28 | Energy twin | M14 | T |
| §28.1 | Envelope as twin calibration diagnostic, never a clamp | M14, M15 | T |
| §29 | Physics calibration hierarchy (with required controls) | M15, M34 | T |
| §30 | Segment-time / energy model | M16 | T |
| §31 | Dynamic programming (deploy_level as fraction of the speed-dependent cap; accounting on delivered energy) | M22 | R |
| §32 | Rule engine (owns `max_electrical_power_kw`) | M19 | T |
| §33 | Planner and baselines | M24, M25 | R |
| §35 | Feature-group ablation | M28 | T harness, both run |
| §37 | Race-context labels | M02 | R |
| §38 | Tyre and fuel context (degradation proxy, causal fuel estimate) | M30, M34 | T |
| §39 | Weather (raw channels retained, track-relative derivation) | M33 | T |
| §40 | Leakage-safe splitting | M29 | R |
| §41 | Regulation-era shift | M13 | both (per chain) |
| §42 | Uncertainty (pass ensemble, rival distribution, twin draws, P(eligible), planner CVaR) | M12, M10/C10, M17, M21, M24 | T, R, T, T, R |
| §44 | LIVE_SAFE / OFFLINE_ONLY | enforced in C4, C10, registry | both |
| §45 | Dataset registry | M31 | T scaffold, both |
| §46 | Feature registry (availability scope, source-gated, decision checkpoint, uncertainty field) | M31 | T scaffold, both |
| §47 | Proposed processed datasets | C1, C2, C6, C7, C8 + `telemetry_20m` (exists) | see §5 |
| §53 | Reproducibility, feature-schema locking | §6.4 manifest | both |
| §54 | Storage and compute | §1 | both |
| §55 | Evaluation by component | §7.4 validation reports | both |
| §56 | Simulator | M26, M27 | R |
| §59 | Phases 3–16 | all rows above | — |

Verification steps for whoever reviews this file:

- [ ] Every model family named in §25 appears under M09 (HMM, HSMM, gradient-boosted rolling-window classifier, GRU, TCN, Transformer).
- [ ] Every model family named in §26 appears under M10 (Logistic, CatBoost/LightGBM, XGBoost, MLP).
- [ ] Every calibration option in §27 appears under M11.
- [ ] Every physics level in §29 appears under M15 (pure analytical physics, global calibrated, team-specific calibrated, event-specific calibrated, physics + ML residual).
- [ ] Every ΔE→Δt candidate in §30 appears under M16 (analytical, linear/ridge, GBM, physics + residual, neural regressor).
- [ ] Every planner baseline in §33 appears under M25 (greedy attack, longest straight, lap-time only, DP, beam + DP, oracle).
- [ ] Every era strategy in §41 appears under M13.
- [ ] Every uncertainty source in §42 has an owner in the checkpoint table.
- [ ] No chain in §3 has items owned by two people.
- [ ] Every hand-off in §4 has a contract in §5.
- [ ] Every contract in §5 declares provenance and failure behaviour.
- [ ] Every module in §7.1 maps to an inventory ID.
- [ ] All three §19 decision checkpoints (DETECTION, ACTIVATION, BRAKING) appear under M07 and M10, and C4 requires `decision_checkpoint`.
- [ ] Every §11 fuel/ERS estimate field (`fuel_load_kg_est`, `fuel_load_uncertainty_kg`, `ers_deploy_power_est_kw`, `ers_harvest_power_est_kw`, `ers_soc_est_mj`, `ers_deploy_budget_remaining_est_mj`, `ers_harvest_budget_remaining_est_mj`) has a producer (M14 or M34) and is never OBSERVED.
- [ ] `normal_race_model_eligible` is produced once (M02 / C7) and named as a filter on M04, M05, M07, M08, M15.
- [ ] Every §12 track-relative weather field has a producer (M33).
- [ ] Every §46 registry field (availability scope, source-gated, decision checkpoint, uncertainty field) appears under M31.
- [ ] The power envelope exists as a curve in M18 config and is evaluated in exactly one place (M19); no module holds an envelope constant.
- [ ] Every §11 ERS field (`ers_deploy_power_est_kw`, `ers_harvest_power_est_kw`, `ers_energy_used_est_mj`, `ers_energy_harvested_est_mj`, `ers_soc_est_mj`, `ers_soc_uncertainty_mj`, `ers_deploy_budget_remaining_est_mj`, `ers_harvest_budget_remaining_est_mj`, `ers_mode_inferred`, `overtake_available`, `override_active_inferred`) has a producer and the correct provenance — `overtake_available` is RULE, the rest INFERRED/SIMULATED.
- [ ] C3 exposes `max_electrical_power_kw` and `legal_actions` takes `speed_kmh`.
- [ ] M22's energy accounting uses delivered power, not the requested deploy level.
