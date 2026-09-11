# TrackShift Model Ownership and Integration Plan

Companion to `TrackShift AGENTS.md`. That file is the engineering contract and is not modified by this plan. This file records **who builds which model, on what compute, and how the two halves join**. Section references (§N) point to `TrackShift AGENTS.md`.

Training procedures are deliberately out of scope here. Each model gets its own design note when its phase starts.

The UI-facing surface of every model listed here is specified in `API.md`. `MODELS.md` says who builds what; `API.md` says what the frontend can call.

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
| M02 | Race-context labeller (CLEAN_AIR / FOLLOWING / CLOSE_FOLLOWING / ATTACKING / DEFENDING / TRAFFIC / SAFETY_CAR / VSC / YELLOW / PIT_IN / PIT_OUT / WET / DRY) | §37 | 3 | deterministic, inferred, or hybrid |
| M03 | Track segmentation (braking onset, throttle return, FIA lines, zone boundaries; 30–40 segments/lap) | §13 | 4 | deterministic algorithm *(not ML)* |
| M04 | Driver / team / field segment baselines (median residuals) | §14, §15 | 5 | statistical |
| M05 | Dynamic pair assignment and battle-episode extraction | §16, §18 | 6 | deterministic *(not ML)* |
| M06 | Pairwise feature derivation (Δv, Δa, Δt, closing rate, …) | §17 | 6 | feature engineering |
| M07 | Overtake-opportunity dataset (one row per opportunity) | §19 | 7 | dataset producer |
| M08 | Rival-state feature dataset (one row per battle segment) | §24 | 8 | dataset producer |
| M09 | Rival-state model benchmark: HMM, HSMM, gradient-boosted rolling-window classifier, GRU, TCN, small temporal Transformer | §25 | 10 | learned, benchmark track |
| M09b | Synthetic labelled-trajectory generator for rival state-recovery evaluation | §25, §55, §57 | 10 | simulation for evaluation |
| M10 | Pass-probability benchmark: Logistic Regression, CatBoost or LightGBM, XGBoost, optional small MLP | §26 | 9 | learned, benchmark track |
| M11 | Probability calibration: uncalibrated vs Platt/sigmoid vs isotonic | §27 | 9 | post-hoc fit |
| M12 | Pass-model uncertainty: ensemble spread | §42 | 9 | ensemble |
| M13 | Regulation-era handling: regulation-era feature, historical pretraining/prior, 2026 recalibration, domain weighting, separate historical and 2026 models followed by comparison | §41 | 9, 10 | cross-cutting on M09 and M10 |
| M14 | Energy twin (longitudinal power balance → P_K, tagged SIMULATED) | §28 | 11 | physics model |
| M15 | Physics calibration hierarchy: pure analytical physics → global calibrated physics → team-specific calibrated physics → event-specific calibrated physics → physics + ML residual correction | §29 | 11 | fitted physics + learned residual |
| M16 | Segment-time / energy model (ΔE → Δt): analytical, linear/ridge, gradient boosting, physics + residual, small neural regressor | §30 | 11 | learned, benchmark track |
| M17 | Physics uncertainty: parameter draws, confidence ranges | §42 | 11 | uncertainty propagation |
| M18 | Event rule configuration (`config/rules/2026/*.yaml`, provenance RULE) | §21 | 12 | configuration *(not ML)* |
| M19 | Deterministic rule engine (legal action set, envelopes, limits, race-control state) | §32 | 12 | deterministic *(not ML)* |
| M20 | 2026 Overtake state machine (Detection Line → armed → Activation Line → envelope) | §20 | 12 | state machine *(not ML)* |
| M21 | Eligibility probability and Overtake-derived features (eligibility_margin, P(g_det < g_thr), energy_required_to_unlock, …) | §22 | 12–13 | probabilistic derived features |
| M22 | Dynamic programming and energy shadow price λ_E(k, e, g, ε) | §1, §31 | 13 | optimisation *(not ML)* |
| M23 | Counterattack / repass valuation inside the two-lap value function | §23 | 13 | part of DP value |
| M24 | Planner (beam search for near-term uncertainty + DP terminal value) | §33 | 14 | optimisation |
| M25 | Planner baselines: greedy attack, longest-straight deployment, lap-time-only, DP, beam + DP, oracle rival-state policy | §33 | 15 | heuristic policies |
| M26 | Simulator (same segments, same rule engine, same pass API, seeded episodes, uncertainty draws) | §56 | 15 | simulation |
| M27 | Explicit rival policies for the simulator | §56 | 15 | policy definitions |
| M28 | Feature-group ablation harness (tyre, weather, team id, driver id, rolling trends, geometry, race context) | §35 | 9–11 | evaluation tooling |
| M29 | Leakage-safe splitter (by event / battle_id / weekend / track / year; year-forward and leave-one-out designs) | §40 | 6+ | evaluation tooling |
| M30 | Tyre-normalised pace features, fuel-proxy labelling | §38 | 5–7 | feature engineering |
| M31 | Dataset registry and feature registry (`config/data_registry.yaml`, `config/feature_registry.yaml`) | §45, §46 | all | configuration |
| M32 | UI, integration, validation, demo | §59 Phase 16 | 16 | integration |

