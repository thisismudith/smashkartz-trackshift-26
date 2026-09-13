# Simulator Change Plan — Documentation Only

## Status and ownership boundary

This is a future work plan, not implementation authority. Under the current UI workstream, do not change simulator code, rendering, physics, replay logic, workers, track tooling, GLB processing, or generated simulator artifacts.

Protected areas: `src/app/sim/**`, `src/sim/ui/**`, `src/sim/engine/**`, simulator workers and replay/race-loop code, `scripts/simdata/**`, GLB assets/publishing, geometry extraction, and generated `/public/sim` artifacts.

The simulator is supporting playback and validation. It must not become the primary strategic-decision interface.

## Intended end state

The simulator renders a deterministic, inspectable replay or declared counterfactual. It consumes backend/replay outputs and never calculates strategy, rule legality, energy, pass probability, or rival belief in the browser.

The final product switches between a primary 2D tactical decision view and a 3D replay of the same frozen state and outcome.

## Preconditions

- A versioned replay/API contract exists for one target battle.
- The bundle supplies track reference, two-car timeline, timestamps, segment IDs, provenance, model versions, and seed where relevant.
- Replay is reproducible from declared input.
- Simulator ownership is explicitly authorised.
- Baseline visual and performance tests run on target demonstration hardware.

## Ordered work plan

### SIM-00 — Baseline and contract audit

Inspect renderer, worker ownership, binary codec, replay timeline, track model, coordinate frame, GLB seam, and static-artifact build path. Record source commit, artifact hashes, coordinate origin/orientation, lap-distance convention, start/finish handling, frame time, memory, load time, asset size, and existing test coverage.

Acceptance: a replay run twice with identical input produces identical state; baseline checks are documented before any code changes.

### SIM-01 — Freeze the replay adapter

Create or verify one adapter from replay/API payload to the renderer contract. It must carry event/session/battle identity, attacker/defender direction, replay timestamp, segment reference, observed versus derived positions, gap, race-control state, provenance, model versions, rule snapshot, and seed.

Rules: do not infer missing positions, gaps, SOC, power, or intent. Preserve `null`. Visual interpolation must not relabel derived positions as observed.

Tests: schema validation, deterministic adapter output, explicit malformed/missing-data state, and replay/service payload-shape parity.

### SIM-02 — Track and coordinate integrity

Align telemetry ring, optional GLB surface, timing lines, pit path, and camera against one track frame.

Tests: lap-distance cursor maps to the expected point; start/finish wraps once; cars do not teleport; lateral offset remains inside the agreed track envelope; missing GLB falls back cleanly; GLB checksum/version matches the track artifact.

### SIM-03 — Replay controls and state inspection

Add play/pause, step, playback rate, scrubber, fixed decision-time marker, contract-supported attacker/defender direction, tactical/chase/driver camera presets, and segment/lap jump.

Do not add free-driving controls to the tactical replay path. If free drive remains, label it as visual/debug mode.

Tests: scrubber and stepping reach identical state; playback never mutates source timeline; keyboard controls work; pausing freezes cars, timeline, and displayed values together.

### SIM-04 — Tactical overlay seam

After real outputs exist, render supplied current segment, detection/activation lines, Overtake-zone state, race-control gate, shadow-price colours, selected action, uncertainty, and provenance.

Rules: never hand-colour strategic value; never draw active/legal state from historical DRS alone; grey or withhold overlays when the normal-race gate is false or output is absent.

Tests: overlay IDs match track artifact; visibility changes at replay transition; every numeric overlay retains provenance; absent output creates explicit unavailable state.

### SIM-05 — Two-car interaction and counterfactual playback

Support only scenarios declared by planner/simulator output: baseline replay, selected legal alternative, declared rival response, and visible seed/version/assumptions.

Tests: baseline and counterfactual share no mutable state; fixed seed repeats positions/events; simulated outcomes are never labelled observed.

### SIM-06 — Rendering quality and performance

After correctness only, add quality toggle, asset-load/progress state, camera easing that does not change replay time, scalable shadows/material quality, and GLB-to-telemetry-ring fallback.

Performance gates: no unbounded per-frame allocations; no frame-dependent progression; responsive controls while assets load; usable low-quality/no-GLB fallback.

### SIM-07 — End-to-end rehearsal

On a clean machine, open a self-contained bundle without raw telemetry or Python; inspect the same decision state in 2D and 3D; show a rule/eligibility transition and one declared alternative; verify versions, provenance, assumptions, and no-network fallback.

Release acceptance: 2D and 3D use identical state/segment IDs; no developer paths or laptop services are required; no simulator view makes unsupported strategic claims; regression, accessibility, and performance checks pass.

## Test matrix

| Area | Required check |
|---|---|
| Determinism | Same artifact/input/seed yields same timeline and final state. |
| Coordinates | Track position, lap wrap, pit path, and surface alignment are stable. |
| Data truth | Observed, derived, inferred, simulated, and rule values stay distinct. |
| Controls | Playback, scrub, step, camera, and keyboard controls stay synchronised. |
| Fallback | Missing GLB, unavailable model output, and no-network replay remain explicit and usable. |
| Performance | Frame time, memory, load, and GPU quality fallback are measured. |
| Accessibility | Reduced motion, focus order, labels, and non-pointer controls work. |

