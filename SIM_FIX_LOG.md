# Simulator fix log

Running log of bugs found in the 3D race simulator (`frontend/src/sim/`) and
their fixes, kept up to date as each one is understood and resolved. Newest
entries at the top of each section.

Status legend: `FIXED` (code changed + tests updated + suite green, and for
the items below, additionally verified live in a real browser against the
actual dev server via a scripted Playwright session) · `ROOT CAUSE FOUND`
(understood, not yet eliminated) · `INVESTIGATING` (reported, not yet
root-caused) · `WON'T FIX` (traced to an honest data limit, not a bug).

## FIXED

### 0b. API.md audit: one missing route, and four display obligations broken
**Asked for as:** go through API.md and MODELS.md and make sure the UI displays
everything properly across /sim, /decision and /lab; check every FastAPI
endpoint is sent and received correctly.

**Endpoint sweep.** Every route in API.md section 5 was called with a realistic
payload and checked for status, stub header and response shape. Result: **17 of
17 now respond, 0 missing, 0 broken** (was 15 responding, 1 missing, 2 failing).

- `POST /rules/eligibility` (section 5.7) **was never registered**. Every call
  fell through to `GET /rules/{event}` with `event="eligibility"` and returned
  405. Implemented for real against the existing, tested M21 projection
  (`trackshift.rules.eligibility`) and M20 state machine -- both already
  existed and had simply never been exposed. It now returns the timeline's
  `eligibility` block: margin against the arming threshold, `p_eligible` from a
  real forward projection with a 95% interval, `sigma_floored`, `trailing_n`
  and `terms_used` naming every contributing input.
- `POST /rules/legal_actions` returned 422 for a payload matching API.md's own
  documented example, because the rule engine additionally requires
  `state.speed_kmh`. The refusal is correct and clearly named; the *documented
  example* is what is incomplete. Noted rather than "fixed" by loosening the
  engine -- an unnamed default speed would silently pick a power cap.
- Real routes: 5.1, 5.2, 5.3, 5.3a, 5.6, 5.7, 5.8. Still honest stubs: the ten
  that need models or battle data that do not exist yet.

**Display obligations (API.md section 8) — four genuine violations, all fixed:**

| Obligation | Was | Now |
|---|---|---|
| Wind as head/cross relative to the track, never a compass direction | `Wind 2.2 m/s → 265°` in both the environment panel and the transport bar | `1.1 m/s headwind at RUS's heading` + `1.9 m/s from the right`, projected onto the focused car's own direction of travel |
| Name the decision checkpoint a pass probability belongs to; never merge them | bare `22%` | `at the DETECTION checkpoint` |
| Badge 2022-25 rows "historical DRS", never Overtake iconography | `DRS` / `OVERTAKE` styled alike | `historical DRS` dimmed and italic, `Overtake` in white |
| Show `p_eligible` as a probability, not just an armed/not badge | `ARMED` only | `inside the arming threshold by 0.70 s`, with P(eligible) when a horizon is supplied |

The wind projection is a new tested module (`sim/replay/wind.ts`, 8 tests)
rather than inline arithmetic, because the sign conventions are exactly where
this goes wrong silently: the first implementation transposed cross-left and
cross-right, and the test caught it. A bearing and a heading in different
angular conventions (compass clockwise-from-north vs ring counter-clockwise-
from-+x) is the trap.

**Already compliant, verified by reading the rendered pages rather than the
source:** provenance tags on every number, `n=` sample sizes, 95% intervals,
`crosses zero` annotations, `no interval fitted` for honest absence, `UNVERIFIED`
badges on unverified envelope keys, energy in MJ tagged `INFERRED · ENERGY TWIN`
with a `± 3.45 MJ` interval and never called "battery" or "SOC", and the power
envelope drawn as a curve against speed rather than a single peak kW.

**Not yet displayable:** roughly a third of section 8's obligations
(`live_safe` marking, `/simulate` assumptions on screen, the
`normal_race_model_eligible` grey-out, `override_active_inferred`,
`envelope_violation`, oracle-policy labelling) depend on routes that are still
stubs. There is nothing to render yet, so these are pending data rather than
pending UI.

**Files:** `src/trackshift/serve/app.py` (eligibility route),
`frontend/src/sim/replay/wind.ts` + test (new),
`frontend/src/sim/ui/{EnvironmentPanel,WeatherStrip,OvertakePanel,SimCanvas}.tsx`,
`frontend/src/api-contract/client.ts` (`postEligibility`).
Verified: 824 Python tests + 8 skipped where starlette's test transport is
absent, 541 frontend tests, and live in a browser with zero console errors.

