# AGENTS.md — E-Delta / Silverstone 3D Environment

## 0. Mission

This repository is the frontend/3D-environment foundation for the **E-Delta / TrackShift** hackathon project.

The immediate goal is **not** to build the final racing physics/energy engine. The immediate goal is to build the best possible, high-fidelity, efficient, extensible **Silverstone 3D environment** in the browser, with clean interfaces so a real physics/simulation engine can be plugged in later.

Treat the current implementation as disposable/prototype code. We are intentionally rebuilding the environment architecture substantially rather than preserving the old Google Street View/Cesium implementation.

---

## 1. Project Context

E-Delta is a race-engineer decision/simulation system centered on Silverstone. The broader project models the circuit in segments and uses distance along the circuit, telemetry, energy state, rival state, rules, and tactical planning. The 3D environment must therefore eventually expose reliable track-relative coordinates and race state, but it must remain decoupled from the simulation/physics logic.

The project context expects things such as:

- Silverstone track/segment map and track lines.
- Track-position-aware simulation.
- Start/finish handling.
- Lap counting and timing.
- Vehicle speed and position.
- Space for future variables such as temperature, humidity, weather, track condition, etc.
- A clean integration point for a future physics loader/physics engine.
- Simulation values should be tagged/treated separately from visual environment state; do not fake real physics results.

The current 3D environment is a visualization foundation. Do not turn rendering code into a tightly coupled physics engine.

---

## 2. Current Direction — Important

### DO NOT use Google Street View as the main simulator environment.

Street View was explored only as a visual reference. It is not the final environment because it is panoramic imagery rather than a controllable 3D world.

### DO NOT make Google Photorealistic 3D Tiles the main local simulation world.

We previously used Cesium + Google Photorealistic 3D Tiles for experimentation. That work is useful as historical context only. The project is now moving to a locally supplied Silverstone `.glb` asset.

### PRIMARY ENVIRONMENT

Use the downloaded **Silverstone GLB** as the authoritative visual 3D environment.

Use **Three.js** (via Vite) as the preferred browser renderer unless there is a strong technical reason to change.

The GLB must be rendered at the **highest practical available quality**. Never intentionally lower model/texture quality merely to make a demo faster.

---

## 3. Asset Information

A downloaded asset archive was supplied as:

`silverstone-circuit-2024-layout.zip`

Archive contents include:

- `source/silverstone.glb`
- a `textures/` directory containing many texture assets.

The GLB is approximately **94.8 MB** in the supplied archive. The archive is approximately **167.8 MB**.

The developer/user has copied the GLB into the Vite project's public model directory and copied the supplied texture assets into a public vectors/textures directory.

Preferred project asset layout:

```text
public/
├── models/
│   └── silverstone.glb
└── vectors/
    └── <supplied texture/vector resources>
```

Do not delete, recompress, resize, or replace the supplied textures unless explicitly required and justified.

Do not assume the external texture directory is unnecessary. First verify whether the GLB embeds all of its required images/materials before removing or ignoring any supplied resources.

---

## 4. Existing Toolchain / Project Location

The actual Vite project is the inner directory:

```text
/Users/divamgupta04/e-delta-simulation/e-delta-simulation
```

The outer `e-delta-simulation` directory is not the actual Vite root.

Expected project files include:

```text
index.html
package.json
package-lock.json
vite.config.js
src/
public/
```

The project previously used:

- Vite
- `vite-plugin-cesium`
- Cesium
- Google Maps JavaScript API / Street View

Those are historical/prototype dependencies. They should not constrain the new architecture. Remove unused dependencies/configuration when the replacement implementation is stable.

Three.js should be used for the new 3D environment:

```bash
npm install three
```

Use modern ES modules and Vite-native imports.

---

## 5. Rendering Quality — NON-NEGOTIABLE

Rendering quality is a first-class requirement.

### Always prioritize:

1. Full GLB geometry fidelity.
2. Full available texture/material quality.
3. Correct material maps, transparency, normals, roughness, metalness, emissive data, etc.
4. Correct color management / sRGB handling.
5. High-quality anti-aliasing where available.
6. Maximum useful device pixel ratio.
7. Stable frame pacing without artificially degrading the model.
8. Aggressive use of the GPU where the browser supports it.

### Do NOT:

- Add arbitrary low-resolution LODs just for speed.
- Downscale textures as a first-line optimization.
- Replace the GLB with a simplified proxy after loading.
- Disable important material maps to gain FPS.
- Hide chunks of the track/environment to make rendering appear faster.
- Use a blurry post-process effect to disguise low detail.

Optimization should come from **efficient rendering**, not from throwing away visual quality.

---

## 6. GPU Rendering / Quality Toggle

The site should expose a user-facing **render-quality / GPU-performance mode** where feasible.

