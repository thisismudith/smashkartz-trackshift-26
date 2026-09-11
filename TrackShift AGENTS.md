# TrackShift / E-Delta Engineering Contract

This file is the project-wide engineering and architecture contract for TrackShift.

It applies to every contributor, script, model, notebook, test, documentation file, and automated coding agent working in this repository.

It is not specific to one developer, one laptop, or one subsystem.

If implementation details conflict with this document, stop and resolve the inconsistency before extending the system.

---

# 1. Project objective

TrackShift E-Delta is an F1 Energy and Overtake Intelligence system.

The system should answer:

> Given the current track position, available electrical energy, gap to the rival, rival behaviour, race context, and applicable 2026 regulations, where should the car deploy, hold, or recover electrical energy to maximize the probability of remaining or finishing ahead?

The system is not a generic lap-time predictor.

The objective is race position over a multi-segment and approximately two-lap strategic horizon.

The central strategic quantity is the energy shadow price:

\[
\lambda_E(k,e,g,\epsilon)
=
\frac{
V(k,e+\Delta e,g,\epsilon)
-
V(k,e,g,\epsilon)
}{
\Delta e
}
\]

where:

- \(k\) is track segment,
- \(e\) is modelled electrical energy state,
- \(g\) is gap to the relevant rival,
- \(\epsilon\) is Overtake eligibility state,
- \(V\) is the value of the state,
- \(\lambda_E\) estimates how valuable additional electrical energy is at that location.

The project should demonstrate that the same amount of electrical energy can have very different strategic value depending on where it is deployed.

---

# 2. Core architecture

The frozen high-level architecture is:

```text
RAW DATA
    |
    +----------------------+----------------------+
    |                      |                      |
    v                      v                      v
Car telemetry         Lap metadata        Session metadata
    |                      |                      |
    +----------------------+----------------------+
                           |
                           v
                  Canonical 20 m lake
                           |
          +----------------+----------------+
          |                |                |
          v                v                v
        TRACK        DRIVER / TEAM       PAIRS
       FEATURES         BASELINES       & BATTLES
          |                |                |
          +--------+-------+--------+-------+
                   |                |
                   v                v
            Rival-state data   Overtake events
                   |                |
                   v                v
          HMM / HSMM /       Logistic / GBM /
          temporal models    calibrated models
                   |                |
                   v                v
            P(rival state)       P(pass)
                   |                |
                   +--------+-------+
                            |
                            v
                    2026 FIA RULE ENGINE
                            |
                  Detection Line evaluation
                            |
                    eligibility state
                            |
                     Activation Line
                            |
                  Overtake power envelope
                            |
                            v
                       ENERGY TWIN
                            |
                            v
                  Delta-energy to delta-time
                            |
                            v
                            DP
                            |
                  +---------+---------+
                  |                   |
                  v                   v
             shadow price        P(ahead)
               lambda_E          after horizon
                  |
                  v
                PLANNER
```

Do not replace this architecture with a monolithic neural model.

Machine learning is used where it improves inference or prediction.

Physics, regulations, state transitions, dynamic programming, and optimization remain explicit.

---

# 3. Current repository state

The repository currently contains the beginning of the data foundation.

Existing functionality includes:

```text
scripts/
    fetch_phase1_raw.sh
    audit_raw_data.py
    build_phase2_dataset.py

src/trackshift/
    raw_loader.py
    resample.py
    validation.py

tests/
    test_resample.py
    test_validation.py
```

Current capabilities include:

- mirroring TracingInsights data for 2022 to 2026,
- auditing raw telemetry schemas,
- discovering telemetry laps,
- validating telemetry,
- joining lap metadata,
- resampling telemetry by distance,
- producing a 20 m Phase 2 representation,
- recording accepted and rejected laps.

This foundation must be preserved where correct.

However, it is not the final data architecture described in this document.

Do not rewrite working Phase 1 or Phase 2 components without a specific reason and regression tests.

---

# 4. Immediate repository rule

Before redesigning the downloader or reorganizing raw data:

1. Audit the data already present on the local/shared development machine.
2. Record its exact directory structure and sizes.
3. Determine which years, events, sessions, drivers, and files already exist.
4. Compare the existing local structure with the target architecture in this document.
5. Only then design replacement or incremental download scripts.

Do not re-download terabytes of data simply because a new folder structure has been proposed.

Do not delete existing raw data during migration.

---

# 5. Team-wide portability requirement

All project code and documentation must work for every team member.

Never commit paths such as:

```text
/home/<person>/
/Users/<person>/
C:\Users\<person>\
Desktop/
```

Do not assume:

- a particular username,
- a particular laptop,
- one person's virtual environment,
- one person's absolute paths,
- one person's GPU,
- one person's shell configuration.

Repository paths must be resolved relative to the repository root or supplied through CLI/configuration.

Preferred pattern:

```python
ROOT = Path(__file__).resolve().parents[1]
```

or an explicit CLI argument such as:

```bash
--raw-root data/raw
```