### 0. Overtake checkpoints, live: real pass probability replaces a 0.5 stub
**Asked for as:** "why can't we make it real numbers... we either use formulas
to compute or we directly see the values from the data... add some +/- and
thus have a confidence", and "see how we can add the detection line too into
the simulation and proper all metrics so that it feels like our proper model".

**What was actually blocking it, found by reading rather than assuming:** the
four strategic models (M22 shadow price, M10 pass, M09 rival, M24 planner) are
real, substantial, in-progress work owned by teammates -- `src/trackshift/
pass_model/` alone is ~2,500 lines of feature matrices, calibration, ensembling
and benchmark harness, specified across CHECKPOINTS_TANVEER.md CP-14 to CP-17.
None of them has a trained artifact yet, so `/pass/predict` returned a flat
0.5 placeholder. The multi-season "lake" that the rigorous pipeline trains on
has not been built either.

**What was built instead, deliberately NOT a competing model:** a contingency
table. For every attacker/defender approach in the raw feed, read the gap at
the circuit's real Detection Line, then read whether the attacker was ahead of
that same defender by the exit of the next activation zone. Count them, bucket
by gap, put a Wilson interval on each bucket. No fitting, no features beyond
the gap, no calibration -- and the response says exactly that, so it can never
be mistaken for M10.

**Measured, over 23,591 real approaches across five seasons:**

| gap at Detection | 2026 (Overtake) | 2022-2025 (DRS) |
|---|---|---|
| 0.0-0.5 s | **21.6%** [18.5, 25.2] n=587 | **13.2%** [11.9, 14.5] n=2655 |
| 0.5-1.0 s | 7.6% [6.0, 9.6] | ~1.9% |
| 1.0-1.5 s | 3.1% | ~1.1% |
| 2.0-3.0 s | 2.1% | ~1.0% |

Monotonic in gap, as physics requires. The era split is the real finding: four
DRS seasons agree with each other (11.8, 11.8, 16.5, 12.3 %) and 2026 breaks
clean away, with non-overlapping 95% intervals. That is why the two eras are
reported separately and never pooled -- the response carries the full
per-season breakdown alongside its answer for exactly that reason.

**Checkpoint geometry is read, never guessed.** The Detection Line is one per
lap at Safety Car Line 1 (`DERIVED_TELEMETRY`, e.g. 5520 m at Silverstone);
activation lines are `PROXY_HISTORICAL_DRS`, derived from where DRS was open
in 2022-2025. Events missing either are skipped, not defaulted -- 9 of 14 are,
and that is a fact about sourcing rather than a gap to paper over.

**In the simulator:** the Detection Line is drawn solid and bright across the
road, activation lines dashed and dimmer, with the zone shaded between them --
different on purpose, so a development proxy never reads as a surveyed line.
Alongside it a panel names the closest battle on track, its gap, whether
Overtake is armed, and the measured pass probability with its interval and the
five-season era comparison.

**Three tiers, in order, so nothing changes downstream when the real model
lands:** M10's trained artifact if present → this measured table → the shaped
stub. The frontend calls one route and is unaware which answered.

**Files:** `src/trackshift/serve/pass_fallback.py` (new),
`scripts/data/build_pass_fallback.py` (new),
`src/trackshift/serve/app.py` (`/pass/predict` tiers; `/rules/{event}` now also
serves the lap-level `detection_line_m`, which it was silently dropping),
`frontend/src/sim/render/overtakeZones.ts` (new),
`frontend/src/sim/ui/OvertakePanel.tsx` (new), `scene.ts` (layer + dispose),
`SimCanvas.tsx`. Artifacts: `artifacts/pass_fallback/{2022..2026}_rate_table.json`.
Verified: 824 Python tests, 533 frontend tests, and live in a real browser
against the running service with zero console errors.

### 1b. Grid centred on the racing line instead of the road, and a single noisy
station spiking one car ~20 m from its neighbours
**Reported as:** "the starting position is going more on the left." (after
fix 1 below stopped cars standing on the grass, the grid was still visibly
skewed to one side of the visible road).

**Root cause:** fix 1's stagger was still centred ON THE RING (lateral 0),
and the ring is a racing line, not the road centre -- already measured and
documented in this file's own comments as 6.5 m off-centre at Silverstone's
grid. Centring a small stagger on an off-centre reference still reads as
the whole field parked against one side, just less severely than before.
Live-checking the fix (worker-message snoop, see the verification method
note below) also surfaced a second, independent issue: one grid slot's own
station computed a road centre ~18-20 m from both immediate neighbours 8 m
either side -- a single noisy station in the GLB road-edge bake, not a real
road feature 8 m of straight tarmac could produce.

