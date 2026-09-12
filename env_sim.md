# Real GLB circuits as an optional 3D environment

> Implementation plan for bringing the `env_sim` prototype's GLB environment into the main
> simulator. The sim keeps its procedural ribbon for every circuit; a circuit that has a fitted
> GLB profile additionally gets the real model as its visual environment.

## Context

The sim draws the track as a procedural ribbon extruded from a 1 m-sampled telemetry centreline. It
works but looks synthetic. We have two real circuit GLBs and want them as the visual environment for
2 of 13 circuits, without disturbing the other 11.

**Decisions taken:**
- The GLB is **visual + surface height only**. The telemetry centreline stays authoritative for
  station/lateral/gaps/timing/laps.
- Cars get a full surface frame (pitch + roll).
- **Per-circuit profiles, not a generic pipeline.** Each model gets the extraction strategy that is
  *best for that model* — use its metadata when it has usable metadata, fall back to geometry only
  when it doesn't. Each new circuit gets its own planning pass before it is added.

**Rename the assets to the sim's own slugs** so there is never a mapping to remember:

```
data/tracks/silverstone.glb  ->  data/tracks/british-grand-prix.glb
data/tracks/shanghai.glb     ->  data/tracks/chinese-grand-prix.glb
```

`data/` is gitignored, so this is a local rename plus the same names in `config/circuits.yaml` and
the published `frontend/public/sim/glb/<slug>.<sha10>.glb`.

---

## Part A — the shared seam (deliberately thin)

Only three things are common to every circuit. Everything else is per-circuit.

**1. The JSON block.** A `surface` sibling of `ring` in the track artifact — cm-quantised ints, own
`provenance`, arrays the length of `ring.xCm` (~+100 KB pre-gzip). Optional, so a circuit without one
takes today's code path unchanged.

```
"surface": {
  "dsMetres": 1.0, "source": "british-grand-prix.glb", "sourceSha256": "…",
  "profile": "silverstone-material",          // which per-circuit extractor produced this
  "transform": {"dxCm": -27601, "dyCm": 44301, "dzCm": 20368, "yawMicroRad": -410},
  "zCm": [...], "slopePermille": [...], "camberPermille": [...], "validMask": [...],
  "residual": {"stdM": 0.054, "maxM": 0.179}, "coverage": 0.998,
  "provenance": "OBSERVED (vertical raycast onto asphalt.001, coverage-fitted, smoothed 25 m)"
}
```
Keep `width.binMetres` + `width.halfWidth` present and symmetric. Bump `SCHEMA_VERSION` to 2.

**2. The registry.** `config/circuits.yaml` keyed by slug, declaring the GLB, its profile name, and
the fitted transform. Python-side; PyYAML is already a dependency. Mirrored client-side by
`frontend/src/sim/render/environments.ts`:

```ts
export const ENVIRONMENTS: Record<string, EnvironmentDef> = { "british-grand-prix": {...} };
export const environmentFor = (slug: string) => ENVIRONMENTS[slug] ?? null;
```
Hooked inside `SimRenderer.setTrack`, which already receives `track.slug` — **neither call site
changes**. Mirrors the existing `setShadowPrice(values | null)` idiom.

**3. The quality gate.** Every profile must report `coverage` and `residual.stdM`, and the build
**fails** below **coverage ≥ 99%** / **residual std ≤ 0.15 m**. This is what keeps a bad alignment
from shipping floating cars, and it is what decides whether a circuit is ready.

**The fallback is free.** The ribbon is built unconditionally; no registry entry, a failed gate, or a
failed fetch all mean "nothing extra is added" — byte-for-byte today's behaviour.

### The principle that makes any model workable

**The telemetry ring is the ground truth for position and scale — not the model.** We already know,
to the metre, the real-world path a car takes around each circuit. So a GLB never has to *tell* us
where its road is:

- **Position and rotation** come from fitting the ring onto the model's drivable surface.
- **Scale** comes from the same fit — the ring's length and shape are real metres, so if a model is
  in feet, half-scale, or arbitrary units, the fit recovers the factor. (Both our models came back
  at **1.000**, i.e. already real metres — but the fit is what *proved* that rather than assuming it.)
- **Which surface is the road** is answered by "the one the ring lands on."

