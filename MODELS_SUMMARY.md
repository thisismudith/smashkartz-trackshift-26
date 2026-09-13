# Every model in TrackShift — purpose and evaluation

**Audience:** judges, reviewers and anyone preparing the pitch. One page per
question: what each model is for, how it was evaluated, and what it measured.

**The headline for a slide:**

> TrackShift is **not one black-box model**. It is a pipeline of 20+ components
> where machine learning predicts uncertainty and behaviour, physics handles
> state transitions, a deterministic rule engine guarantees legality, and
> dynamic programming decides how to spend the energy budget.

**Only 5 of the components below are learned models.** The rest are deterministic
algorithms, physics, statistics or configuration. That is a deliberate design
choice, and a strong answer to "why not just train a neural network?" — a network
cannot guarantee a recommendation is legal under FIA regulations. The rule engine
can.

---

## The five chains

```
Chain C — data foundation      →  turn telemetry into a causal race state
Chain R — regulations          →  turn FIA rules into legal action sets
Chain E — energy & physics     →  estimate what telemetry does not report
Chain P — pass prediction      →  probability of changing position
Chain V — planning             →  decide deploy / hold / harvest
```

---

## Chain C — Data foundation

| ID | Model | Type | Purpose |
|---|---|---|---|
| **M03** | Track segmentation | Deterministic | Split each lap into 30–40 geometry-based segments (braking onset, throttle return, FIA lines) so "the same place on track" is a join key |
| **M33** | Track-relative weather | Feature engineering | Project wind onto the direction of travel — a headwind on the main straight is not the same as a compass bearing |
| **M01** | Practice-lap classifier | Deterministic/hybrid | Label each lap `PUSH`, `LONG_RUN`, `RACE_PACE`, `COOLDOWN`, `OUT_LAP`, `IN_LAP`, `INTERRUPTED`, `INVALID` so physics calibrates on clean air only |
| **M30** | Tyre context | Feature engineering | Causal tyre-degradation proxy and tyre-normalised pace, per driver/car/compound/stint — not one global curve |
| **M04** | Segment baselines | Statistical | Driver, team and field median residuals per segment; the reference every later model measures against |

**Evaluation and results**

| Model | How it was evaluated | Measured |
|---|---|---|
| M03 | Geometry consistency, wrap-around handling | **1,124,954** segment rows across 14 circuits |
| M33 | Physical identity check: `head² + cross² ≈ wind_speed²` | Error **8.9 × 10⁻¹⁶**; air density 1.171–1.187 kg/m³ (Silverstone in July ≈ 1.18, as expected) |
| M01 | Clean-air share and unknown-reason audit | Full audit in `cp07_audit.json`; feeds CP-20 |
| M30 | Proxy distribution and availability by reason | **408,078** rows, 15,038 laps, 5,663 stints; proxy median **0.000 s**, p05–p95 **±1.02 s**; every unavailable row carries a reason |
| M04 | Eligibility filtering and holdout discipline | 744,655 source rows → **523,687 eligible**; British GP excluded (75,672 rows) |

**The war story for this chain.** The Detection Line sits near the lap end, so
**90% of opportunities wrap into the following lap** — 100% at every circuit
except Canada and Monaco. Ordering by raw lap distance rejected every wrapping
opportunity. Before this was found, **9 of 11 circuits produced nothing**.

---

## Chain R — Regulations (deterministic, not ML)

| ID | Model | Type | Purpose |
|---|---|---|---|
| **M18** | Event rule configuration | Configuration | The speed-dependent electrical power envelope as piecewise-linear curves, per-lap energy budget, Overtake line geometry — every value carrying `source` and `verified` |
| **M19** | Rule engine | Deterministic | **Sole owner** of `max_electrical_power_kw(speed, mode, rules)`. Every consumer calls it; no envelope constant may appear anywhere else |
| **M20** | Overtake state machine | State machine | Detection Line → armed → Activation Line → envelope; resolves *which* envelope applies |
| **M21** | Eligibility probability | Probabilistic | `P(gap at Detection < threshold)`, eligibility margin, energy required to unlock |

**Evaluation and results**

- **Legality is structural, not scored.** The engine generates only legal
  candidate actions *before* planning, so an illegal action can never win — there
  is no accuracy metric because there is no opportunity to be wrong.
- **Single-source enforcement:** one implementation of the power curve, verified
  by test. A stray constant in a physics module is the failure this prevents.
- **Zero rule violations** in the development simulator across seeded episodes.

> **The pitch line:** "Rules are a first-class input to the intelligence, not a
> post-processing filter."

---

## Chain E — Energy and physics