**Fix:** when both sides of the road are measured, centre the two-column
stagger on the ROAD's own measured midpoint (`(leftRoom - rightRoom) / 2`
from the ring), not the ring itself -- falling back to the prior ring-
relative placement only when just one side has a measurement. Smoothed the
centre over a handful of nearby stations (median, robust to one outlier)
against the single-station bake noise, while still clamping each car to fit
within THIS station's own measured room (never trusting the smoothed value
over a hard "don't stand in the grass here" check).

**Files:** `frontend/src/sim/replay/timeline.ts` (`columnLateral`, new
`roadCentreFromRing`). Verified: `timeline.test.ts` 43/43, full suite
509/509; live-rechecked after the fix that no grid slot's centre differs
from both its immediate neighbours by more than a few metres.

### 1. Grid cars flung off track (into gravel/sand) on wide-road sides
**Reported as:** cars visibly standing outside the tarmac at the start, worse
on one side of the grid than the other; screenshot showed HUL/SAI/ALB/BEA/
GAS/OCO/BOT sitting on sand at Silverstone's grid.

**Root cause:** `columnLateral()` in `frontend/src/sim/replay/timeline.ts`
scaled a stationary grid car's lateral offset as `measured * 0.5`, where
`measured` is the REAL per-side paved width from the GLB road-edge bake
(`measuredLateralRoomM`). At Silverstone's grid the ring (racing line) sits
close to the road's left edge, so the right side measures 17.0-17.5 m of
paved surface (it reaches into the pit apron beside the straight, which is
real asphalt but not where a grid box is painted). Half of that is 8.75 m --
a grid column almost 9 m off the racing line, standing well past the actual
grid boxes into the verge/gravel beyond the apron.

**Fix:** use the same small, fixed stagger already used for the ribbon's
degenerate case (`GRID_LATERAL_FALLBACK_M`, 1.8 m) on a surfaced circuit too,
capped by whatever room is actually measured on that side, instead of
scaling the offset by the full measured width.

**Files:** `frontend/src/sim/replay/timeline.ts`. Verified:
`timeline.test.ts` 43/43.

### 2. New Race engine silently ignored real per-track fitted data
**Root cause:** `fit_params.py` keys every per-track leaf (`pitLoss`,
`tyreDegradation.trackIndex`, `dirtyAirLossPerSecondOfProximity`,
`sessionPaceTrendPerLap.perTrack`) by the circuit's EVENT NAME (e.g.
`"British Grand Prix"`). `lapModel.ts`/`raceEngine.ts` looked all four up by
`track.slug`, which never matches, so every real circuit silently fell
through to flat cross-track defaults. Real values span 0.03-0.09 for
degradation and 0.008-0.5 for dirty air between British and Monaco.

**Fix:** renamed `LapModelContext.trackSlug` to `trackEvent`, keyed all four
lookups off `track.event`, and used the real fitted
`neutralisedNetLossSeconds` for safety-car stops instead of a guessed
multiplier.

**Files:** `lapModel.ts`, `raceEngine.ts`, and three test fixtures that had
baked in the same mismatch. Verified: full frontend suite green, full Python
suite 416/416.

### 3. Duplicate driver codes corrupting the New Race grid builder
**Reported as:** a driver (seen with ALO) stuck reading "GRID" for the whole
race, rendered off-track, while the rest of the field raced normally.

**Root cause, confirmed live in a real browser session (Playwright driving
the actual dev server, not a code-reading guess):** the season-wide driver
registry (`catalogue.drivers`, used by `NewRaceCanvas`'s `GridBuilder` as a
fallback field) is deliberately keyed `(code, team)` in
`scripts/simdata/catalogue.py` -- a driver who changed teams mid-season gets
TWO rows with the SAME `code` (measured: LAW Racing Bulls -> Red Bull
Racing, ARO Audi -> Alpine, IWA Racing Bulls -> Red Bull). The frontend
rendered this list keyed by `code` alone, so React saw two children sharing
one key on the very first paint (before a specific event's own `entries`
settle) -- reproduced live as three real
`"Encountered two children with the same key"` console errors for exactly
LAW, ARO and IWA. React's own docs say a duplicate key means a component can
be "duplicated and/or omitted," and the corrupted identity from that first
render can persist for the session even after the data becomes clean --
which is indistinguishable, from the leaderboard, from "a car stuck on
GRID." ALO's own code was never duplicated; the corruption from the three
that were can misplace an unrelated row in the same reconciliation pass.