That is why Shanghai is tractable despite having no usable metadata at all. Metadata, when a model
has it (Silverstone), is a **refinement** that makes the fit sharper and the raycast 45× cheaper —
never a requirement. The fit metric is always the same: **the fraction of ring stations that land on
drivable surface**, which is also exactly what the quality gate measures.

---

## Part B — Circuit profile: `british-grand-prix` (Silverstone)

**Status: verified and ready to build.**

### The asset
165.8 MB, Sketchfab-17.19.0, `KHR_materials_specular` + `KHR_texture_transform`, 96 nodes / 87 meshes
/ 87 primitives / 70 materials / 69 images, **1,197,030 tris**, no animations, fully self-contained.

The geometry is **identical** to the earlier 94.8 MB export — `asphalt.001` bbox matches exactly
(min `[-532.6, -889.6, -4.1]`, max `[576.8, 874.5, 7.5]`), 26,764 tris vs 26,768 (Sketchfab dropped 4
degenerates). Only the textures changed. **So all the alignment work below is already validated.**

### Extraction strategy: **use the material names** — this model has good ones
`asphalt.001` (26,764 tris, median triangle edge 7.3 m) is the drivable road; `Curb_new.001` /
`curb.001` (83,663 verts) are kerbs; `asph_pitlane.001` the pit lane; `groove*.001` racing-line
decals. `env_sim`'s regex already works here:
```
/(?:^|[_ .-])(asphalt|asph|curb)(?:[_ .-]|$)|pitlane/i
```
Raycast vertically against `asphalt.001` only — 26,764 triangles, not 1.2 M.

### Alignment — fitted and verified
Fit by **maximising the fraction of ring stations landing inside an asphalt triangle**, not by ICP.
Plain ICP lands ~5 m off with a spurious 0.7° yaw, because **Silverstone's runoff is also asphalt**
and drags the fit outward at Abbey/Stowe/Club/Copse.

In raw-GLB (Z-up) coordinates, **scale = 1.0, real metres**:
```
glb_x =  telem_x − 276.01
glb_y = −telem_y + 443.01        yaw = −0.0235°
glb_z = −telem_z + 203.679
```

| | naive ICP | refined |
|---|---|---|
| stations on the road | 76.8% | **99.8%** (5822 / 5832) |
| elevation residual std / max | 0.086 / 0.34 m | **0.054 / 0.179 m** |

`glb_z = −1.00131 × telem_z + 203.679`. Slope p50 0.37°, p99 1.81°, max 8.4°. **Passes the gate.**

### Render placement
Node `matrix` chain (`Sketchfab_model` → `root` → `GLTF_SceneRootNode` → …) composes to **+90° about
X**, i.e. `world = (raw_x, −raw_z, raw_y)` — verified on the asphalt corner. With `toRenderFrame`
fixed to `(x, z, −y)` (Part D), the root transform is a **pure translation**:
```
position = (276.23, 203.68, −442.88)    rotation.y ≈ 0.0004    scale = 1
```
Verified across all 5832 stations — required offset std **0.21 m X / 0.00 m Y / 0.12 m Z** (residual
is purely the un-applied yaw). Against the actual raycast surface the vertical offset is
**203.414 ± 0.054 m**.

### Asset prep — VRAM is this circuit's one real risk
~196 MB of texture VRAM (+mips ≈ 261 MB) plus ~104 MB geometry ≈ **365 MB**. But **two 4096² and one
2048² textures account for 151 MB of the 196 MB (77%)** and 50.8 MB of the file. Downscale those
three to 2048/1024 → saves ~120 MB VRAM and ~40 MB file, nothing else touched. Do that before
reaching for KTX2. Then `gltf-transform prune + dedup + weld + meshopt`, and drop the unused extra UV
sets (this model carries TANGENT + up to TEXCOORD_2).

### Materials at load — port these verbatim from `env_sim/.../main.js`
Each is a diagnosed bug on *this exact asset*: `metalness ≤ 0.25` / `roughness ≥ 0.42` because the
specular-workflow export renders grandstands solid black without an HDR env; `NoToneMapping` at
exposure 1 because ACES made the scene far darker than its source render; anisotropy 16 and sRGB on
`map`/`emissiveMap`; lighting hemisphere 1.65 + ambient 0.45 + directional 1.1. Skip its
`centreModel()` and rotation sliders — the fitted transform replaces both.

---

## Part C — Circuit profile: `chinese-grand-prix` (Shanghai)