Machine-specific settings belong in ignored local configuration, environment variables, or CLI arguments.

Every Markdown context file must describe the project for the whole team.

Avoid phrases such as:

```text
my dataset
my machine
Rishabh's folder
my model
```

Prefer:

```text
project dataset
development machine
local raw mirror
TrackShift pass model
```

---

# 6. Data sources

Primary telemetry source:

```text
TracingInsights/2022
TracingInsights/2023
TracingInsights/2024
TracingInsights/2025
TracingInsights/2026
```

Additional sources may include:

- FastF1,
- OpenF1,
- FIA sporting regulations,
- FIA technical regulations,
- FIA event-specific Power Unit information,
- FIA race-control/event documents.

Every externally sourced field must record its provenance.

Do not silently mix sources.

---

# 7. Raw-data principle

Raw data is immutable.

Target raw layout:

```text
data/
    raw/
        tracinginsights/
            2022/
            2023/
            2024/
            2025/
            2026/

        fia/
            regulations/
            event_documents/

        external/
            fastf1/
            openf1/
```

The exact migration into this layout must happen only after the local-data audit.

Raw files must never be altered to fit our schema.

Normalization happens downstream.

---

# 8. Session inclusion policy

Different F1 sessions answer different modelling questions.

Do not combine them blindly.

## 8.1 Historical seasons: 2022 to 2025

Core sessions:

```text
Qualifying
Sprint Qualifying where available
Sprint where available
Race
```

Practice is not part of the default historical core dataset.

Historical Practice may be added later only if ablation testing demonstrates meaningful benefit.

Primary historical uses:

- overtaking priors,
- driver behaviour,
- team behaviour,
- braking and throttle baselines,
- race-combat dynamics,
- DRS-era pass modelling,
- track-specific historical behaviour.

Do not use historical electrical deployment as ground truth for 2026 ERS behaviour.

---

## 8.2 2026

Core sessions:

```text
Practice 1
Qualifying
Sprint Qualifying where available
Sprint where available
Race
```

FP1 is retained because it provides useful clean-air and physics calibration information without requiring all Practice sessions.

Additional FP2/FP3 data may be downloaded selectively for:

- the demo event,
- target teams,
- physics calibration,
- cases where FP1 has insufficient usable laps.

Do not automatically ingest all Practice sessions across all events unless justified.

---

# 9. Roles of individual sessions

## Practice

Use primarily for:

- clean-air vehicle behaviour,
- long-run pace,
- braking references,
- throttle references,
- physics calibration,
- tyre degradation estimates.

Practice laps must be classified before use.

Possible labels:

```text
PUSH
LONG_RUN
COOLDOWN
OUT_LAP
IN_LAP
INTERRUPTED
INVALID
UNKNOWN
```

Do not treat all Practice laps as equivalent.

---

## Qualifying

Use primarily for:

- high-performance envelope,
- near-maximal acceleration,
- braking references,
- high-performance exit speed,
- maximum straight-line behaviour,
- clean-air segment baselines.

Qualifying is not directly equivalent to Race due to fuel, tyres, conditions, traffic, and race management.

---

## Sprint and Race

Use primarily for:

- overtaking,
- defending,
- following behaviour,
- tactical state inference,
- race energy management,
- battle episodes,
- pass probability,
- gap dynamics,
- planner evaluation.

Race and Sprint data are the highest-value behavioural datasets.

---

# 10. Canonical telemetry lake

All usable telemetry must ultimately map into a common distance-based representation.

Default spatial resolution:

```text
20 m
```

Target storage:

```text
data/
    processed/
        telemetry_20m/
            year=YYYY/
                event=<event>/
                    session=<session>/
                        ...
```

Parquet is preferred for processed tabular data.

Partitioning should support efficient access by:

- year,
- event,
- session.

Driver/team partitioning may be added if benchmarking shows a practical benefit.

Do not create thousands of tiny Parquet files unnecessarily.

---

# 11. Canonical telemetry fields

The 20 m lake should preserve or derive the following core fields where the source supports them:

```text
year
event
session
session_type
driver
team
lap
distance_m
lap_fraction

lap_elapsed_s
session_time_s

speed_kmh
engine_rpm
gear
throttle_pct
brake_on

aero_or_drs_raw
drs_open_observed

x_m
y_m
z_m

acc_longitudinal_mps2
acc_lateral_mps2
acc_vertical_mps2

driver_ahead
distance_to_driver_ahead_m
driver_behind
distance_to_driver_behind_m
gap_to_ahead_s
gap_to_behind_s

race_position
sector
tyre_compound
tyre_life_laps
stint

track_status
race_control_state
safety_car_active
virtual_safety_car_active
pit_state
pit_stop_duration_s_offline

air_temperature
track_temperature
rainfall
humidity_pct
air_pressure_hpa
wind_speed_mps
wind_direction_deg
```

Use `pit_state` rather than only a Boolean. Its normalized values are `ON_TRACK`, `PIT_IN`, `PIT_LANE`, `PIT_OUT`, and `UNKNOWN`. `pit_stop_duration_s_offline` is retained for retrospective analysis and labels only.

