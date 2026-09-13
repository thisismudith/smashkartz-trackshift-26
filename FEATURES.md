# TrackShift — UVP, features and engineering basis

**Audience:** judges, reviewers and anyone preparing the pitch. Every number here
is measured and traceable to an artifact in this repository; where something is
estimated rather than observed, it says so.

---

## 1. Unique value proposition

> **Every joule has a different value.** TrackShift decides *where* one more unit
> of electrical energy is most valuable for winning race position — not merely
> for lap time.

Most race analysis asks "where is the car fastest?". TrackShift asks:

> *"Is spending energy now worth more than saving it for the next Overtake
> opportunity or defence phase?"*

The hero concept is the **energy shadow price**: the marginal race-position value
of one additional unit of energy at each track segment. The same joule is worth
different amounts depending on gap, eligibility, rival state, regulations and the
opportunity coming up.

**The formula for a slide:**

```
Energy value = track position × gap × eligibility × rival behaviour × regulations
```

We optimise **probability of being ahead**, not sector time.

---

## 2. Four novelty pillars

### 2.1 Energy has contextual value, not a fixed value

A pass is not a lap-time problem. Deploying 0.5 MJ on a straight where no
overtake is legal buys a tenth and loses the tenth back. Deploying it 300 m
before an Activation Line, against a rival who is conserving, can change track
position for the rest of the race. The planner prices that difference.

### 2.2 Regulations are inside the intelligence, not a post-filter

Detection, Activation, eligibility and Overtake states are part of the decision
itself. The rule engine generates **only legal candidate actions before
planning** — illegal actions are never scored, so they cannot win.

For 2026 specifically, the system **prevents historical DRS fields from silently
becoming 2026 Overtake inputs**. DRS is admitted only for named 2022–2025 audit
and prior consumers and is refused for every 2026 model path, enforced in code
(`validate_feature_admission`) rather than by convention.

> *"We do not let an old racing rule leak into a new regulation model."*

### 2.3 It models a battle, not a single car

A pass is a two-car interaction: can I close the gap, am I eligible, is
deployment legal and worthwhile, what will the rival do, and does the pass expose
me to a counterattack? The pipeline carries **33,649 detected battle episodes**
and resolves every opportunity to the specific episode it belongs to.

### 2.4 Trust is designed in

Every quantity carries provenance — `OBSERVED`, `DERIVED`, `INFERRED`,
`SIMULATED` or rule-based — plus uncertainty, causal cutoff and rule version.

> *"When public telemetry cannot tell us the real battery state or rival intent,
> we do not fake certainty. We show the estimate, its provenance and its
> uncertainty."*

Public telemetry does not reveal battery SOC or fuel load. Fuel is `INFERRED`,
energy is `SIMULATED`, and both are labelled that way in the API. That is
stronger than pretending to hold private team data.

---

## 3. A pass is three decisions, not one

The single most defensible piece of causal engineering in the project.

| Checkpoint | The question | What it may see |
|---|---|---|
| **DETECTION** | "Can we get within the required gap at the Detection Line?" | Everything at or before the Detection Line |
| **ACTIVATION** | "The opportunity is legal now. Should we spend energy to convert it?" | Also activation-time quantities |
| **BRAKING** | "After committing, does the move still improve race position?" | Also braking-point quantities |

**A DETECTION model can never see activation speed.** This is enforced three
times over — in the feature builder, in the model's feature selection, and again
in the serving layer, which returns `CHECKPOINT_VIOLATION` if a caller supplies a
future-scoped field. A model that knows the activation speed at the Detection
Line scores beautifully in validation and is worthless at the decision point.

Measured evidence: feature counts rise 15 → 16 → 17 across the checkpoints, and
held-out performance rises with them.

---

## 4. Data preprocessing

### 4.1 The pipeline

```
Public F1 telemetry (FastF1) + FIA 2026 regulations
  ↓  resample to a fixed 20 m spatial grid          →  15,427,924 rows
  ↓  segment the track by geometry                  →   1,124,954 segment rows
  ↓  overlay weather, tyre pace, fuel, energy twin
  ↓  detect attacker–defender battle episodes       →      33,649 episodes
  ↓  emit opportunities at 3 decision checkpoints   →       4,970 opportunities
```

