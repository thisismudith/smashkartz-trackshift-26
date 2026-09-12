# TrackShift: Rishabh's Build Checkpoints

Execution plan for Owner A, Rishabh: Chain S (rival state), Chain V (value and decision), and shared foundations M02, M05, M06, and M29.

- **TrackShift AGENTS.md**: engineering contract and non-negotiable data, causality, regulation, and claim rules.
- **MODELS.md**: ownership, inventory IDs M01 to M34, and contracts C1 to C10.
- **API.md**: UI-facing request and response schema.
- **CHECKPOINTS_TANVEER.md**: Owner B's rules, pass, energy, and physics work.
- **CHECKPOINTS_RISHABH.md**: this Owner A implementation plan.

Written against the strategic-state contract at commit 7d2f5fd. This plan consumes C1 to C6, produces C7 to C10, and owns the value, planner, and simulator side. It does not duplicate Tanveer's work.

---

## 0. How to use this file

Each checkpoint uses the same structure.

| Field | Meaning |
|---|---|
| Goal | The one thing that must be true when it is complete |
| Depends on | Required earlier checkpoints or external contracts |
| Inputs / outputs | Exact contract boundary and provenance |
| Steps | Concrete implementation work |
| Acceptance gates | Conditions required before moving ahead |
| If output is bad | Likely cause and corrective action |
| Deliverables | Files and artifacts that must exist |

### Non-negotiable rules

1. Do not train, calibrate, or tune on the 2026 British Grand Prix. It is the demo event and final held-out test.
2. Do not emit a live feature that uses a later decision checkpoint, centered window, realized future gap, future tyre degradation, future deployment, future braking point, or pass outcome.
3. Every modelled value carries provenance. Energy is SIMULATED, fuel is INFERRED, regulations are RULE, and rival tactical state is INFERRED.
4. Every artifact records git commit, source datasets, configuration, feature schema, rule configuration version, seed, and CPU-inference verification.
5. All code runs on CPU. The RTX 4080 may accelerate neural rival benchmarks and vectorised simulation, but is not a runtime requirement.
6. C3, C4, and C5 are consumed through their public api.py contracts only. Do not import Tanveer's implementation internals.

### Progress tracker

| CP | Item | IDs | Status |
|---|---|---|---|
| 00 | Contract sync and environment | shared | ☐ |
| 01 | Race-context labels and eligibility gate | M02, C7 | ☐ |
| 02 | Leakage-safe splitter | M29, C9 | ☐ |
| 03 | Dynamic pairs and battle episodes | M05, C8 | ☐ |
| 04 | Causal pairwise features | M06, C8 | ☐ |
| 05 | Rival-state feature dataset | M08 | ☐ |
| 06 | Synthetic labelled trajectories | M09b | ☐ |
| 07 | Rival-state benchmark | M09, C10 | ☐ |
| 08 | Rival-side regulation-era evaluation | M13 | ☐ |
| 09 | Strategic-state adapter and stubs | C3 to C6 | ☐ |
| 10 | Dynamic programming and shadow price | M22 | ☐ |
| 11 | Counterattack valuation | M23 | ☐ |
| 12 | Planner | M24 | ☐ |
| 13 | Planner baselines | M25 | ☐ |
| 14 | Simulator | M26 | ☐ |
| 15 | Explicit rival policies | M27 | ☐ |
| 16 | Routes, replay hand-off, release evidence | API.md | ☐ |

---

# CP-00: Contract sync and environment

**Goal:** Owner A can build against current public contracts and fail immediately when an upstream schema changes.

**Depends on:** the current main branch and the contract PR.

**Inputs / outputs:** read-only contract documents. No model artifact is produced here.

### Steps

1. Update the portable environment from repository requirements. Keep local paths, cache locations, and GPU settings outside committed files.
2. Read C1 to C10 before implementation. Create public module boundaries for race context, pairing, battles, pairwise features, rival state, value, planner, and simulation.
3. Add C1 to C6 schema fixtures, including unavailable modelled fields and normal-race-ineligible rows.
4. Define one shared StrategicState type matching API.md. It groups reference, Energy Store distribution and flows, tyre state, time and distance gap, relative speed, relative acceleration, gap rate, Overtake/race-control state, power envelope, rival belief, and uncertainty.
5. Reject missing required state fields with a clear contract error. Never invent defaults from future telemetry.