| ID | Model | Type | Purpose |
|---|---|---|---|
| **M14** | Energy twin | Physics | Longitudinal power balance → ERS deploy/harvest/SOC estimates. Public telemetry does **not** report battery state; this infers it |
| **M34** | Fuel-load estimator | Estimator | `fuel_load_kg_est` with uncertainty, from the twin's ICE work. Causal — uses no future information |
| **M35** | Override discriminator | Probabilistic | Above the speed where the normal and Overtake envelopes separate, a car deploying more than the normal cap **cannot** be in normal mode. Rare: public telemetry constraining a rival's energy decision |
| **M15** | Physics calibration | Fitted physics + ML residual | Five rungs: analytical → global → **per-team** → **per-event** → physics + residual |
| **M16** | Segment-time model | Learned | The causal transition the planner consumes: how energy changes segment time (ΔE → Δt) |
| **M17** | Physics uncertainty | Uncertainty propagation | 200 parameter draws → confidence ranges, so the API returns `Uncertain` rather than false point estimates |

**Evaluation and results**

| Model | Gate | Measured | Status |
|---|---|---|---|
| **M14** | Envelope violations concentrated at high speed, never clamped | **1.1%** violation rate, median 266 km/h, **none below 150 km/h** | ✅ |
| **M34** | Median final fuel in a 0–3 kg band | **1.00 kg**, **100%** of finishers in band | ✅ |
| **M35** | False-positive rate on a pre-2026 control season, where override did not exist | **10.9%** on 2024 (379,473 samples); Monaco correctly reports **0% discriminable** — it never exceeds 290 km/h | ◐ needs a calibrated twin |
| **M15** | Each rung beats the previous; no parameter at a bound | 11 team cells, 17 event cells (**126 fell back** for too few clean laps); all four parameters **at their bounds** | ◐ |
| **M16** | `a_k > 0` everywhere; largest on straights | **214 segments fitted, all monotone**; but `a_k` **inverted** by segment type | ◐ |
| **M17** | 80% intervals contain the truth ~80% of the time | **84.7%** coverage against 80% nominal, 200 draws | ✅ gate, ◐ inherits M15 |

**The honest finding worth telling.** M15 plateaus at **0.274 s** against a
**0.163 s best-possible-constant floor**, and §29's 0.15 s target sits *below*
that floor. No model predicting from segment identity plus physics can reach it.
Three of four parameters pin at the bounds that minimise the physics correction —
the optimiser saying the correction does not earn its place. Because of this, the
twin, planner and simulator routes are **deliberately held as synthetic**: M16's
energy sensitivity is inverted, and wiring it would hand the planner a transition
pointing the wrong way.

> **This is a strength in the pitch, not a weakness.** "We measured that our
> physics target was below the theoretical floor, and we did not ship a planner
> on a transition we know points the wrong way."

---

## Chain P — Pass prediction *(the main learned track)*

| ID | Model | Type | Purpose |
|---|---|---|---|
| **M07** | Opportunity dataset | Dataset producer | One row per opportunity **per decision checkpoint** — DETECTION, ACTIVATION, BRAKING — with a strict feature cutoff |
| **M10** | Pass-probability benchmark | **Learned** | 5 families × 3 checkpoints = **15 fits**: Logistic Regression, LightGBM, XGBoost, CatBoost, MLP |
| **M11** | Probability calibration | **Post-hoc fit** | Uncalibrated vs Platt/sigmoid vs isotonic |
| **M12** | Ensemble spread | **Ensemble** | Bagged members → prediction uncertainty |
| **M13** | Fine-tuning | **Learned** | Hyperparameter search on the selected family |
| **M28** | Ablation harness | Evaluation tooling | Leave-one-group-out **and** add-one-in, over repeated seeds |
| **M29** | Leakage-safe splitter | Evaluation tooling | Splits by `battle_id`, never by row |

### M07 — the dataset

**4,970 opportunities → 14,910 rows**, base rate **14.9%** (CP-13 expects 10–35%;
below 5% would mean counting hopeless approaches, above 60% only completed
passes). **15/16/17 features** at DETECTION/ACTIVATION/BRAKING.

**Leakage test passes:** no activation- or braking-scoped column is populated in
any DETECTION row — enforced by building from a *truncated view*, never by
blanking fields afterwards.

### M10 — the benchmark

Selected on **calibration, not accuracy** (§26): the planner consumes
probabilities, so a model with slightly lower ROC-AUC and materially better
calibration wins.

**Winner: LightGBM at all three checkpoints.**

Measured on the **frozen 2026 British Grand Prix** (412 opportunities, 43 passes,
base rate 10.4%) — an event held out of every training, validation and
calibration path and read exactly once:

| Checkpoint | ROC-AUC | Accuracy | Brier | Skill | **ECE** |
|---|---:|---:|---:|---:|---:|
| DETECTION | 0.725 | 0.893 | 0.0866 | +0.093 | 0.026 |
| ACTIVATION | 0.745 | 0.908 | 0.0828 | +0.133 | **0.024** |
| BRAKING | 0.699 | 0.898 | 0.0915 | +0.042 | 0.059 |

All five families on the same held-out rows, at ACTIVATION:

| Model | ROC-AUC | Accuracy | Brier | Skill |
|---|---:|---:|---:|---:|
| CatBoost | 0.788 | 0.898 | 0.0789 | +0.173 |
| **LightGBM** *(selected)* | 0.745 | **0.908** | 0.0828 | +0.133 |
| Logistic | 0.668 | 0.891 | 0.0993 | −0.040 |
| XGBoost | 0.695 | 0.881 | 0.1007 | −0.055 |
| MLP | 0.702 | 0.871 | 0.1150 | −0.204 |

**Read the MLP row carefully** — 87% accuracy with **negative skill**, meaning
worse than knowing the base rate. That single row is the clearest demonstration
of why accuracy alone cannot be the headline at a 10% base rate.

### M11 — calibration

| Checkpoint | Winner | ECE |
|---|---|---:|
| DETECTION | isotonic | 0.052 |
| ACTIVATION | isotonic | 0.045 |
| BRAKING | **uncalibrated** | 0.050 |

Uncalibrated winning at BRAKING is worth recording rather than smoothing over: it
says that model is already well calibrated, and a calibrator there would add a
fitting step for nothing. §27's point exactly — calibration is *compared*, not
assumed.

### M12 — ensemble spread

**21 members** across 4 bagged families; spread relative standard error **0.158**.
Gives the API a genuine uncertainty band rather than a point estimate.

### M13 — fine-tuning

**Verdict: `KEEP_BASELINE` at all three checkpoints, on both objectives.**

That is a real result, not a null one — the CP-14 parameter block was already
right. Tuning for ROC-AUC lifted it 0.011 (inside a 0.041 fold-to-fold spread, so
not distinguishable from noise) while **log loss worsened 58%**. §26 as a
measurement rather than a principle.

### M28 — ablation

Leave-one-out **and** add-one-in over 3 seeds, with intervals against a measured
seed-to-seed noise floor. Two designs, because leave-one-out alone cannot tell a
*redundant* group from a *useless* one — removing either of two correlated groups
leaves the signal in the other.

---

## How the data was divided

**Split unit is `battle_id`** — never row, never event. The leak it stops: the
same two cars over the same laps scored on both sides of a boundary.

Resolving each opportunity to its exact battle needed distance, not just the lap.
Matching on `(pair, lap)` left **28.4% ambiguous**, because C8 emits mostly
single-lap episodes and often several per pair per lap. Adding the within-lap
distance extent resolved it — all 5,745 multi-episode cells have **disjoint**
spans:

| Join key | Joined | Ambiguous |
|---|---:|---:|
| pair + lap | 68.4% | 28.4% |
| pair + lap + **distance** | **92.5%** | **0%** |

The assignment is **written to disk and hashed** (2,428 groups), so two runs
cannot silently disagree. Opportunities with no episode keep a null `battle_id`
and are **excluded, never relabelled**.

---

## Status, stated honestly

| Ready to present | Development-stage |
|---|---|
| 20 m causal race-state pipeline | Final accepted physics twin (M15) |
| Rule engine and legal-action masking | Calibrated segment-time transition (M16) |
| Battle detection and opportunity dataset | Final planner and simulator on real data |
| Pass model with held-out evaluation | Production replay bundle |
| Calibration, ensemble and ablation harnesses | |

The pass model is graded **`INTERIM`**: the numbers are real measurements on a
leakage-safe split, but CP-14's documented split needs 2022–2025 seasons that are
not yet built. The grade is carried in code — a run cannot be quoted as a pass
unless the year table was actually used.

---

## Three claims that survive hostile questioning

1. **Calibration transferred.** ECE **0.024** on an event the model never saw —
   *better* than in cross-validation. For a planner reading probabilities, this
   is the metric that matters.
2. **6.2× lift on the positive class.** At ACTIVATION's F1-optimal threshold the
   model is right **65% of the time it calls a pass**, against a 10.4% prior.
3. **It beats a gap-only reference at all three checkpoints** — the other
   fourteen features earn their place on unseen data.

**And the one to avoid:** do not claim a final accepted planner or a measured
battery SOC. The restraint is what makes the rest credible.
