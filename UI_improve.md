# E-Delta UI & Feature Roadmap

## Purpose

E-Delta should be a race-strategy intelligence product, not only a 3D driving
scene or a simulator. Simulation is one way to validate and illustrate a
decision; it is not the product's only interface.

The product needs to answer:

> Given the rival, rules, energy, tyres, gap, weather, and future
> opportunities, should we attack, defend, hold, harvest, or wait—and what is
> the upside, downside, and confidence in that choice?

The existing Three.js circuit remains valuable as a cinematic playback view. A
2D tactical track map is the primary decision surface because it makes segment
value, Overtake lines, uncertainty, rival interaction, and recommendations
understandable across the full lap.

## Product pillars

The UI should be built around four connected layers:

| Layer | Question answered | Main outputs |
|---|---|---|
| Race awareness | What is happening now? | gap, closing rate, tyres, weather, race control, segment context |
| Decision intelligence | What should we do now? | recommended action, legal actions, opportunity value, energy allocation |
| Rival intelligence | How might the other car respond? | belief distribution, defence/attack likelihood, counterattack risk |
| Evidence and trust | How reliable is this? | uncertainty, CVaR, validation, simulator comparisons, rule sources |

Simulation belongs primarily in the fourth layer. It proves the trade-off and
compares policies; it should not crowd out the live strategic decision view.

## Demo configuration first

The reference documents alternate between a British Grand Prix Sprint replay
and a British Grand Prix Race replay. Do not hardcode either choice in the UI.

Create a small demo configuration that declares:

- event
- session
- battle ID
- attacker and defender
- replay bundle location

The initial product should demonstrate one two-lap Silverstone battle well.
The reverse direction should be included later for the counterattack/repass
story.

## Current state

The current project is a Vite and Three.js circuit viewer with:

- GLB track loading
- camera controls
- a manually placed/driven car
- generic road-surface detection

It does not yet contain the documented replay bundle, battle timeline, rules
engine, planner output, or model API client.

The free-driving car is a useful visual/debug feature. It is not the same as
the two-car strategic simulator and should not be presented as such.

## Target interface

```text
Top bar
Event • session • lap • battle • REPLAY/LIVE • model/data status

Left rail
Battle selector
Race-control / eligibility state
Track/rule legend
Optional 3D scene controls

Decision context
- attacker / defender state
- current opportunity and next opportunity
- risk profile

Centre
2D strategy map / 3D playback toggle
- energy shadow-price heatmap
- current segment
- Detection and Activation lines
- opportunities and Overtake zones

Right rail
Recommended action
Why now?
Estimated energy / headroom
Eligibility and pass probabilities
Rival belief distribution

Action comparison
- attack now / save / defend alternatives
- expected value, risk, energy cost, repass risk

Bottom
Segment playback timeline
Gap • energy • speed • deployment/harvest • tactical action
```

Every numerical value must display its provenance:

```text
OBSERVED | DERIVED | INFERRED | SIMULATED | RULE
```

Estimated energy must never be labelled as measured battery/SOC data.

## Architecture

Build against the documented replay/API contract, never model internals.

```text
ReplayClient ─┐
              ├─ TrackShiftAdapter ─ DemoState ─ UI components
ApiClient ────┘
```

### Data modules

- `ReplayClient`: reads static demo JSON files.
- `ApiClient`: later requests the same response shapes from `/api/v1`.
- `TrackShiftAdapter`: validates required fields and normalises responses for
  the UI.
- `DemoState`: selected battle, current segment, playback state, selected view,
  scenario, loading/error status.

### Required states

- loading
- missing artifact/model
- replay data
- live-service data
- stub-data badge
- unverified-rule badge
- race-control/pit gate
- unavailable model output with a reason

## Implementation roadmap

### Decision model: what every recommendation must compare

Do not present a recommendation as a single deploy command. Every important
decision should compare at least three legal alternatives:

```text
ATTACK NOW       spend energy to improve the immediate pass chance
HOLD / UNLOCK    retain or deploy selectively to reach the next eligibility line
DEFEND / SAVE    protect energy and position against the rival's likely response
```

For each alternative, show a compact decision scorecard:

| Measure | Meaning |
|---|---|
| Expected strategic value | Expected two-lap race-position outcome, not only lap time |
| P(pass) | Pass probability at the correct checkpoint |
| P(repass) | Counterattack/repass probability after a successful move |
| P(ahead at horizon) | Probability of retaining or gaining position over the plan horizon |
| Energy cost | Estimated deployed/harvested energy and remaining headroom |
| Downside risk | CVaR or bad-tail outcome measure |
| Stability | Fraction of uncertainty draws that retain the recommendation |
| Rule status | Legal/blocked, applicable power mode, and source |

The selected action should explain the trade-off in plain language. Example:

> A full attack improves immediate pass probability by 7 points, but consumes
> energy that has higher value before the next Detection line and raises repass
> risk. Hold-for-Detection has the best downside-adjusted position value.

### Strategic factors to expose

The UI should make the factors behind a recommendation visible, without
overwhelming the main view:

- current and projected gap at Detection/Activation lines
- closing rate, relative speed, and relative acceleration
- energy availability, deploy/harvest allowance, current cap, and headroom
- shadow price of energy now versus the next opportunity
- attacker and defender tyre context and degradation proxy
- fuel estimate and uncertainty
- segment type, braking zone, corner phase, and speed regime
- track-relative head/cross wind, temperature, and wet-track flag
- race-control and pit-state eligibility gate
- rival belief distribution and explicit rival policy assumptions
- likely counterattack/repass consequences
- future opportunities over the two-lap horizon

### Phase 0 — freeze the integration contract

1. Create a demo configuration for the selected event, session, and battle.
2. Add JSON fixtures shaped exactly like the replay bundle/API responses.
3. Implement `ReplayClient`, `ApiClient`, and `TrackShiftAdapter`.
4. Add a visible replay/live/stub state in the top bar.
5. Do not call model internals from the browser.

Required replay/API surfaces:

```text
/meta
/track/{event}
/rules/{event}
/battles
/battles/{battle_id}/timeline
/value/{event}/shadow_price
/plan
/simulate
/validation
```

### Phase 1 — build one complete replay story

Build one polished vertical slice before making every panel.

1. Select the demo battle.
2. Load track geometry and the battle timeline.
3. Scrub to a meaningful Detection-line moment.
4. Show the shadow-price heatmap and next opportunity.
5. Compare attack-now, hold/unlock, and defend/save alternatives.
6. Show eligibility, pass probability, repass risk, and energy cost.
7. Show how an alternate rival decision changes the recommendation.

Use precomputed plan files per timeline segment first. This provides instant
scrubbing and keeps the demo robust without a live backend.

### Phase 2 — reorganise the existing UI

Move camera, render-quality, and model-rotation controls into a collapsed
`3D Scene` or developer drawer.

Use the primary interface for:

- event/session/battle selection
- lap and segment scrubber
- playback speed and pause
- 2D map / 3D scene switcher
- attacker/defender direction
- reset scenario
- optional what-if controls

### Phase 3 — build the tactical track map

Render the API centreline and segments using SVG or Canvas. Add:

- shadow-price (`lambda_E`) colour scale by segment
- current segment marker
- Detection and Activation lines
- Overtake-zone shading
- tactical action markers: harvest, hold, balanced, deploy
- opportunity markers
- optional head/cross-wind arrows
- hover detail: segment ID, energy value, speed, gap, eligibility margin, and
  action

The map must use DP-supplied shadow-price data. Do not manually colour
important areas.

### Phase 4 — build the decision panel

The recommendation panel should show:

- tactical label and deploy level
- lift-and-coast amount when relevant
- applicable power mode and legal cap
- estimated electrical energy with uncertainty
- expected gap at the next key line
- probability of eligibility
- pass probability now
- repass probability over the horizon
- planner stability and CVaR/downside-risk measure
- rule violations, visibly zero for a valid plan

Directly beneath the recommendation, add an **Action Trade-off Table**. It
should compare the recommended action with the best immediate-attack and
best-conserve legal alternatives. This is where the user sees risk/reward
rather than merely trusting a recommendation.

Add a deterministic radio message based only on planner output. Example:

> Hold energy through Copse. Deploy before Detection Zone 1; eligibility
> likelihood rises to 64%.

### Phase 5 — build the explanation layer

Every recommendation needs a concise explanation:

| Panel | Explanation |
|---|---|
| Why this action? | Dominant mechanism, for example `ELIGIBILITY_UNLOCK` |
| Why not attack now? | Energy shadow price and upcoming opportunity |
| What flips it? | Gap, energy, rival belief, or risk condition that changes the decision |
| What is uncertain? | Energy interval, pass-model spread, rival-state distribution |
| What might the rival do? | Most likely rival policy and counterattack/repass exposure |

Show rival state as probability bars for `CONSERVING`, `BALANCED`,
`DEPLOYING`, and `DERATING`; never show one state as confirmed rival intent.

### Phase 6 — add the opportunity timeline

Pass probabilities belong to distinct checkpoints:

```text
DETECTION → ACTIVATION → BRAKING
```

Show each checkpoint, whether it is reached, its probability, and its
uncertainty. Do not merge them into one generic overtake percentage.

Synchronise checkpoint state with the bottom timeline and current map segment.

### Phase 7 — add energy and regulations

Create an engineering drawer or secondary tab containing:

- estimated energy and uncertainty
- deploy/harvest power strip
- remaining energy allowances
- power cap versus estimated deploy power
- normal and override speed-dependent envelope curves
- current power headroom
- inferred override probability
- rule sources, verification state, and regulation version

When race conditions are not model-eligible (Safety Car, VSC, pit state,
yellow/red restriction, or unknown state), grey model panels and show the
race-control gate instead of stale outputs.

### Phase 8 — show simulation and validation evidence

Add an evidence tab with:

- beam + DP versus greedy attack
- longest-straight policy
- lap-time-only policy
- DP baseline
- oracle rival-state upper bound, explicitly marked non-deployable
- probability ahead, final energy, CVaR, runtime, and zero rule violations
- pass, rival, twin, and planner validation cards
- sample size next to every reported metric

The simulator panel must render its returned assumptions on screen.

### Phase 8a — add dedicated strategic workspaces

After the core replay is stable, expose the intelligence layers as focused
views rather than hiding all factors in one right rail.

#### Opportunity board

Show upcoming Overtake opportunities as ranked cards:

- distance/time to Detection and Activation
- projected gap and eligibility probability
- energy required to unlock eligibility
- pass and repass probabilities
- energy shadow price
- recommended tactical mode

This view answers: *attack here, or save energy for the next chance?*

#### Rival-response board

Show the rival belief distribution and compare explicit rival responses:

- conserving defender
- balanced defender
- deploying defender
- derating/limited defender

For each, display the recommended response, `P(ahead)`, energy consequence,
and repass exposure. Treat these as modelled scenarios, not confirmed intent.

#### Energy allocation board

Show the full two-lap plan as an energy budget:

- deploy, hold, harvest, and lift segments
- expected energy after each decision
- energy-value peaks and their causes
- legal cap/headroom at the selected segment
- effect of spending an additional energy increment now

This view answers: *where is the next kJ worth most?*

#### Risk and reward board

Present the decision frontier, not a single percentage:

- expected `P(ahead)`
- downside/CVaR
- pass versus repass probability
- final-energy range
- decision stability across uncertainty draws
- best expected-value action versus best risk-adjusted action

This allows a race engineer to choose an aggressive or conservative policy
intentionally.

### Phase 9 — constrained what-if controls

Add only after the replay experience is stable:

- starting estimated energy
- current gap
- risk tolerance/CVaR setting
- rival policy or belief mix
- tyre-age/degradation scenario where supported by the model
- weather/headwind scenario where supported by the model
- whether preserving position or maximising immediate pass chance is prioritised
- measured-SOC override when a team provides it

Each change should reveal the new action, expected outcome, eligibility/pass
change, energy profile, and provenance. A measured team input must be marked
`OBSERVED`; the public-data energy estimate remains `SIMULATED`.

Do not let the browser supply arbitrary rule constants, raw telemetry, or
hidden model parameters.

## Feature priority

| Priority | Build | Defer |
|---|---|---|
| P0 | Replay loader, tactical map, shadow-price heatmap, opportunity board, action trade-off table, provenance, rule gate | — |
| P1 | Rival-response board, checkpoint timeline, energy allocation board, risk/reward view, radio line, synced 3D playback | — |
| P2 | Planner comparisons, validation dashboard, simulator playback | Live what-if controls |
| Defer | — | Multi-circuit comparison, free-driving polish, grid-box detail, advanced sky/cinematic effects |

## Demo flow

1. Choose the Silverstone battle.
2. Start a two-lap replay.
3. Pause before a Detection line.
4. Show the shadow-price heatmap and why this location matters.
5. Compare attack-now, hold-for-Detection, and defend/save alternatives.
6. Show legal action, energy state, rival belief, eligibility, and repass risk.
7. Explain the selected recommendation and deterministic radio message.
8. Change a rival decision, energy, or risk-tolerance assumption.
9. Show how the action, risk/reward frontier, and expected outcome change.
10. Finish with planner-versus-baseline evidence and validation metrics.

## Non-negotiable display rules

- Show provenance beside every numerical value.
- Label energy and fuel estimates honestly as estimated/inferred/simulated.
- Show rival belief as a distribution, not fact.
- Display rule sources and unverified-rule warnings.
- Show uncertainty intervals instead of false precision.
- Visibly mark replay/stub/live mode.
- Grey model panels outside normal-race eligibility.
- Mark the oracle rival policy as an upper bound.
- Display metric sample sizes and do not rely on one overall-accuracy score.

## Success definition

The first complete release is successful when a viewer can understand, in one
minute:

1. where energy is most strategically valuable,
2. which legal action has the best risk/reward trade-off now,
3. how rival decisions and counterattack risk change that answer,
4. why the recommendation matters before the next Overtake opportunity,
5. what uncertainty remains, and
6. how the planner compares with understandable baseline strategies.

One credible, transparent Silverstone replay is more valuable than several
polished but unsupported track visualisations.