### Acceptance gates

- All Owner A modules import on CPU.
- C1, C3, C4, and C5 fixtures validate at the public boundary.
- Deliberately missing or reordered fields raise clear errors.
- No code assumes a GPU or personal path.

### Deliverables

**tests/fixtures/contracts/**, Owner A public module skeletons, shared StrategicState adapter.

---

# CP-01: Race-context labels and eligibility gate

**Goal:** produce C7 so every downstream model can distinguish normal racing from safety, pit, and control-state effects.

**Depends on:** Phase 2 lake.

**Inputs / outputs:** telemetry rows, lap status, and race-control messages in. C7 fields on telemetry_20m and segments out.

### Steps

1. Normalize race control to GREEN, YELLOW, DOUBLE_YELLOW, VSC, SC, RED, or UNKNOWN.
2. Normalize pit state to ON_TRACK, PIT_IN, PIT_LANE, PIT_OUT, or UNKNOWN.
3. Derive safety-car flags, transition flags, and green-flag elapsed time.
4. Derive normal_race_model_eligible. It is true only for on-track green-flag rows with no pit state, safety condition, restriction, or unknown control state.
5. Produce race_context deterministically or with a documented hybrid rule. A yellow-flag slowdown must never become CONSERVING or DERATING.
6. Make race-control and pit transitions hard boundaries for windows, pairs, and battles.

### Acceptance gates

- Every non-green or pit row is ineligible.
- No rolling feature, battle, or sequence crosses a transition flag.
- A full-lap audit reconciles state changes with race-control messages and lap status.
- C7 fields are registered with units, provenance, and availability.

### If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| Green rows appear ineligible | Session-time alignment error | Test lap-relative and session-relative clocks separately |
| Safety pace reaches rival features | Gate applied after feature construction | Filter and split before rolling windows |
| Unknown status becomes green | Missing-value shortcut | Preserve UNKNOWN and make it ineligible |

### Deliverables

**src/trackshift/race_context.py**, **scripts/features/build_race_context.py**, **tests/test_race_context.py**, C7 table and manifest.

---

# CP-02: Leakage-safe splitter

**Goal:** implement C9 once so Chain S and Tanveer's Chain P share the same split definitions.

**Depends on:** CP-01. Battle-level splitting is finalized after CP-03.

**Inputs / outputs:** rows with event, track, year, and optionally battle ID in. Persistent split assignment out.

### Steps

1. Implement make_split(rows, unit, design, seed) in **src/trackshift/data/splits.py**.
2. Support battle ID, event, track, year, leave-one-event-out, and year-forward designs.
3. Add the British Grand Prix hold-out guard. It raises if training or calibration receives the demo event.
4. Persist split assignments. Training scripts must never create private random splits.
5. Require every model manifest to reference its split assignment version.

### Acceptance gates

- No battle ID appears in more than one fold.
- Event and year-forward splits are deterministic for a seed.
- British Grand Prix is absent from training and calibration.
- Tanveer can consume C9 directly.

### Deliverables

**src/trackshift/data/splits.py**, **tests/test_splits.py**, **scripts/data/build_splits.py**, split artifacts.

---

# CP-03: Dynamic pairs and battle episodes

**Goal:** produce C8 battle episodes with role switching and hard boundaries.

**Depends on:** CP-01 and C1 segments.

**Inputs / outputs:** normal-race-eligible segments plus driver-ahead observations in. Battle episodes and pairwise keys out.

### Steps

1. Assign an attacker to the immediate car ahead only. Never generate every driver pair.
2. Start a battle after the declared close-following criterion and end it on pair switch, pass, pit transition, race-control transition, or session end.
3. A pass terminates the old directed relation. The previous defender may later become the new attacker.
4. Store bounded_by, pass_attempted, pass_completed, Detection opportunities, minimum gap, and maximum closing rate.
5. Retain excluded raw rows for audit but do not include them in initial model battles.

### Acceptance gates

- No self-pair is possible.
- Episodes never cross race-control or pit transitions.
- Directed relation changes correctly after a pass.
- Every battle ID is stable and unique.
- Battle table is usable as C9 split unit.

### Deliverables

**src/trackshift/features/pairing.py**, **src/trackshift/features/battles.py**, **scripts/features/build_battles.py**, **tests/test_pairing.py**, C8 artifacts.

---

# CP-04: Causal pairwise features

**Goal:** produce M06 with explicit gap dynamics, not one ambiguous speed-gap quantity.

**Depends on:** CP-03, C1, C2, C7, and C5 when energy/fuel estimates exist.

**Inputs / outputs:** aligned attacker and defender segment rows in. Pairwise feature rows out.

### Steps

1. Keep time_gap_s, distance_gap_m, relative speed, relative acceleration, and gap rate separate.
2. Derive rolling trends only from contemporaneous or trailing observations.
3. Add attacker-defender deltas for tyre context, causal fuel and energy estimates with uncertainty, speed, acceleration, braking, residual pace, and track-relative weather.
4. Register every feature with causal status, checkpoint scope where relevant, uncertainty field, and interaction group.
5. Run the truncation test on every live-safe rolling feature.

### Acceptance gates

- A feature on a truncated battle equals the full-battle value at the same retained row.
- Units and signs are documented. Positive relative speed means attacker faster.
- No future segment, pass label, or outcome distance enters the matrix.
- Time and distance gaps are never silently substituted.

### Deliverables

**src/trackshift/features/pairwise.py**, **scripts/features/build_pairwise_features.py**, **tests/test_pairwise.py**, C8 pairwise table.

---

# CP-05: Rival-state feature dataset

**Goal:** create M08, one causal normal-race-eligible feature row per battle segment.

**Depends on:** CP-04, C2, C5, C6, C7, C9.

**Inputs / outputs:** C8 rows and baseline, rule, and twin contracts in. rival_state_features out.

### Steps

1. Join driver, team, and field residuals without replacing missing baselines with zero.
2. Join tyre context, energy/fuel uncertainty, gap dynamics, weather, geometry, and race context.
3. Keep latent tactical state absent from inputs. It is inferred, not observed.
4. Add state-estimation reliability flags so unknown is not confused with low confidence.
5. Write feature schema, producer manifest, split reference, and event-level row counts.

### Acceptance gates

- All rows are normal-race eligible.
- Each feature exists at or before segment entry.
- Source-gated sensors are absent unless registry availability is documented.
- No raw driver number, team colour, raw coordinate, or arbitrary timestamp appears.

### Deliverables

**src/trackshift/features/rival_state_features.py**, **scripts/features/build_rival_features.py**, processed feature dataset, tests, manifest.

---

# CP-06: Synthetic labelled trajectories

**Goal:** provide M09b ground truth for state-recovery evaluation without claiming real teams expose tactical labels.

**Depends on:** CP-05.

**Inputs / outputs:** declared dynamics and state-transition parameters in. Labelled synthetic battle sequences out.

### Steps

1. Generate CONSERVING, BALANCED, DEPLOYING, and DERATING trajectories.
2. Make emissions depend on pace residual, throttle/braking, relative speed, gap rate, tyre state, and energy state.
3. Randomize event, weather, segment type, noise, and state dwell duration.
4. Include merged-state cases where CONSERVING and DERATING are intentionally indistinguishable.
5. Freeze separate seed sets for regression and benchmark evaluation.

### Acceptance gates

- Generator records true hidden state and all parameters.
- State dwell and transition distributions are non-degenerate.
- Simple scenarios recover above chance.
- Synthetic metrics are never reported as real tactical truth.

### Deliverables

**src/trackshift/rival/synthetic.py**, **tests/test_rival_synthetic.py**, synthetic validation artifacts.

---

# CP-07: Rival-state benchmark

**Goal:** select the simplest reliable M09 model for probabilistic rival belief.

**Depends on:** CP-05, CP-06, C9.

**Inputs / outputs:** rival feature sequences and split assignments in. C10 distribution, artifacts, and report out.

### Steps

1. Implement HMM and HSMM as interpretable baselines.
2. Add GBM rolling-window classification as nonlinear comparison.
3. Add GRU and TCN after reproducible baselines. Add Transformer only for a documented sequence-length failure.
4. Train every candidate with identical splits and feature availability.
5. Select state count by recovery, predictive likelihood, calibration, stability, latency, and interpretability. Merge states when they cannot be separated.
6. Expose rival_state(battle_segments) through public api.py.

### Acceptance gates

- Distribution sums to one and lists merged states.
- Synthetic recovery, next-segment prediction, likelihood, calibration, stability, and latency are reported.
- Chosen model loads and infers on CPU.
- Neural candidates are not retained merely for sophistication.

### If output is bad

| Symptom | Cause | Fix |
|---|---|---|
| CONSERVING and DERATING swap | Signals not identifiable | Merge the states and document it |
| Neural model wins only in sample | Leakage or overfit | Inspect split and causal windows, then prefer simpler model |
| Posterior flips under noise | Overreactive filtering | Regularize transitions or smooth causally |

### Deliverables

**src/trackshift/rival/api.py**, HMM, HSMM, GBM, neural modules, training and evaluation scripts, rival artifacts, rival report.

---

# CP-08: Rival-side regulation-era evaluation

**Goal:** determine whether 2022 to 2025 behaviour improves 2026 rival-state inference without conflating DRS and Overtake.

**Depends on:** CP-07.

### Steps

1. Compare era feature, historical pretraining, 2026 recalibration, domain weighting, separate models, and 2026-only baseline.
2. Attach rule configuration version to every 2026 row.
3. Treat 2026 configuration changes as version drift, distinct from historical era shift.
4. Select using held-out 2026 predictive metrics, calibration, and stability.

### Acceptance gates

- Historical DRS is never supplied as 2026 Overtake state.
- Results report N and rule configuration by evaluation split.
- If 2026-only wins, retain it and state that history did not help.

### Deliverables

**src/trackshift/rival/era.py**, comparison in rival report, selected strategy in manifest.

---

# CP-09: Strategic-state adapter and dependency stubs

**Goal:** let Chain V start before C3 to C6 are complete without cementing fake semantics.

**Depends on:** CP-00 and CP-04. Real C3 to C6 replace stubs as they land.

**Inputs / outputs:** C7, C8, C9, C10 plus public stub contracts in. Typed StrategicState and transition adapters out.

### Steps

1. Convert a battle step to API.md StrategicState.
2. Implement deterministic stubs for legal actions, pass probability, eligibility, and segment transition. Every stub is marked and cannot enter final evaluation.
3. Define versioned state discretization for energy, tyre state, gaps, relative speed, gap rate, Overtake state, power regime, rival belief, uncertainty, and horizon.
4. Preserve uncertainty and correlation from C5.
5. Require rule snapshot/configuration version in every value or planner state.

### Acceptance gates

- State converts to and from API shape without loss.
- Offline-only and future-derived fields are rejected.
- Stubs set STUB_RESPONSE and cannot enter final bundle.
- C3 filters illegal actions before value evaluation.

### Deliverables

**src/trackshift/value/state.py**, **tests/test_strategic_state.py**, stub fixtures, discretization config.

---

# CP-10: Dynamic programming and shadow price

**Goal:** implement M22, legal-action dynamic programming over the minimum sufficient strategic state.

**Depends on:** CP-09 and C3, C4, C5. Stubs are valid for development, not final validation.

**Inputs / outputs:** StrategicState, legal actions, causal transition, rival belief, and terminal utility in. Value table and shadow-price profile out.

### Steps

1. Begin with segment, energy, time gap, and Overtake eligibility.
2. Add tyre state, relative speed, gap rate, power/rule state, rival belief, uncertainty, and horizon only when each improves decision quality and remains tractable.
3. Evaluate value only on legal actions.
4. Estimate shadow price by finite energy perturbation at the same full state.
5. Support deterministic DP first, then expectation over uncertainty when it changes action.
6. Persist state grid, rule snapshot, model versions, and seed.

### Acceptance gates

- Zero illegal actions in every candidate set.
- Energy Store, deployment, harvest, and recharge budget account correctly through transitions.
- Shadow price changes for explainable opportunity or constraint changes.
- Small perturbations produce stable decisions unless reported otherwise.
- Demo grid runs on CPU.

### Deliverables

**src/trackshift/value/dp.py**, **src/trackshift/value/shadow_price.py**, **scripts/simulate/run_dp.py**, **tests/test_dp.py**, value tables.

---

# CP-11: Counterattack valuation

**Goal:** implement M23 so a completed pass is not terminal success.

**Depends on:** CP-10, C4, C6, C10.

### Steps

1. Define post-pass role switch.
2. Carry post-pass energy margin, tyre state, gap, relative speed, next Detection opportunity, rule state, and rival belief.
3. Estimate pass, remain-ahead, later rival eligibility, and repass contributions.
4. Explain outcomes as PASS_AND_SECURE, PASS_BUT_EXPOSED, NO_PASS_BUT_PROTECT, or NO_PASS_AND_LOSE.
5. Treat categories as explanations, not ground-truth labels.

### Acceptance gates

- A pass can lower terminal value when counterattack risk is credible.
- Role switch retains provenance and legal filtering.
- Controlled scenario can prefer a no-pass defensive policy.

### Deliverables

**src/trackshift/value/counterattack.py**, **tests/test_counterattack.py**, counterattack terms in value artifacts.

---

# CP-12: Planner

**Goal:** implement M24, translating value and uncertainty into an auditable strategy.

**Depends on:** CP-10, CP-11, C3, C4, C5, C10.

### Steps

1. Consume full StrategicState and obtain legal actions before scoring.
2. Use DP terminal value with optional beam search for near-term uncertainty.
3. Return segment plan with expected time gap, Energy Store state, lambda, power regime, and rule configuration version.
4. Record alternatives, expected value, regret, dominant mechanism, primary constraint, and decision stability.
5. Use only declared risk settings such as expected value or CVaR.

### Acceptance gates

- Every plan has zero rule violations.
- Alternatives are feasible.
- Dominant mechanism is traceable to decomposition.
- CPU latency meets replay and interactive API target.

### Deliverables

**src/trackshift/planner/beam.py**, planner evaluation script, **tests/test_planner.py**, planner artifact and report.

---

# CP-13: Planner baselines

**Goal:** implement M25 so the planner is measured against meaningful alternatives.

**Depends on:** CP-12.

### Steps

1. Implement greedy attack, longest-straight deployment, lap-time-only, DP, beam plus DP, and oracle-rival-state upper bound.
2. Give each the same legal actions, energy accounting, horizon, and rule snapshot.
3. Label oracle rival state as an upper bound, never deployable.
4. Compare terminal P(ahead), energy, rule violations, downside risk, latency, regret, and decision stability.

### Acceptance gates

- A baseline cannot bypass C3 or use future data.
- Results are stratified by segment, Overtake state, and uncertainty.
- Report where planner does not beat simpler strategies.

### Deliverables

**src/trackshift/planner/baselines.py**, tests, baseline table in planner report.

---

# CP-14: Simulator

**Goal:** implement M26 as controlled counterfactual evaluation, not proof of alternate real-race outcomes.

**Depends on:** CP-12, CP-13, C3, C4, C5, C10.

### Steps

1. Use the same segments, rule engine, pass API, and transitions as planner.
2. Freeze a decision-time state and change only the feasible future policy.
3. Propagate energy, tyre, gap, eligibility, pass, counterattack, and terminal position under the same sampled environment.
4. Seed every episode and record assumptions, policy, model versions, and rule snapshot.
5. Mark all results SIMULATED.

### Acceptance gates

- Same seed and input produces same trace.
- Policies start from same state and environment.
- No output is claimed as observed alternate race outcome.
- One-step simulator and planner agree on legality and transition.

### Deliverables

**src/trackshift/sim/simulator.py**, simulator script, **tests/test_simulator.py**, seeded episode artifacts.

---

# CP-15: Explicit rival policies

**Goal:** implement M27 for simulator and UI policy picker.

**Depends on:** CP-14.

### Steps

1. Define transparent policies such as DEFEND_CONSERVE, DEFEND_MIRROR, and ATTACK_GREEDY.
2. Give each name, one-line description, parameters, legal-action handling, and assumptions.
3. Route every policy through C3.
4. Provide the registry for GET /simulate/policies.

### Acceptance gates

- Names and descriptions are stable and UI-ready.
- Fixed-seed policies are deterministic unless documented otherwise.
- Unsupported policy name fails clearly.

### Deliverables

**src/trackshift/sim/rival_policies.py**, **tests/test_rival_policies.py**, policy fixtures.

---

# CP-16: Routes, replay hand-off, and release evidence

**Goal:** deliver Owner A API routes and replay-compatible outputs to Owner C without hidden dependencies.

**Depends on:** CP-07, CP-12, CP-14, CP-15. Timeline integration also requires C3, C4, and C5.

### Owner A routes

- GET /battles
- GET /battles/{battle_id}/timeline
- POST /rival/state
- GET /value/{event}/shadow_price
- POST /plan
- POST /simulate
- GET /simulate/policies

### Steps

1. Implement only API.md documented shapes. Change shape only through a contract PR.
2. Validate StrategicState for value, planner, and simulator input.
3. Include rule snapshot/configuration version, model versions, provenance, uncertainty, and causal cutoff in responses.
4. Timeline retains time and distance gap, energy flows, power envelope, tyre performance state, rival belief, eligibility uncertainty, and planner explanation.
5. Build replay JSON by calling routes in process, not a parallel serializer.
6. Assemble release evidence: component metrics, strategic ablations, no-stub manifest, zero rule violations, and held-out demo-event statement.

### Acceptance gates

- API.md examples validate through schemas.py.
- Replay and service shapes are identical for same state.
- Final bundle contains no stubs.
- UI marks counterfactual values modelled or simulated.
- British Grand Prix remains held out in every training and calibration manifest.

### Deliverables

Owner A route modules in **src/trackshift/serve/**, service tests, replay inputs for the bundle generator, planner report.

---

# Cross-checkpoint invariants

| Invariant | First checkpoint to enforce |
|---|---|
| No British Grand Prix rows in training or calibration | CP-02 |
| Race-control and pit transitions are hard boundaries | CP-01 |
| Every live feature passes truncation test | CP-04 |
| Battle rows are normal-race eligible | CP-03 and CP-05 |
| Time gap, distance gap, relative speed, relative acceleration, and gap rate remain distinct | CP-04 |
| Rival tactical state is inferred, never observed | CP-05 and CP-07 |
| C3 removes illegal actions before DP, planner, or simulator scoring | CP-09 and CP-10 |
| Energy Store, deployment, harvest, and recharge budget remain distinct | CP-10 |
| Counterfactuals use decision-time state and intervention, never future data | CP-09 and CP-14 |
| Rule configuration version is attached to value, plan, and simulation artifacts | CP-09 |
| Every output follows API.md provenance and uncertainty rules | CP-16 |
| Final artifacts run on CPU | every checkpoint |

---

# Open integration items

| Item | Owner | Required before |
|---|---|---|
| C1 segments, C2 baselines, C3 rules, C4 pass, C5 twin, C6 eligibility | Tanveer | final CP-10 to CP-16 validation |
| C7 race context, C8 battles, C9 splits, C10 rival belief | Rishabh | Tanveer CP-04 onward and end-to-end planning |
| Shared registry additions | both | each producing checkpoint |
| UI and final demo composition | Owner C | final release |
| Joint integration plan | Rishabh through `CHECKPOINTS_INTEGRATION.md` | end-to-end merge and rehearsal |
