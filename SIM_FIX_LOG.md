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

### 0e. The launch stagger never decayed, so the whole field ran lap 1 off the
racing line
**Reported as:** "the cars are sometimes going too much outside the track? is the
track properly as per the track in the 3d model for the british gp?"

**Is the track right?** Yes, and it is now measured end to end rather than argued.
Every drawn car position across the whole 2026 British GP race (57,640 of them,
sampled every 2 s, through the real ReplayTimeline, declutterLanes and carPlanPos)
was raycast against the shipped Silverstone model: 93.6 % stand on `asphalt.001`,
4.9 % + 1.0 % on `Curb_new.001` (a car on a kerb is racing, not a defect), and
99.59 % on some drive surface. The ring fit itself is 100 % coverage at 0.051 m
residual std. The circuit is aligned; what was off the road was the CARS.

**Root cause -- a regression from 0d.** The launch blend eases the grid stagger into
the feed's own lateral over the run to turn 1, and it decided "how far is this car
from its box" with a SIGNED difference clamped at zero:

    Math.max(0, (((stationM - box) % L) + L * 1.5) % L - L / 2)

which maps every car more than half a lap from its box back onto "still in its box".
So the blend never switched off. Measured at the 2026 British GP: at t = 60 s, with
the field 2,800-3,500 m down lap 1, all 19 running cars were drawn 2.0-9.0 m off the
racing line (mean 5.67 m) while the feed itself reported 0.03-0.15 m. It is a
FORWARD distance, with only the centimetre-scale backwards jitter of a stationary car
folded to zero (BOX_JITTER_M). After the fix the stagger decays 8.99 m -> 0.24 m by
t = 9 s and the field holds the feed's own 0.04-0.15 m from there on.

**...and the second cause, fixed with it.** `pin` is the LANE's timing point, not its
entrance: measured over all 52 in-laps of the British race the car is already more
than 15 m off the ring 3.0-4.9 s BEFORE its own `pin` (median 3.7 s), every lap
without exception. For those seconds it was drawn at honest pit-lane coordinates
while being called an on-circuit car -- taking the racing surface's elevation instead
of the lane's, with declutterLanes free to shove it sideways. 127 of the 230 far-off
on-track positions were within 20 m of the traced pit lane.

`inPit` now asks `onPitRoad` as well as the clock, with two measured tests. The
published `pitLane.entryStation` is one; the other is a lateral beyond
`PIT_DIVERGENCE_M` (8 m), because the entry is a POINT and a car does not leave the
track at a point -- the divergence begins 18 m BEFORE the published entry at Spa and
4 m after it at Silverstone, so the station test alone misses the first seconds at
some circuits. 8 m sits above every genuine on-circuit reading (99.84 % of
status-"track" instants are within 3 m, p99.9 = 4.57 m) and below half the nearest
part of any lane (19.3 m at Silverstone's exit, ~22 m at Spa's entry). Both tests are
gated on the lap actually having a `pin`, so a car running wide on a normal lap is
untouched.

**Files:** `frontend/src/sim/replay/timeline.ts` (`launchBlend`, `BOX_JITTER_M`).

**Verified:** sim suite 565/565, including two new real-data regressions -- the
field's MEDIAN |lateral| must be under 1 m at t = 45/60/90/150 s on every shipped
pack (it was 5.67 m at Silverstone), and the stagger must still be over 4 m at t = 0
so the fix cannot be "switch the stagger off".

### 0d. Grid calibrated against the model's painted boxes; the launch stops
teleporting the field onto the racing line; /sim opens on the modelled circuit
**Reported as:** "slightly left and increase gap between cars and get it back, 1st
car is outside the starting line"; "when starting all cars go left and come in one
line? why? why not from their position?"; "make default prix in /sim the british
grand prix and ensure it loads the 3d model too, or shows on top loading 3d model
in a like overlay".

**Why the field slid left into one file.** THE FEED CARRIES NO LATERAL. Measured
over the whole of lap 1 at the 2026 British GP: the standard deviation of every
car's reported lateral is 0.03-0.16 m for the first 1200 m and the largest single
reading is 0.72 m, on a road 15-18 m wide with a field that is genuinely two
abreast off the line. Every car's x/y projects onto the reference line, so "their
own position" and "one file on the racing line" are the same thing in this data.
The grid stagger is a labelled RULE placement, and it was being dropped the instant
telemetry took over -- so the whole field stepped sideways onto the ring at once.

