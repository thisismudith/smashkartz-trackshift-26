# TrackShift UI Plan — surfaces beyond the simulator

Companion to `API.md` (what the UI may call) and `MODELS.md` (who builds what). This file
is the UI owner's plan: what to build, in what order, and from which inputs.

## 0. Amendments since this plan was written

Two things changed materially during the build. They are recorded here rather than silently
edited into the sections below, so the plan reads as what was decided and when.

### 0.1 Scope narrowed to Energy & Overtake only

The user's instruction, verbatim: *"do not go into pit-stop and shit, like we need related to our
ENERGY AND OVERTAKE Intelligence problem statement only"*.

Removed accordingly, after being built:

- `/insights` — the tyre-degradation-index metric and the whole pit-loss-by-circuit section.
- `/lab` — the Tyre parameter group and the Stops-and-interruptions group.
- `/sessions/[track]/[session]` — the entire "Stints & tyres" view (stint timeline, degradation
  scatter), replaced by an **Overtaking** view.

Section 4.4 below (Stints & tyres) is therefore **void**. Tyre and fuel context may still appear
where it is an input to an energy or overtaking question, never as a subject of its own.

The session views are now, in tab order: **Energy & ERS**, **Overtaking**, **Pace & progression**.

### 0.2 The processed lake exists, and it changes section 1.3 and section 6

`data/processed/` was empty when this plan was written. It is now 789 MB and holds two datasets
that between them supply most of what section 6 proposed to build:

| Dataset | Shape | What it unblocks |
|---|---|---|
| `data/processed/segments/circuit=<name>/segments.parquet` | 42 columns, 14 circuits; 75 672 rows for British alone | **M03 segmentation is done.** `segment_id`, `kind`, `corner_type`, `corner_id`, `track_heading_deg`, `start/end_distance_m`, `entry_speed_kmh`, `segment_time_s_offline`, brake and throttle fractions, `race_position`, `track_status` |
| `data/processed/telemetry_20m/year=2026/event=<E>/session=<S>/telemetry_20m.parquet` | 48 columns, the 20 m lake | `gap_ahead_m` **per 20 m bin**, `driver_ahead_number`, `engine_rpm`, `speed_kmh`, `throttle_pct`, `brake_on`, `gear`, `acc_*`, `x/y/z_m`, `lap_fraction` |

Consequences:

- **Section 1.3 rows 1, 2 and 5 are obsolete.** Segmentation is built, and the gap-to-car-ahead
  channel exists in both datasets (`gap_ahead_m_entry` per segment, `gap_ahead_m` per 20 m bin).
  It was never missing from the pipeline — only from the *frontend artifacts*.
- **Section 6.1 and 6.2 shrink from "compute and emit" to "aggregate and emit".** The Python work
  is no longer a twin change plus a new binary channel; it is an aggregator that reads the lake and
  writes a compact per-session artifact. Nothing needs to be recomputed.
- Rows 6 and 8 of section 1.3 stand unchanged: the Detection/Activation lines are still absent
  from every dataset, so overtake *eligibility* remains uncomputable, and λ_E, pass probability,
  rival belief and the planner are still unbuilt.

Note also `data/processed/` requires `pyarrow`, which is in `requirements.txt` but not installed in
the base interpreter on this machine.

### 0.3 What the twin's numbers actually look like

Measured on the shipped British GP Race artifact, and now stated on the Energy view rather than
discovered by a reader:

- **90% of estimated laps have `socEndMj == 0`** (1006 of 1113). The modelled store is at its floor.
- **Median per-lap energy balance is −2.89 MJ** (range −7.05 to +6.88).
- **221 of 255** estimated laps for a five-driver selection exceed the unverified envelope.

The balance does not close, which is a known, deliberately untuned open item. The Energy view
therefore leads with a calibration statement, promotes the *balance* to the headline chart, and
demotes the *store* trace to last with an explicit "not yet usable as a state estimate" label —
rather than drawing a flat zero line that reads as a claim about the cars.

---

## Context

`frontend/src/app/page.tsx` is still a stub: a hero, four hard-coded cards all reading `status: "planned"`, and — measurably — **no link to `/sim` or `/sim/new` at all**. The only working surfaces in the app are the two simulator routes, and the only `<Link>`s anywhere are `/` ↔ `/about`. Meanwhile `AGENTS.md §43`'s entire **UI** checklist block is unticked.

At the same time the pipeline is far richer than the UI admits. `frontend/public/sim/` already holds 160 MB of built artifacts across **13 circuits and 18 sessions** — full per-lap tables, race-control feeds, neutralisation intervals, weather series, a 12-byte-per-sample telemetry blob, fitted model parameters with confidence intervals, and a complete 2026 rule configuration. Measured: **17 301 driver-laps, every one of which carries an energy-twin estimate.** Almost none of it is drawn anywhere except inside the 3D simulator, and most of it is drawn only for the one focused car.

The ask, from the user: add surfaces around the home page that display the project's different datasets properly; take `formula-telemetry.com/sessions` as a reference for how a session-analysis site is organised; bend it toward **energy and overtake intelligence**; and give the user parameters they can change to get answers *without running a whole simulation*. Understanding what **inputs** that needs, and what else must be built, is an explicit part of the deliverable.

The intended outcome: the project stops looking like "a nice 3D race replay plus some planned cards" and starts looking like the decision-engine `AGENTS.md §1` describes — with the analysis surfaces a judge can actually read, the honest gates where models do not yet exist, and a what-if lab that answers small questions instantly.

**Step 0 of implementation: copy this file to the repo as `UI.md`** (root, beside `API.md` / `MODELS.md`), so the UI owner's plan sits with the other contracts. It is referenced below as the UI-side companion to `API.md`.

---

## 1. What the audit found

### 1.1 Already built, already shipped, currently undrawn

| Asset | Where | Used by UI today |
|---|---|---|
| Per-lap energy twin output — `socEndMj`, `socUncertaintyMj`, `ersEnergyUsedMj`, `ersEnergyHarvestedMj` (+ braking/ICE split), `energyBalanceMj`, `peakWheelPowerKw`, `peakBrakingKw`, `envelopeCapViolations`, `warnings[]` — for **every driver, every lap, every session** | `<slug>-race.<hash>.json` → `RawLapEntry.energy` | Six static meters, **one car only**, no time series anywhere |
| Fitted parameters with `se` / `ci95` / `n` / `provenance`: pace trend per track, tyre-deg index per track, compound multipliers, driver & team offsets, dirty-air loss per track, pit loss + IQR, SC/VSC/red hazards, retirement rates, noise sigmas | `params.<hash>.json` | Consumed numerically by the generated-race engine; **never displayed** |
| 2026 rule config — both power-envelope curves as piecewise-linear breakpoints, separation speed 300 km/h, three distinct energy quantities (store 4.0 MJ, deploy 8.5 MJ/lap, harvest 9.0 MJ/lap), every key with `source`, `citation`, `verified: false` | `rules.<hash>.json` | Used to grade the driver meters; the **curves themselves are never drawn** |
| Per-sample telemetry: station, lateral, speed, gear, brake, throttle at ~3.7 Hz | `<slug>-race.<hash>.bin`, `codec.ts` | Feeds car poses in the 3D scene only |
| Race control (248 messages at Silverstone), neutralisation intervals, weather series, tyre stints, pit in/out, positions | session manifest | Feed / scrubber markers / strip in the sim HUD |
| Catalogue — 13 tracks with `raceLaps`, entries, winsorised weather stats, tyre compound counts; teams with colours | `catalogue.<hash>.json` | Grid builder only |
| `applyShadowPriceOverlay()` — per-vertex track colouring, **fully implemented and unit-tested**, wired into `SimRenderer.setShadowPrice` | `render/trackMesh.ts`, `render/scene.ts` | **Zero callers.** The "Shadow-price map" card is the missing front end for code that already exists |