**Coverage:** 32 drivers, 11 teams, 14 events, 5 session types (Practice 1,
Qualifying, Sprint Qualifying, Sprint, Race).

### 4.2 Why 20 metres

Time-sampled telemetry is not comparable between cars — two drivers at the same
lap time occupy different positions at the same clock reading. Resampling onto a
**fixed 20 m distance grid** makes "the same place on track" a join key, which is
what turns two independent telemetry streams into a battle.

### 4.3 Causal cutoffs, not row filters

Features are built from a view of the telemetry **truncated at the checkpoint
distance**, never by building a full row and blanking fields afterwards. The
difference matters: blanking leaves the value computable from siblings, while
truncation makes it unavailable by construction.

### 4.4 Handling the lap boundary

The Detection Line sits near the lap end, so **90% of opportunities wrap into the
following lap** — 100% at every circuit except Canada and Monaco. Ordering by raw
lap distance rejects every wrapping opportunity, because the activation distance
is numerically *smaller* than the detection distance. Rows therefore carry
`feature_cutoff_offset_m`, distance travelled since detection, which is strictly
increasing. Before this was found, 9 of 11 circuits produced nothing.

### 4.5 What is inferred, and said so

| Quantity | Provenance | Why |
|---|---|---|
| Battery SOC / ERS energy | `SIMULATED` | Not in public telemetry; estimated by a physics twin |
| Fuel load | `INFERRED` | Estimated from the twin's ICE work |
| Tyre degradation | `DERIVED` | Computed from pace, offline |
| Gap, speed, position | `OBSERVED` | Directly in the feed |
| Overtake legality | Rule-based | From the versioned FIA rule config |

---

## 5. How the dataset is divided

### 5.1 The split unit is `battle_id`, not the row and not the event

The leak this prevents: the same two cars, the same few laps, scored on both
sides of a train/test boundary. Splitting by row would put one battle's three
checkpoints in different folds.

Every opportunity is resolved to its exact C8 episode. Matching on the lap alone
was not enough — C8 emits mostly single-lap episodes and often several per pair
per lap, leaving **28.4% ambiguous**. Adding the within-lap distance extent
resolved it: on the real data all 5,745 multi-episode (pair, lap) cells have
**disjoint** distance spans. Join coverage went **68.4% → 92.5%, with zero
ambiguity**.

Opportunities with no C8 episode keep a null `battle_id` and a recorded reason.
They are **excluded from training, never relabelled** — a fabricated id would
place unrelated rows in one split group and defeat the guarantee entirely.

### 5.2 The assignment is written down, not re-derived

`data/processed/split_assignments/` holds the C9 assignment — **2,428 battle
groups**, hashed as `c9_split_assignments_v1:6a1d0320dee7`. Every consumer reads
it rather than recomputing, so two runs cannot silently disagree about which
battle went where. A stale assignment is refused.

### 5.3 The frozen final test

The **2026 British Grand Prix is held out of every training, validation and
calibration path** — 412 labelled opportunities. It is read exactly once, behind
a deliberate `--i-understand-this-is-one-shot` flag, because a frozen test set is
only worth having if it is hard to spend by accident.

### 5.4 The documented split, and what we can currently run

| | CP-14 specifies | Currently available |
|---|---|---|
| Train | 2022–2024, all events | 2026 only |
| Validate | 2025 | — |
| Test | 2026 excluding British GP | 2026 excluding British GP ✅ |
| Frozen | 2026 British GP | 2026 British GP ✅ |

The historical seasons are not yet built, so the design resolves to
**leave-one-event-out over `battle_id`** across 6 scorable circuits. The split
unit — the thing that protects against leakage — is unchanged. What is missing is
the era axis.

This is graded **`INTERIM`** by the code itself, and the grade travels into every
manifest, gate list and report. A run cannot be quoted as a CP-14 pass unless the
year table was actually used.

---

## 6. How training is organised by driver, team and event