`drs_open_observed` may be populated only after the source-specific meaning of the raw DRS/aero channel has been verified. It must otherwise remain unavailable.

Fuel and ERS state are high-value context, but are not public observations in the current sources. The lake may contain the following estimates only after the energy twin has produced them:

```text
fuel_load_kg_est
fuel_load_uncertainty_kg
ers_energy_state_est_kj
ers_deployment_est_kw
ers_harvest_est_kw
```

All five must carry `INFERRED` or `SIMULATED` provenance. Never name, store, or train on an estimate as if it were observed telemetry.

The following suggested channels are source-gated extensions, not part of the current core schema:

```text
brake_pressure
steering_angle
tyre_temperature
tyre_pressure
brake_temperature
damage
fuel_consumption
```

Add one only when a documented source provides continuous values with defined units and an availability pattern compatible with the intended deployment data. Do not synthesize these channels from a future pit stop, post-race report, or an unvalidated proxy. An unknown damage state must remain `UNKNOWN`, never be encoded as no damage.

Every field must have a known source, unit, provenance, and availability scope. Fields unavailable in a season must remain explicitly unavailable rather than fabricated. Missing and unavailable are not numeric zero.

---

# 12. Derived telemetry features

Derive meaningful physical and contextual features rather than feeding arbitrary raw channels into models. Every live-model feature must use only information available at or before that 20 m row.

Core physical and geometry features include:

```text
speed_mps
longitudinal_acceleration
gradient
curvature_proxy
track_heading_deg

full_throttle_flag
throttle_lift_flag
coasting_flag

brake_onset
brake_release
braking_intensity_proxy

straight_flag
corner_id
corner_phase
corner_type

distance_to_next_brake
distance_to_next_detection_line
distance_to_next_activation_line
```

`corner_type` must be a versioned, geometry-based track classification, for example hairpin, chicane, slow, medium, fast, left, right, or straight. It is not a free-text circuit label.

Weather must be transformed into track-relative effects before modelling:

```text
wind_head_component_mps
wind_cross_component_mps
air_density_proxy
wet_track_flag
```

Project wind direction onto the local `track_heading_deg`; raw wind direction alone is not comparable between circuit segments. Keep raw weather values in the lake and decide their use by held-out-event ablation.

Derive explicit race-context and data-quality features:

```text
normalized_race_control_state
normal_race_model_eligible
race_control_transition_flag
pit_transition_flag
green_flag_elapsed_s
```

`normal_race_model_eligible` is true only for on-track, normal green-flag racing with no Safety Car, VSC, red/yellow restriction, pit-lane state, or unknown control state affecting the row. Safety Car and VSC telemetry must be retained in the lake for audit and future strategy work, but be excluded from the initial normal-race baselines, battle extraction, pass model, rival-state model, and energy/segment-time calibration.

A race-control or pit-state transition is a hard sequence boundary. Do not smooth, resample windows, calculate rolling features, or continue a battle episode across it. This prevents Safety Car/VSC pace from being mislearned as lifting, coasting, energy saving, or a passing signal.

Pit-lane, pit-in, and pit-out rows are retained with `pit_state` but are outside the initial normal-race model family. `pit_stop_duration_s_offline` is known only after the stop completes and must never be a live feature.

Derive pair-aware battle features explicitly:

```text
relative_speed_to_ahead_mps
relative_speed_to_behind_mps
closing_rate_ahead_mps
closing_rate_behind_mps
relative_acceleration_to_ahead_mps2
gap_trend_ahead_s_per_s
gap_trend_behind_s_per_s
```

These features must be calculated from aligned contemporaneous or strictly trailing observations. Do not use centered windows or future telemetry. Relative speed must not be skipped: it is a core short-horizon signal for both attack and defence, and complements rather than duplicates the later pass-probability model.

Derive regulated aero and energy context without conflating regulation eras:

```text
historical_drs_eligible
historical_drs_open
overtake_eligible
overtake_state
fuel_load_kg_est
fuel_load_uncertainty_kg
ers_energy_state_est_kj
ers_deployment_est_kw
ers_harvest_est_kw
```

For 2022 to 2025, DRS values are historical covariates only. For 2026, derive `overtake_eligible` and `overtake_state` exclusively through the rule engine and FIA event configuration, never from a raw DRS channel. Fuel and ERS estimates must be causal, uncertainty-aware, and tagged `INFERRED` or `SIMULATED`.

Do not assume raw `x`, `y`, or `z` coordinates generalize across circuits. Prefer geometry and track-relative quantities derived from them. A candidate feature group earns inclusion only through leakage-safe, held-out-event ablation with the same availability it will have at deployment.

---

# 13. Segment representation

20 m is the physics/replay resolution.

Strategic decisions operate on track segments.

Target segment boundaries are based primarily on:

- braking onset,
- return to sustained high/full throttle,
- important FIA lines,
- major zone boundaries.