**Status: not ready. Build Silverstone first; this needs its own pass.**

### The asset
102.1 MB, Sketchfab-17.20.0, 137 nodes / 133 meshes / 133 primitives / 100 materials / 93 images,
**1,366,364 tris**, self-contained. Same node-matrix convention (+90° about X).

### Why it needs a different strategy: there is no usable metadata
Its 100 materials are `Merged_materials` plus **`advert_side_a_1 … advert_side_a_97`** — merge
artifacts. The model is **spatially chunked**: the 12 largest primitives (~61.5k tris each,
**734k tris = 54% of the model**) all share **material index 12 and a single texture atlas**, and
mesh names are `Object_N`. Each chunk mixes road, grass and buildings. There is no material, mesh or
texture split that isolates the road. Confirmed directly — this is not a naming-convention problem
that a better regex would solve.

### Extraction strategy: geometry + the telemetry ring as prior
1. **Near-horizontal triangles in a ground band** (|normal·up| > 0.9, world Y within the ground
   range). That is 575,974 of 1,366,364 tris (42%), 1.33 km².
2. **The ring tells us where the road is** — we never need to *identify* the road, only to sample
   height beneath known racing-line positions.
3. **Disambiguate multiple hits by nearest-to-telemetry-z, never "topmost."** Shanghai has a large
   pit structure over the track; "topmost" grabs its roof.

### Alignment — the method works, the accuracy does not yet
Fitted geometrically via a rasterised occupancy grid: **coverage 93.8%**, yaw **−0.75°**, mirror
**−1** (same handedness relation as Silverstone), translation **(68.0, 178.0)** in world XZ.

Elevation at 91.7% of stations: surface range 7.51 m vs telemetry 7.09 m, correlation **+0.869**, but
**residual std 0.699 m, p95 0.896 m, max 7.15 m**.

**That is 13× Silverstone's error and fails the gate.** Some is method error — I used a coarse 2 m
occupancy grid and topmost-vertex height rather than exact barycentric raycasting — and the 7.15 m
max says some stations are hitting a wall or kerb. Before this circuit ships it needs: exact
point-in-triangle sampling, the nearest-to-z hit rule, refitting against that, and a re-measure. If
it still won't pass, it stays on the ribbon — which costs nothing.

### Asset prep
The opposite problem to Silverstone: only ~35 MB texture VRAM, but maps cap at **256²**, so it will
look markedly softer up close. Geometry is 89.8 MB with uint32 indices — meshopt is the win here.
Accept the softness or source a better export.

---

## Part D — Sim-side changes (shared; land before any GLB work)

These are renderer refactors that apply to all 13 circuits, so there is one code path, not two.

### Two pre-existing bugs to fix first

**`toRenderFrame(x,y,z) => [x, z, y]` is a reflection (det −1).** So **every circuit renders mirrored
today** — data-left draws screen-right, hidden by the ribbon's symmetry and `DoubleSide`. Must become
`[x, z, −y]`; otherwise the GLB needs `scale.z = −1`, which mirrors its advertising boards, sponsor
logos and signage. **11 call sites** across `trackMesh.ts` and `scene.ts`, plus **4 coupled sign sites
that are not textual matches**: `setFromAxisAngle(upVec, -heading)` → `+heading`; the three camera
offsets (every `Math.sin(h)` Z term); triangle winding in the ribbon and pit mesh (verify with
`FrontSide` before restoring `DoubleSide`). Pit-lane side, grid stagger and declutter lane order flip
for all 13 tracks — correct, but visibly different.

**`carWorldPos` disagrees with what is drawn** — omits `PRESENTATION_SCALE`, skips
declutter/smoothing. Delete it; cache each car's final world position + basis in `updateCars`.

### What else changed since the first audit
- **`PRESENTATION_SCALE` is already `1`** — that planned step is obsolete.
- Cars are a merged F1 silhouette (~380 tris, 1 InstancedMesh). **Ride height is wrong**: code adds
  `0.50 m` but the geometry's wheel bottoms sit at local **Y = −0.30**, so cars float ~0.2 m.