**Fix:** `dedupeByCode()` in `NewRaceCanvas.tsx` collapses the registry to
one row per code (keeping whichever `(code, team)` pairing has the most
`sessions`, i.e. that driver's most representative team this season) before
it is ever rendered or sent into a race.

**Verified live, twice:** before the fix, a scripted run (select British
Grand Prix -> Start race) reliably printed the three duplicate-key console
errors; after the fix, an identical run against the same dev server printed
zero console errors, with all 22 drivers (including ALO) racing normally to
the end.

**Files:** `frontend/src/sim/ui/NewRaceCanvas.tsx`. No unit test (this is a
page-level component wired to a live catalogue fetch); verified by the live
Playwright run above instead.

### 4. A single corrupted telemetry sample teleports a car backward or
forward for one interpolation window
**Reported as:** "sometimes lag, sometimes goes back in movement, sometimes
goes ahead too fast."

**Root cause, confirmed live:** instrumented the actual running app (wrapped
`Worker.postMessage` from the page side to log every real pose the worker
emits) and found real backward jumps (worst 55 m) and forward spikes (worst
1080 kph -- impossible for any 2026 car) in Replay mode within seconds of
starting a real British-adjacent race. `sampleLap()` in
`frontend/src/sim/data/codec.ts`, which linearly blends between two decoded
telemetry samples, trusted the raw station channel unconditionally -- a
single corrupted sample (the raw feed already has several documented fault
classes: sentinel coordinates, stale-then-catch-up positions, aliasing) reads
as a real, large, fast movement instead of the glitch it is. A "credible
step" check already existed elsewhere in the codebase
(`lapSamples.ts`'s `alongLapDistance`, which compares a station step against
what the recorded speed channel says should have happened) but was **never
wired into the actual runtime interpolator that draws each frame** --
`alongLapDistance` was unused outside its own test file.

**Fix:** applied the same credibility test inside `sampleLap` itself: when a
step's distance disagrees with the speed-channel-implied distance by more
than the existing tolerance, treat it as not real and hold at the prior
station for that interval, rather than blending into it. Verified this
actually eliminated real anomalies live: a diagnostic count showed the guard
firing 72 times in a single 25-second live sample of one race (i.e. 72
would-be teleports now suppressed).

**Files:** `frontend/src/sim/data/codec.ts` (`sampleLap`); two pre-existing
tests in `codec.test.ts` had physically-inconsistent fixture numbers (huge
implied speeds the guard now correctly objects to) and were fixed to be
internally consistent; two new tests added exercising the guard directly.
Verified: full frontend suite green (509/509 after this and item 3 landed).

**Not fully closed:** a rarer, smaller set of anomalies survived this fix in
the same live test (down from many originally, but a few remained -- worst
still ~55 m / ~1000 kph implied). Live diagnostic confirmed these do NOT
coincide with any of the 72 single-step guard firings, meaning they are not
one bad sample -- more likely a SUSTAINED multi-sample run of anomalously
fast-looking (but individually-plausible) telemetry, which a per-step check
can't catch, or a lap-boundary handoff discontinuity in `ReplayTimeline`
(not yet isolated). Two of the six observed instances were within the first
11 seconds of the race, which lines up with the plan's own documented fault
("lap-one positions are stale for 16-60 seconds") for a car that is already
moving (the existing stationary-grid override only covers speed < 1 kph, so
a car doing 150+ kph on a still-stale position channel falls through
untouched); the remaining instances (at 60s, 187s, 190s, 480s) are
unexplained. **Next step if picked back up:** log every `sampleLap` call
(fired or not) for one car across a whole lap and look for a short run of
consecutive steps that are each individually credible but jointly imply a
trajectory reversal.

## INVESTIGATING

### 5. Car model "wobbling"
**Reported as:** a car visibly wobbles (orientation/elevation/lateral
unclear). Not yet reproduced or root-caused -- needs which car, which
circuit, stationary or moving, to chase further.

## WON'T FIX (real data limit, not a bug)

- **Cars run essentially single-file once racing.** The 2026 position feed
  is genuinely ~one-dimensional: median lateral spread between cars at a
  fixed point on track is 0.33-0.61 m. There is no real per-car lateral
  telemetry to draw once a car leaves its grid box. What WAS a bug (item 1)
  is that the grid box itself, before launch, was computed far too wide.

## Verification method note

Items 3 and 4 were confirmed and fixed by actually driving the running app
in a real Chromium browser (Playwright, using the machine's already-cached
browser binary and a locally-installed `playwright` package -- `npm install
--no-save playwright`, since the repo doesn't depend on it) against the live
Next.js dev server, rather than by reading code and guessing. This is a much
stronger verification standard than the earlier entries in this log (which
were code-read-only) and is why they went from "reported" to "root-caused
and fixed" in one pass instead of staying speculative. Recommended for any
future "it looks wrong on screen" report in this project: reproduce it live
before proposing a fix, and re-verify live after.