The physics calibration is a **five-rung hierarchy**, each rung more specific than
the last, each accepted only if it beats the previous on held-out error:

| Rung | Fitted | Cells | Result |
|---|---|---:|---|
| 1. Analytical | Nothing — priors only | 1 | baseline |
| 2. Global | One `(CdA, Crr, η, P_ICE)` for all cars | 1 | all four parameters at their bounds |
| 3. **Team-specific** | Per-team aerodynamics and efficiency | **11 teams** | no improvement |
| 4. **Event-specific** | Per team, per circuit | **17 cells fitted, 126 fell back** | no improvement |
| 5. Physics + ML residual | Rung 4 plus a small learned residual | 1 | best MAE, still short of target |

**Why per-team.** Two cars with the same power unit can have very different aero.
A single global `CdA` forces one number onto eleven different cars.

**Why per-event.** Downforce configuration changes per circuit — Monza and Monaco
are not the same car.

**Why 126 cells fall back.** A (team, event) cell needs ≥30 *clean laps* to be
fitted honestly. Most cells do not have them, and they fall back to the team rung
with the fallback recorded. This guard was originally counting segment **rows**
rather than laps, making it ~30× too lenient; every cell passed and nothing ever
fell back.

**Driver identity is held behind a flag.** §17: if identity alone carries most of
the signal, the model is memorising drivers rather than learning racecraft. The
benchmark runs with and without it and reports both.

---

## 7. How evaluation is done

### 7.1 Selected on calibration, not accuracy

The planner consumes **probabilities**, not classifications. So a model with
slightly lower ROC-AUC and materially better calibration wins. Primary metric is
**Brier**; ECE and reliability diagrams carry equal weight.

This is enforced, not merely stated: the fine-tuning harness flags a calibration
regression even when the objective it was asked to maximise improved.

### 7.2 Out-of-fold by construction

The evaluation refits per fold and scores only that fold's held-out rows. Scoring
the saved artifact on rows it was fitted on produces a beautiful and meaningless
picture — and it is the easy mistake, because the artifact is right there.

### 7.3 Thresholds are a decision, so four are reported

A confusion matrix needs a threshold the model does not have. At a ~10% base
rate the textbook 0.5 predicts almost nothing and scores ~89% accuracy by doing
nothing. Reported instead: `naive_0.5`, `base_rate`, `max_f1`, `max_youden`, with
the full sweep plotted so the precision/recall trade is the subject rather than a
footnote.

### 7.4 Every gate is a measurement

- Beat a constant-base-rate predictor on Brier
- Trees beat logistic regression, or the features are weak rather than the models
- **ACTIVATION and BRAKING outperform DETECTION** — they see strictly more, so if
  DETECTION wins, suspect leakage
- Leave-one-track-out variance below a ceiling
- Feature-group ablation (leave-one-out **and** add-one-in) with intervals
  against a measured seed-to-seed noise floor

### 7.5 Measured results on the frozen British GP

412 opportunities, 43 passes, base rate 10.4%, model **LightGBM (M10)**:

| Checkpoint | ROC-AUC | Accuracy | Brier | Skill | ECE |
|---|---:|---:|---:|---:|---:|
| DETECTION | 0.725 | 0.893 | 0.0866 | +0.093 | 0.026 |
| ACTIVATION | 0.745 | 0.908 | 0.0828 | +0.133 | **0.024** |
| BRAKING | 0.699 | 0.898 | 0.0915 | +0.042 | 0.059 |

**The three claims worth making:**

1. **Calibration transferred.** ECE 0.024 on an event the model never saw —
   better than in cross-validation. For a planner reading probabilities, this is
   the metric that matters.
2. **A 6.2× lift on the positive class.** At ACTIVATION's F1-optimal threshold
   the model is right **65% of the time it calls a pass**, against a 10.4% prior.
3. **It beats a gap-only reference at all three checkpoints** — so the other
   fourteen features earn their place on unseen data, not just in
   cross-validation.

Accuracy runs 86–91%, but read it beside ROC-AUC: at this base rate a model
answering "no pass" to everything already scores ~90%, so accuracy alone does not
demonstrate skill. ROC-AUC is prevalence-independent and shows the model is
genuinely ordering opportunities.