### 1.2 Computed in Python, then thrown away

This is the highest-leverage finding.

- **`scripts/simdata/twin.py` already returns per-sample arrays** for `ers_deploy_power_est_kw` (+ uncertainty), `ers_harvest_power_est_kw` split into braking and ICE paths, `ers_soc_est_mj` (+ uncertainty), both budget remainders, `wheel_power_kw`, `drag_kw`, `rolling_kw`, `gradient_kw`, `ice_power_est_kw`, and `cap_normal_kw` / `cap_override_kw`. `scripts/simdata/replay.py` calls `lap_summary()` on all of it and ships **only the ~24 scalars**. Every energy-over-distance chart below is blocked on nothing but choosing to emit this.
- **`Lap.gap_ahead` and `Lap.ahead`** (from raw `DistanceToDriverAhead` / `DriverAhead`) are parsed in `rawio.py`, carried into `replay.py`, and **never encoded** into either the binary or the manifest — while `capabilities.hasDriverAhead` is hardcoded `True`. Proximity and battle views are blocked on this one omission.

### 1.3 Genuinely missing — and what it blocks

| Missing | Model | Blocks |
|---|---|---|
| Track segmentation, stable `segment_id` | M03 | Every per-segment view. `MODELS.md §4`: *"Segmentation is the first thing to freeze, because `segment_id` is the join key for every downstream table."* |
| **Detection / Activation line positions** | M18 | All overtake-eligibility work. `CHECKPOINTS_TANVEER.md` records these are **nowhere in the raw data** and must come from FIA documents. This is the single biggest blocker on the "overtake intelligence" half of the problem statement |
| Battle episodes, pairwise features | M05, M06 / C8 | Battle picker, `/battles/{id}/timeline` |
| `normal_race_model_eligible` gate in the sim artifacts | M02 / C7 | Greying model panels honestly (it exists in `src/trackshift/race_context.py`, but not in `frontend/public/sim/`) |
| λ_E / DP tables | M22 | The hero visual — renderer already ready |
| Pass probability, rival belief, planner, simulator | M10, M09, M24, M26 | Recommendation, belief bars, baselines comparison |

Note also: the 2026 `drs` channel is dead (all zeros), so `drs_open_observed` must stay unavailable — a UI that shows a DRS indicator for 2026 would be fabricating.

---

### 1.4 Data quality — four measured faults the UI must not launder

Recorded separately during the September audit, each proven with numbers and then
re-measured by an adversarial verifier. They matter here because a session-analytics
surface is precisely the thing that turns a silent data fault into a confident-looking
chart.

| Fault | Measured | What it breaks in this plan |
|---|---|---|
| **Sentinel coordinates** — "position unknown" written as a real coordinate on the grid and on every pit visit, while speed/gear/throttle keep running (median 79.6 km/h, the pit limit) | at least three sentinel families; China uses (−832.5, −705.8) m, Canadian P1 (345, 86) m at 6.21%, Miami Q and Belgian P3 the origin | any distance-binned series (§6.1), any track-position overlay |
| **Hungary Race is a stale-anchor sample-and-hold, laps 15–70** | only 53.6% of (x,y) samples per lap distinct; 920 of 1136 clean laps affected; integrated path +10% | `?view=telemetry` and the `?view=energy` along-lap panels. The fastest laps are late-race, so a naive "fastest lap" default lands **inside** the corrupt window |
| **Monaco Race has almost no positions** | 1324 of 1452 laps have null x; all-or-nothing per lap; the 128 laps that do carry positions are fine | the Monaco session page must say "positions unavailable for this session", not render an empty or wrong map |
| **Suzuka's ring self-crosses** | 529 vertex pairs within 12 m but >150 m apart in station; a stateless projection jumps 2365 m | distance-binned anything at Suzuka |

Two consequences that are **requirements, not polish**:

1. **The session browser must surface per-session capability, not hide it.** `capabilities`
   already exists in the manifest (`hasPositions`, `hasDriverAhead`) and is currently
   partly dishonest — `hasDriverAhead` is hardcoded `true`. Extend it with a per-session
   data-quality block (position coverage fraction, sample-uniqueness fraction, sentinel
   fraction) and render it on the session card **before** the user opens a view. A view
   that cannot be drawn honestly is disabled with its reason, exactly like the model gates
   in §4.10.
2. **Never calibrate against the telemetry `distance` channel.** It is per-driver
   integrated wheel speed and disagrees between cars at the same circuit by up to 4.14%
   (ALB reads 5945 m at Monza where PIA reads 5735 m). The ring is the coordinate system.
   Any x-axis labelled "distance" in §4.5 must be ring station, not the `distance` channel.

### 1.5 The artifacts on disk are stale