Very short segments should be merged where appropriate.

Expected order of magnitude:

```text
30 to 40 strategic segments per lap
```

Store:

```text
segment_id
start_distance_m
end_distance_m
segment_length_m

segment_time_s

entry_speed_kmh
exit_speed_kmh
max_speed_kmh
mean_speed_kmh

brake_onset_m
brake_fraction

full_throttle_fraction
lift_fraction
coast_fraction

mean_gradient
elevation_change

gap_entry
gap_exit

tyre_compound
tyre_life
stint
```

Target dataset:

```text
data/processed/segments/
```

---

# 14. Baseline hierarchy

Do not compare every driver directly to the entire field without context.

Maintain three levels of baseline.

## Driver baseline

For example:

\[
r_{driver}
=
x_{current}
-
median(x_{same\ driver,same\ track,same\ segment})
\]

## Team baseline

\[
r_{team}
=
x_{current}
-
median(x_{same\ team,same\ track,same\ segment})
\]

## Field baseline

\[
r_{field}
=
x_{current}
-
median(x_{all\ cars,same\ track,same\ segment})
\]

Maintain these for quantities such as:

- segment time,
- exit speed,
- braking point,
- throttle commitment,
- top speed.

This allows all teams to contribute to global models without pretending that all cars have identical characteristics.

---

# 15. Combining teams

Global models should generally use all teams.

Do not train a separate base model for every team unless evaluation proves that necessary.

Preferred hierarchy:

```text
global behaviour
    |
team context
    |
driver context
    |
track context
    |
session/context
```

Mercedes, McLaren, Ferrari, Red Bull, and other teams may all contribute to shared models.

Team-specific effects may be encoded explicitly.

Physics parameters may also be team/event specific where appropriate.

Never assume that cars using similar PU architecture have identical aerodynamic behaviour.

---

# 16. Driver pair definition

A "pair" does not mean every possible pair of drivers.

Do not generate all \(N(N-1)\) combinations.

A pair exists when one driver is directly interacting with the car immediately ahead.

Example:

```text
attacker = HAM
defender = ANT
```

only while ANT is the relevant car ahead of HAM.

If HAM passes ANT and begins following NOR:

```text
HAM -> ANT
```

ends and:

```text
HAM -> NOR
```

begins.

Pairing is therefore dynamic.

---

# 17. Pairwise features

For an attacker \(A\) and defender \(D\), derive:

\[
\Delta v=v_A-v_D
\]

\[
\Delta a=a_A-a_D
\]

\[
\Delta t_{segment}=t_A-t_D
\]

\[
\Delta tyreAge=age_A-age_D
\]

and closing rate:

\[
closingRate
=
-\frac{d(gap)}{dt}
\]

Useful pair features include:

```text
gap
gap_change

delta_speed
delta_exit_speed
delta_segment_time
delta_acceleration

tyre_age_delta
compound_pair

recent_pace_delta

closing_rate
closing_rate_trend

brake_point_delta

attacker_team
defender_team

attacker_driver
defender_driver

track
segment
zone
```

Identity features must be validated carefully to ensure models do not merely memorize drivers or teams.

---

# 18. Battle episodes

Segment-level pair rows belonging to the same continuous fight should be grouped into a battle episode.

Example identifier:

```text
2026_GBR_Sprint_HAM_ANT_Battle03
```

A battle may contain:

```text
segment 21
segment 22
segment 23
segment 24
...
```

Store battle-level information such as:

```text
battle_id
attacker
defender

start_lap
end_lap

duration_segments
duration_s

minimum_gap
maximum_closing_rate

detection_opportunities

pass_attempted
pass_completed
```

Battle episodes serve two purposes:

1. sequence modelling,
2. leakage-safe train/test splitting.

Never split rows from the same battle between training and test sets.

---

# 19. Overtake opportunity dataset

The pass model should not train on every telemetry row.

Construct one row per meaningful overtaking opportunity.

Target features may include:

```text
year
event
lap
zone

attacker
defender
attacker_team
defender_team

gap_at_detection
gap_at_activation
gap_at_braking

delta_speed_detection
delta_speed_activation
delta_speed_braking

tyre_age_delta
compound_pair

recent_pace_delta
closing_rate

eligibility

distance_activation_to_brake
distance_remaining_in_zone

position_context

passed
```

Historical 2022 to 2025 opportunities are DRS-era priors.

2026 opportunities are the current-regulation calibration domain.

Do not claim historical DRS equals 2026 Overtake.

---

# 20. 2026 Overtake system

The 2026 Overtake mechanism must be represented explicitly in the state machine.

Do not infer Overtake eligibility directly from the TracingInsights `drs` channel.

Regulatory/event information must come from FIA configuration.

For each event maintain configuration containing, where applicable:

```text
detection_gap_s
detection_line_m
activation_line_m

overtake_enabled

normal_power_envelope
overtake_power_envelope

active_aero_zones

PU power-limited sectors

race-control disable state
```

The normal eligibility transition is conceptually:

```text
approach Detection Line
        |
        v
evaluate gap at Detection Line
        |
        v
gap inside regulatory threshold?
     /       \
   yes        no
    |          |
 armed       not armed
    |
reach Activation Line
    |
Overtake may become available
subject to regulations and control state
```

The key strategic effect is that energy spent before the Detection Line can alter whether Overtake becomes available later.

Therefore the planner must understand:

```text
energy deployment
    ->
segment time
    ->
projected gap at Detection Line
    ->
eligibility probability
    ->
future Overtake availability
    ->
future velocity / time advantage
    ->
pass probability
```

This discontinuity is one reason the shadow price of energy may spike before a Detection Line.

---

# 21. Event rule configuration

Regulatory values must not be scattered as constants through Python modules.

Use versioned configuration such as:

```text
config/
    rules/
        2026/
            common.yaml
            british_grand_prix.yaml
            spa.yaml
            ...
```

Every regulatory key should include or reference its source.

Example structure:

```yaml
overtake:
  detection_gap_s: ...
  detection_line_m: ...
  activation_line_m: ...
  source: ...

mgu_k:
  source: ...
```

Before changing rule values, verify the latest FIA Sporting Regulations, Technical Regulations, and event-specific documents.

Regulations are external inputs, not learned parameters.

---

# 22. Overtake-derived strategic features

Derive:

```text
gap_at_detection
eligibility_margin

projected_gap_at_detection
probability_eligible

energy_required_to_unlock

gap_at_activation
delta_v_at_activation

distance_detection_to_activation
distance_activation_to_brake

predicted_overtake_advantage
```

Define eligibility margin:

\[
m_{elig}
=
g_{threshold}
-
g_{detection}
\]

Also estimate:

\[
P(g_{detection}<g_{threshold})
\]

when projected gap is uncertain.

Do not reduce eligibility to a deterministic boolean when uncertainty is material.

---

# 23. Counterattack modelling

Passing the rival does not end the planning problem.

After a pass:

```text
our car becomes defender
previous defender becomes attacker
```

The previous rival may become Overtake-eligible at a later Detection Line.

The two-lap value function should therefore price:

```text
P(pass now)
P(remain ahead)
P(rival eligible later)
P(repass)
```

The planner should optimize probability of being ahead at the horizon rather than only probability of completing the immediate pass.

---

# 24. Rival-state dataset

Create one row per battle segment.

Candidate features include:

```text
segment_time_residual_driver
segment_time_residual_team
segment_time_residual_field

exit_speed_residual_driver
exit_speed_residual_team

brake_point_shift
full_throttle_clipping

closing_rate
gap_change
recent_gap_trend

tyre_age
compound

traffic_context
race_context
```

Target conceptual hidden states:

```text
CONSERVING
BALANCED
DEPLOYING
DERATING
```

These states are latent.

Do not pretend real telemetry supplies ground-truth tactical labels.

---

# 25. Rival model benchmark track

Do not decide beforehand that one rival model is best.

Compare candidates such as:

```text
HMM
HSMM
gradient-boosted rolling-window classifier
GRU
TCN
small temporal Transformer if justified
```

The HMM/HSMM family provides interpretability.

Neural temporal models provide nonlinear sequence benchmarks.

Select using validation rather than model complexity.

Relevant metrics include:

```text
synthetic state recovery
predictive log likelihood
next-segment prediction
stability
calibration
latency
```

If CONSERVING and DERATING cannot be separated reliably, merge states rather than invent confidence.

---

# 26. Pass-probability model benchmark track

Train multiple candidates.

Minimum benchmark set:

```text
Logistic Regression
CatBoost or LightGBM
XGBoost
optional small MLP
```

The logistic model is the interpretable baseline.

Tree models provide nonlinear alternatives.

Final selection must prioritize calibrated probability quality.

Metrics include:

```text
Brier score
log loss
reliability / calibration
ROC-AUC
PR-AUC when appropriate
```

A model with slightly lower ROC-AUC but significantly better probability calibration may be preferable for DP.

The planner consumes probabilities, not just classifications.

---

# 27. Probability calibration

Where needed, compare:

```text
uncalibrated
Platt / sigmoid calibration
isotonic calibration
```

Calibration fitting must use data separate from final test data.

Do not calibrate and evaluate on the same event.

---

# 28. Energy twin

Public telemetry does not directly expose the real battery SOC or MGU-K electrical power.

Do not claim that it does.

The energy twin estimates electrical contribution from a longitudinal power balance.

Conceptually:

\[
P_{wheel}
=
mav
+
P_{drag}
+
P_{rolling}
+
P_{gradient}
\]

and:

\[
P_K
\approx
\frac{P_{wheel}}{\eta}
-
P_{ICE}
\]

where model parameters may include:

```text
mass
CdA
rolling resistance
drivetrain efficiency
ICE effective power map
aero state
gradient
```

Electrical state estimates must be tagged:

```text
SIMULATED
```

unless measured values are explicitly supplied.

---