Preferred UX:

```text
Render Mode
[ Balanced ] [ Max Quality / GPU ]
```

The high-quality mode should enable the most aggressive practical browser/WebGL renderer settings available without becoming unsafe or unstable.

The application should detect and report WebGL/GPU capabilities where possible.

Examples of quality controls that may be appropriate:

- renderer pixel ratio
- antialiasing choice
- tone mapping / color space
- shadow quality
- anisotropy where useful
- environment/background quality
- optional post-processing only when it improves quality
- render-on-demand vs continuous rendering when appropriate

Do not claim that JavaScript can force the browser to use hardware GPU acceleration if the browser has disabled it. The UI can request/highlight GPU mode and report actual capabilities.

A future diagnostics panel should be able to show items such as:

- WebGL renderer/vendor (where safely exposed)
- max texture size
- max anisotropy
- current DPR
- FPS / frame time
- triangle count
- draw calls
- loaded asset size

---

## 7. Camera Requirements

The environment needs two fundamentally different camera modes.

### A. Free/Editor/Overview Camera

The user must be able to freely inspect Silverstone:

- orbit
- pan
- zoom
- rotate around the environment
- inspect any area
- top-down view
- oblique view
- close track-level inspection

### B. Driver / Vehicle Camera

A camera should be attachable to a vehicle and follow it through the track.

It must support:

- camera position relative to vehicle
- yaw / pitch / roll
- FOV
- chase view
- cockpit/driver view later
- smooth following

Camera logic must be separated from physics logic.

---

## 8. Track Coordinate System — CORE ARCHITECTURE

Do not build the final car movement system around free world-space WASD translation.

World-space movement is acceptable only for an early visual prototype.

The final architecture must support a **track-relative coordinate system**.

Preferred conceptual model:

```text
track distance s
      ↓
track sampler / spline
      ↓
position (x,y,z)
      ↓
heading / tangent
      ↓
vehicle transform
```

Eventually the car should be representable as something like:

```js
vehicle.state = {
  distanceAlongTrack: 2847.4,
  lateralOffset: 0.0,
  speed: 82.3,
  heading: 1.57,
  lap: 3
};
```

The renderer should consume this state rather than owning the simulation rules.

---

## 9. Track Boundaries / Out-of-Bounds Behavior

Vehicles must NOT be allowed to freely drive through:

- trees
- buildings
- grandstands
- barriers
- runoff zones when the rule is meant to prohibit them
- unrelated terrain

The environment layer must provide a mechanism for defining valid driving surfaces and track boundaries.

A practical architecture is:

```text
Track centerline / vector data
            ↓
track width / boundary data
            ↓
valid racing corridor
            ↓
vehicle constraint / collision query
```

Do not hard-code a giant arbitrary rectangle around Silverstone and call it the track.

Prefer the supplied vector resources and/or dedicated track geometry to establish:

- centerline
- left boundary
- right boundary
- start/finish line
- sector boundaries
- DRS/detection/other zones when available
- pit lane
- pit entry/exit
- marshal/runoff areas if useful later

The vector resources should be inspected before deciding how much must be generated procedurally.

---

## 10. Race Structure

The environment foundation must leave explicit hooks for:

```text
Race
├── start line
├── finish line
├── sectors
├── laps
├── session timer
├── lap timer
├── sector timers
├── vehicle speed
├── vehicle position
├── track distance
└── future environmental variables
```

At minimum the renderer/UI should eventually be able to display:

- current lap
- total laps
- elapsed time
- current lap time
- best lap time
- speed
- distance along track
- sector

Do not implement a fake racing result system. Build the structural hooks and clear state interfaces.

---

## 11. Vehicle Architecture

The current placeholder car can be used for rendering/testing only.

Vehicle logic should be isolated from the track renderer.

Preferred conceptual structure:

```text
VehicleController
    ↓
VehicleState
    ↓
VehiclePhysicsAdapter  <--- future physics engine
    ↓
TrackSampler / TrackRules
    ↓
VehicleTransform
    ↓
Three.js scene
```

The renderer should be able to accept a future physics module such as:

```js
physics.update(dt, inputs, trackState, environmentState)
```

and receive back:

```js
{
  position,
  velocity,
  speed,
  heading,
  lap,
  trackDistance,
  energy,
  ...
}
```

Do NOT couple the visual car directly to a future E-Delta energy model.

---

## 12. Physics Loader / Simulation Adapter

Build a clean seam for a future physics implementation.

Example interface:

```js
export class PhysicsAdapter {
  reset(initialState) {}
  update(dt, inputs, context) {
    return {
      position: null,
      velocity: null,
      speed: 0,
      heading: 0,
      lap: 1,
      trackDistance: 0,
    };
  }
}
```