---

## 3. Ownership split

Models are grouped into **chains**. A chain is a sequence where each item is a direct follow-up of the previous one (its input is the previous item's output, or it post-processes it). Chains are never split between owners.

### 3.1 Owner B — Tanveer (CPU + remote A6000)

| Chain | Items | Compute |
|---|---|---|
| **Chain R — Rules** | M18 → M20 → M19 → M21 | CPU. Pure config, state machine, and deterministic logic. |
| **Chain P — Pass probability** | M07 → M10 → M11 → M12 → M13 (pass side) | CPU for LogReg / LightGBM / CatBoost / XGBoost / isotonic / Platt. Optional MLP: A6000 (persistent access), artifact verified on CPU before merge. |
| **Chain E — Energy / physics** | M14 → M15 → M16 → M17 | CPU for analytical physics, least-squares calibration, ridge, gradient boosting, residual fits. A6000 for the small neural regressor candidate in M16 and for large parameter-draw ensembles; every artifact verified on CPU before merge. |
| **Foundations owned** | M01, M03, M04, M30 | CPU. Segmentation and baselines are median/threshold work over Parquet. |

Why this grouping:

- Chain R is the prerequisite for Chain P (opportunities need Detection and Activation Lines) and for Chain E's ΔE→Δt (legal envelopes bound the action set). One owner keeps line definitions, envelopes, and eligibility semantics consistent.
- Chain P is tree-model work. CPU is the natural home; §26 puts calibration quality above raw accuracy, and calibration is a CPU fit.
- Chain E is physics first, ML second (§29). Fitting `mass`, `CdA`, rolling resistance, efficiency, and an ICE map is scipy work.
- M01 (Practice lap classification) belongs with Chain E because Practice is the physics-calibration session (§9). M03 segmentation is physics-derived (braking onset, throttle return). M04 baselines are a per-segment follow-up of M03.

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
   M14/M15/M16/M17 energy (T)                   |
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
| `tyre_compound`, `tyre_life`, `stint` | —, laps, — | OBSERVED |

Failure: a lap that cannot be segmented is written to a rejection manifest, never silently dropped (§49).

**C2. Baselines** — `data/processed/driver_segment_baselines/`, `data/processed/team_segment_baselines/`, `data/processed/field_segment_baselines/`

Keyed by `(track, segment_id, [driver|team])`; one column per baselined quantity (segment time, exit speed, brake point, throttle commitment, top speed) holding the median and sample count `n`. Rows with `n` below a declared minimum carry `baseline_valid = false` rather than being omitted.

**C3. Rule engine API** — `src/trackshift/rules/api.py`

```text
legal_actions(state, event_rules) -> ActionSet
    state:       segment_id, energy_e, gap_g, eligibility_eps, race_control_state
    returns:     the set of permitted (deploy_level, lift_amount) pairs
    guarantee:   illegal actions are absent from the set, never low-scored (§31)

eligibility(state, event_rules) -> EligibilityResult
    returns:     armed: bool, p_eligible: float in [0,1], eligibility_margin_s: float
    provenance:  RULE for thresholds, INFERRED for p_eligible when gap is projected
```

Failure: unknown event or missing rule key raises; it never defaults to "Overtake enabled".

**C4. Pass probability API** — `src/trackshift/pass_model/api.py`

```text
predict_pass(features: OpportunityFeatures) -> PassPrediction
    p_pass:            calibrated float in [0,1]
    ensemble_spread:   float (std across ensemble members)
    model_version:     str
    feature_schema_id: str  (rejects a differently ordered matrix, §53)
    provenance:        INFERRED
```

Input features are `LIVE_SAFE` only (§44). The model ships with `feature_schema.json`; a mismatch raises.

**C5. Energy twin / segment-time API** — `src/trackshift/twin/api.py`

```text
segment_time(segment_id, deploy_level, lift_amount, context) -> SegmentTimeEstimate
    t_s:          float
    t_draws_s:    array of samples from parameter uncertainty (§42)
    delta_e_kj:   electrical energy consumed, tagged SIMULATED
    provenance:   SIMULATED

energy_state(telemetry_window) -> EnergyEstimate
    e_kj, e_low_kj, e_high_kj; provenance SIMULATED
```

Never labelled OBSERVED (§28, §58).

**C6. Opportunity and rule-derived features** — appended to `overtake_opportunities` and available to Chain S: `gap_at_detection`, `eligibility_margin`, `projected_gap_at_detection`, `probability_eligible`, `energy_required_to_unlock`, `distance_detection_to_activation`, `distance_activation_to_brake`.

### 5.2 Rishabh → Tanveer

**C7. Race-context labels** — column set on `telemetry_20m` and `segments`: `race_context` (enum from §37) plus `race_context_provenance` ∈ {DERIVED, INFERRED}. Chain E filters Practice laps and Chain P filters opportunities on these.

**C8. Battle episodes and pairwise rows** — `data/processed/battle_episodes/`, `data/processed/pairwise_segment_features/`

Battle key `battle_id` (format `YYYY_EVT_Session_ATT_DEF_BattleNN`), `attacker`, `defender`, `start_lap`, `end_lap`, `duration_segments`, `duration_s`, `minimum_gap`, `maximum_closing_rate`, `detection_opportunities`, `pass_attempted`, `pass_completed`. Chain P builds M07 by joining opportunities onto these.

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
| **I0** | Scaffold registries (M31), rules config skeleton (M18) | Splitter (M29), race-context labels (M02) | contracts C7, C9 |
| **I1** | Segmentation (M03) → freeze `segment_id` | — | everything |
| **I2** | Baselines (M04), practice classifier (M01) | Pairs + battles (M05), pairwise features (M06) | C1, C2, C8 |
| **I3** | Rule engine + state machine + eligibility (M19, M20, M21) | Rival-state features (M08) | C3, C6 |
| **I4** | Opportunities (M07) → pass benchmark (M10, M11, M12, M13) | Rival benchmark (M09, M09b, M13) | C4, C10 |
| **I5** | Energy twin → calibration → ΔE→Δt (M14–M17) | DP + λ_E (M22, M23) using stub C4/C5 | C5 |
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
        planner_report.md               (R)  P(ahead), final energy, rule violations, CVaR, latency
        ablation_report.md              (both)
    dp/
        value_tables/<event>/           (R)  V(k,e,g,eps) and lambda_E
```

### 7.5 Tests (§52)

```text
tests/
    test_segmentation.py                (T)
    test_baselines.py                   (T)
    test_lap_classifier.py              (T)
    test_race_context.py                (R)
    test_pairing.py                     (R)  driver-ahead, battle start/end, switching after pass, no self-pair
    test_splits.py                      (R)  no battle in two folds
    test_rules.py                       (T)  below / at / above every threshold; Detection, Activation, disabled
    test_pass_api.py                    (T)  feature-order rejection, calibration bounds
    test_twin.py                        (T)  physical constraints, SIMULATED tagging
    test_rival_api.py                   (R)  distribution sums to 1, merged-state path
    test_dp.py                          (R)  zero illegal actions, energy accounting, boundary conditions
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

---

## 9. Coverage checkpoint

Every section of `TrackShift AGENTS.md` that names a model, fitted component, or the compute the models plug into, mapped to an inventory ID and an owner. Use this table to confirm nothing was dropped.

| §Ref | Topic | Inventory | Owner |
|---|---|---|---|
| §1 | Energy shadow price λ_E | M22 | R |
| §9 | Practice lap labels | M01 | T |
| §13 | Segment representation | M03 | T |
| §14 | Baseline hierarchy | M04 | T |
| §15 | Combining teams (global → team → driver → track → session) | M04, M13 | T / both |
| §16 | Dynamic pair definition | M05 | R |
| §17 | Pairwise features | M06 | R |
| §18 | Battle episodes | M05, M29 | R |
| §19 | Overtake-opportunity dataset | M07 | T |
| §20 | 2026 Overtake state machine | M20 | T |
| §21 | Event rule configuration | M18 | T |
| §22 | Overtake-derived strategic features, eligibility probability | M21 | T |
| §23 | Counterattack modelling | M23 | R |
| §24 | Rival-state dataset | M08 | R |
| §25 | Rival model benchmark (HMM, HSMM, gradient-boosted rolling-window classifier, GRU, TCN, Transformer) | M09, M09b | R |
| §26 | Pass-probability benchmark (LogReg, CatBoost/LightGBM, XGBoost, MLP) | M10 | T |
| §27 | Probability calibration (Platt, isotonic) | M11 | T |
| §28 | Energy twin | M14 | T |
| §29 | Physics calibration hierarchy | M15 | T |
| §30 | Segment-time / energy model | M16 | T |
| §31 | Dynamic programming | M22 | R |
| §32 | Rule engine | M19 | T |
| §33 | Planner and baselines | M24, M25 | R |
| §35 | Feature-group ablation | M28 | T harness, both run |
| §37 | Race-context labels | M02 | R |
| §38 | Tyre and fuel context | M30 | T |
| §40 | Leakage-safe splitting | M29 | R |
| §41 | Regulation-era shift | M13 | both (per chain) |
| §42 | Uncertainty (pass ensemble, rival distribution, twin draws, P(eligible), planner CVaR) | M12, M10/C10, M17, M21, M24 | T, R, T, T, R |
| §44 | LIVE_SAFE / OFFLINE_ONLY | enforced in C4, C10, registry | both |
| §45 | Dataset registry | M31 | T scaffold, both |
| §46 | Feature registry | M31 | T scaffold, both |
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
