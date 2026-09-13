# Simulator fix log

Running log of bugs found in the 3D race simulator (`frontend/src/sim/`) and
their fixes, kept up to date as each one is understood and resolved. Newest
entries at the top of each section.

Status legend: `FIXED` (code changed + tests updated + suite green) · `ROOT
CAUSE FOUND` (understood, fix in progress) · `INVESTIGATING` (reported, not
yet root-caused) · `WON'T FIX` (traced to an honest data limit, not a bug).

## FIXED

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
grid boxes into the verge/gravel beyond the apron. The intended stagger is a
few metres, not a fraction of however wide the nearest paved surface happens
to be.

**Fix:** use the same small, fixed stagger already used for the ribbon's
degenerate case (`GRID_LATERAL_FALLBACK_M`, 1.8 m) on a surfaced circuit too,
capping it at whatever room is actually measured on that side (so a narrow
or unmeasured side still collapses toward single file) instead of scaling
the offset by the full measured width. `GRID_COLUMN_FRACTION` (0.5) now
applies only to the ribbon's symmetric RULE half-width, which is genuinely a
small number (6-7.5 m) by construction.

**Why this also explains "pressing play puts them all in one line":** once a
car's speed exceeds ~1 km/h it leaves the stationary-grid lateral override
and starts reporting its real telemetry lateral, which the 2026 data shows is
essentially zero once cars are moving (see "single line once racing" below --
that part is real, not a bug). The snap from an 8.75 m-wide grid to ~0 m was
huge and jarring; with the grid capped at 1.8 m the same transition is far
smaller and reads as a normal launch, not a teleport.

**Files:** `frontend/src/sim/replay/timeline.ts` (`columnLateral`,
`GRID_COLUMN_FRACTION` doc comment). Verified: `timeline.test.ts` 43/43.

### 2. New Race engine silently ignored real per-track fitted data
**Reported as:** (found during a code audit, not from a screenshot) -- every
generated race behaved the same on every circuit regardless of measured
tyre-degradation or dirty-air character.

**Root cause:** `fit_params.py` keys every per-track leaf (`pitLoss`,
`tyreDegradation.trackIndex`, `dirtyAirLossPerSecondOfProximity`,
`sessionPaceTrendPerLap.perTrack`) by the circuit's EVENT NAME (e.g.
`"British Grand Prix"`). `frontend/src/sim/engine/lapModel.ts` and
`raceEngine.ts` looked all four up by `track.slug` (`"british-grand-prix"`),
which never matches, so every real circuit silently fell through to flat
cross-track defaults (degradation 0.05, dirty air 0.1, pit loss 22 s +
an unmeasured `x0.4` "free stop" guess) -- erasing the track-to-track
character the fit exists to capture. Real values span 0.03-0.09 for
degradation and 0.008-0.5 for dirty air between British and Monaco, an order
of magnitude apart.

**Fix:** renamed `LapModelContext.trackSlug` to `trackEvent`, keyed all four
lookups off `track.event`, and used the real fitted `neutralisedNetLossSeconds`
for safety-car pit stops instead of the guessed multiplier (falling back to
it only when a circuit has too few neutralised-stop samples to fit one).

**Files:** `frontend/src/sim/engine/lapModel.ts`, `raceEngine.ts`, and the
three test fixtures that had baked in the same slug/event mismatch
(`lapModel.test.ts`, `raceEngine.test.ts`, `generatedTimeline.test.ts`).
Verified: full frontend suite 485/485, full Python suite 416/416.

## ROOT CAUSE FOUND / IN PROGRESS

### 3. A driver stuck on "GRID" status forever, rendered off-track mid-race
**Reported as:** leaderboard shows one car (seen with ALO) still reading
"GRID" while every other car has a real gap deep into the race; the car
itself renders off the tarmac in a gravel trap at a corner, oddly rotated.

**Status: not reproduced at the data/engine level after an exhaustive pass --
suspect it is a rendering/dashboard-layer bug, not an engine bug.** What was
checked, all clean:
- Traced ALO through every real shipped Replay pack directly
  (`ReplayTimeline.sampleAt`, all 13 circuits x every session) at ten points
  across each race: never once shows "grid" past the opening minute, and
  never wrongly stuck anywhere. (One unrelated, harmless case found: PER at
  the Dutch GP Sprint legitimately still shows "grid" at 10% race distance,
  i.e. a genuinely late/delayed start, and is racing normally well before
  30% -- not a bug.)