# 29. Physics calibration hierarchy

Compare:

```text
pure analytical physics
global calibrated physics
team-specific calibrated physics
event-specific calibrated physics
physics + ML residual correction
```

A hybrid model may take the form:

\[
prediction
=
physics(x)
+
MLResidual(x)
\]

Any residual model must improve held-out performance without violating physical expectations.

Potential metrics:

```text
MAE
RMSE
error by speed regime
error by segment type
physical constraint violations
```

---

# 30. Segment-time / energy model

The planner needs an estimate of:

\[
\Delta E
\rightarrow
\Delta t
\]

for each segment.

A simple representation may be:

\[
t_k(d,L)
=
t_{base,k}
-
a_k\Delta E
+
c_kL
\]

More advanced models may be benchmarked.

Candidate approaches:

```text
analytical physics
linear / ridge model
gradient boosting
physics + residual model
small neural regressor
```

The winner must remain physically plausible.

---

# 31. Dynamic programming

DP is an optimization algorithm, not a trained ML model.

Core state contains at least:

```text
segment k
energy e
gap g
Overtake eligibility epsilon
```

Additional state variables may be included where required by legal energy accounting.

Actions may include:

```text
deploy level
lift/coast amount
```

with tactical names attached afterwards.

Example deployment levels:

```text
0
0.25
0.50
0.75
1.00
```

Illegal actions must be removed before scoring.

Do not assign a low reward to illegal actions.

Illegal actions must not enter the candidate action set.

---

# 32. Rule engine

The rule engine is deterministic.

It should answer:

> Which actions are legally and operationally available in this state?

It must enforce:

- applicable power envelopes,
- electrical limits,
- Overtake state,
- event-specific limits,
- disabled conditions,
- race-control state,
- relevant energy accounting.

Every boundary rule should have tests just below and above its threshold.

---

# 33. Planner

The planner combines:

```text
current state
rival belief
energy twin uncertainty
legal actions
DP terminal value
```

Beam search may be used for near-term uncertainty while DP supplies long-horizon value.

Candidate strategies must be benchmarked against baselines such as:

```text
greedy attack
longest-straight deployment
lap-time-only optimization
DP
beam + DP
oracle rival-state policy
```

Metrics include:

```text
P(ahead)
final energy
rule violations
CVaR / downside risk
latency
```

---

# 34. Model selection principle

Never write:

> Model X is best because it is more advanced.

Every model family must earn its place through evaluation.

Selection hierarchy:

1. generalization,
2. probability calibration where relevant,
3. physical/regulatory validity,
4. stability,
5. latency,
6. interpretability,
7. implementation complexity.

Complexity alone is not a benefit.

---

# 35. Feature inclusion principle

More columns do not automatically improve the system.

Every candidate feature group should pass an ablation test.

Examples of feature groups:

```text
tyre context
weather
team identity
driver identity
rolling battle trends
geometry
race context
```

Keep a feature group only if it improves relevant held-out metrics or provides necessary causal/contextual information without materially degrading other metrics.

---

# 36. Features that should not be added blindly

Avoid directly feeding meaningless identifiers or noisy metadata such as:

```text
raw x coordinate
raw y coordinate
driver number
team colour
headshot URL
arbitrary timestamp
raw race-control text
```

Convert raw data into useful structured context first.

Weather variables such as pressure, humidity, and wind may be retained in the lake but need not enter every model.

---

# 37. Race-context labels

Race telemetry should be contextualized.

Possible labels include:

```text
CLEAN_AIR
FOLLOWING
CLOSE_FOLLOWING
ATTACKING
DEFENDING
TRAFFIC
SAFETY_CAR
VSC
YELLOW
PIT_IN
PIT_OUT
WET
DRY
```

These labels may be deterministic, inferred, or hybrid.

Their provenance must be recorded.

A slow segment under yellow flags must not be interpreted as energy conservation.

---

# 38. Tyres and fuel context

Tyre compound and tyre life are required contextual variables where available.

Develop tyre-normalized pace features where useful.

Fuel load is generally not directly available.

If lap/session position is used as a fuel proxy, label it explicitly as a proxy.

Do not represent estimated fuel as observed fuel.

---

# 39. Weather

At minimum retain:

```text
rainfall
track_temperature
air_temperature
```

Use these as contextual variables or filters.

Additional weather channels may be incorporated only after demonstrating usefulness.

---

# 40. Data leakage rules

Never randomly split telemetry rows from the same race into train and test.

Preferred split units include:

```text
event
battle_id
weekend
track
year
```

Useful evaluation designs include:

```text
train 2022-2024, validate 2025, test 2026

leave-one-event-out

leave-one-track-out

earlier 2026 events -> later 2026 events
```

All rows belonging to one battle stay in the same split.

Final test events must remain untouched until model and feature choices are frozen.

---

# 41. Regulation-era shift

2022 to 2025 and 2026 are different regulatory domains.

Historical data provides useful behavioural priors.

2026 provides final current-regulation calibration.

Do not assume:

```text
historical DRS behaviour == 2026 Overtake behaviour
```

Potential approaches include:

- regulation-era feature,
- historical pretraining/prior,
- 2026 recalibration,
- domain weighting,
- separate historical and 2026 models followed by comparison.

---

# 42. Uncertainty

The decision engine must propagate uncertainty rather than hiding it.

Examples:

Pass model:

```text
calibrated probability
ensemble spread
```

Rival model:

```text
full state distribution
```

Physics twin:

```text
parameter draws
confidence range
```

Detection eligibility:

```text
P(eligible)
```

Planner:

```text
expected value
CVaR or downside metric
```

If confidence is poor, average across plausible states rather than forcing a single categorical decision.

---

# 43. Provenance

Every displayed or important derived quantity should carry one of:

```text
OBSERVED
DERIVED
INFERRED
SIMULATED
RULE
```

Definitions:

OBSERVED  
Directly supplied by telemetry or trusted external data.

DERIVED  
Deterministically calculated from observed values.

INFERRED  
Estimated probabilistically from observations.

SIMULATED  
Produced by a physics model or simulation.

RULE  
Defined by regulations or event configuration.

Do not blur these categories.

---

# 44. Live-safe versus offline-only features

Every model feature must be classified as:

```text
LIVE_SAFE
OFFLINE_ONLY
```

A LIVE_SAFE feature is available at or before the prediction timestamp.

An OFFLINE_ONLY feature may use future information and can only be used for:

- labels,
- evaluation,
- retrospective analysis.

Training a live model on future information is prohibited.

---

# 45. Dataset registry

Create a machine-readable dataset registry.

Recommended location:

```text
config/data_registry.yaml
```

For every processed dataset record:

```text
name
description
source
schema_version
partitioning
primary key
producer script
consuming components
live/offline status
```

---

# 46. Feature registry

Recommended location:

```text
config/feature_registry.yaml
```

For every important feature record:

```text
name
definition
unit
source
derivation
allowed sessions
supported years
live_safe
provenance
consuming models
```

This prevents training on poorly defined or unavailable features.

---

# 47. Proposed processed datasets

The target system should eventually produce:

```text
telemetry_20m
segments
driver_segment_baselines
team_segment_baselines
field_segment_baselines
pairwise_segment_features
battle_episodes
overtake_opportunities
rival_state_features
physics_calibration
race_context
event_rules
```

Not all need to be built immediately.

Each should have an explicit schema and producer.

---

# 48. Target repository structure

Long-term structure may evolve toward:

```text
Trackshift/
|
+-- AGENTS.md
+-- README.md
|
+-- config/
|   +-- data_registry.yaml
|   +-- feature_registry.yaml
|   +-- rules/
|       +-- 2026/
|
+-- data/
|   +-- raw/
|   +-- interim/
|   +-- processed/
|
+-- artifacts/
|   +-- schema_audit/
|   +-- data_quality/
|   +-- models/
|   +-- validation/
|   +-- dp/
|
+-- scripts/
|   +-- data/
|   +-- features/
|   +-- train/
|   +-- evaluate/
|   +-- simulate/
|
+-- src/
|   +-- trackshift/
|       +-- data/
|       +-- features/
|       +-- track/
|       +-- rules/
|       +-- pass_model/
|       +-- rival/
|       +-- twin/
|       +-- value/
|       +-- planner/
|       +-- sim/
|       +-- ui/
|
+-- tests/
```

Do not reorganize the repository purely to match this tree before there is corresponding functionality.

Move incrementally.

---

# 49. Script conventions

Scripts must:

- use CLI arguments,
- have `--help`,
- avoid personal absolute paths,
- fail clearly,
- never silently skip malformed critical data,
- write manifests,
- record schema/version information,
- support deterministic reruns where practical.

Data-producing scripts should report:

```text
input scope
files discovered
accepted records
rejected records
output path
configuration
schema version
```

---

# 50. Download-script conventions

Future download scripts must be driven by explicit scope configuration.

They should support selections such as:

```text
years
events
sessions
drivers
```

Default scope should follow the project session policy rather than blindly downloading every available file.

However, no new downloader should be finalized until the current local data has been audited.

Downloaders must be:

- resumable,
- idempotent,
- non-destructive,
- explicit about source repository,
- safe if partial data already exists.

Never delete valid local data merely because it is not currently in the core training subset.

---

# 51. Audit before migration

The next data task is a local inventory audit.

It should answer:

```text
What exists locally?
Where is it?
How large is it?
Which years?
Which events?
Which sessions?
Which drivers?
Which telemetry files?
Which metadata?
Which processed artifacts?
Which duplicates?
Which incomplete repositories?
```

Only after this audit should the data layout or downloader be changed.

The audit should itself be usable by every team member.

---

# 52. Testing

Every reusable component should have tests.

Critical test categories include:

## Data

```text
schema validation
monotonic distance
valid ranges
missing-value behaviour
resampling
metadata joining
```

## Pairing