**Fix:** the stagger now DECAYS into the measured lateral over the circuit's own
run from pole to the first corner (336.6 m at Silverstone, 318.9 m at Zandvoort,
602.6 m at Monza) instead of being switched off. The station stays the measurement
it always was; only the lateral is placed, and it is placed over exactly the
stretch where the feed has nothing to place against. A signed-travel bug found
while checking it -- an unsigned modulo read a car 5 cm short of its own box as
5825.69 m past it -- made STR flicker onto the racing line for a frame at t=1-2 s.

**Grid calibration.** With the field on its measured boxes, a top-down render at a
known scale (helicopter camera, 23.479 px/m, so one pixel is 4.3 cm) put ANT 3.67 m
and HAM 4.00 m IN FRONT of the box painted under them, and 0.54-0.80 m to the right
of it. The forward error is half a car length, which is what a position reported at
the front of the car rather than at its centre looks like; it is now carried as
`CAR_REFERENCE_AHEAD_M` (half the car's TRUE length) and applied to the drawn body
only, in carPlanPos. The lateral is carried as `GRID_COLUMN_CALIBRATION_M` (0.65 m
left) on the road centre the edge walk computes, which corrects the centre without
touching the stagger, and only where there is a measured road.

**Car size.** A real grid is 8.0 m of pitch holding a 5.6 m car: 2.4 m of air, 43 %
of a car length. Drawn at CAR_VISUAL_SCALE 1.3 the car was 7.28 m in that same 8.0 m
box, leaving 0.72 m -- a solid queue of touching cars. The pitch cannot absorb it
(8.0 m is where the boxes measurably are), so the scale goes back to 1.0 and the car
is its true 5.6 x 2.0 m. Nothing can interpenetrate at the minima the simulation
permits any more, which was not true at 1.3 (1.68 m of nose-to-tail overlap at the
launch minimum gap). The three carGeometry tests that pinned the inflation now
exercise it at an explicit 1.3 so they keep failing for whoever raises it again.

**/sim opening state.** Defaults to `british-grand-prix` -- the one circuit with a
real 3D model -- rather than the alphabetically first slug, which opened on
Melbourne's procedural ribbon. And because NOTHING WAITS FOR THE MODEL (the race
runs on the ribbon and the 126 MB asset is swapped in whenever it lands), a
`ModelLoadOverlay` now says so while it is happening: downloading, with MB progress
or an indeterminate sweep when the response carries no Content-Length. Not a modal --
the ribbon underneath is live. It is silent on the twelve circuits that have no model,
which is what lets "no news" mean "there is nothing to load", and SUCCESS IS NEVER
DRAWN: the model appearing on the circuit is the notification, so a banner announcing
it would only cover the thing it announces. A FAILURE does stay up (8 s), because
nothing else ever reports it -- the circuit keeps the ribbon for the rest of the
session and there is no later moment at which the viewer would find out why.

**...and the overlay's own bug, found while verifying it.** `onEnvironmentLoad` was
registered AFTER `renderer.setTrack(track)` -- and setTrack is what starts the
download. The first report, the one that puts the overlay on screen, was therefore
made while the callback was still null and dropped. It went unnoticed while the
"loaded" state was still drawn (the later reports land fine, so the banner appeared
at the end and looked correct); the moment success stopped being drawn, the overlay
went completely silent for the whole 126 MB fetch and the model looked like it was
not loading at all. Reported as "3d map not loading??" -- and it WAS loading, traced
to a clean `RESP 200` with no console error. The callback is now registered before
setTrack.

**Files:** `frontend/src/sim/replay/timeline.ts`, `frontend/src/sim/render/scene.ts`,
`frontend/src/sim/render/presentation.ts`, `frontend/src/sim/ui/SimCanvas.tsx`,
`frontend/src/sim/ui/sim.module.css`, `frontend/src/sim/render/carGeometry.test.ts`.

**Verified:** frontend 642/642, tsc clean, producer suites green. Live: /sim opens on
British Grand Prix, the overlay reports the download and then clears itself, pole
sits behind the painted start line, the field has visible gaps, and every car leaves
its own box and converges onto the racing line over ~9 s instead of stepping sideways.

### 0c. The whole starting grid 120 m behind its real boxes, and three defects
in the pit-lane start
**Reported as:** "why is it going so on left outside track ... pressing play why
all cars come in one single line ahead, isn't the starting position already
supposed to be that ... see image 2 for deviation of starting pos left/right and
up/down ... also the car in the pit, why is it #1 and the car is floating on
air" (British Grand Prix 2026, on the Silverstone GLB).