- Confirmed British GP's real catalogue has exactly 22 genuine entrants for
  2026 (11 teams x 2 -- Cadillac is the season's new 11th team), so 22 rows
  including ALO is correct, not a data error.
- Ran a full `runRace()` + `GeneratedTimeline` simulation end-to-end with the
  exact real 22-car British GP entry list (the same order the catalogue and
  `GridBuilder` would produce) and sampled every driver's status across the
  whole race: no driver ever showed "grid" past the opening laps. So the New
  Race ENGINE (`raceEngine.ts`, `generatedTimeline.ts`) does not reproduce it
  either, for the realistic case that matches the screenshot.
- Read `buildStandingStart()`: every entry in `RaceConfig.entries` gets a
  grid placement unconditionally (`placements = entries.map(...)`), so there
  is no "driver falls through with no placement" path in a generated race --
  that only happens in Replay (`grid.unplaced`), and Replay is clean per
  above.
- Read the pose-buffer index plumbing (`worker/pose.ts`, `sim.worker.ts`):
  `driverOrder` is captured once from `timeline.driverList` at race start and
  never reordered, so a car-index/driver mismatch between the 3D scene and
  the leaderboard is not evident there either.

**Leading hypothesis, unconfirmed:** the dashboard snapshot / running-order
logic (`replay/dashboard.ts`, built once per worker tick from
`timeline.sampleAt`) implements *hysteresis* for on-track position swaps
(plan: "requiring the gap sign to persist a second") -- i.e. it is NOT a pure
function of a single instant, it carries state across sequential ticks. My
tests above all sample single instants directly and would not catch a bug
that only appears after many sequential ticks build up bad hysteresis state
for one specific car. This needs either (a) a sequential tick-by-tick replay
of the worker loop rather than one-shot sampling, or (b) a live repro:
**which mode (Replay or New Race), which Grand Prix, and roughly how far
into the race** would let this be pinned down directly instead of guessed at
further.

### 4. Simulation not smooth: stutters, and the car occasionally jumps
backward or forward in position
**Reported as:** playback sometimes lags, sometimes a car appears to move
backward, sometimes jumps ahead too fast.

**Status: the mechanisms I know to check are sound; not yet reproduced.**
Read the whole pipeline from clock to pixel:
- `sim.worker.ts`'s fixed-step loop: `dtWall` is wall-clock, clamped to 0.25s
  (handles a stalled/backgrounded tab), `sessionTime` only ever moves forward
  (`Math.max(0, next)`) while playing -- no path that decrements it.
- `scene.ts`'s `blendPose()`, which interpolates between the last two worker
  ticks for a smooth picture at the display's own refresh rate: it explicitly
  blends `stationM` the SHORT way around the lap (handles the start/finish
  wraparound correctly -- a naive linear blend across that wrap is exactly
  the classic "car sweeps backward across the whole track" bug, and this
  code already guards it, per its own comment and a dedicated `d > trackLen/2`
  correction).
- Heading is deliberately NOT blended/lerped (which would fight the station
  blend and could wobble) -- it is re-derived from the ring tangent at the
  already-blended station every frame.

None of this rules out a real bug (a `setInterval`-based tick isn't
perfectly regular under main-thread load, which could make `alpha` in
`blendPose` spike when a tick arrives late), but nothing was found broken by
reading it. This needs a live repro to profile further: does it happen in
Replay, New Race, or both; at 1x speed or only at higher playback speeds;
and does it correlate with anything else running (camera drag, tab
backgrounded, DevTools open)?

### 5. Car model "wobbling"
**Reported as:** a car visibly wobbles (unclear yet whether in orientation,
elevation, or lateral position).
**Status:** not yet root-caused. `smoothedLateral`/`smoothedValid` in
`scene.ts` suggest lateral position is ALREADY eased for exactly this reason
(a lane-change sliding instead of snapping), so a residual wobble is more
likely in heading (ring-tangent noise at a low-curvature/high-jitter station)
or elevation (`surfaceAt` changing quickly bin-to-bin on a bumpy stretch)
than in lateral placement. Needs more detail to reproduce: which car,
stationary or moving, which circuit, and whether it is positional or purely
visual (geometry jitter vs. camera shake).

## WON'T FIX (real data limit, not a bug)

- **Cars run essentially single-file once racing.** The 2026 position feed
  is genuinely ~one-dimensional: across 674-1082 clean laps per circuit the
  median lateral spread between cars at a fixed point on track is 0.33-0.61 m
  (max 4.5 m during a committed pass). There is no real per-car lateral
  telemetry to draw once a car leaves its grid box, so the renderer correctly
  shows near-zero lateral once moving -- this is not "not enough variation
  added", it is the data having none to give. What WAS a bug (item 1 above)
  is that the grid box itself, before launch, was computed far too wide.
