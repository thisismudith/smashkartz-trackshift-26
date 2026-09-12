# SmashKartz frontend — TrackShift 2026

Next.js 16 (App Router) + React 19 + TypeScript. Demo surface for E-Delta; all model/planner logic
stays in Python (see `../AGENTS.md`).

## Run

```bash
npm install
npm run dev      # http://localhost:3000
npm run build && npm start
npm run lint
npm test         # vitest: physics + effects unit tests (node, no DOM)
```

## Data (required before anything renders)

Every chart, table and replay reads `public/sim/*.json|.bin`. Those are **gitignored build
output**, not repo content, so a fresh clone or a machine that just pulled has none.

`npm run dev` and `npm run build` check for them first (`scripts/ensure-sim-data.mjs`, wired
as `predev`/`prebuild`) and build them automatically when the raw mirror is present. So the
only step that is ever manual is the mirror itself, which is ~8 GB per season and cannot be
conjured:

```powershell
# once per machine, per season
.\scripts\data\download_initial_dataset.ps1 -Years 2026
npm run dev        # sees the mirror, builds the artifacts, then starts
```

Without a mirror, dev still starts and says exactly what is missing rather than failing with
a JSON parse error. Force a check or rebuild any time with `npm run sim:data`; build by hand
with:

```powershell
python scripts/build_sim_data.py --year 2026 --all --jobs 0 --fresh --prune
```

`--prune` deletes artifacts the new index no longer references; without it the directory
keeps every superseded rebuild (it had grown to 326 MB against a live set of 166 MB).

One build directory holds exactly ONE season: artifact filenames carry the circuit slug but
no year, so `--year 2024` into the same directory is refused unless you also pass `--fresh`.
The season that was built is recorded as `year` in `index.json`'s target.

Deploying: `public/sim` is not in the repo, so a hosted build serves no artifacts and every
panel falls back to its no-data state. Host them somewhere and set `NEXT_PUBLIC_SIM_BASE` to
that base URL; unset, the app reads `/sim` locally as before.

## Palette (strict)

| Token           | Hex       | Use                                              |
| --------------- | --------- | ------------------------------------------------ |
| `--haas-white`  | `#EFEFEF` | body text, car shell, smoke core                  |
| `--haas-grey`   | `#AEAEAE` | muted text, floor edges, smoke, tarmac lift       |
| `--haas-red`    | `#DA291C` | start lights, HAAS logotype, race number, tyre heat |
| `--haas-blue`   | `#1B40A6` | VF-21 front-wing endplate flash                   |
| `--haas-yellow` | `#FFD60A` | painted grid-slot line (FIA track marking)        |
| `--haas-black`  | `#111111` | background, tyres, carbon, rubber marks           |

Source of truth: `src/lib/palette.ts` and the CSS variables in `src/app/globals.css`. The two canvas
layers only ever use alpha blends of white/grey/black (source-over, never additive) — the harness
asserts the tarmac stays on the grey axis. Blue and yellow are livery/track colours, used in the DOM
and SVG only.

## Type

One variable family, **Archivo**, for the whole app (`src/app/layout.tsx`; `globals.css` aliases
`--font-body` to it), self-hosted by next/font at build. The wordmark runs `900` weight at
`font-stretch: 118%` with a `-8deg` oblique — heavy, wide and fast, the way a team wordmark reads.

## Intro animation (F1 standing start)

This is the page-load intro only — **not** the race simulator. `src/components/loader/`:

| File | Role |
| --- | --- |
| `RaceLoader.tsx` | Mounted once in `app/layout.tsx` → plays on every full load, never on client navigation. SSR frame is the static pose (car on the grid, gantry dark); everything else starts in the effect. Self-unmounts on the overlay fade (fail-safe 7 s). |
| `runtime.ts` | The only file that touches both a `dt` and the DOM: start-light clock, rev presentation signal, fixed-step accumulator (≤1/240 s, sim time = 1.1× wall), car transform + tread offsets, letter ignition, camera and end-state classes. |
| `physics/` | Pure, unit-tested launch model: Pacejka longitudinal tyre, implicit-stiffness wheel step, driver slip-target throttle with a clutch-dump window, viscous diff, weight transfer, bicycle-model yaw with tyre relaxation length + combined-slip collapse and a lagged counter-steer (that is where the fishtail comes from), rear tread temperature. `constants.ts` is the single source of numbers. |
| `effects/` | Struct-of-arrays smoke pool (pre-rendered sprites, oldest-recycled), two-regime rubber deposition (`depositAlpha`), asphalt speckle + lighting. |
| `HaasCarTop.tsx` | Top-view Haas VF-21 SVG (560×200 viewBox, 100 units/m, nose +x): white shell, carbon floor/wings/suspension, red logotype and race number, blue endplate flash. Tyres are sibling `div`s (`[data-wheel]`) so tread can animate without re-rasterising the SVG. |

Layer stack (bottom → top): marks canvas → grid paint → wordmark + tagline → car → smoke canvas →
start-light gantry. The name literally emerges through the launch smoke.

### Beat sheet (wall clock from navigation, 1440 px, measured)

```
0.00  car still on the grid, gantry dark, grid L painted
0.35  ●○○○○
0.77  ●●○○○   engine spools: rev signal ramps, exhaust glow in
1.18  ●●●○○   rears start to turn, tread blurs, tyres heat
1.59  ●●●●○   first smoke wisps, shudder escalating
1.89  ●●●●●   FULL REV — max shudder + 11 Hz limiter flutter
1.89–2.73     all-red HOLD — length is drawn fresh from the RNG every load (0.45–1.0 s),
              the way the FIA varies it so nobody can anticipate lights-out
2.73  ○○○○○   LIGHTS OUT → clutch dump: 33 m/s slip, thick smoke, long stripes
              gantry + grid fade (0.45 s) — you leave the grid behind
3.05–4.11     letters ignite as the rear axle passes them
4.11          rear axle past 85 % width → marks dim to 60 %, smoke clears
4.61–5.88     tagline at full brightness (a real ~1.3 s hold, not a flash)
5.88–6.29     overlay fade → unmount
```

The **grid slot** is an open L — a lateral line just ahead of the nose plus one longitudinal line
down the left flank, open at the rear and on the right — not a closed box. It is DOM, so it is in
the SSR frame and fades with a compositor-only opacity.

**What is physics and what is presentation.** Car motion, wheel speeds, smoke rate, rubber alpha,
tyre heat and the yaw wobble all come from the model in real units. The presentation-only signals,
each labelled in code: `revFrac` (the model has no clutch, so "engine on the limiter" is shown
through shudder and exhaust glow, never through fake wheel speed) and the yaw gains in `runtime.ts`
— `VISUAL_YAW_GAIN` ×4 on the body, `VISUAL_PATCH_YAW_GAIN` ×1.5 on the contact patches, because a
fully-exaggerated patch path braids the rubber stripes into wire. Tread stripes scroll at the true
surface speed until ~1.5 m/s, then cross-fade to a blur band and only crawl, so they never strobe.

`prefers-reduced-motion: reduce` shows the static composition (name above, car parked below) and
fades at 0.9 s — no canvases, no lights, no physics.

Per-frame budget: ≈1–2 ms JS; smoke canvas at DPR 1, marks canvas at DPR ≤2 (≤6 Mpx). Measured in
headless Edge: frame interval p95 17.2 ms at a steady 60 fps. The overlay captures pointer events
while opaque and never locks page scroll (no scrollbar reflow on release).

Verification: `npm test` (55 tests: tyre curve anchors, solver stability across dt, grid-hold vs
launch smoke, clutch dump, exit distance per viewport, determinism, yaw sign chain), `npm run lint`,
`npm run build`, plus a Playwright harness that asserts the light order, the all-red hold, that the
car cannot move before lights out, the tagline hold, palette compliance and the total duration.
