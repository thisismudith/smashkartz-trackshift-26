# E-Delta UI & Feature Roadmap

## Purpose

E-Delta should be a decision-replay product, not only a 3D driving scene.
The product needs to answer:

> Where should the car spend electrical energy now, why, and what happens if it does not?

The existing Three.js circuit remains valuable as a cinematic playback view. A
2D tactical track map is the primary decision surface because it makes segment
value, Overtake lines, uncertainty, and recommendations understandable across
the full lap.

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
4. Show the shadow-price heatmap.
5. Show the planner recommendation.
6. Show eligibility and pass-probability context.
7. Show an alternate action/rival scenario.

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

### Phase 9 — constrained what-if controls

Add only after the replay experience is stable:

- starting estimated energy
- current gap
- risk tolerance/CVaR setting
- rival policy or belief mix
- measured-SOC override when a team provides it

Each change should reveal the new action, expected outcome, eligibility/pass
change, energy profile, and provenance. A measured team input must be marked
`OBSERVED`; the public-data energy estimate remains `SIMULATED`.

Do not let the browser supply arbitrary rule constants, raw telemetry, or
hidden model parameters.

## Feature priority

| Priority | Build | Defer |
|---|---|---|
| P0 | Replay loader, tactical map, shadow-price heatmap, recommendation panel, provenance, rule gate | — |
| P1 | Rival-belief bars, checkpoint timeline, energy uncertainty, radio line, synced 3D playback | — |
| P2 | Planner comparisons, validation dashboard, simulator playback | Live what-if controls |
| Defer | — | Multi-circuit comparison, free-driving polish, grid-box detail, advanced sky/cinematic effects |

## Demo flow

1. Choose the Silverstone battle.
2. Start a two-lap replay.
3. Pause before a Detection line.
4. Show the shadow-price heatmap and why this location matters.
5. Show legal action, energy state, rival belief, and eligibility probability.
6. Explain the selected recommendation and deterministic radio message.
7. Change a rival belief or energy assumption.
8. Show how the action and expected outcome change.
9. Finish with planner-versus-baseline evidence and validation metrics.

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
2. which action is legal and recommended now,
3. why the recommendation matters before the next Overtake opportunity,
4. what uncertainty remains, and
5. how the planner compares with understandable baseline strategies.

One credible, transparent Silverstone replay is more valuable than several
polished but unsupported track visualisations.