**Root cause (grid position).** `grid.slots[].station` and the renderer both put
pole ONE GRID PITCH BEHIND THE TIMING LINE. That is not where a grid is: the
front box's distance past the line is measured per session and runs -53.3 m
(Austria) to +288.1 m (Monza) across the 2026 packs. `scripts/simdata/track.py`
already FITTED that anchor and wrote it to the artifact as `grid.anchorMetres`
(+110.8 m at Silverstone, from a box lattice at coherence 0.514 against a 0.193
floor) -- and then both producers ignored it. Measured against the pack: every
one of the 21 cars' first lap-1 samples sits 109.5-122.6 m ahead of the slot it
was drawn in. Silverstone's start straight only begins ~45 m before the line, so
two thirds of the field was laid out around the 45 deg exit of Club -- the
left/right AND up/down scatter in the screenshot -- and pressing play teleported
the whole field ~120 m forward onto the racing line, which is the "single line
ahead". The lateral break was a consequence: `columnLateral` centres the stagger
on the road's measured midpoint, and around a corner exit that midpoint swings
14 m over 160 m, so the drawn grid broke in half (P1-P10 at -4.7..-9.3 m, P11-P21
at +3.0..+8.8 m).

**Root cause (Belgium, found while fixing the above).** `grid()` took a car's
box position from the first `speed == 0` ANYWHERE in lap 1. At Spa 2026 RUS's
lap opens at 2 km/h -- a creep under a stationary car -- so the search ran 298
samples down the road and returned a station 2357.8 m past the rest of the field.
That one reading moved the fitted anchor 2353.5 m and made the lattice claim 290
empty boxes; the artifact shipped `anchorMetres: 2455.38` against a real pole of
101.9 m.

**Root cause (pit lane), three separate defects.** (a) ALO's lap 1 is already
rolling at t=0, so it took the MEASURED branch and its pit-lane station -- 351.2 m,
a lane coordinate projected onto the racing ring -- outranked a whole field still
standing on its boxes at 110.8 m and behind: the one car that had not started the
race was shown as P1. `rank()` already refuses to compute a GAP from such a
station; it was still ranking on it. (b) The pre-telemetry placement paired
`pitLane.exitStation` with `pitLane.loopLateral` -- the BOX's offset at the
EXIT's station -- putting the car 15.6 m beyond the pit road; the right field,
`exitLateral` (-19.33 m), was not even parsed into `TrackModel`. (c) The lap-1
stationary hold fired on any car below 1 km/h and indexed straight into
`gridOrder`, which for a pit starter is a slot PAST the last published box: the
moment ALO stopped at the pit-exit light it was teleported into a phantom 22nd
grid box on the racing line and left there for the rest of the race.

**Root cause (floating).** `carRenderPos` took a car's elevation from the RING at
its station and applied lateral purely horizontally. That is right to within the
road's camber for the +-3 m of lateral the feed ever reports on the circuit, and
wrong by metres for a car 19-35 m away in the pit lane. Raycast against the
shipped GLB: the pit road under the released car is 3.28 m BELOW the racing line
at the same station -- exactly the float on screen. The drawn pit RIBBON had the
same defect (`pitPathElevation` drapes it from the nearest racing surface),
because no measurement of the lane's own height existed.

**Fix.**
- One shared derivation of a grid box's station, anchored on the measurement:
  `grid_slot_station` (Python) and `gridSlotStation` (TS), both
  `sf + anchor - slot * pitch`, falling back to the old rule only where the
  session resolved no lattice (4 of 13 packs, which say so with a null anchor).
  Used by the pre-launch grid, the lap-1 stationary hold and the parked queue.
- `_grid_box_sample` restricts the stationary search to the leading,
  not-yet-launched run (`GRID_LAUNCH_KPH = 10`), plus an independent credibility
  guard that refuses a lattice claiming more empty boxes than it has cars.
- `columnLateral` refuses an edge pair that cannot describe one road
  (`MAX_MEASURED_ROAD_WIDTH_M = 25`; Silverstone's edge walk leaks onto asphalt
  run-off at 23.3 % of stations, reaching 80 m), falling back to the single-sided
  placement it already had.
- A pit-lane starter ranks behind the last grid box until its own measured `pout`
  releases it, while its DRAWN position stays the measurement throughout; the
  stationary hold now applies only to a driver the producer actually placed in a
  box; the pre-telemetry placement uses `exitLateral`.
