# TrackShift UI Workflow

## Purpose

This is the implementation ledger for non-simulator frontend work. `../UI.md` remains the broad product roadmap; this document records current execution order and status.

## Scope lock

Allowed: non-simulator `src/app/**`, shared components/styles, `src/sim/charts/**` as an analytical presentation primitive, static-artifact display adapters, tests, and documentation.

Forbidden unless the user explicitly lifts the restriction: `src/app/sim/**`, `src/sim/ui/**`, `src/sim/engine/**`, simulator workers, replay/race simulation behaviour, physics, rendering, `scripts/simdata/**`, GLB processing, track construction, and simulator-artifact generation.

Do not use placeholder data to make strategic panels appear complete. Missing replay/API outputs stay visibly unavailable.

## Current architecture

The frontend is a Next.js App Router application. Analytical pages read versioned static artifacts through `src/sim/data/source.ts`; this is the future API seam. The browser displays results and must not calculate physics, energy, or planner logic.

Existing data-backed pages are Home, Sessions, session analysis, Telemetry, Rules, Lab, League, and Insights. Strategic replay inputs—battle timeline, shadow price, legal actions, planner, pass/repass, rival belief, and strategic simulation evidence—are absent.

## Progress ledger

| ID | Stage | Status | Definition of done |
|---|---|---|---|
| UI-00 | Preserve data truth | Active | Correct provenance; missing models are gated, never invented. |
| UI-01 | Insights readability | Complete | Ranked uncertainty charts, value gutters, table fallback, `ns`, and headline summaries. |
| UI-02 | Shared chart interaction | Complete | Hover/focus treatment, crosshair readouts where data exists, reduced motion, and chart-entry motion. |
| UI-03 | Product shell and navigation | In progress | Navigation now separates core analysis from tools; shared context and URL-state work remain. |
| UI-04 | Session engineering workspace | Complete | Session summary, synchronised analysis, annotations, telemetry handoff. |
| UI-05 | Two-driver telemetry comparison | In progress | Persistent A/B comparison, distance cursor, segment context, explainable deltas. |
| UI-06 | Rules applicability view | Complete | Rule evidence and current applicability are clear and provenance-safe. |
| UI-07 | Decision readiness gate | Complete | Missing strategic dependencies form a structured readiness chain. |
| UI-08 | Tactical replay workspace | Blocked | Requires real versioned replay/API artifacts. |
| UI-09 | Accessibility and quality pass | Complete | Keyboard access, focus behavior, responsive and reduced-motion checks. |

## Execution order

### UI-03 — Product shell and navigation

Progress: core navigation is grouped into primary evidence routes and secondary tools. The existing
simulator route remains only a link target; no simulator code was changed. Remaining work is shared
analysis context and URL-backed selection state.

1. Audit repeated navigation and page headers.
2. Define shared non-simulator context: event, session, selected drivers, and data mode.
3. Keep shareable state in URL parameters.
4. Make Overview, Sessions, Telemetry, Insights, Rules, and Decision primary navigation.
5. Standardise loading, missing-artifact, and provenance states.

Test deep links, browser history, keyboard navigation, narrow layouts, and missing artifacts.

### UI-04 — Session engineering workspace

Progress: added an evidence-aware session readiness strip before the controls. It reports only
manifest-backed facts (track length, represented cars, timed laps, race-control and neutralisation
windows, and weather availability) and labels the source context without inventing strategic data.

1. Add a command strip for session state, selected drivers, lap scope, tyre/stint context, weather/race control, and provenance.
2. Establish summary → primary pace/gap → supporting-context reading order.
3. Synchronise lap selection and chart hover only where artifacts supply data.
4. Surface observed pit/stint and race-control annotations.
5. Link into telemetry with preserved track/session/driver context.

Test route handoff, missing-data behaviour, and chart readability under realistic density.

### UI-05 — Two-driver telemetry comparison

Progress: exposed the existing shared-distance A/B telemetry view as a real session sub-route and
added a preserved-context handoff from the session workspace. The view continues to refuse unusable
position traces and does not synthesize missing channels.

1. Formalise persistent A/B driver selection.
2. Add fixed delta summaries from measured/derived telemetry only.
3. Synchronise all telemetry plots on one distance cursor.
4. Add segment/corner context from existing track artifacts.
5. Explain visible deltas through available speed, throttle, brake, gear, and estimated-energy signals.

Test cursor alignment at lap wrap, null-channel gaps, and textual as well as visual driver identity.

### UI-06 — Rules applicability view

Progress: added a configuration snapshot strip showing season, schema, snapshot verification, and
legal-mask state before the envelope charts. The strip is driven by the published RuleSet and keeps
unverified configuration visibly distinct from verified state.

1. Make configuration/version and verification state prominent.
2. Keep source evidence beside every threshold and curve.
3. Link from analysis only when a real input speed exists.
4. Distinguish observed race-control state from unverified regulation constants.

Test that unverified values cannot look FIA-verified and that browser-side envelope interpolation never appears.

### UI-07 — Decision readiness gate

Progress: replaced the flat empty-state framing with an explicit evidence chain. Session state and
rule mask are shown as available; energy, pass, rival-belief, and planner outputs remain waiting
with reasons, so the page cannot be mistaken for a recommendation.

1. Replace generic empty cards with a dependency chain: state, rule mask, energy, pass model, rival belief, planner, and simulation evidence.
2. State why each missing dependency matters and name its future replay/API source.
3. Link users to the strongest existing evidence surface.

Test that missing model output never resembles a recommendation and real artifacts can unlock independently.

### UI-08 — Tactical replay workspace

Status: blocked. The current `public/sim` artifact set contains tracks, session manifests, telemetry
blobs, parameters, and rules, but no versioned battle timeline, shadow-price, pass, rival-belief,
planner, or strategic simulation bundle. This phase therefore remains gated in the Decision page
until those artifacts are published; no simulator code is changed to bypass the gate.

### UI-09 â€” Accessibility and quality pass

Progress: added a global keyboard skip link and a focusable main-content landmark so keyboard users
can bypass the persistent navigation on every non-simulator route.

## Quality gates

- `npx tsc --noEmit`
- Relevant unit tests when Vite can start
- Desktop and narrow-layout review
- Keyboard-only and reduced-motion review
- Provenance, missing-data, and unverified-rule review
- No simulator source or tooling changes
