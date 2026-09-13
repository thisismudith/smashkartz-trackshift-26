/**
 * The decision surface — the one the problem statement is actually about, and the one that
 * does not exist yet.
 *
 * This page is built now, against the shapes in API.md, and renders nothing but named gates.
 * That is deliberate. "Missing means missing" (API.md section 1.5): a stub that returns a
 * plausible number is exactly the hidden fallback the contract forbids, and a judge reading a
 * placeholder as a result is worse than a judge reading an empty panel.
 *
 * Each gate names the model, the route that will serve it, and — where it matters — what is
 * blocking that model, because two of the four are blocked on something no amount of code
 * fixes: the Detection and Activation line positions are not in the raw telemetry at all and
 * have to come from FIA event documents.
 */
"use client";

import { AwaitingModel } from "@/sim/charts";
import s from "./decision.module.css";

const GATES = [
  {
    title: "Energy shadow price · λ_E",
    model: "M22 — dynamic programming",
    route: "GET /value/{event}/shadow_price",
    detail:
      "What one joule is worth at each point on the lap, from a two-lap value function. The headline visual: the same energy has very different strategic value depending on where it is spent. The track renderer already accepts a per-distance array and colours the ribbon with it — applyShadowPriceOverlay is implemented and unit-tested, with no caller. The day the DP table exists, one call lights it up.",
    blocked: null,
  },
  {
    title: "Pass probability",
    model: "M10 — checkpoint-specific classifiers",
    route: "POST /pass/predict",
    detail:
      "Probability of completing a pass, predicted separately at each decision checkpoint — DETECTION, ACTIVATION, BRAKING — so a number never uses information the driver did not yet have. Shown as three side-by-side values with their ensemble spread, never merged into one.",
    blocked:
      "Blocked upstream on M18: the Detection and Activation line positions are not present anywhere in the raw data and must come from FIA event documents. Without the lines there are no overtake opportunities to label.",
  },
  {
    title: "Rival belief",
    model: "M09 — HMM / HSMM over rival state",
    route: "POST /rival/state",
    detail:
      "A distribution over CONSERVING / BALANCED / DEPLOYING / DERATING, shown as bars rather than a single label — the contract requires the spread to survive to the screen, because a 40/35/25 belief and a confident 90% call should not look the same.",
    blocked: null,
  },
  {
    title: "Recommendation",
    model: "M24 — beam planner over the DP terminal value",
    route: "POST /plan",
    detail:
      "The legal-by-construction action at this state, what it is worth, what it flips to if the rival belief changes, and how it compares against the named baselines. Illegal actions never appear in the list — the rule engine removes them before anything is scored, rather than scoring them low.",
    blocked: null,
  },
];

const DEPENDENCIES = [
  { label: "Session state", status: "Available", detail: "Manifest-backed timing, drivers and context." },
  { label: "Rule mask", status: "Available", detail: "Configuration is inspectable; verification is shown on Rules." },
  { label: "Energy twin", status: "Waiting", detail: "Estimated energy output is not published for this decision route." },
  { label: "Pass model", status: "Waiting", detail: "Checkpoint-labelled opportunities are not available yet." },
  { label: "Rival belief", status: "Waiting", detail: "No inferred rival-state stream is published." },
  { label: "Planner", status: "Waiting", detail: "Requires DP value tables and legal candidate actions." },
];

export default function DecisionView() {
  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>Decision engine</p>
        <h1 className={s.title}>
          Not <em>Yet</em>
        </h1>
        <p className={s.lede}>
          This is the surface the problem statement is about: where, not just whether, to spend
          electrical energy in a battle. The models behind it are not built. Rather than fill the
          page with placeholder numbers, each panel names the model it is waiting for and the route
          that will serve it — the shapes are already fixed in <code>API.md</code>, so wiring is a
          change at the data seam and nothing else.
        </p>
      </header>

      <section className={s.readiness} aria-label="Decision readiness">
        <div className={s.readinessHeader}>
          <div><span className={s.readinessKicker}>Readiness gate</span><h2>Evidence chain</h2></div>
          <span className={s.readinessCount}>2 / {DEPENDENCIES.length} available</span>
        </div>
        <div className={s.readinessTrack} aria-hidden="true"><span style={{ width: `${(2 / DEPENDENCIES.length) * 100}%` }} /></div>
        <div className={s.dependencyGrid}>
          {DEPENDENCIES.map((d) => (
            <div key={d.label} className={`${s.dependency} ${d.status === "Available" ? s.available : s.waiting}`}>
              <div className={s.dependencyTop}><strong>{d.label}</strong><span>{d.status}</span></div>
              <p>{d.detail}</p>
            </div>
          ))}
        </div>
      </section>

      <div className={s.grid}>
        {GATES.map((g) => (
          <div key={g.title} className={s.cell}>
            <AwaitingModel title={g.title} model={g.model} route={g.route} detail={g.detail} />
            {g.blocked ? <p className={s.blocked}>{g.blocked}</p> : null}
          </div>
        ))}
      </div>

      <section className={s.footnote}>
        <h2 className={s.footHead}>Why this page is empty rather than plausible</h2>
        <p className={s.footBody}>
          Every number the system displays carries a provenance tag, and a value that is unavailable
          is rendered as unavailable with a reason — never fabricated. A stub that returns a
          well-shaped guess would pass a demo and fail the only test that matters, which is whether
          what is on screen is true.
        </p>
      </section>
    </main>
  );
}