- `POSE_FLOATS_PER_CAR` is 13; `[2] elevationM` is still ignored by the renderer.
- `blendPose` does not interpolate heading.
- Driver labels are Sprites with `depthTest: false` — they draw **through** grandstands.
- `buildPitLaneMesh` fakes depth with `group.position.y = -0.15` — meaningless against a GLB.
- **`setTrack` leaks** and `NewRaceCanvas` re-calls it on every re-roll; replay disposes and recreates
  the whole renderer per selection change.
- Width is now a flat RULE constant (6.0–7.5 m half-width for every circuit).

### Phased order — each phase ends in a working, checkable state

Phases 1–2 touch all 13 circuits; 3–8 are additive and inert until a circuit is registered.

**Phase 1 — `scene.ts` hygiene.** No behaviour change intended.
`setTrack` removes/disposes the prior surface, outline and pit group. Delete `carWorldPos` in favour
of a cached per-car world position + basis. Dispose `material.map` in `dispose()`. Fix ride height
`0.50` → `0.30`.
*Expect:* visually identical, except cars now sit flush on the ribbon instead of floating ~0.2 m.
*Check:* `npx vitest run` green. On `/sim/new`, re-roll 3× and confirm the scene object count stops
growing (log `scene.children.length`) — today it grows every re-roll. Eyeball a car's wheels against
the road.

**Phase 2 — handedness flip.** Standalone commit; the single riskiest step.
`toRenderFrame` → `[x, z, −y]`, plus the 4 coupled sign sites.
*Expect:* every circuit renders **mirrored relative to today** — which is the fix, not a regression.
*Check:* new unit test asserting a car at known heading has forward `(cos θ, 0, −sin θ)` and that
data-left renders left. Then load **Silverstone, Monaco, Spa and Suzuka** and compare each against a
reference circuit map: corner sequence and pit-lane side must now match reality. Do not proceed until
this is right — everything downstream inherits it.

**Phase 3 — bake infrastructure, no output yet.**
`scripts/simdata/glb_surface.py`: shared helpers (node-matrix composition, exact barycentric raycast,
`smooth_circular`, the gate) plus a **profile registry**, initially just `silverstone_material`. Bake
*after* `prepare_ring` returns — `Ring.rotated()` rolls only x/y/z and would not roll new arrays.
Pure functions, IO in a thin loader. No new deps (`struct` + `json` + `numpy` + `scipy.cKDTree`).
*Expect:* importable, no artifact change.
*Check:* unit-test the node-matrix composition against the known answer
(`world = (raw_x, −raw_z, raw_y)`) and the raycast against a hand-built triangle.

**Phase 4 — bake `british-grand-prix`.**
*Expect:* **coverage ≥ 99.8%, residual std ≈ 0.054 m, max ≈ 0.179 m** — these are already-measured
numbers, so treat any material deviation as a bug in the bake, not a new result.
*Check:* the gate passes; the artifact gains a `surface` block; `schemaVersion` is 2; every other
circuit's artifact is byte-identical apart from the version bump.

**Phase 5 — types + parsing.** Optional `surface` on `TrackModel` / `RawTrackModel` /
`parseTrackModel`; add `surfaceAt(track, station)`.
*Expect:* no visible change — nothing consumes it yet.
*Check:* tests green (optional field ⇒ existing literals still compile); log `surfaceAt` at a few
stations and confirm it tracks `track.z` within ~0.2 m.

**Phase 6 — car basis + ride height on the baked surface.**
`Matrix4.makeBasis` from an **orthonormalised** tangent/normal (Gram-Schmidt, then
`right = normal × tangent` — a non-orthogonal basis shears the car). Ride height **along the normal**.
*Expect:* on Silverstone cars now pitch over crests and roll with camber; the other 12 circuits are
unchanged (no `surface` ⇒ old path).
*Check:* watch a car over the Abbey crest and through Maggotts–Becketts — the nose should follow the
slope, no wheel-through-road at 20× playback. Load Monaco and confirm it is identical to Phase 5.

**Phase 7 — environment layer.** `environments.ts`, `setEnvironment(def | null)`, **module-scope**
GLB cache. Ribbon hidden (not deleted): `visible = false`, `polygonOffset` (−2/−2),
`depthWrite: false`, ~0.55 opacity, toggled back on by `setShadowPrice`. Hide outline + pit ribbon.
*Expect:* Silverstone shows the real circuit with cars on it; the other 12 are untouched.
*Check:* the model registers exactly on the ribbon (toggle the ribbon visible and confirm they
coincide). Switch sessions 3× and confirm the GLB is fetched **once** in the Network tab and is not
destroyed by `dispose()`. Kill the GLB URL and confirm it falls back to the ribbon cleanly.

