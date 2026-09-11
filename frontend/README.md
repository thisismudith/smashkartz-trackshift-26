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

## Palette (strict)

| Token          | Hex       | Use                                          |
| -------------- | --------- | -------------------------------------------- |
| `--haas-white` | `#EFEFEF` | body text, car body, grid paint, smoke core   |
| `--haas-grey`  | `#AEAEAE` | muted text, floor edges, smoke, tarmac lift   |
| `--haas-red`   | `#DA291C` | start lights, wing tips, swooshes, tyre heat  |
| `--haas-black` | `#111111` | background, tyres, rubber marks               |

Source of truth: `src/lib/palette.ts` and the CSS variables in `src/app/globals.css`. Canvas layers
only ever use alpha blends of these four (source-over, never additive) — the harness asserts it.

## Type

One variable family, **Archivo**, for the whole app (`src/app/layout.tsx`; `globals.css` aliases
`--font-body` to it), self-hosted by next/font at build. The wordmark runs `900` weight at
`font-stretch: 118%` with a `-8deg` oblique — heavy, wide and fast, the way a team wordmark reads.

## Intro animation (F1 standing start)

This is the page-load intro only — **not** the race simulator. `src/components/loader/`:

| File | Role |
| --- | --- |
| `RaceLoader.tsx` | Mounted once in `app/layout.tsx` → plays on every full load, never on client navigation. SSR frame is the static pose (car on the grid, gantry dark); everything else starts in the effect. Self-unmounts on the overlay fade (fail-safe 4.4 s). |
| `runtime.ts` | The only file that touches both a `dt` and the DOM: start-light clock, rev presentation signal, fixed-step accumulator (≤1/240 s, sim time = 1.1× wall), car transform + tread offsets, letter ignition, camera and end-state classes. |
| `physics/` | Pure, unit-tested launch model: Pacejka longitudinal tyre, implicit-stiffness wheel step, driver slip-target throttle with a clutch-dump window, viscous diff, weight transfer, bicycle-model yaw with tyre relaxation length + combined-slip collapse and a lagged counter-steer (that is where the fishtail comes from), rear tread temperature. `constants.ts` is the single source of numbers. |
| `effects/` | Struct-of-arrays smoke pool (pre-rendered sprites, oldest-recycled), two-regime rubber deposition (`depositAlpha`), asphalt speckle + lighting. |
| `HaasCarTop.tsx` | Top-view SVG (560×200 viewBox, 100 units/m, nose +x). Tyres are sibling `div`s (`[data-wheel]`) so tread can animate without re-rasterising the SVG. |

Layer stack (bottom → top): marks canvas → grid paint → wordmark + tagline → car → smoke canvas →
start-light gantry. The name literally emerges through the launch smoke.

### Beat sheet (wall clock from navigation, 1440 px, measured)

```
0.00  car still on the grid, gantry dark, grid L painted
0.35  ●○○○○
0.50  ●●○○○   engine spools: rev signal ramps, exhaust glow in
0.67  ●●●○○   rears start to turn, tread blurs, tyres heat
0.82  ●●●●○   first smoke wisps, shudder escalating
0.99  ●●●●●   FULL REV — max shudder + 11 Hz limiter flutter
0.99–1.33     all-red HOLD
1.33  ○○○○○   LIGHTS OUT → clutch dump: 33 m/s slip, thick smoke, long stripes
              gantry + grid fade (0.45 s) — you leave the grid behind
1.52–2.57     letters ignite as the rear axle passes them
2.57          rear axle past 85 % width → marks dim to 60 %, smoke clears
3.04–3.55     tagline at full brightness (a real hold, not a flash)
3.55–3.97     overlay fade → unmount
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