- New measurement: `bake_path_z` raycasts the circuit model under every pit-lane
  vertex and the artifact carries it as `surfaceZCm` per segment. The ribbon and
  any car in the lane are drawn on THAT (`pitLaneHeightAt`, `carPlanPos`, and an
  elevation override on `carRenderPos`); a vertex the bake found nothing under
  keeps the old drape.

**Files:** `scripts/simdata/track.py`, `scripts/simdata/glb_surface.py`,
`scripts/simdata/build_track.py`, `frontend/src/sim/contract/types.ts`,
`frontend/src/sim/data/manifest.ts`, `frontend/src/sim/replay/timeline.ts`,
`frontend/src/sim/render/trackMesh.ts`, `frontend/src/sim/render/scene.ts`.
All 13 track artifacts rebuilt.

**Verified:** producer suite 423/423, frontend suite 637/637, tsc clean. New
real-data regression tests over every shipped pack: every anchored circuit now
draws its boxes within 9.75 m of where those cars actually stood (was 109.5-122.6 m
at Silverstone; the residual is the 8.0 m rule pitch against a fitted 7.6-8.2 m),
no pit-lane starter is ever ranked ahead of a car still on the grid, and none is
ever given a grid box. Spa's anchor comes out 102.1 m against a measured 101.9 m.
Live in the browser: the grid stands on the Hamilton straight under the start
gantry in two columns, ALO reads P22 PIT from t=0 and sits on the pit-lane tarmac
inside the pit wall.

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

---

# 13 Sep 2026 — the model was loading but starving

Reported as "is the model even loading?". It was. `is_stub: false`,
`provenance: INFERRED`, a real LightGBM artifact answering every request. It
was also returning **0.231 for every battle on the grid**, which is what a
model does when you ask it fifteen questions and answer none of them.

Six defects, all confirmed against the running service before and after.

### 1. `decision_checkpoint` was read as `checkpoint` — CRITICAL

`app.py` read `payload.get("checkpoint")`. API.md §5.8 and every frontend
caller send `decision_checkpoint`. So an ACTIVATION request was scored by the
DETECTION model and then refused with `CHECKPOINT_VIOLATION` for carrying
activation speed — a leakage refusal raised against a request that leaked
nothing. Fixed; `checkpoint` still accepted as the older spelling. An unknown
checkpoint is now a 422 rather than a silent fall-through to DETECTION.

### 2. Zero of fifteen features reached the model — CRITICAL

`features_supplied: []` on every live request. The sim sent `time_gap_s`; the
locked schema wants `gap_at_checkpoint`. INTEGRATION.md §3 states the mapping
requirement in as many words and nobody had implemented it. Added
`FEATURE_ALIASES` in `pass_service.py` (renames only — never a unit change) and
`normalise_features()`, which also accepts API.md §5.8's nested `features`
block. The sim now supplies **14 of 15**.

Measured: 0.2 s gap → 21.7%, 1.5 s → 7.5%. Before, every gap → 23.1%.

`gap_rate_ahead_s_per_s` is deliberately NOT aliased to
`closing_rate_s_per_s`. They are the same magnitude with opposite signs
(`rules/eligibility.py`: positive when closing). Aliasing them would feed the
model a car pulling away as one closing in, and no response could reveal it.

### 3. The wrong artifact version was loading

`load_predictor` and `available_checkpoints` each defaulted to `version="v1"`.
INTEGRATION.md §2 and §8 both say prefer `v2`, and v2 was on disk, unused.
One `DEFAULT_ARTIFACT_VERSION = "v2"` now, so the readiness report and the
serving path cannot drift apart while each looks right alone.

### 4. `/rules/eligibility` returned a constant — CRITICAL

A 0.30 s gap and a 2.90 s gap returned **byte-identical bodies**. The handler
read `gap_s` off the top level; the UI nests it under `state.gap.time_gap_s`,
so it always used the 0.72 default. Now resolved through `_first_number()`
across every documented spelling, and a request with no gap anywhere is
**refused** rather than defaulted — a constant that looks like a projection is
worse than no projection. Also emits `eligibility_margin_s` alongside
`margin_s`; the panel read the first, the server sent only the second.

### 5. `/track/{event}` ignored the real geometry