**Phase 8 — cameras + perf tiering.** Surface-frame offsets so the boom rides the slope; clamp to
`surfaceZ + 1.5 m`; **no camera roll** except damped (~40%) onboard. Tier 1 hides far detail, tier 2
drops the GLB. Never attempt it on software rendering. Tighten `near` to 0.5.
*Expect:* the chase cam no longer clips into crests; the horizon stays level.
*Check:* broadcast and onboard laps end-to-end with no camera penetration; FPS and **VRAM** measured
on the integrated GPU, not the discrete one.

**Phase 9 — `chinese-grand-prix`, planned separately.** Needs its own pass (see Part C): exact
barycentric sampling, the nearest-to-z hit rule, refit, re-measure against the gate.

### Orientation: bake it, don't read face normals
At ~7 m triangles, face normals are piecewise-constant and roll would step at every edge. Bake
`pitch = dz/ds` and `roll = atan2(z(+2 m lat) − z(−2 m lat), 4)`, both smoothed ~25 m with the
existing `smooth_circular(v, window_m, ds)` in `geom.py`. Pitch derived from the same z array the car
is *placed* on is self-consistent; a triangle normal is not.

### One height source, not three
`track.z`, pose `[2]` and baked `surface.z` differ. The renderer reads baked `surface.z` when
present, else `track.z`. Leave `pose[2]` to the dashboard.

### Three hazards
1. Replay disposes the renderer per session switch → cache the GLB at **module scope**.
2. **`dispose()`'s blanket `scene.traverse` would destroy that shared cache** — remove the environment
   from the scene *before* the traverse and exclude it from disposal.
3. `setTrack` is not idempotent and `NewRaceCanvas` re-calls it — make `setEnvironment` idempotent.

---

## Part E — Track width: do not change it

Measured from Silverstone's asphalt: total width **p50 14.5 m** (p05 11.6, p95 29.4), left/right
half-width p50 **6.0 / 9.0 m**, median left−right asymmetry **8.0 m**. The p50 is a correct F1 width.
The p95 is **runoff bleed** — material name cannot separate track from run-off when they are the same
asphalt. The asymmetry confirms the centreline is the *racing line*, not the road centre. Kerbs as an
edge signal exist on both sides at only **13% of stations** (corners only).

The current rule gives a 12–15 m road, bracketing the measured 14.5 m. Once the GLB is the visible
road the ribbon isn't drawn, and width's only remaining job is clamping declutter lateral, which it
already does correctly.

---

## Part F — Serving

Neither GLB goes in git (`data/` is gitignored; GitHub rejects >100 MB anyway). Publish processed
assets as `frontend/public/sim/glb/<slug>.<sha10>.glb` with URL + sha256 in the track JSON.

**Concrete catch:** `frontend/next.config.ts`'s immutable-cache rule matches `:ext(json|bin)` only, so
a content-hashed `.glb` would get `max-age=0` and re-download every load. **Add `glb` to that regex.**

`GLTFLoader` resolves via `three/addons/*` (verified present in `node_modules`) — **no new npm
dependency**. Both assets need `KHR_texture_transform`, supported natively.

**Loading UX:** render the ribbon and start the race immediately; fetch the GLB in parallel with
`onProgress`; swap only on success; on failure stay on the ribbon and log once.

**Optional toggle:** copy the `gpuPref` pattern — state in `SimCanvas.tsx`, control inside the
existing `<details>Display & hardware</details>`, applied imperatively rather than via boot deps.

---

## Part G — Standing regressions (re-run at every phase)

Per-phase checks are in Part D. These four must hold throughout:

1. `cd frontend && npx vitest run` green.
2. **Monaco (no GLB) behaves exactly as it does today** — this is the proof the other 11 circuits are
   safe, and it should be checked at every phase, not just at the end.
3. **`/sim/new` re-roll 3×** — no stacked ribbons, no duplicated environment, scene object count flat.
4. **Session switching on `/sim`** — no leak, no re-fetch, no disposal of the shared GLB cache.

The one thing that cannot be caught by a test is Phase 2's mirroring flip: it changes every circuit's
appearance and must be confirmed by eye against a reference map before anything is built on top.