The energy twin was rebuilt during the track-mapping campaign (real ICE power map off the
feed's own rpm; the duplicate envelope deleted). **The 13-circuit artifact set in
`frontend/public/sim/` predates that rebuild**, and the full rebuild is still pending.

So the 17 301 driver-laps are real and the schema is right, but the energy *values* will
change under a rebuild. This does not block building the charts — the shapes and fields
are settled — but it does mean **no screenshot of an energy chart should be treated as a
result until the rebuild has run**, and the rebuild should happen before §4.3 is reviewed
for correctness rather than for layout.

Also still open from that campaign and worth knowing before touching these files:
`manifest.ts` still drops the corner metadata (`markerLateral`, `labelAngleDeg`,
`rotationDeg`) that `parseCorners` exposes; the `py-core` gates in `rawio.py` were never
written. Gating the four faults above belongs at `rawio.py`'s `Lap.__init__` — the one
choke point every consumer already passes through — with flagged samples marked ABSENT,
never OBSERVED.


## 2. Reference site, mapped to our problem

`formula-telemetry.com` organises around six jobs. Each maps onto something we can do, and in four cases we can say something they cannot, because we have an energy twin.

| Their section | Our version | Status |
|---|---|---|
| Telemetry comparison — speed/throttle/brake/gear overlaid for two drivers | Same four channels **+ estimated ERS deploy and the regulatory cap at that speed**, plus the delta trace | Channels ready; energy trace needs §6.1 |
| Performance analysis — lap deltas, sector splits, leaderboard | Same, **+ energy-normalised pace (s per MJ deployed)** | Ready today |
| Race progression — order lap by lap, gap to leader, overtakes | Same, **+ an overtake ledger annotated with each car's energy state** | Order/gaps ready; proximity needs §6.2 |
| Strategy — stints, compounds, degradation curves | Same, **+ per-stint energy balance and budget overrun** | Ready today |
| Track visualisation — racing line in 2D/3D | 3D already exists and is better than a line; add a **2D map** that can be coloured by a per-distance quantity | 2D map ready; λ_E colouring gated on M22 |
| Season analytics — standings, box plots, head-to-head | Cross-event **energy and overtaking league** — deg index, dirty-air index, harvest efficiency by circuit | Ready today |

The differentiator to lean on: they show what the car *did*. We show what the energy was *worth* — and, until M22 lands, at minimum what the energy *was*, with its uncertainty and its distance to the regulatory cap.

---

## 3. Information architecture

```
/                         Hub — what the system is, live data summary, entry points
/sessions                 Session browser: 13 circuits × available sessions
/sessions/[track]/[session]
      ?view=pace          Pace & progression
      ?view=energy        Energy & ERS            ← the differentiator
      ?view=strategy      Stints & tyres
      ?view=telemetry     Driver-vs-driver traces
      ?view=battles       Proximity & overtakes   (partly gated, see §6.2)
/lab                      Parameter lab — what-if without a sim run
/rules                    Regulation panel — envelopes, budgets, provenance
/insights                 Cross-event league tables
/decision                 Shadow price · rival belief · recommendation  (model-gated shell)
/validation               Model card / evidence                          (model-gated shell)
/sim, /sim/new, /about    Existing, unchanged — now actually linked
```

A shared `<SiteNav>` is new work and is the fix for "nothing links to the simulator". It belongs in `src/components/nav/`, mounted in `src/app/layout.tsx` beside the existing `<RaceLoader />`, and must not render over the simulator canvas routes (the sim HUD owns the full viewport) — gate it on pathname.

`?view=` as a query param rather than nested routes keeps one data fetch for the session manifest across all five views; the manifest is 300–750 KB and should be fetched once per session and shared.

**Data-loading shape** — this matters more than it looks:

- `?view=pace`, `?view=energy`, `?view=strategy` need **only the session manifest**. Per-lap timing, positions, stints, pit, race control, weather *and the per-lap energy for every driver* are all already in it. One fetch, no binary, no decode — these three views are cheap.
- `?view=telemetry` and the along-lap panels of `?view=energy` need the **`.bin`** (3–10 MB). Lazy-load it on entering that view only, and reuse the existing worker rather than decoding on the main thread.
- `/lab`, `/rules`, `/insights` need only `params.json` / `rules.json` / `catalogue.json` — all small, all cacheable as `immutable` (the content-hashing and cache headers in `next.config.ts` already do this).

All of it goes through `SimSource` in `src/sim/data/source.ts`. Extend that interface with the new artifact accessors; do not let a page `fetch()` a `/sim/...` path directly, or the eventual `ApiSimSource` swap stops being a one-file change.

---

## 4. Surface specifications

Every number on every surface carries a provenance tag. Reuse the existing `provPill[data-kind=observed|inferred|missing]` in `src/sim/ui/panels.module.css` rather than inventing a second convention.

### 4.1 `/` — Hub

Replace the four `status: "planned"` cards. The hub should prove the system has data, not promise it will.

- **Hero** — keep the existing wordmark treatment; keep the lede from `AGENTS.md §1`.
- **Live data summary** — a small stat row computed from `catalogue.json` + `index.json`: **13 circuits · 18 sessions · 17 301 driver-laps with an energy estimate**, plus total estimated energy deployed. These are *stat tiles*, not charts — a single headline number each, per the form heuristic. Compute them at build time from the index rather than hard-coding, so they cannot go stale.
- **Entry cards** — one per real surface (`/sessions`, `/lab`, `/rules`, `/insights`, `/sim`), each with one sentence and a live count. Model-gated surfaces (`/decision`, `/validation`) keep a `status` badge, but the badge now names the blocking model (`awaiting M22`) rather than the word "planned".
- **Footer** — keep the provenance sentence; add the version footer `API.md §3.6` asks for, sourced from the artifact manifest.

### 4.2 `/sessions/[track]/[session]?view=pace` — Pace & progression

| Chart | Form | Encoding |
|---|---|---|
| Lap-time traces | Multi-line, x = lap, y = lap time (s) | One line per selected driver, team colour; SC/VSC/red spans as recessive background bands from `neutralisation[]`; pit laps marked, not hidden |
| Position progression | Step line, x = lap, y = position (inverted axis) | Classic "spaghetti"; hover isolates one driver, dims the rest |
| Gap to leader | Line, x = lap, y = gap (s) | Derived from `sesT` per lap |
| Lap-time distribution | Box plot per driver, ordered by median | Excludes in/out laps and neutralised laps — state the exclusion rule on screen |
| Sector splits | Grouped bars, s1/s2/s3 | Only where `s1/s2/s3` are present |

Controls: driver multi-select (default: top 5 finishers), "exclude neutralised laps" toggle, "exclude in/out laps" toggle, lap range brush.

### 4.3 `/sessions/[track]/[session]?view=energy` — Energy & ERS

The surface that does not exist anywhere else. Everything here is `SIMULATED` or `INFERRED` and must be labelled *estimated*, never "battery" or "SOC" (`API.md §8`).

| Chart | Form | Encoding |
|---|---|---|
| Estimated store over the race | Line + uncertainty band, x = lap, y = MJ | `socEndMj` ± `socUncertaintyMj`; capacity 4.0 MJ as a RULE reference line, badged unverified |
| Deploy vs harvest per lap | Diverging bars around zero, x = lap | Deploy negative (red), harvest positive (blue), neutral grey midpoint; the one place a diverging pair is correct |
| Harvest split | Stacked bars, braking vs ICE path | From `ersEnergyHarvestedBrakingMj` / `...IceMj` — currently computed and dropped by `lap_summary`; see §6.1 |
| Budget burn-down | Line, x = lap, y = MJ remaining | Deploy 8.5 / harvest 9.0 per lap, both `verified: false` — badge every one |
| Cap vs estimated deploy along a lap | Two stacked panels sharing one x = distance axis: speed on top, power below | **Never a dual-axis chart.** Cap line from `cap_normal_kw`/`cap_override_kw`, estimate with its uncertainty band, headroom as the shaded difference |
| Envelope-violation strip | Heat strip along the lap | `envelopeCapViolations` — framed as a **twin calibration warning**, never "the car exceeded a limit" (`AGENTS.md §28.1`) |
| Energy-normalised pace | Scatter, x = MJ deployed, y = lap time | One point per driver-lap; the closest thing we have today to "what was the energy worth" |

Controls: driver select, lap select (for the along-lap panels), mode NORMAL/OVERRIDE for the cap curve, uncertainty band on/off.

### 4.4 `/sessions/[track]/[session]?view=strategy` — Stints & tyres

- **Stint timeline** — horizontal bars per driver, segmented by stint, coloured by compound, pit stops as gaps. Compound colour is a *status-like* categorical: fixed order, direct-labelled with the compound initial so it is never colour-alone.
- **Degradation curves** — lap time vs tyre life, one line per compound, fitted slope from `params.tyreDegradation` drawn with its CI band beside the observed points. This is the first place the fitted parameters become visible.
- **Pit-loss reference** — the track's `pitLoss.netLossSeconds` with its IQR, and the neutralised variant, as a labelled reference.
- **Undercut/overcut view** — for a selected pair, lap-time delta around their pit laps.

### 4.5 `/sessions/[track]/[session]?view=telemetry` — Driver-vs-driver traces

The direct `formula-telemetry.com` analogue, plus energy.

Stacked panels, **one shared x = distance-along-lap axis**, each its own y:
speed (km/h) · throttle (%) · brake (on/off as a band) · gear (step) · **estimated ERS deploy (kW) with the cap overlaid** · cumulative time delta between the two drivers.

Controls: driver A / driver B, lap A / lap B, x-axis toggle distance ↔ time, corner markers from `corners.station` shown as recessive verticals with corner numbers.

Performance note: a Silverstone lap is ~700 samples; two drivers across six panels is ~8 400 points. That is fine as SVG with the decimation described in §7.

### 4.6 `/sessions/[track]/[session]?view=battles` — Proximity & overtakes

Partly gated. What is buildable once §6.2 lands:

- **Gap-to-car-ahead trace** for a selected driver — line, x = distance or lap, y = gap (s or m), with a 1.0 s threshold reference line.
- **Time-within-1s table** — per driver, laps and seconds spent inside the threshold. This is the honest proxy for "overtake opportunity" until the Detection Lines exist.
- **Overtake ledger** — position changes derived from lap-end `pos`, each annotated with both cars' energy state that lap.

Explicitly gated with a named reason (`awaiting M18 — Detection/Activation line positions are not in the raw data`): eligibility state, armed/not-armed, pass probability.

### 4.7 `/rules` — Regulation panel

Fully real today, and it is the surface that most directly proves the project's honesty discipline.

- **The two power-envelope curves** drawn against speed, with the 300 km/h separation point marked and the region below it shaded "mode not discriminable" (`API.md §3.11`). Draw the curves; never print a peak kW as "the" power limit.
- **The three energy quantities** side by side — store 4.0 MJ (instantaneous), deploy 8.5 MJ (per lap), harvest 9.0 MJ (per lap) — deliberately three different numbers, shown with their accounting windows so they cannot be read as interchangeable.
- **Provenance ledger** — every key with its `source`, `citation`, `note`, and a prominent unverified badge. `describe_compliance()` already returns `legal_by_construction: False`; render that verbatim rather than hiding it.

### 4.8 `/lab` — Parameter lab

The answer to *"for small things they do not need to run an entire simulation"*.

Two calculators, both instant, both honest.

**A. Envelope calculator — fully real today.** Inputs: speed (km/h), mode, event. Output: the cap at that speed, both curves drawn with the current speed marked, headroom against a chosen deploy power, and whether the mode is discriminable at that speed. Python samples the curve via the single owner `max_electrical_power_kw` (`API.md §5.3a` already specifies a `step_kmh` sampling route for exactly this); the UI reads off the sampled table and performs no interpolation of its own.

**B. Strategy / energy what-if — needs the grid artifact in §6.3.** Sliders over the fitted parameters, each shown with its fitted value, `se`, `ci95` and `n` so the user can see how far they have moved from what the data supports:

- pace trend per lap, tyre-deg index, compound multipliers
- dirty-air loss, pit loss, SC/VSC hazard
- driver / team offset
- stint plan (compound, length), race length

Outputs: stint-time curves, crossover lap, pit window, race-time delta — each with a confidence band, and each labelled `SIMULATED`.

**The rule this respects:** the browser computes no physics. Python evaluates the pure functions over a declared grid and emits a lookup table; sliders **snap to grid points** and the UI reads values off. This is the pattern `API.md §5.12` and `§7` already specify for shadow price (`grid` in the response, one file per point, `index.json` enumerating them). When the FastAPI service lands, the same sliders become continuous by swapping `StaticSimSource` for `ApiSimSource` — no panel or chart changes.

**Important constraint discovered during the audit.** `frontend/src/sim/engine/lapModel.ts:24` — `composeLapTime()` — is the lap-time model re-implemented in TypeScript: track base + driver offset + `fuel × lapIndex` + `trackDegIndex × compoundMult × tyreLife` + dirty-air proximity + a Box-Muller noise core + incident tail, then a neutralisation multiplier. That is exactly the model `scripts/simdata/fit_pace.py` fits, evaluated forward in the browser — a pre-existing divergence from `AGENTS.md §25` ("model logic must live outside UI code").

It is also, inconveniently, precisely the maths the lab needs. The resolution: **the grid generator in Python becomes the canonical home for this composition**, and the lab reads grid values only. Do not add a second TS implementation. Consolidating `composeLapTime` itself into Python is a sensible follow-on — it would mean reworking the generated-race engine at `/sim/new`, so it is deliberately *out of scope here*, but it should not be forgotten: once the grid generator exists, the TS copy is a duplicate of fitted maths that can drift.

### 4.9 `/insights` — Cross-event league

Uses `params.json`, which is per-track by construction and currently invisible.

- **Overtaking difficulty by circuit** — `dirtyAirLossPerSecondOfProximity` as a ranked bar chart with CI whiskers. Directly on-theme for PS1.
- **Tyre-deg index by circuit** — ranked bars with CI.
- **Pace trend by circuit** — fuel-burn + track-evolution slope, with the honest note the artifact already carries: *"the two are collinear in lap number and cannot be separated from lap data alone."* Render that note.
- **Driver / team offsets** — a forest plot (point + CI, sorted). `se`, `ci95` and `n` are already in the artifact; this is the single highest information-per-pixel chart available today.
- **Energy league** — mean estimated deploy per lap, harvest efficiency, envelope-violation rate per circuit, from the per-lap energy already shipping.

### 4.10 Model-gated shells — `/decision`, `/validation`

Built now against the `API.md` schema, rendering an explicit empty state.

The gate names the blocking model and the eventual route, e.g.:

```
SHADOW PRICE · λ_E
AWAITING MODEL — M22 (dynamic programming)
renderer ready: SimRenderer.setShadowPrice()
source when available: GET /value/{event}/shadow_price
```

This is `API.md`'s "missing means missing" applied to a whole panel, and it converts the four dead "planned" cards into something a judge reads as discipline rather than as a gap. The λ_E panel is a special case worth calling out: the renderer is finished, tested and unused — the day M22 produces a `Float32Array` per distance bin, one call to `setShadowPrice` lights up the hero visual from `AGENTS.md §31 step 2`.

`/validation` renders the `API.md §5.15` shape: per-component metrics, each with `n` and its split. `AGENTS.md §30` and `API.md §8` both forbid one overall accuracy number — the panel must be per-component by construction, not by convention.

---

## 5. Inputs — the explicit question

### 5.1 Inputs we already have

| Input | Source artifact | Provenance |
|---|---|---|
| Track ring geometry (1 m, cm-quantised), corners, timing lines, pit lane, width, reference speed profile | `<slug>.<hash>.json` | DERIVED |
| Per-sample station, lateral, speed, gear, brake, throttle | `<slug>-<session>.<hash>.bin` | OBSERVED |
| Per-lap time, sectors, position, compound, life, stint, fresh, pit in/out, status, validity flags | session manifest | OBSERVED |
| Per-lap energy scalars (24 fields) | session manifest `.energy` | SIMULATED / INFERRED |
| Race control (classified), neutralisation intervals | session manifest | OBSERVED / DERIVED |
| Weather series — air/track temp, humidity, pressure, wind speed & direction, rain flag | session manifest | OBSERVED |
| Entries, teams, colours, circuit facts, race laps, tyre compound counts, winsorised weather stats | `catalogue.<hash>.json` | OBSERVED / DERIVED |
| Fitted parameters with `se`, `ci95`, `n` | `params.<hash>.json` | DERIVED |
| Power envelopes, energy budgets, compliance description | `rules.<hash>.json` | RULE (all `verified: false`) |

### 5.2 Inputs the user sets (the what-if surface)

Grouped as the eventual `StrategicState` groups them, so the lab's controls map 1:1 onto `API.md §3.4` when the service lands.

| Group | Controls |
|---|---|
| **Regulation** | mode NORMAL / OVERRIDE · deploy budget MJ/lap · harvest budget MJ/lap · store capacity MJ · envelope curve selection |
| **Race state** | track · session · lap · driver (and rival) · time gap s · relative speed m/s · eligibility state |
| **Energy** | estimated store MJ · deploy budget remaining MJ · deploy level ∈ {0, 0.25, 0.5, 0.75, 1.0} (a fraction of the *speed-dependent* cap, never of a fixed power) |
| **Car / strategy** | compound · tyre life laps · stint number · fuel-load lap proxy · stint plan |
| **Environment** | track temp °C · air temp °C · wind head/cross m/s · wet flag |
| **Fitted model** | pace trend · deg index · compound multiplier · dirty-air loss · pit loss · SC/VSC hazard · driver & team offset · noise sigma |

Two rules the controls must respect. Wind is entered as **head/cross components relative to the track**, never a compass bearing (`API.md §8`) — raw direction is not comparable between segments. And `deploy_level` is a fraction of the cap at the current speed, so the same slider position means different kilowatts at different points on the lap; the readout must show the resulting kW, not just the fraction.

### 5.3 Inputs still missing

| # | Missing input | Needed for | Effort |
|---|---|---|---|
| 1 | Per-sample energy series shipped to the UI | Every along-lap energy chart (§4.3, §4.5) | Small — the data is already computed, see §6.1 |
| 2 | Gap-to-car-ahead channel | Proximity, battles (§4.6) | Small — already parsed, see §6.2 |
| 3 | What-if grid artifact | `/lab` calculator B (§4.8) | Medium, see §6.3 |
| 4 | Sampled envelope curve table | `/lab` calculator A, `/rules` | Small, see §6.4 |
| 5 | Segmentation / `segment_id` | Per-segment anything | Blocked on M03 |
| 6 | Detection / Activation line positions | All eligibility work | Blocked on M18 + FIA documents |
| 7 | `normal_race_model_eligible` gate in the sim artifacts | Honest greying of model panels | Small once M02's output is joined into the sim pipeline |
| 8 | λ_E, pass probability, rival belief, plan | `/decision` | Blocked on M22 / M10 / M09 / M24 |

---

## 6. Python work required

All four items are additions to `scripts/simdata/`, all pure functions, all emitted through the existing content-addressed artifact writer in `scripts/build_sim_data.py`. None of them moves maths into the browser.

> **Status:** the design below is concrete and implementable, but the sizing and axis choices in §6.3 deserve one more pass before coding — see §10.

### 6.1 + 6.2 One derived-series blob (energy + gap)

These two are the same change and should land together: a **second binary blob per session**, binned by distance, carrying the channels the twin already computes and the gap channel that is already parsed.

**Why a separate blob rather than widening `SAMPLE_STRUCT`.** The existing 12-byte struct is per-sample at ~3.7 Hz — roughly 890 samples per Silverstone lap, and the `.bin` files already dominate the 160 MB artifact directory (~10.7 KB per driver-lap). Adding bytes there multiplies across all 17 301 driver-laps and would break `codec.ts` for every existing artifact. A separate blob binned at **20 m** gives ~295 bins per Silverstone lap instead of ~890 — a 3× reduction — and 20 m is the principled choice because it is exactly the spacing the lake uses (`build_phase2_dataset.py --spacing-m 20.0`).

**Struct** — `<HHHHHHHHB`, little-endian, **17 bytes per bin**:

| Field | Type | Scale | Source |
|---|---|---|---|
| `ersDeployKw` | u16 | 1 kW | `ers_deploy_power_est_kw` |
| `ersDeploySigmaKw` | u16 | 1 kW | `ers_deploy_power_uncertainty_kw` |
| `ersHarvestBrakeKw` | u16 | 1 kW | `ers_harvest_brake_power_est_kw` |
| `ersHarvestIceKw` | u16 | 1 kW | `ers_harvest_ice_power_est_kw` |
| `socMj` | u16 | ×1000 (1 kJ) | `ers_soc_est_mj` |
| `socSigmaMj` | u16 | ×1000 | `ers_soc_uncertainty_mj` |
| `capKw` | u16 | 1 kW | `cap_normal_kw` / `cap_override_kw` at this bin's speed |
| `gapAheadM` | u16 | 1 m | `Lap.gap_ahead` (`DistanceToDriverAhead`) |
| `aheadIdx` | u8 | — | index into the session's driver array |

Harvest is shipped as its two paths rather than a total, because the braking-vs-ICE split is exactly what §4.3's stacked chart needs and the total is recoverable by addition. `capKw` is shipped rather than derived so the UI never interpolates the envelope — the drawn cap is provably the one `max_electrical_power_kw` returned.

**Missing is missing.** `0xFFFF` on any u16 and `0xFF` on `aheadIdx` are null sentinels and must decode to `null`, never `0`. This matters most for `gapAheadM`: a leader has no car ahead, and a zero-metre gap would be a collision.

**Size and gating.** ~295 bins × 17 B ≈ 5 KB per driver-lap, so a British GP Race (946 driver-laps) is ≈ 4.8 MB. Across all 18 sessions it would be ≈ 85 MB, a 50% increase on the artifact directory — too much to do unconditionally. Gate it behind a new `--energy-series` flag on `build_sim_data.py`, defaulting to the demo event. `build_sim_data.py` already takes `--events` and defaults to `["British Grand Prix"]`, so this fits the existing shape: build the series for the events you are demoing.

**Frontend changes:** `codec.ts` gains `decodeDerivedLap()` alongside `decodeLap()`; `manifest.ts` gains the per-lap `derivedByteOffset` / `derivedBinCount`; `source.ts`'s `SimSource` gains a `derivedUrl(ref)` accessor. `capabilities` gains `hasEnergySeries` / `hasGapAhead` — and `hasDriverAhead`, currently hardcoded `true` in `replay.py:192` while the channel is discarded, must become an honest computed flag.

### 6.3 The what-if grid artifact

New `scripts/simdata/whatif.py`. It must call `fit_pace.py`, `twin.py` and `rules.py` — never re-derive their maths. A source-grep test (the pattern `test_twin.py` already uses to assert no envelope constant leaked into the twin) should assert the same here.

**Questions the lab answers:**

1. Optimal stop lap and stint crossover for a chosen compound plan.
2. Undercut / overcut window — how many laps of tyre-life advantage a stop buys.
3. Cumulative stint time vs stint length, by compound.
4. Race-time delta under a parameter change (e.g. "what if degradation were 30% worse here").
5. Deploy feasibility — given the budget and the envelope, what fraction of the lap can sit at the cap.

**Structure — a response surface per track, not one file per grid point.** `API.md §7` writes one file per grid point for shadow price because each point is a whole lap profile. Here each point is a handful of scalars, so the efficient form is **one file per track holding a dense flattened array** over declared axes:

```jsonc
// whatif/<track-slug>.<hash>.json
{
  "schemaVersion": 1,
  "track": "british-grand-prix",
  "raceLaps": 52,
  "baseline": { /* the fitted leaves used, each {value, se, ci95, n, provenance} */ },
  "axes": [
    { "name": "degMultiplier",  "values": [0.5, 0.6, "…", 2.0] },
    { "name": "pitLossDeltaS",  "values": [-5, -4, "…", 5] },
    { "name": "compoundPlan",   "values": ["S-M", "M-H", "S-M-H"] }
  ],
  "outputs": ["optimalStopLap", "raceTimeS", "raceTimeLowS", "raceTimeHighS", "crossoverLap"],
  "data": [ /* flattened row-major over axes, one record per combination */ ],
  "provenance": "SIMULATED",
  "assumptions": ["…shown verbatim in the UI, per API.md §8"]
}
```

With 16 × 11 × 3 = 528 records per track and ~40 bytes per record, that is ≈ 20 KB per track and **≈ 270 KB for all 13** — negligible, and it loads instantly.

**Axis discipline.** Do **not** take the cross product of every parameter — 13 tracks × six 10-step axes is ~226 000 combinations and is not shippable. Grid only the two or three axes that drive the headline answer; hold everything else at its fitted value and show that value with its CI beside the slider. Any axis that genuinely needs to be continuous is a reason to reach for the API, not a reason to grow the grid.

**Slider behaviour.** Sliders snap to grid points; the readout shows the snapped value and how far it sits from the fitted value in units of `se`. An off-grid combination is never interpolated in the browser — it either snaps or reports "not on the grid", consistent with "missing means missing".

### 6.4 Sampled envelope curve

The smallest item and the one that unblocks `/rules` and lab calculator A. `API.md §5.3a` already specifies the shape: sample `max_electrical_power_kw` at `step_kmh` (default 5) for both modes and emit the sampled curves plus the breakpoints and the separation speed. Add it to the existing `rules.<hash>.json` under a `sampled_curves` key rather than creating a new artifact, since it is small and always wanted with the rules.

The point of shipping samples rather than letting the UI interpolate: there is exactly one implementation of the envelope in the system (`rules.py`, per `MODELS.md` M19), so the drawn line is provably the line the optimiser will see.

---

## 7. Chart module

New: `frontend/src/sim/charts/` — hand-rolled inline SVG, no new dependency. This matches the existing hand-rolled meters in `DriverPanel.tsx` and the SVG-over-bitmaps preference, and keeps colour under our control.

### 7.1 Primitives

| File | Exports |
|---|---|
| `scale.ts` | `linearScale`, `bandScale`, `niceTicks`, `invert` — pure, unit-tested |
| `Axis.tsx` | x/y axis with recessive ticks, tabular-nums labels |
| `Line.tsx` | 2px polyline, optional step mode, `decimate()` for dense traces |
| `Band.tsx` | uncertainty band (low/high pair as one filled path) |
| `Bars.tsx` | vertical / horizontal / diverging / stacked, 4px rounded data-ends, 2px surface gap between segments |
| `Dots.tsx` | scatter, ≥8px markers, 2px surface ring on overlap |
| `HeatStrip.tsx` | one-row sequential strip along a distance axis |
| `Forest.tsx` | point + CI whisker, sorted — for `params.json` leaves |
| `Box.tsx` | box plot |
| `Crosshair.tsx` | shared hover layer: vertical rule + tooltip, one per chart group |
| `ChartFrame.tsx` | title, legend, units, provenance pill, "show table" toggle |

### 7.2 Colour — computed, not eyeballed

`scripts/validate_palette.js` was run against the app's real surface `#111111`. Results:

- **The raw Haas set fails as a categorical palette.** White `#EFEFEF` and grey `#AEAEAE` are achromatic (chroma 0 — they read as grey, not as series); blue `#1B40A6` sits at 1.93:1 contrast, below the 3:1 floor; white and yellow sit above the dark-mode lightness band (L 0.48–0.67).
- **A 3-slot categorical set derived from the Haas hues passes every check:**

  | Slot | Hex | Derivation |
  |---|---|---|
  | 1 | `#DA291C` | Haas red, unchanged |
  | 2 | `#3A63D6` | Haas blue lightened to clear the 3:1 contrast floor |
  | 3 | `#A88606` | Haas yellow darkened into the dark-mode lightness band |

  `ALL CHECKS PASS` on surface `#111111`: lightness band, chroma floor, CVD separation (worst adjacent ΔE 27.8 protan / 19.7 tritan), normal-vision floor (32.6), contrast ≥ 3:1.

**Consequence to design around:** the palette supports **at most three chromatic categorical series**. For anything with more categories:

- **Driver / team series → team colours from `catalogue.json`.** This is already the project's one agreed palette exception, and it is the *correct* choice here because colour then follows the entity, never its rank — a filter that changes the driver count must not repaint the survivors. These are not CVD-validated as a set, so identity must never be colour-alone: every series carries its 3-letter code as a direct label, and ≤ 4 selected drivers are direct-labelled at the line end.
- **Compound (SOFT/MEDIUM/HARD) → the 3-slot set above**, plus the compound initial as a direct label.
- **More than three non-entity categories** → small multiples or fold into "Other". Never a generated hue.

**Sequential** (magnitude — λ_E, deploy kW, violation density): one hue, light→dark, stepped from red; validate with lightness monotonicity, not the categorical checker (a sequential ramp fails that by design).

**Diverging** (harvest positive / deploy negative, gap opening / closing): `#3A63D6` ↔ neutral grey → `#DA291C`. Two hues plus a *neutral* midpoint — never a hue at the middle.

**Status** stays reserved and never doubles as a series colour: red = envelope violation / critical, gold = unverified regulation / warning. Always paired with an icon or word, never colour alone.

**Text never wears the series colour.** Values, labels and legends stay in `--foreground` / `--muted`; a colour swatch beside them carries identity. `panels.module.css` already records why (`--haas-blue` is unreadable as text on near-black) — that constraint becomes a general rule.

### 7.3 Rules the module enforces

- **Never a dual-axis chart.** Two measures of different scale become two stacked panels sharing one x axis (this is why §4.3 and §4.5 are specified as panel stacks).
- Legend present for ≥ 2 series; none for one (the title names it). ≤ 4 series also direct-labelled.
- Every chart ships a crosshair/tooltip by default and a "show table" toggle — the table is also the accessibility fallback.
- Thin marks, recessive grid and axes, selective direct labels — never a number on every point.
- Dark surface only; the app is `color-scheme: dark` with a black ground, so there is one validated mode, not two.

### 7.4 Performance

Decimate before rendering, never after: a Silverstone lap is ~700 samples, and `/sessions/…?view=telemetry` draws two drivers across six panels. `decimate()` uses largest-triangle-three-buckets to the pixel width of the chart, so point count is bounded by viewport, not by lap length. Target: no chart path exceeding ~2 000 points. If any single view measures over ~8 ms of scripting per interaction on an integrated GPU, that view moves to canvas — but start with SVG and measure.

---

## 8. Build order

Sequenced so that each step ships something usable and nothing is blocked on a model.

| # | Step | Depends on | State |
|---|---|---|---|
| 0 | This plan committed to the repo as `UI.md` | — | **done** |
| 1 | `<SiteNav>` + route skeletons for every new route; fix "nothing links to the simulator" | — | |
| 2 | `charts/` primitives + `scale.ts` unit tests; commit the validated palette into `src/lib/palette.ts` as a `CHART` block beside `HAAS` | — | |
| 3 | `/rules` — fully real, fastest proof the chart module works, most on-message surface | 2 | |
| 4 | `/insights` — forest plots and ranked bars straight from `params.json`; no new Python | 2 | |
| 5 | `/sessions` browser + `?view=pace` and `?view=strategy` | 2 | |
| 6 | **Python §6.4** — sampled envelope table → `/lab` calculator A | 3 | |
| 7 | **Python §1.4** — data-quality gates in `rawio.py`; honest `capabilities` per session | — | |
| 8 | **Python §6.1 + §6.2** — the derived-series blob (energy + gap), gated per event | 7 | |
| 9 | **Rebuild the 13-circuit artifact set** against the current twin (§1.5) | 7, 8 | |
| 10 | `?view=energy` and `?view=telemetry` | 2, 8 | |
| 11 | `?view=battles` | 8 | |
| 12 | **Python §6.3** — what-if grid → `/lab` calculator B | 6 | |
| 13 | `/decision` and `/validation` model-gated shells | 2 | |
| 14 | Rewrite `/` as the hub, now that every card points at something real | 1–13 | |

**Steps 1–5 need no Python at all and can land immediately** — that is the fastest route to
a UI that looks like the project it belongs to.

Order changes forced by §1.4 and §1.5: the data-quality gates (7) now come **before** the
derived-series blob (8), because binning a sentinel coordinate or a Hungary held anchor
into a 20 m series bakes the fault into the artifact. And the artifact rebuild (9) sits
before the energy views are reviewed for correctness, since the values on disk predate the
twin rebuild. Layout work on those views can proceed in parallel against stale data —
just do not read the numbers as results.
---

## 9. Verification

**Python** — pytest, following the existing `scripts/simdata/test_twin.py` / `test_rules.py` pattern:

- Per-sample energy emission: round-trip a synthetic lap through encode→decode and assert the decoded series matches `estimate_ers` output within the quantisation step; assert array lengths agree with `sampleCount`; assert purity and determinism as `test_twin.py` already does.
- Gap channel: assert `gap_ahead` survives to the artifact; assert a driver with no car ahead yields `null`, never `0` (missing is missing).
- What-if grid: assert every grid point file is reachable from the index; assert the grid maths calls `fit_pace` / `twin` / `rules` and holds no second implementation — reuse the source-grep test pattern that `test_twin.py` uses to assert no envelope constant leaked into the twin.
- Envelope table: assert the sampled curve equals `max_electrical_power_kw` at every sample point, so the drawn line is provably the line the optimiser will see.

**TypeScript** — vitest (`npm test`), node environment:

- `scale.ts` and `decimate()` unit tests, including the wrap and empty-array edges.
- `codec.ts` decode tests for the new struct, mirroring the existing lap-decode tests.
- A test asserting no chart component references a hex literal outside `palette.ts`.

**Palette** — re-run `node scripts/validate_palette.js "<series hexes>" --mode dark --surface "#111111"` whenever the categorical set changes; a FAIL blocks the change. Record the passing output in `UI.md`.

**Visual** — `npm run build` then screenshot each route at 1440 px and 400 px through the existing Playwright harness (`playwright-core`, local Edge channel `"msedge"`). Two rules from earlier sessions apply: never occupy port 3000 (the user runs their own dev server — use a spare port and kill it after), and never pipe `next start` to `head` (SIGPIPE kills it). Check for label collisions and horizontal overflow — the validator checks colour, not layout.

**Honesty checks** — these are acceptance criteria, not polish:

- No surface shows a `SIMULATED` energy value without the tag.
- No surface prints a peak kW as "the" power limit; `/rules` draws both curves.
- Every `verified: false` key is badged wherever a number derived from it appears.
- No overall-accuracy number anywhere (`AGENTS.md §30`, `API.md §8`).
- Every gated panel names its blocking model and its eventual route.
- No 2026 DRS indicator anywhere — the channel is dead and showing it would be fabrication.

---

## 10. Open items to settle on resume

1. **§6.3 axis selection.** The 2-to-3-axis choice is defensible but was not adversarially reviewed — the design agent was stopped mid-run at the user's request. Worth one pass to confirm the axes match the five questions and that 16 × 11 × 3 is the right resolution.
2. **§6.1 bin width.** 20 m is justified by the lake spacing, but 25 m would align with the existing `width.binMetres` and shrink the blob ~20%. Cheap to decide once, expensive to change later.
3. **Sequential ramp** for λ_E / deploy magnitude has not been generated or validated yet — only the 3-slot categorical set has, and that one genuinely passed. The ramp needs a lightness-monotonicity check before §4.3 ships.
4. **`composeLapTime` consolidation** (§4.8) is deliberately out of scope but should be tracked so the TS and Python copies do not drift.
5. Whether `/insights` should cover 2025 as well — `data/2025/` is a full mirror and the cross-season comparison would be strong, but no 2025 artifacts are built from it yet.

---

## 11. Interaction layer — filters and dynamic charts

Added after the user asked for "all filters and choosing searchselect multiple options … and just
more features to add/remove/select/all/none … dynamic graphs are better than static ones".

### 11.1 Filter kit — `src/components/filters/`

| File | Role |
|---|---|
| `filterState.ts` | Pure, **not** a `"use client"` module: `FACETS`, `FilterState`, `parseFilters`, `serialiseFilters`. It must be importable from a server component, and a client-only module throws *"Attempted to call parseFilters() from the server"* — that was a real 500-and-reload-loop during the build. |
| `useFilters.ts` | The client half: reads/writes the address bar with `replaceState`. |
| `MultiSelect.tsx` | Searchable multi-select: trigger, search box, All / None / Invert, grouped options with colour swatch and meta, removable chips. |
| `FilterBar.tsx` | The five facets plus Reset, with `useFacetOptions()` doing downward cross-filtering. |

**The URL is the store.** `?circuits=british-grand-prix,monaco-grand-prix&teams=Ferrari&drivers=HAM,LEC`.
A filtered view is a thing you send someone; a chart you can only describe in words is worth less.
Resolved server-side in `page.tsx` so the first paint is already filtered — no read-then-correct
flash. Writes use `replaceState`, because a checkbox click is a refinement, not a new page.

**Empty means empty.** There is no "empty implies all" shorthand: it would make *I cleared this*
and *I never touched this* render identically, which is the same class of error as zero-filling a
missing value.

**Bulk actions apply to what is visible.** "All" after a search means all matches, not everything
including things you cannot see.

**Colour follows the entity.** Option swatches come from the option, so deselecting a driver never
recolours the survivors.

Three facet decisions worth knowing, each verified against the artifacts:

- **Year has exactly one value (2026).** Rendered, disabled, with the reason. Omitting it would
  imply the dimension does not exist; faking it would be worse.
- **Driver options come from `tracks[].entries`, not `catalogue.drivers`.** Measured:
  `catalogue.drivers` has 35 rows / 32 distinct codes because it includes practice-only and
  reserve entries (ARO, BEG, BRO, CRA, FOR, HER, HIR, IWA, VES) who have no fitted parameter.
  `tracks[].entries` has exactly 23 codes — precisely the key set of `params.driverOffsetSeconds`.
  Offering the other nine would mean offering filters that can only empty a chart.
- **`LAW` is the one driver appearing under two teams** (Racing Bulls → Red Bull Racing) within the
  entry lists. The two entries are merged into one person, and the merge is **stated in the panel
  note** rather than done silently.

### 11.2 Chart interaction — `src/sim/charts/`

| Capability | Where | Notes |
|---|---|---|
| Crosshair + shared tooltip | `Plot`'s `hover` prop | Snaps to the nearest x across all series; a series with no sample near the snapped x is omitted rather than carried forward, which would invent a value it does not have. |
| Keyboard access | `Plot` | The chart is focusable when hoverable; ←/→/Home/End step the crosshair. Without it the numbers are pointer-only. |
| Legend click-to-isolate | `useSeriesToggle` + `ChartFrame`'s `hidden` / `onToggleSeries` | The caller filters what it passes to the marks, so the **y domain recomputes from the visible series** — hiding the outlier rescales the chart, which is the point of hiding it. |
| Responsive sizing | `Plot` | The viewBox **tracks the rendered width 1:1** via `ResizeObserver`. With a fixed 720-unit box scaled to 350 px on a phone, 10 px labels rendered at ~5 px and were unreadable. |
| Teammate disambiguation | `driverStyles()` | Two cars of one team share a colour; the second is dashed, in the line, the legend swatch, the option swatch and the chip. |

**Pointer → data.** The SVG is scaled by CSS, so a client x means nothing until it is divided by
the rendered width and multiplied by the viewBox width. Done by hand rather than via
`getScreenCTM()` to stay cheap enough for every `pointermove`.

### 11.3 Shared control — `src/components/ui/Segmented.tsx`

There were five spellings of the segmented button bar (two `role="tablist"`/`aria-selected`, three
`role="group"`/`data-active`). The two on the analysis pages are now one component with the correct
tablist semantics. The three inside `src/sim/ui/` are deliberately untouched — that package was
being edited concurrently.

### 11.4 Defects this work surfaced and fixed

- Marks were painted **outside the plot** — `.plot` needs `overflow: visible` for axis labels, so
  data now wears a `clipPath` and axes do not.
- `Grid` drew ticks outside the domain, because `niceTicks` rounds outward and only the axes
  bounds-checked. A stray rule appeared in the margin.
- Direct labels and `RefLine` labels ran off the right edge; both now flip their anchor.
- `CatalogueEntry.number` was typed `number` but is a **string** in the artifact, and
  `Catalogue.drivers` was typed with a `driver` key it does not have.

---

## Appendix — where the findings came from

| Claim | Verified by |
|---|---|
| 13 circuits, 18 sessions, 17 301 driver-laps, 100% with energy | walking every manifest in `frontend/public/sim/` |
| Twin computes per-sample arrays that `replay.py` discards | `scripts/simdata/twin.py` `estimate_ers()` vs `lap_summary()` |
| `gap_ahead` parsed then never encoded; `hasDriverAhead` hardcoded | `scripts/simdata/rawio.py`, `replay.py:192` |
| `applyShadowPriceOverlay` implemented, tested, zero callers | `src/sim/render/trackMesh.ts`, `scene.ts` |
| Nothing links to `/sim`; only `<Link>`s are `/` ↔ `/about` | grep across `frontend/src` |
| Raw Haas palette fails categorical checks; 3-slot derived set passes | `scripts/validate_palette.js` on surface `#111111` |
| `composeLapTime()` duplicates the fitted pace model in TS | `src/sim/engine/lapModel.ts:24` |
| Detection/Activation lines absent from raw data | `CHECKPOINTS_TANVEER.md` |
| 2026 `drs` channel is all zeros | `CHECKPOINTS_TANVEER.md` |