The environment should work with a simple placeholder adapter today and a real physics module later.

The renderer should not need to be rewritten when physics is introduced.

---

## 13. Environmental Variables

Design an extensible environment state from the beginning.

Example:

```js
environmentState = {
  temperature: null,
  humidity: null,
  airPressure: null,
  windSpeed: null,
  windDirection: null,
  rainIntensity: null,
  trackTemperature: null,
  trackWetness: null,
};
```

These are extension points, not requirements to simulate immediately.

The 3D environment should eventually be capable of responding visually to some of them without embedding the actual scientific/physics calculations into the renderer.

---

## 14. UI / Panels

The UI should be clean and useful for a hackathon demo, but it must not cover the circuit unnecessarily.

Useful controls include:

- Camera mode.
- Free camera reset.
- Top/overview view.
- Driver/chase view.
- Render quality.
- GPU diagnostics.
- Track rotation/debug mode if needed.
- Vehicle selection.
- Start/pause/reset simulation.

Debug controls should be clearly separated from the final/demo UI.

Avoid large opaque panels that block the model unless explicitly requested.

---

## 15. Coordinate / Model Normalization

The downloaded GLB may have its own origin, scale, and axis orientation.

On first load:

1. Inspect its bounding box.
2. Inspect model dimensions.
3. Inspect scene hierarchy.
4. Inspect whether the track is centered or offset.
5. Inspect which axis is up.
6. Preserve the model's relative geometry.
7. Establish a stable application/world coordinate system.

Do NOT blindly apply arbitrary scale/rotation values without measuring the asset.

The measured model should be kept in a stable coordinate frame, with any normalization isolated in one place.

---

## 16. Performance Strategy

The goal is **high quality + low latency**, not low quality + high FPS.

Optimize in this order:

1. Correct asset loading.
2. GPU-friendly renderer configuration.
3. Material/texture correctness.
4. Frustum culling.
5. Efficient scene graph.
6. Efficient shadow configuration.
7. Efficient draw calls/material batching where possible without damaging asset fidelity.
8. Avoid unnecessary per-frame allocations.
9. Avoid expensive DOM/UI updates every frame.
10. Use profiling/telemetry before making quality compromises.

When performance is poor, first diagnose:

- draw calls
- triangle count
- texture memory
- shader/material cost
- shadow cost
- post-processing cost
- browser/WebGL limitations

Only after diagnosis consider carefully scoped quality changes.

Never silently lower the entire scene to make FPS look good.

---

## 17. GLB Loading Requirements

Use `GLTFLoader` from Three.js.

The loader must correctly handle:

- external resources if the asset references them
- embedded images
- PBR materials
- transparency
- normal maps
- emissive maps
- texture color spaces
- animations if the GLB contains them

Do not strip scene contents simply because they are not immediately used.

If the asset contains many meshes, preserve them unless profiling proves some element can be managed more efficiently without visual loss.

---

## 18. Asset Inspection Before Major Refactoring

Before writing sophisticated car/track logic, inspect the actual downloaded assets programmatically.

Determine:

- scene node hierarchy
- number of meshes
- number of materials
- number of textures/images
- animation clips
- bounding box
- dimensions
- axis orientation
- whether geometry/materials are embedded
- whether vector files contain track boundaries/centerline information

Do not guess the track coordinate system.

---

## 19. Recommended New Project Structure

A strong target structure is:

```text
src/
├── main.js
│
├── environment/
│   ├── Environment.js
│   ├── AssetLoader.js
│   └── RenderQuality.js
│
├── track/
│   ├── Track.js
│   ├── TrackSampler.js
│   ├── TrackBounds.js
│   ├── TrackZones.js
│   └── RaceSession.js
│
├── vehicle/
│   ├── Vehicle.js
│   ├── VehicleController.js
│   └── VehicleCamera.js
│
├── physics/
│   ├── PhysicsAdapter.js
│   └── PlaceholderPhysics.js
│
├── ui/
│   ├── HUD.js
│   ├── ControlsPanel.js
│   └── DiagnosticsPanel.js
│
└── data/
    └── ...track/vector configuration...
```

This is a target architecture, not a requirement to create every file immediately.

Prefer small modules with clear responsibilities.

---

## 20. Separation of Concerns

### Environment owns

- Three.js scene
- renderer
- lighting
- GLB assets
- camera infrastructure
- rendering quality
- visual effects

### Track system owns

- centerline
- boundaries
- track distance
- sectors
- start/finish
- zones
- surface queries

### Vehicle system owns

- vehicle transform
- model representation
- camera attachment
- control input mapping

### Physics adapter owns

- acceleration
- braking
- traction
- aerodynamic effects
- energy model
- thermal model
- future physics logic