```text
driver-ahead assignment
battle start/end
pair switching after pass
no impossible self-pair
```

## Rules

```text
threshold below
threshold at boundary
threshold above

Detection Line behaviour
Activation Line behaviour
disabled Overtake behaviour
```

## Features

```text
no lookahead
unit correctness
residual definitions
rolling windows
```

## Planner

```text
zero illegal actions
deterministic seeded simulations
energy accounting
boundary conditions
```

---

# 53. Reproducibility

Every major generated artifact should record:

```text
git commit
source datasets
source years/events
configuration
schema version
model parameters
random seed where relevant
created timestamp
```

Model artifacts must record their feature schema.

A model must not silently accept a differently ordered feature matrix.

---

# 54. Storage and compute

Processed large tables should prefer:

```text
Parquet
PyArrow
Polars
DuckDB
```

Pandas remains acceptable for smaller operations.

PyTorch may be used for temporal neural models.

Do not use GPU compute merely because it is available.

Use the simplest compute stack that satisfies the modelling requirement.

---

# 55. Evaluation by component

Do not report one meaningless "overall AI accuracy."

Pass model:

```text
Brier
log loss
calibration
ROC-AUC
PR-AUC
N
```

Rival model:

```text
state recovery on labelled simulation
predictive log likelihood
stability
```

Energy/segment model:

```text
MAE
RMSE
physical sanity
```

Planner:

```text
P(ahead)
final energy
rule violations
CVaR
latency
```

Always report sample size.

---

# 56. Simulator

The simulator is required to evaluate strategies that cannot be safely validated from historical race outcomes alone.

It should:

- operate on the same segment representation,
- use the same rule engine,
- use the same pass probability API,
- use explicit rival policies,
- support uncertainty draws,
- record seeded episodes.

The simulator is not proof of real-world performance.

Its assumptions must be disclosed.

---

# 57. Claims we may make

Examples:

```text
The rule engine is legal by construction under the encoded rule configuration.

The planner was evaluated against defined heuristic baselines.

The pass model was evaluated on held-out events.

The rival filter was evaluated using synthetic labelled trajectories and real predictive metrics.

The energy state is inferred/simulated from public telemetry rather than observed directly.
```

---

# 58. Claims we must not make

Do not claim:

```text
we reconstructed the real F1 battery SOC

we know the rival's real deployment mode

historical DRS is identical to 2026 Overtake

public drs telemetry directly represents 2026 active aero or Overtake

simulator performance proves real race performance

one global accuracy number validates the entire system
```

---

# 59. Development sequencing

The preferred implementation sequence is:

```text
Phase 0
local data inventory

Phase 1
raw data scope + downloader redesign

Phase 2
canonical data lake

Phase 3
session/lap classification and context

Phase 4
track segmentation and geometry

Phase 5
driver/team/field baselines

Phase 6
pair and battle extraction

Phase 7
overtake-opportunity dataset

Phase 8
rival-state feature dataset

Phase 9
pass-model benchmarks

Phase 10
rival-model benchmarks

Phase 11
physics calibration and segment-time model

Phase 12
2026 rule engine

Phase 13
dynamic programming and shadow price

Phase 14
uncertainty-aware planner

Phase 15
simulator and baselines

Phase 16
UI, integration, validation, demo
```

Some phases may overlap once interfaces are stable.

---

# 60. Interfaces first

Before implementing a major component, define:

```text
input schema
output schema
units
provenance
failure behaviour
```

Subsystems must communicate through explicit contracts rather than importing internal implementation details from each other.

---

# 61. Scope discipline for automated agents

When a task says:

```text
implement only Phase X
```

do not opportunistically implement later phases.

Do not:

- refactor unrelated code,
- redesign public interfaces without need,
- train models outside requested scope,
- create UI while working on data,
- change rules while working on resampling.

Small focused changes are easier to review and safer during the hackathon.

---

# 62. Documentation discipline

Major modules should have nearby documentation or clear module docstrings describing:

```text
purpose
inputs
outputs
assumptions
units
provenance
tests
```

Documentation must be written for all team members.

Do not create context files that only make sense on one person's machine.

If a subsystem requires special setup, document the portable setup first and machine-specific overrides separately.

---

# 63. Current immediate next step

Do not change the current downloader yet.

First audit the local data organization.

The audit should produce a report that we can use to design:

1. the final raw-data layout,
2. selective session download policies,
3. migration logic,
4. revised download scripts,
5. processed-data build commands.

Existing raw mirrors and working processed outputs should be preserved during this transition.

---

# 64. Final engineering principle

TrackShift should not be a collection of models connected after the fact.

It should be a coherent decision system:

```text
observations
    ->
validated data
    ->
context
    ->
probabilistic inference
    ->
physics
    ->
rules
    ->
value
    ->
decision
```

Every component must have a reason to exist.

Every learned model must beat meaningful alternatives.

Every rule must have provenance.

Every inferred quantity must be labelled as inferred.

Every simulator assumption must be explicit.

Every script and Markdown file must be usable by the full team.