**Status: `INTERIM`.** These are real measurements on a leakage-safe split. They
are not a CP-14 acceptance pass, because the documented era split needs the
historical seasons.

---

## 8. Feasibility

**Already built and running.**

| Capability | Evidence |
|---|---|
| 20 m causal race-state pipeline | 15.4 M rows across 93 partitions |
| Battle detection | 33,649 episodes |
| Three-checkpoint opportunity dataset | 4,970 opportunities, leakage test passing |
| Rule engine with legal-action masking | Real FIA 2026 rule config, versioned |
| Pass model | 15 artifacts, 3 checkpoints × 5 families |
| Calibration comparison | Uncalibrated vs Platt vs isotonic |
| Ensemble spread | 21 members, 4 families |
| Service API | 17 routes, CPU-only |
| Replay bundle | Self-contained, stub-gated |

**CPU-only.** No GPU is required for any of it. A prediction is well under 50 ms
once loaded; the whole benchmark runs on a laptop.

**Honest about what is development-stage:** the physics twin (CP-20) has no
accepted rung, so the twin, planner and simulator routes remain synthetic and are
labelled `SIMULATED`. That is a deliberate hold — CP-21's energy sensitivity is
currently inverted by segment type, and wiring it would hand the planner a
transition pointing the wrong way.

---

## 9. Viability

**The data is free and public.** FastF1 exposes everything the pipeline consumes.
No private team telemetry, no paid feed, no partnership required — which is
exactly why the provenance discipline matters: the hard problems are *inference*
problems, and we are explicit about which quantities are estimated.

**The output is a decision, not a dashboard.** "Deploy, hold or harvest, and
why" is a thing a strategist can act on. Lap-time prediction is a thing they
already have.

**The honesty is a feature.** Refusing to invent battery SOC is what makes the
rest of the system credible under technical questioning — and it is the reason a
judge can trust the numbers that *are* claimed.

---

## 10. Scalability

**Data.** The pipeline is partitioned by circuit and year, built in parallel
(`--jobs N`), and every stage is incremental. Adding a season means adding
partitions, not reprocessing. The full 2026 opportunity build takes **18 seconds
across 10 cores**.

**Models.** Artifacts are versioned per checkpoint and family with locked feature
schemas, so two runs never silently overwrite each other. The serving layer loads
once at startup and caches per checkpoint.

**Seasons.** The era-handling module already exists and reports, per strategy,
exactly what it needs — five of six strategies are blocked on 2022–2025 data and
say so rather than returning numbers that look like results. When the historical
lake lands, CP-14 re-runs with `--require-documented-split` and everything
downstream inherits the upgraded grade.

**Compute budget.** The heaviest remaining job is a hyperparameter search at 432
fits, minutes on a laptop. Nothing in the critical path needs a GPU.

---

## 11. Claims to make, and claims to avoid

**Safe and strong:**

- "We built a causal telemetry-to-decision pipeline."
- "We model the 2026 Overtake decision as a legal state machine."
- "We prevent historical DRS leakage into 2026 logic."
- "We combine physics, probabilistic inference, rules and planning."
- "We surface uncertainty and provenance instead of inventing telemetry."
- "Our pass model is calibrated to ECE 0.024 on a held-out Grand Prix."

**Do not claim:**

- Exact real-world battery SOC or private team deployment maps
- A *final accepted* pass model — the current artifacts are graded `INTERIM`
- A calibrated C5 planner artifact — the twin has no accepted rung
- Hand-written UI fixtures as live model output

That restraint is what makes the rest survive technical questioning.

---

## 12. The lines to say out loud

**Opening:**

> "In Formula 1, energy is not scarce because there is too little of it. It is
> scarce because spending it at the wrong moment can lose the race."

**The architecture, in one breath:**

> "Machine learning predicts uncertainty and behaviour. Physics handles state
> transitions. The rule engine guarantees legality. Dynamic programming decides
> how to spend the energy budget."

**Closing:**

> "TrackShift does not tell a driver to push because the car is fast. It tells
> them when an extra unit of energy can change the race."