### Race session owns

- lap state
- timing
- race start/finish
- session state

### UI owns

- displaying state
- toggles
- debug tools
- user interactions

Do not collapse all of this into `main.js`.

---

## 21. Development Order

Work in this order unless the user explicitly changes priorities:

### Phase 1 — Asset-first environment

- Load the real Silverstone GLB.
- Verify every available visual element renders correctly.
- Measure bounds and coordinate system.
- Establish camera controls.
- Establish high-quality rendering mode.
- Establish GPU diagnostics.

### Phase 2 — Track semantics

- Inspect supplied vector resources.
- Establish track centerline.
- Establish valid track corridor/bounds.
- Establish start/finish.
- Establish sectors.
- Establish track-distance sampling.

### Phase 3 — Vehicle foundation

- Add placeholder/high-quality vehicle model.
- Place at a known track-relative position.
- Implement track-relative transform.
- Implement driver/chase camera.
- Prevent invalid movement outside the driving corridor.

### Phase 4 — Race session

- Start sequence.
- Lap count.
- Timers.
- Sectors.
- Speed display.
- Pause/reset.

### Phase 5 — Physics integration seam

- Add placeholder physics adapter.
- Document the input/output contract.
- Make the renderer consume physics state.
- Do NOT implement the full E-Delta physics model inside the environment.

### Phase 6 — E-Delta integration

Only after the environment and interfaces are stable:

- energy state
- rival state
- strategy state
- telemetry replay
- planning outputs
- shadow price overlays
- provenance labels

---

## 22. Coding Style

The user explicitly prefers compact code formatting.

### DO

- Keep code readable but compact.
- Use normal indentation.
- Use concise comments only where useful.
- Group logically related sections.
- Prefer modules over huge files.

### DO NOT

- Add excessive blank lines.
- Add giant ASCII banners everywhere.
- Pad every statement with blank lines.
- Repeat obvious comments above every line.
- Turn simple functions into giant verbose blocks.

Example preferred style:

```js
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x101418);

const camera = new THREE.PerspectiveCamera(60, width / height, 0.1, 100000);
camera.position.set(500, 250, 500);
```

Not dozens of blank lines between every statement.

---

## 23. Safety / Data / Licensing Context

Do not attempt to reconstruct, scrape, or locally cache Google Street View / Google Photorealistic 3D Tiles as a substitute for the supplied GLB.

The supplied GLB is now the intended local 3D asset.

Respect whatever license/source terms came with the downloaded GLB and vector resources. Preserve attribution requirements if the asset license requires them.

Do not invent asset provenance.

---

## 24. What Codex Should Do When Starting From This File

When this file is loaded into Codex:

1. Treat the existing implementation as a **prototype**, not sacred architecture.
2. Inspect the current repository before making changes.
3. Inspect the actual GLB and vector/texture assets before guessing about coordinates or scale.
4. Establish a high-quality Three.js environment first.
5. Avoid bringing back Google Street View/Cesium as the primary renderer.
6. Keep the GLB at maximum practical visual fidelity.
7. Build clean interfaces for track, vehicle, race session, physics adapter, and UI.
8. Do not implement a complex physics model unless explicitly requested.
9. Preserve room for future E-Delta simulation integration.
10. When making changes, prioritize the 3D environment and rendering quality first.

---

## 25. Definition of Done for the Environment Foundation

The foundation should eventually reach this state:

```text
              SILVERSTONE 3D ENVIRONMENT
                         │
        ┌────────────────┼────────────────┐
        │                │                │
      Camera           Track            Vehicle
        │                │                │
   free / driver    bounds / zones    transform
        │           centerline / s        │
        │                │                │
        └────────────────┼────────────────┘
                         │
                    Race Session
                         │
                    Physics Adapter
                         │
                    Future E-Delta
```

The environment should be visually high fidelity, responsive, locally rendered, track-aware, and ready to accept a real simulation engine without requiring a renderer rewrite.

---

## 26. First Task for a Fresh Codex Session

Do **not** immediately start implementing physics.

Start by:

1. Inspecting the repository structure.
2. Confirming `public/models/silverstone.glb` exists.
3. Inspecting the GLB hierarchy/materials/textures/bounds.
4. Inspecting `public/vectors/` and identifying whether those resources contain track geometry/vector information or only texture assets.
5. Rebuilding the current page into a clean Three.js Silverstone viewer.
6. Adding high-quality render mode and diagnostics.
7. Establishing a stable world coordinate system.
8. Only then building track semantics and vehicle placement.

The first successful milestone is:

> **A sharp, high-fidelity, fully inspectable Silverstone GLB scene running locally in Three.js with responsive free camera controls and no intentional quality reduction.**

Everything else should be layered on top of that foundation.