`track_data.py` has read the real M03 segmentation from `config/geometry/`
all along — 33 segments for Silverstone with real `corner_type`. The route
returned synthetic points instead. Now wired: 14 circuits, `DERIVED`, with the
rule lines merged in. This is the ONLY source of a `corner_type` that lands in
a trained category — the server runs the same classifier the model was fitted
with, so a browser-side curvature reimplementation would emit plausible labels
the model has never seen.

### 6. `pass_fallback` was disconnected

The empirical season rate tables (23,591 opportunities, 2022-26, Wilson
intervals) had zero references from the rewritten `app.py`. Reconnected — but
**beside** the model rather than under it. "The model says 9% where 508
comparable approaches produced 13%" is the comparison that says whether to
believe the model; neither number alone carries it.

### Also
- `X-TrackShift-Stub` header was never set, so `api-contract/client.ts`'s
  `stub` flag was permanently false and every stub rendered as real.
- `/meta` returned a hard-coded `"stubs": []` while `stubs_used` accumulated
  route names two lines away — the exact failure the field exists to detect.
- No CORS. Browser calls failed at the preflight while curl to the same URL
  succeeded, which sends a reader looking for a fault that is not there.

## Sector: can be supplied, changes nothing

`TrackModel.timingLines` gives the Detection Line's sector directly, so the sim
*can* send it. It does not, because the artifact was fitted with `sector` null
in every row and its only trained category is `__missing__`. Measured: with
`sector: 2` the model returns 0.44982; without, 0.44980. Sending it would make
the panel read "15 of 15" while the model ignored it — claiming more evidence
than exists. The tooltip says so rather than leaving it looking like an
oversight. Unlocking it is a retrain, not a UI change.

## Two panel bugs found from a user screenshot

**"Detection Line — not sourced" on every circuit.** The parser read
`detection_line_m` at the top of the `overtake` block. The config carries one
**per zone**: Silverstone has four, at 5789.3 / 1338.2 / — / 4104.6 m. Four
measured, sourced lines were being reported as absent.

**Zones read "not sourced" while showing "measured in telemetry".** The panel
required both `activationM` and `endM`; `zone_end_m` is null and `UNVERIFIED`
on every 2026 event, so an absence in one field erased a sourced value in
another. Now the activation line shows with "end unsourced" beside it. Zone
labels are the config's own "A1".."A4" again rather than renumbered 1..n.

**"scoring…" forever.** The scoring effect re-runs on every 10 Hz gap change
and its cleanup set `disposed = true`, cancelling the one run that had passed
the 1.5 s throttle — while every later run returned early on that same
throttle without starting another. The result was never applied and the
throttle guaranteed it never would be. Replaced with a run id plus a mount
flag, so a re-render that started no run cannot cancel one.

# Model evidence reel

New: `scripts/features/export_rivalry_showcase.py` scores every 2026
Detection-Line opportunity with the real v2 artifact against the telemetry's
own outcome label, and exports the approaches it called correctly and
confidently — interleaved between both directions, because a reel of twelve
"it predicted a pass and one came" is weaker evidence than one that also shows
the model correctly ruling passes out.

**The threshold is the base rate, not 0.5.** Passes are ~10% of opportunities,
so a 0.5 cut would collapse "agreement" into the trivial statement that most
approaches produce no overtake. A true positive is an approach that converted
AND was rated in the top decile; a true negative the mirror.

**Selecting the hits IS cherry-picking unless the population is on screen.** So
each event carries its opportunity count, pass count, base rate and ROC AUC
over *all* of them, and `ShowcasePanel` renders that line above the reel where
it cannot be collapsed away.

| circuit | shown | of | passes | base | AUC |
|---|---|---|---|---|---|
| Australian | 12 | 762 | 129 | 16.9% | 0.80 |
| Italian | 12 | 754 | 204 | 27.1% | 0.82 |
| Canadian | 12 | 1228 | 160 | 13.0% | 0.77 |
| Miami | 12 | 870 | 104 | 11.9% | 0.74 |
| Japanese | 12 | 362 | 63 | 17.4% | 0.74 |
| British | 12 | 412 | 43 | 10.4% | 0.68 |
| Barcelona | 12 | 324 | 33 | 10.2% | 0.65 |
| Monaco | 8 | 61 | 1 | 1.6% | 0.89 |

The reel filters to the session on screen — a weekend produces Race and Sprint
opportunities and lap 3 of one is not lap 3 of the other. Entering it hides the
rail, board and feed and dims every car but the two, because the whole point is
two cars and one claim about them.
