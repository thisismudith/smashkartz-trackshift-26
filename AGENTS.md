# TrackShift 2026: E-Delta Energy & Overtake Intelligence
## Repository operating guide for humans and coding agents

> **Status date:** 11 September 2026  
> **Hackathon kickoff:** 12 September 2026  
> **Repository:** `CheekyRishi/Trackshift`  
> **Primary source of truth for project behavior:** this file  
> **Solution brief:** E-Delta Team Brief v2, 10 September 2026  
> **Team:** SmashKartz  
> **Problem space:** TrackShift 2026, PS1 Energy & Overtake Intelligence

---

# 0. READ THIS FIRST

This repository is being developed by multiple teammates and multiple coding agents in parallel under severe time pressure.

The main risk is not that one individual component is too difficult. The main risk is that parallel contributors make inconsistent assumptions about:

- telemetry schemas,
- units,
- 2026 regulations,
- battery-energy semantics,
- track segmentation,
- overtake eligibility,
- model inputs,
- state/action definitions,
- file paths,
- output schemas,
- or which modules are allowed to own which logic.

This file exists to prevent that.

## 0.1 Single source of truth

`AGENTS.md` is the authoritative project context and integration contract.

If `CLAUDE.md` exists, it should only point to this file. Do not maintain two independent copies of project instructions.

When a design decision changes:

1. update the relevant implementation,
2. update tests,
3. update this file if the change affects another subsystem.

Do not silently change a shared schema or interpretation.

## 0.2 Before modifying code

Every contributor or coding agent must:

1. inspect the repository tree,
2. read this file fully,
3. identify which subsystem they own,
4. inspect upstream contracts they consume,
5. inspect downstream contracts they produce,
6. avoid unrelated refactors,
7. preserve existing outputs unless the task explicitly changes the contract.

Do not redesign another teammate's subsystem while completing your own task.

## 0.3 Core project rule

**Never fabricate unavailable telemetry.**

Public telemetry does not expose the real F1 car battery SOC, real MGU-K electrical power, private energy-deployment maps, or rival strategy intent.

Those quantities must be explicitly tagged as:

- `OBSERVED`
- `DERIVED`
- `INFERRED`
- `SIMULATED`
- `RULE`

The system must never present an inferred or simulated quantity as if it were directly measured from the car.

---

# 1. WHAT WE ARE BUILDING

E-Delta is a segment-level race engineering decision engine for the 2026 Formula 1 energy and overtaking regime.

At every relevant track segment, the system should answer:

> How should the car deploy or harvest electrical energy here, given the current gap, estimated energy state, overtake eligibility, rival behavior, upcoming opportunities, regulations, uncertainty, and the value of race position over approximately the next two laps?

The output is not simply "use energy on the longest straight."

The central idea is that the value of electrical energy depends strongly on **where on the track and where in the tactical interaction it is spent**.

For example, energy spent shortly before an Overtake eligibility line may be strategically more valuable than a larger amount spent on an otherwise faster section of track.

The system quantifies this using a value function and an energy shadow price.

---

# 2. THE KEY IDEA: VALUE OF ENERGY, NOT JUST LAP TIME

The decision engine optimizes race-position value, not isolated lap time.

Let the strategic state at segment `k` be approximately:

\[
s_k = (k, e, g, \epsilon, \text{running energy constraints})
\]

where:

- `k` = track segment index
- `e` = estimated usable electrical-energy state on a discretized grid
- `g` = time gap to relevant rival, positive when our car is behind
- `epsilon` = whether Overtake is currently armed/eligible
- running energy constraints include the values necessary to enforce FIA energy accounting rules

The action is conceptually:

\[
a = (d, L)
\]

where:

- `d` = fraction of legal electrical deployment envelope
- `L` = lift-and-coast distance before the next braking zone

The initial design uses:

```text
d in {0, 1/4, 1/2, 3/4, 1}
L in {0 m, 50 m, 100 m}
```

The planner later maps these low-level controls to human tactical labels such as:

```text
HARVEST
HOLD
BALANCED
DETECTION_PUSH
ATTACK
DEFEND
```

These tactical names are labels attached **after** the action has been selected. They are not separate optimization actions.

---

# 3. VALUE FUNCTION AND ENERGY SHADOW PRICE

The central strategic object is a two-lap dynamic-programming value function.

Conceptually:

\[
V_k(e,g,\epsilon)
=
\max_{a \in A_{\text{legal}}}
\mathbb{E}
[
V_{k+1}(e', g', \epsilon')
]
\]

The terminal value is based primarily on race position, with a smaller value attached to remaining energy:

\[
V_T \approx 1[g < 0] + \beta e
\]

where:

- `g < 0` means our car is ahead,
- `beta` is a modeling choice for residual energy value.

The energy shadow price is then:

\[
\lambda_E(k,e,g,\epsilon)
=
\frac{
V(k,e+0.1,g,\epsilon) - V(k,e,g,\epsilon)
}{
0.1\text{ MJ}
}
\]

Interpretation:

> `lambda_E` measures how much strategic value an extra unit of energy has at this exact point, in this exact race state.

This is a major demo output.

The intended hero visualization is a track map colored by `lambda_E`.

Important: `lambda_E` is computed from the DP table. It must not be hand-drawn or manually assigned.

---

# 4. HIGH-LEVEL ARCHITECTURE

The intended end-to-end flow is:

```text
RAW PUBLIC TELEMETRY
        |
        v
VALIDATED CANONICAL LOADER
        |
        v
20 m DISTANCE RESAMPLING
        |
        v
TRACK SEGMENT CUTTER
        |
        +-----------------------------+
        |                             |
        v                             v
SEGMENT TIME MODEL               RIVAL FEATURES
        |                             |
        v                             v
ENERGY / PHYSICS TWIN            RIVAL HMM
        |                             |
        +--------------+--------------+
                       |
                       v
               CURRENT STATE ESTIMATE
                       |
             +---------+---------+
             |                   |
             v                   v
        RULE / LEGALITY      PASS MODEL
             MASK                |
             |                   |
             +---------+---------+
                       |
                       v
                DP VALUE TABLES
                       |
                       v
              SHORT BEAM PLANNER
                       |
                       v
       ACTION + EXPLANATION + PROVENANCE
                       |
                       v
                 DEMO / UI / RADIO
```

The intended live path should remain lightweight.

Heavy work such as DP table construction should be precomputed.

The target online planning latency from the solution brief is:

```text
p95 < 100 ms
```

---

# 5. MODELING PHILOSOPHY

This is **not** primarily a deep-learning project.

Do not introduce neural networks simply to make the project look more "AI."

The architecture intentionally combines:

- public telemetry,
- explicit preprocessing,
- physics,
- probabilistic inference,
- calibrated logistic regression,
- rule constraints,
- dynamic programming,
- short-horizon uncertainty-aware search,
- simulation,
- interpretable outputs.

There are two main statistical learning components:

1. pass-probability model
2. rival-belief model

Everything else should be transparent enough to explain to judges.

---

# 6. DATA SOURCES

## 6.1 Primary telemetry repositories

Raw telemetry mirrors are sourced from:

```text
https://github.com/TracingInsights/2022
https://github.com/TracingInsights/2023
https://github.com/TracingInsights/2024
https://github.com/TracingInsights/2025
https://github.com/TracingInsights/2026
```

Local mirrors are expected under:

```text
data/raw/2022/
data/raw/2023/
data/raw/2024/
data/raw/2025/
data/raw/2026/
```

These raw mirrors are intentionally ignored by Git.

Do not commit them.

## 6.2 Raw hierarchy observed so far

The validated raw hierarchy is:

```text
data/raw/{year}/{event}/{session}/{driver}/{lap}_tel.json
data/raw/{year}/{event}/{session}/{driver}/laptimes.json
```

Session-level metadata may include:

```text
weather.json
rcm.json
drivers.json
corners.json
session_laptimes.json
```

Do not assume every file exists in every year/session.

The loader must treat optional files as optional while still validating required telemetry.

## 6.3 Raw telemetry fields observed consistently in the audit

The following telemetry channels have been observed consistently across the 2022 to 2026 mirrors:

```text
time
rpm
speed
gear
throttle
brake
drs
distance
rel_distance
DriverAhead
DistanceToDriverAhead
acc_x
acc_y
acc_z
x
y
z
dataKey
```

Notes:

- `distance` is lap distance in metres.
- `time` is lap-relative telemetry time.
- `speed` is km/h.
- `throttle` is percentage-like 0 to 100 telemetry.
- `brake` is effectively binary in this dataset.
- `drs` is a raw historical/public channel. Retain it for 2022–2025 audit and
  explicitly named historical covariates only; it is not a 2026 strategic,
  legality, active-aero, or deployment signal. The observed all-zero 2026
  channel must remain unavailable rather than being interpreted as "closed".
- `DriverAhead` identifies the car ahead where available.
- `DistanceToDriverAhead` is a spatial gap, not automatically the official eligibility time gap.
- `acc_x`, `acc_y`, `acc_z` are derived channels in the TracingInsights extraction pipeline, not necessarily raw IMU values.
- `x`, `y`, `z` are positional channels useful for track geometry and alignment.

## 6.4 Lap metadata

Per-driver `laptimes.json` files can contain information such as:

```text
lap
time
sesT
lST
s1
s2
s3
vi1
vi2
vfl
vst
compound
life
fresh
stint
position / pos
other year-dependent fields
```

Do not hardcode one year's exact laptime schema without validation.

## 6.5 Other data sources expected later

Some inputs may require:

- FastF1
- OpenF1
- official FIA event documents
- official 2026 technical regulations
- official 2026 sporting regulations
- FIA circuit / Race Director documents

These should be used only when needed and with source provenance.

---

# 7. MAIN DEMO DATASET

The intended primary replay is:

```text
2026 British Grand Prix
Sprint
Hamilton (HAM)
Antonelli (ANT)
```

Phase 1 audit results already confirmed:

```text
HAM telemetry lap files: 1 through 17
ANT telemetry lap files: 1 through 17
non-monotonic distance laps observed in this audit: 0 for HAM, 0 for ANT
```

This replay is expected to become the central integrated demo case.

Do not restrict reusable modules to only HAM/ANT or only Silverstone. Track-specific configuration should remain separate from generic telemetry/model code.

---

# 8. CURRENT REPOSITORY STATE

## 8.1 GitHub snapshot

As of 11 September 2026, the remote `main` branch contains the initial Phase 1 tooling.

The latest observed Phase 1 commit was:

```text
dbc187c
feat: Add Phase 1 raw-data acquisition and audit tooling
```

Current tracked root structure is approximately:

```text
.gitignore
README.md
scripts/
    audit_raw_data.py
    fetch_phase1_raw.sh
```

Do not assume later phases are already merged merely because they are being developed locally.

## 8.2 Intentional `.gitignore` behavior

Current ignores include:

```text
data/raw/
data/derived/
artifacts/schema_audit/*.csv
artifacts/schema_audit/*.json
__pycache__/
*.pyc
```

Therefore:

- raw telemetry is local,
- generated audit artifacts are local,
- derived bulk data should not be committed unless the team explicitly changes this policy.

Code, compact configuration, schemas, tests and small model parameters can be committed when appropriate.

---

# 9. PHASE 1: COMPLETED / ESTABLISHED WORK

Phase 1 was defined as:

> Raw data acquisition and schema audit only.

No model training.

No DP.

No pass model.

No HMM fitting.

No energy twin.

## 9.1 Phase 1 scripts

### `scripts/fetch_phase1_raw.sh`

Purpose:

- acquire or refresh the 2022 to 2026 TracingInsights raw mirrors,
- preserve raw data rather than destructively rebuilding it.

### `scripts/audit_raw_data.py`

Purpose:

- read raw mirrors without modifying them,
- inspect telemetry and laptime schemas,
- compare year availability,
- calculate basic data-quality diagnostics,
- audit 2026 British Sprint HAM/ANT,
- emit a proposed canonical schema for Phase 2.

The script currently defines a telemetry field list and a proposed canonical mapping.

It checks, among other things:

- row counts,
- field presence,
- null rates,
- numeric min/max,
- telemetry value types,
- distance monotonicity,
- telemetry time-step statistics.

## 9.2 Phase 1 generated artifacts

The audit is designed to generate:

```text
artifacts/schema_audit/
    repository_summary.json
    events_sessions.csv
    field_availability.csv
    schema_differences.json
    canonical_schema.json
    data_quality_summary.csv
    british_sprint_2026.json
```

These outputs are generated locally and ignored by Git.

## 9.3 Current Phase 1 canonical proposal

The audit currently proposes canonical names along the lines of:

```text
lap_elapsed_s
distance_m
lap_fraction
speed_kmh
engine_rpm
gear
throttle_pct
brake_on
historical_drs_open  # 2022–2025 only; never populated for 2026
x_m
y_m
z_m
acc_longitudinal_mps2
acc_lateral_mps2
acc_vertical_mps2
driver_ahead_number
gap_ahead_m
lap_number
lap_time_s
session_time_s
sector_1_s
sector_2_s
sector_3_s
race_position
tyre_compound
tyre_life_laps
stint
driver
team
track_status
```

This was explicitly a **Phase 2 proposal**, not a guarantee that every field exists for every year.

Phase 2 must validate and refine this contract.

---

# 10. CURRENT ACTIVE WORK: PHASE 2

Phase 2 is the active telemetry-engineering workstream.

Its intended scope is strictly:

> validated telemetry loading and 20 m distance resampling.

Do not expand Phase 2 into model training or strategic planning.

## 10.1 Phase 2 responsibilities

Phase 2 should:

1. load telemetry from the Phase 1 raw hierarchy,
2. validate required arrays and lengths,
3. normalize fields into a canonical internal representation,
4. reject malformed/unusable laps explicitly,
5. preserve metadata identifying year/event/session/driver/lap,
6. resample continuous telemetry to a 20 m distance grid,
7. use suitable interpolation rules by variable type,
8. preserve categorical/binary semantics,
9. record quality/provenance information,
10. produce deterministic reusable output for later phases.

## 10.2 Phase 2 must not implement

Do not add:

- track segmentation,
- overtaking labels,
- pass-probability model,
- HMM,
- energy twin,
- DP,
- beam search,
- planner labels,
- UI.

Those belong to later or parallel workstreams.

## 10.3 20 m resampling principles

The system should operate on distance rather than raw irregular telemetry timestamps.

Conceptually, for one clean lap:

```text
raw distance:
0.0, 8.7, 19.4, 31.8, 44.2, ...

target grid:
0, 20, 40, 60, 80, ...
```

Continuous variables may be interpolated carefully:

```text
time
speed
rpm
throttle
x
y
z
acc_x
acc_y
acc_z
DistanceToDriverAhead
```

Discrete / categorical variables should not be linearly interpolated as arbitrary fractions:

```text
gear
brake
drs
DriverAhead
```

Use a defensible nearest/forward/backward assignment according to the Phase 2 implementation contract.

Do not invent intermediate driver numbers or fractional boolean values.

---

# 11. TARGET REPOSITORY ARCHITECTURE

The long-term code organization from the design brief is conceptually:

```text
edelta/
    rules/
        rules_2026.yaml
        mask.py
        test_mask.py

    track/
        cutter.py
        segmodel.py
        zones.py

    twin/
        power_balance.py
        calib.py

    value/
        dp.py
        shadow.py

    rival/
        features.py
        hmm.py
        policies.py

    pass/
        extract.py
        logistic.py
        calibration.py

    plan/
        beam.py
        cvar.py
        labels.py
        windows.py
        flips.py
        penalty.py
        budget.py

    sim/
        env.py
        baselines.py
        run.py

    ui/
        app.py
        overlay.py
        radio.py

scripts/
    fetch_phase1_raw.sh
    audit_raw_data.py
    ...

data/
    raw/
    derived/

artifacts/
    schema_audit/
    models/
    evaluation/
    demo/
```

This is a design target, not permission to create empty modules.

Only create a package or module when the assigned phase needs it.

---

# 12. PARALLEL WORKSTREAMS AND OWNERSHIP BOUNDARIES

Multiple teammates may work at the same time.

To reduce merge conflicts, separate work by subsystem.

## 12.1 Workstream A: telemetry/data engineering

Owns:

```text
scripts/
edelta/data/          # if/when introduced
data-contract schemas
20 m resampling
validation
derived cached datasets
```

Responsibilities:

- raw loaders
- canonical fields
- quality checks
- interpolation/resampling
- deterministic metadata

Must not own:

- FIA strategic legality semantics,
- DP decisions,
- HMM behavior policies,
- UI rendering.

## 12.2 Workstream B: FIA rules and Silverstone configuration

Owns:

```text
edelta/rules/
edelta/track/zones.py or equivalent track configuration
rules/*.yaml if configuration is kept at repository root
track/*.yaml if configuration is kept at repository root
```

Responsibilities:

- 2026 rule audit
- exact source references
- deployment envelope constants
- Overtake eligibility rules
- high-speed taper
- energy-accounting constraints
- active-aero legality
- Race Control disable states
- Silverstone Detection / activation / zone landmarks
- boundary-test cases

Must not infer telemetry schemas.

Must not fabricate exact distances if the FIA source only gives landmarks.

Track-landmark distances may require later alignment against telemetry/GPS.

## 12.3 Workstream C: physics / simulator

Owns later:

```text
edelta/twin/
edelta/sim/
```

Responsibilities:

- longitudinal power balance
- energy state inference
- parameter uncertainty
- per-lap physical checks
- two-car simulator
- baseline policies

Must consume the canonical/resampled data contract rather than re-reading raw JSON independently.

## 12.4 Workstream D: inference / pass model / UI

Owns later:

```text
edelta/rival/
edelta/pass/
edelta/ui/
```

Responsibilities:

- overtaking-event extraction
- logistic pass model
- probability calibration
- rival feature extraction
- HMM inference
- belief visualization
- UI presentation

Must not redefine the segment schema unilaterally.

## 12.5 Workstream E: value and planning

Owns later:

```text
edelta/value/
edelta/plan/
```

Responsibilities:

- DP tables
- energy shadow price
- legal action enumeration
- uncertainty-aware short beam search
- tactical labels
- flips-if threshold explanation
- penalty-aware terminal logic
- race budget if time permits

Must only score actions that pass the rule mask.

---

# 13. SHARED CONTRACTS: DO NOT BREAK THESE SILENTLY

## 13.1 Telemetry unit contract

Use explicit unit suffixes in canonical names where practical.

Examples:

```text
distance_m
speed_kmh
lap_elapsed_s
gap_ahead_m
acc_longitudinal_mps2
```

Do not mix m/s and km/h under the same field name.

Do not mix seconds and milliseconds.

## 13.2 Track-distance contract

Distance through the lap is the primary spatial alignment axis.

Later components should not independently re-index raw telemetry by timestamp unless explicitly required.

## 13.3 Segment ID contract

Once segmentation lands, segment IDs must be deterministic for a given track configuration.

Do not let each model invent its own segment boundaries.

## 13.4 Gap sign convention

The strategic DP convention is:

```text
g > 0: our car is behind the rival
g < 0: our car is ahead
```

Do not flip this convention in another module.

## 13.5 Energy sign convention

For the intended twin:

```text
P_K > 0: electrical deployment
P_K < 0: electrical harvesting / regeneration
```

If an implementation uses an opposite low-level convention internally, convert at its public API boundary.

## 13.6 Provenance contract

Displayed values should carry one of:

```text
OBSERVED
DERIVED
INFERRED
SIMULATED
RULE
```

Examples:

```text
speed_kmh from telemetry              -> OBSERVED
20 m interpolated speed               -> DERIVED
pass probability                      -> DERIVED
rival hidden-state belief             -> INFERRED
estimated energy state                -> SIMULATED or INFERRED, depending on implementation
FIA maximum deployment constraint     -> RULE
```

Be precise. Do not use `OBSERVED` merely because a value was produced from observed inputs.

---

# 14. TRACK SEGMENTATION DESIGN

Segmentation is a future phase, not part of Phase 2.

The intended segment cutter starts a new segment around:

- brake onset,
- return to sustained full throttle.

Initial rule from the brief:

```text
brake onset approximately brake > 10%
```

However, the public TracingInsights `brake` telemetry is effectively binary.

Therefore the actual implementation must adapt to the observed channel semantics rather than blindly applying a percentage threshold.

Short segments under approximately:

```text
80 m
```

should be merged according to a deterministic rule.

The target is roughly:

```text
30 to 40 segments per lap
```

not hundreds of microsegments.

Each segment should eventually expose features such as:

```text
segment_id
start_distance_m
end_distance_m
length_m

base_time_s
entry_speed_kmh
exit_speed_kmh
max_speed_kmh
mean_speed_kmh

full_throttle_fraction
brake_fraction
brake_onset_distance_m
full_throttle_return_distance_m

mean_gradient
elevation_change_m

historical_drs_fraction  # 2022–2025 observed-only
aero_state_fraction      # 2026 only when derived from the rule engine; otherwise unavailable
```

Additional physics-model parameters later include:

```text
a_k  # seconds saved per MJ deployed
c_k  # seconds lost per metre of lift
```

---

# 15. SEGMENT TIME MODEL

The intended compact timing approximation is:

\[
t_k(d,L)
=
t_{\text{base},k}
-
a_k \Delta E_{\text{dep}}(k,d)
+
c_k L
\]

where:

- `t_base,k` comes from clean replay behavior,
- `a_k` estimates sensitivity to electrical deployment,
- `c_k` estimates lift-and-coast time cost.

Sanity expectations:

- `a_k` should generally be larger on useful straights where additional power changes acceleration materially,
- `a_k` should approach low usefulness where the power envelope tapers to zero,
- `c_k` should be zero or irrelevant where no applicable braking zone follows.

Do not fit arbitrary neural segment-time models unless the team explicitly changes the architecture.

---

# 16. ENERGY TWIN

Public telemetry does not expose real battery SOC or MGU-K power.

The project therefore uses a transparent virtual energy estimator.

Conceptually:

\[
P_{\text{wheel}}
=
mav
+
\frac{1}{2}\rho C_dA v^3
+
C_{rr}mgv
+
mgv\sin\theta
\]

and:

\[
P_K
=
\frac{P_{\text{wheel}}}{\eta_{dl}}
-
P_{ICE}
\]

where:

- `m` = car + fuel mass assumption
- `a` = longitudinal acceleration
- `v` = vehicle speed
- `rho` = air density
- `CdA` = aerodynamic drag parameter
- `Crr` = rolling resistance
- `theta` = track gradient
- `eta_dl` = drivetrain efficiency
- `P_ICE` = estimated combustion-engine contribution
- `P_K` = inferred MGU-K contribution

Initial modeling ranges in the brief include approximate ranges for:

```text
mass
CdA
straight-line CdA
Crr
drivetrain efficiency
```

Do not hardcode these as facts without documenting them as assumptions or calibrated ranges.

The planner should propagate uncertainty over twin parameters rather than pretending the hidden energy state is exact.

## 16.1 Calibration concept

The brief proposes calibrating aerodynamic / ICE parameters in regions where the regulations force electrical deployment to zero.

This is a clever calibration opportunity, but it depends on the final verified 2026 rules.

The rules workstream must resolve the exact speed/deployment envelope before twin calibration treats any region as guaranteed zero-MGU-K deployment.

## 16.2 Energy accounting

The brief warns explicitly:

> the approximately 4 MJ rule must not automatically be interpreted as battery capacity.

The relevant regulation may instead define a running energy excursion or another accounting quantity.

The rules module owns the exact interpretation.

Do not let the physics twin invent legality semantics.

---

# 17. PASS-PROBABILITY MODEL

This is one of the two main trainable statistical models.

The intended form is interpretable logistic regression:

\[
P_{\text{pass}}
=
\sigma(
\beta_0
+
\beta_1 \Delta v_{\text{end}}
+
\beta_2 g_{\text{brake}}
+
\beta_3 \epsilon
+
\beta_4 D_{\text{zone}}
)
\]

Conceptual features:

```text
delta_v_end
gap_at_braking_zone
eligibility
zone / track context
```

The exact final feature contract must come from the extracted event dataset.

## 17.1 Historical prior

Use 2022 to 2025 DRS-era overtaking data as a historical prior.

Do not claim those years are physically identical to 2026.

The system must say explicitly that the prior is DRS-era.

Historical DRS values, including any DRS-zone proxy, must not be supplied to
a 2026 pass feature vector, Overtake state, power-envelope selection, or
legality mask. A proxy may support a labelled development fixture only and
must block final calibration, replay, and release claims.

## 17.2 2026 calibration

2026 data should recalibrate the model for the new regime.

The brief proposes fitting historical coefficients and then recalibrating at least the intercept / Overtake-related terms on 2026 observations.

Do not pool every year as if regulation context were identical.

## 17.3 Target label

A row represents an overtaking opportunity.

The target should be based on a well-defined future position swap by a consistent downstream timing point.

Do not label every 20 m telemetry sample as a separate pass example.

## 17.4 Evaluation

Primary probability-quality metrics:

```text
Brier score
reliability / calibration diagram
N of held-out examples
```

Accuracy alone is not sufficient because the planner consumes a probability.

---

# 18. RIVAL BELIEF MODEL

The rival's true energy state and tactical intent are private.

The system uses a hidden-state belief instead of pretending they are observed.

Initial hidden types:

```text
CONSERVING
BALANCED
DEPLOYING
DERATING
```

The belief vector is:

\[
b_t(z) = P(z_t = z \mid y_{1:t})
\]

Update concept:

\[
b_{t+1}(z)
\propto
p(y_{t+1}\mid z)
\sum_{z'} T(z\mid z') b_t(z')
\]

## 18.1 Intended observation features

Five initial features:

```text
segment_time_residual
exit_speed_residual
brake_point_shift
full_throttle_clipping_flag
closing_rate
```

Residuals should generally be measured relative to the rival's own normal behavior for the same segment where possible.

Example:

```text
segment_time_residual
=
current segment time
-
rival median segment time for this track segment
```

This is preferable to absolute segment time because it reduces car/driver baseline effects.

## 18.2 HMM training philosophy

Historical telemetry does not provide ground-truth labels such as:

```text
"this segment was CONSERVING"
```

Do not pretend it does.

The brief's preferred approach is:

1. define explicit rival-type policies in the simulator,
2. generate labeled synthetic trajectories,
3. estimate emission parameters using known simulated type labels,
4. test whether the resulting model predicts real telemetry better than a constant baseline,
5. report synthetic type recovery after 5/10/20 segments.

Initial transition matrices can be sticky priors and later be refit.

If `CONSERVING` and `DERATING` cannot be distinguished reliably, merge them and report the simplification rather than forcing four visually attractive but meaningless states.

---

# 19. RULES ENGINE

Legality must be enforced **before** action scoring.

Never score an illegal action and then penalize it.

The legality mask should remove invalid actions from the candidate set.

This is a central project claim:

> legal by construction.

The rules workstream must verify the current official 2026 FIA documents.

Do not copy outdated limits from slides or old regulation versions without checking the official current issue.

The rules audit must resolve at least:

- maximum permitted electrical deployment power,
- exact interpretation of the commonly cited 350 kW quantity,
- speed-dependent deployment taper,
- Overtake eligibility threshold,
- exact Detection Line semantics,
- activation conditions,
- energy deployment / recharge constraints,
- active-aero conditions,
- Race Control disable states,
- Silverstone-specific configuration.

Every rule constant should have:

```text
source document
issue/version
article
page if practical
official URL/reference
interpretation
confidence / unresolved status
```

---

# 20. RULE BOUNDARY TESTS

For every important numerical legality constraint, create tests immediately around the boundary.

Conceptually:

```text
below limit
exactly at limit
above limit
```

Examples from the design intent include:

```text
speed just below / above a deployment threshold
gap just below / above Overtake eligibility threshold
energy excursion just below / above maximum
Race Control enabled / disabled
```

Do not hardcode example threshold values from this document if the rules audit has not confirmed them.

Tests must use the verified configuration.

---

# 21. DP TABLES

The DP state grid proposed in the brief is approximately:

```text
energy e:
0.0 to 4.0 MJ in 0.1 MJ increments

gap g:
-3.0 s to +3.0 s in 0.1 s increments

eligibility epsilon:
0 or 1

track:
two laps unrolled

rival:
one table per rival type
```

This creates on the order of hundreds of thousands of states rather than an enormous continuous optimization problem.

The action grid is small enough for vectorized NumPy.

The DP output should include:

```text
V tables
argmax policy
lambda_E / shadow-price values
```

No neural network is required.

---

# 22. SHORT-HORIZON BEAM PLANNER

The DP gives long-horizon value.

The beam planner handles near-term uncertainty.

Intended design:

```text
horizon: ~6 segments
beam width: ~20
uncertainty draws: ~16
terminal value: DP table
```

The score concept is:

\[
Q
=
\mathbb{E}[V]
-
\gamma \operatorname{CVaR}_{0.2}[-V]
\]

where CVaR focuses on bad-tail outcomes.

The planner should consider uncertainty over:

- rival type,
- energy-twin parameters,
- potentially gap noise.

The beam is not allowed to replace the DP with a tiny local horizon.

Its purpose is:

> handle the uncertain next few segments, then use the DP for everything beyond them.

---

# 23. PENALTY-AWARE VALUE

Penalties may affect the value of race position.

They do not legalize an illegal energy action.

Do not "price" a technical-regulation breach into the objective.

A breach of an ECU-enforced rule is not an acceptable strategic trade.

Penalty-aware logic should only alter the terminal value of relative race position.

---

# 24. SIMULATOR

A two-car simulator is required to validate the planner in a transparent environment.

Both cars advance through the same segment list.

The rival follows a policy corresponding to its hidden type.

The simulator should include:

```text
segment-time noise
twin-parameter draws
pass outcomes from P_pass
legality mask assertions
```

Initial brief values include:

```text
500 episodes
g0 ~ Uniform(0.5, 2.5) s
e0 ~ Uniform(1.0, 3.5) MJ
segment-time noise ~ 0.05 s
```

Treat these as design defaults, not sacred values.

## 24.1 Required baselines

Compare the planner against understandable baselines such as:

```text
greedy attack
longest-straight strategy
lap-time-only strategy
type-revealed oracle
```

## 24.2 Metrics

Report:

```text
P(ahead)
final energy
rule violations
CVaR / risk metric
runtime
95% bootstrap confidence intervals
```

The required rule-violation count is zero.

---

# 25. USER INTERFACE AND EXPLANATION

The UI is a demonstration surface, not the source of truth.

The model logic must live outside Streamlit/UI code.

Intended views:

```text
track colored by lambda_E
current rival belief bars
recommended action
window / opportunity table
"flips if" explanation
counterfactual control if time allows
rule/config sidebar
measured SOC override
provenance labels
deterministic radio line
```

The radio message should be generated from deterministic templates rather than an LLM.

Example concept:

```text
<mode> from <location>, <harvest/deploy instruction>. Unlock likely at <line>.
```

The exact wording can change, but the message must be grounded in planner outputs.

---

# 26. MEASURED SOC OVERRIDE

The public-data demo uses an inferred virtual energy state.

The architecture should allow a team-provided measured SOC or equivalent energy state to override the twin.

When this happens:

```text
estimated energy provenance -> no longer presented as simulated inference
measured input provenance   -> OBSERVED
```

Do not redesign the entire planner for this.

The strategic state interface should accept either source.

---

# 27. PROPOSED DATA PRODUCTS

The broader preprocessing/modeling plan expects artifacts such as:

```text
data/derived/
    telemetry_20m/
    segments/
    overtake_events.parquet
    rival_features.parquet

artifacts/models/
    pass_prior_2022_2025.pkl
    pass_scaler.pkl
    hmm_initial_params.npz

artifacts/config/
    feature_schema.json
    preprocessing_config.yaml
```

Exact paths may evolve, but do not create duplicate competing versions of the same artifact in different folders.

Prefer one canonical producer and one documented contract.

---

# 28. TRAINING PLAN

There is no GPU-heavy training requirement in the current architecture.

## 28.1 Train / fit

Likely statistical fitting:

```text
historical logistic pass model
2026 pass-model recalibration
HMM emission / transition parameters
physics-twin calibration parameters
```

## 28.2 Compute, not train

These are computed:

```text
20 m resampling
segments
segment features
energy shadow prices
DP value tables
beam plans
rule masks
simulator rollouts
```

Do not describe DP tables as a learned model.

---

# 29. DATA REGIME POLICY

2026 is a materially different regulatory regime.

Do not blindly pool 2022 to 2026 as homogeneous training data.

General policy:

```text
2022 to 2025:
historical DRS-era prior and behavioral evidence

2024 to 2025:
most recent historical regime, usually highest-value pre-2026 prior

2026:
new-regime calibration / independent evaluation / demo replay
```

Attach year/regime context to extracted examples.

Historical data can provide priors, but it is not ground truth for 2026 energy-system physics.

The raw DRS channel stays in the lake for auditability; deleting it would make
the era distinction impossible to verify. Its use is intentionally confined to
the historical domain and must be visible in artifact manifests and UI labels.

---

# 30. ACCEPTANCE TARGETS

From the current solution brief, the integrated system should aim for:

## 30.1 Rules

```text
0 rule violations in simulation
boundary tests green
```

## 30.2 Planner latency

```text
p95 < 100 ms per online planning call
```

## 30.3 Strategic performance

Planner should outperform the chosen heuristic baselines with uncertainty reported.

Do not cherry-pick one successful trajectory.

## 30.4 Pass model

Report:

```text
Brier score
reliability diagram
held-out N
```

## 30.5 Rival filter

Report:

```text
synthetic recovery after 5 segments
synthetic recovery after 10 segments
synthetic recovery after 20 segments
real predictive log-likelihood vs constant model
```

## 30.6 Robustness

The brief targets stable recommendations across most uncertainty draws and checks sensitivity to the residual-energy weight `beta`.

---

# 31. DEMO STORY

The planned demo is centered on a two-lap replay and should tell a coherent story.

Conceptual sequence:

1. Replay selected 2026 British Sprint interaction.
2. Show Silverstone colored by energy shadow price.
3. Pause before an important eligibility line.
4. Show current recommendation and explanation.
5. Show how recommendation changes if rival belief changes.
6. Change a verified rule/config parameter and recompute.
7. Optionally provide measured SOC and show provenance change.
8. Finish with simulator/evaluation results and boundary tests.

The demo should not claim that a historical British GP position change proves the energy model is physically correct.

The replay demonstrates the system behavior, not ground-truth validation of private ERS strategy.

---

# 32. WHAT WE CLAIM

The project may claim, if the implementation and tests support it:

- legal actions are enforced by a hard rules mask,
- important rule boundaries are explicitly tested,
- the shadow price is computed from a value function,
- the rival belief is probabilistic and validated,
- the pass model is calibrated and evaluated,
- the planner is compared against transparent baselines in simulation,
- assumptions and uncertainty are visible,
- measured team SOC can replace the public-data virtual energy estimate.

---

# 33. WHAT WE DO NOT CLAIM

Do not claim:

- exact reconstruction of a real team's battery SOC from public telemetry,
- exact real MGU-K power from public telemetry,
- exact private rival intent,
- real-race validation of the entire planner merely because one historical pass looks plausible,
- equilibrium game-theoretic optimality,
- prediction of FIA stewarding,
- that historical DRS is the same system as 2026 active aero,
- that a DRS-era pass model is automatically valid in 2026,
- that a modeling assumption is an FIA rule,
- that simulated outputs are observed telemetry.

This section is non-negotiable.

---

# 34. KNOWN RISKS

## 34.1 Energy-rule semantics

The exact energy excursion / recharge accounting window must come from current FIA regulations.

Do not assume "4 MJ" means battery capacity or per-lap energy without source verification.

## 34.2 Active aero vs DRS

Public telemetry may continue to expose a field named `drs`.

Do not interpret its name as proof that it semantically represents every aspect of the 2026 active-aero system. In particular, an all-zero 2026 field is
unavailable, not evidence that the car is in a non-Overtake mode. Do not use a
historical DRS activation zone as a 2026 final configuration; only a sourced
2026 event rule may define Detection/Activation geometry in final mode.

## 34.3 Detection / eligibility lines

Official line locations may be given as track landmarks rather than exact telemetry distances.

If so:

```text
FIA source -> landmark
telemetry/GPS alignment -> derived distance
```

Keep those two steps separate.

## 34.4 Rival-state identifiability

CONSERVING and DERATING may be hard to distinguish.

If validation is weak, simplify the state space rather than reporting misleading confidence.

## 34.5 Sparse 2026 pass events

If a single circuit has too few events, pool appropriate 2026 circuits with a circuit/zone term and report `N`.

## 34.6 Physics-twin uncertainty

If the twin cannot satisfy basic physical/energy checks, show uncertainty or a warning.

Do not silently clip everything until plots look reasonable.

---

# 35. PARALLEL DEVELOPMENT RULES

## 35.1 Branches

Each substantial workstream should use its own branch.

Suggested pattern:

```text
data/phase2-resampling
rules/2026-audit
track/silverstone-config
twin/power-balance
pass/event-extraction
rival/hmm
value/dp
plan/beam
sim/evaluation
ui/demo
```

Do not have three people editing the same central module on separate branches if the work can be split by interface.

## 35.2 Commits

Prefer small coherent commits.

Examples:

```text
feat(data): add validated telemetry loader
feat(data): resample laps to 20 m grid
test(data): cover malformed telemetry arrays
feat(rules): add sourced 2026 deployment limits
test(rules): cover gap eligibility boundary
feat(value): add backward DP on toy track
```

Avoid commit messages such as:

```text
changes
update
fix stuff
final
```

## 35.3 Generated data

Do not commit large generated datasets.

Commit:

- code,
- compact configs,
- schemas,
- tests,
- tiny fixtures,
- concise evaluation summaries if useful.

Do not commit:

- cloned telemetry repositories,
- multi-GB intermediate data,
- caches,
- duplicate Parquet trees.

## 35.4 Integration

Before merging a subsystem:

1. run its tests,
2. verify its public output schema,
3. compare against the contracts in this file,
4. ensure it did not import another subsystem's internal implementation details,
5. communicate any shared-schema changes.

---

# 36. CODING STYLE

Primary language: Python.

Priorities:

1. correctness,
2. explicit units,
3. deterministic behavior,
4. readable scientific logic,
5. tests around boundaries,
6. speed where it materially affects the demo.

Prefer:

```text
pathlib
dataclasses or small typed structures where useful
NumPy
pandas for table operations
scikit-learn for lightweight models
PyYAML or equivalent for configuration
pytest
```

Avoid unnecessary framework complexity.

Do not turn this into an MLOps platform.

## 36.1 Errors

Fail visibly on malformed required input.

Do not use broad:

```python
except Exception:
    pass
```

for critical data paths.

Optional metadata may be missing, but required telemetry corruption should produce a clear error or rejected-lap record.

## 36.2 Reproducibility

Where random simulation/model fitting is used:

```text
set random seeds
record configuration
record dataset scope
record feature list
```

---

# 37. TESTING PHILOSOPHY

Tests should focus on the things most likely to invalidate a judge-facing claim.

Highest priority:

```text
raw schema validation
20 m grid determinism
interpolation correctness
discrete field handling
rule boundaries
energy sign conventions
DP state transitions
pass-label correctness
HMM probability normalization
simulator rule assertions
planner latency
```

Small synthetic fixtures are encouraged.

Do not require the full multi-year raw dataset for every unit test.

---

# 38. INTERFACE-FIRST IMPLEMENTATION

When two teammates work on adjacent modules, agree on the interface before both sides are complete.

Example:

Telemetry preprocessing can expose a record/table containing:

```text
year
event
session
driver
lap_number
distance_m
lap_elapsed_s
speed_kmh
engine_rpm
gear
throttle_pct
brake_on
drs_open
x_m
y_m
z_m
acc_longitudinal_mps2
acc_lateral_mps2
acc_vertical_mps2
driver_ahead_number
gap_ahead_m
```

Segmentation consumes this.

It should not reopen `{lap}_tel.json` and build a second private parser.

Likewise:

```text
segment table -> twin / rival feature code
rule configuration -> legality mask
P_pass API -> DP/simulator
V table API -> beam planner
planner result -> UI
```

One producer per contract.

---

# 39. EXPECTED PHASE ORDER FOR THE DATA / ML WORKSTREAM

The current planned sequence is:

## Phase 1: raw acquisition and schema audit

Status: substantially completed and committed.

Outputs:

```text
raw mirrors
schema audit
quality reports
proposed canonical schema
British Sprint audit
```

## Phase 2: validated loader + 20 m resampling

Status: active workstream.

Output:

```text
validated canonical lap representation
20 m resampled telemetry
quality/rejection metadata
```

## Phase 3: track segmentation and segment features

Output:

```text
stable segment map
segment-level telemetry summaries
```

## Phase 4: ML datasets

Output:

```text
overtake_events
rival_features
```

## Phase 5: pass prior / HMM preparation

Output:

```text
historical pass model
calibration diagnostics
HMM initial parameters / synthetic validation
```

## Phase 6: integration with twin, value and planner

This is where the strategic system becomes end-to-end.

Do not skip contracts just to reach the UI sooner.

---

# 40. RULES WORKSTREAM DELIVERABLES

The teammate doing regulations should aim to create compact machine-readable outputs such as:

```text
rules/
    rules_2026.yaml
    sources_2026.yaml

track/
    silverstone_2026.yaml

docs/
    rules_audit.md
    silverstone_audit.md
    unresolved_rules.md

tests/
    rule_boundary_cases.yaml
```

Exact placement can be adapted to the eventual `edelta/` package, but there should be one authoritative rule/config source.

Every unresolved interpretation should remain explicitly unresolved until verified.

Do not "pick the most likely answer" silently.

---

# 41. DEMO-PRESERVING PRIORITY ORDER

If time becomes constrained, protect the following capabilities first:

```text
1. correct data pipeline
2. sourced hard legality mask
3. segment model
4. energy twin with uncertainty
5. DP value function
6. shadow-price visualization
7. pass probability
8. rival belief
9. simulator + baselines
10. deterministic recommendation / radio output
```

Lower-priority items that can be dropped if needed include:

```text
race-wide energy budget optimization
second-circuit comparison
large robustness sweeps
rich counterfactual UI
extra polish
```

Do not drop the legality mask or falsify validation results to save time.

---

# 42. AGENT-SPECIFIC INSTRUCTIONS

If you are an AI coding agent working in this repository:

## 42.1 Do not expand scope

Implement only the phase/task requested.

If asked for Phase 2, do not "helpfully" add:

- pass models,
- HMMs,
- DP,
- UI,
- simulation.

## 42.2 Inspect before editing

Always inspect existing code and tests first.

Do not overwrite a teammate's implementation with a from-scratch rewrite merely because a different structure would be cleaner.

## 42.3 Preserve contracts

If an existing function/output is already consumed elsewhere, preserve compatibility unless the task explicitly authorizes a contract change.

## 42.4 No fabricated scientific certainty

Never write comments, README text or UI copy implying that:

```text
estimated SOC == true SOC
inferred MGU-K power == measured MGU-K power
HMM state == true rival intent
historical DRS prior == verified 2026 overtake physics
```

## 42.5 No hidden fallbacks

If required data are missing, return a clear status or error.

Do not create plausible-looking synthetic values inside production data-loading code.

Synthetic data belongs in simulation/test fixtures and must be labeled.

## 42.6 Avoid unnecessary dependencies

Use the existing stack unless a new dependency materially simplifies the task.

Do not add a large framework to solve a 50-line problem.

---

# 43. STATUS CHECKLIST

Keep this section updated as work lands.

## Data

- [x] Identify TracingInsights 2022 to 2026 sources
- [x] Establish local `data/raw/{year}` mirror convention
- [x] Add raw fetch helper
- [x] Add schema-audit script
- [x] Confirm primary telemetry channels
- [x] Audit 2026 British Sprint HAM and ANT lap coverage
- [x] Confirm HAM/ANT audited lap distance monotonicity
- [ ] Merge validated canonical loader
- [ ] Merge 20 m resampling
- [ ] Produce stable derived telemetry contract

## Track / segments

- [ ] Implement segment cutter
- [ ] Produce Silverstone segment map
- [ ] Validate segment count / boundaries
- [ ] Add segment feature table

## Rules

- [ ] Verify latest 2026 technical regulation issue
- [ ] Verify latest 2026 sporting regulation issue
- [ ] Resolve exact 350 kW semantics
- [ ] Resolve speed-taper formula
- [ ] Resolve Overtake eligibility and line semantics
- [ ] Resolve energy excursion / recharge accounting
- [ ] Resolve active-aero legality
- [ ] Build Silverstone config
- [ ] Add boundary tests

## Twin

- [ ] Implement power balance
- [ ] Add parameter ranges
- [ ] Calibrate against rule-forced zero-deployment region if valid
- [ ] Add energy accounting checks
- [ ] Add uncertainty / warning behavior

## Pass model

- [ ] Extract historical overtake-event table
- [ ] Fit 2022 to 2025 prior
- [ ] Extract 2026 events
- [ ] Recalibrate
- [ ] Report Brier score / reliability / N

## Rival belief

- [ ] Implement segment residual features
- [ ] Implement rival policy definitions
- [ ] Build synthetic labeled runs
- [ ] Fit / set HMM parameters
- [ ] Validate recovery at 5/10/20 segments
- [ ] Compare real predictive likelihood vs constant

## Value / planning

- [ ] Implement toy DP
- [ ] Implement real segment DP
- [ ] Generate lambda_E
- [ ] Add legal action mask integration
- [ ] Implement uncertainty-aware beam planner
- [ ] Add tactical labels
- [ ] Add flips-if explanation

## Simulator / evaluation

- [ ] Implement two-car simulator
- [ ] Add heuristic baselines
- [ ] Run planned episode evaluation
- [ ] Verify zero rule violations
- [ ] Bootstrap confidence intervals
- [ ] Benchmark planner latency

## UI

- [ ] Track shadow-price visualization
- [ ] Belief bars
- [ ] Recommendation panel
- [ ] Rule/config display
- [ ] Provenance tags
- [ ] deterministic radio line
- [ ] measured-SOC override

---

# 44. QUICK CONTEXT FOR A NEW TEAMMATE

If you have five minutes and need the project in one page:

1. We are optimizing when a 2026 F1 car should spend or recover electrical energy during a battle.
2. Public telemetry lacks battery SOC and MGU-K power, so we estimate them transparently and retain uncertainty.
3. Telemetry is normalized and resampled every 20 m.
4. The lap is cut into approximately 30 to 40 meaningful segments.
5. A compact physics model estimates how deployment/lift changes segment time and energy.
6. FIA rules create a hard legality mask.
7. A logistic model estimates pass probability.
8. An HMM maintains a belief over rival tactical type.
9. A two-lap DP computes long-horizon race-position value.
10. The derivative of that value with respect to energy gives the energy shadow price.
11. A short beam search handles uncertainty over the next few segments and uses DP value for the remaining horizon.
12. A simulator compares this planner against understandable heuristics.
13. The UI explains the recommendation, why it matters, what would change it, and whether each value is observed, derived, inferred, simulated or a rule.

---

# 45. FINAL PRINCIPLE

The project wins only if it is defensible.

A simpler result with:

- correct telemetry,
- verified rules,
- explicit uncertainty,
- interpretable models,
- clean interfaces,
- real tests,
- honest limitations,

is stronger than a more complicated system built on invented battery data or inconsistent assumptions.

When in doubt, preserve traceability and correctness.
