/**
 * The decision surface — the one the problem statement is actually about.
 *
 * The default view now calls the real dev service (src/trackshift/serve/app.py, run via
 * `python scripts/serve/run_service.py`) through ScenarioControls: real rule-engine data
 * for legal actions and the power envelope, and honest `X-TrackShift-Stub: true` responses
 * for the four models that aren't built yet (M22 DP, M10 pass, M09 rival, M24 planner) —
 * see API.md §3.7/§10. Nothing here fabricates a number; a stub is badged as one, and a
 * network error (backend not running) is shown as exactly that.
 *
 * `?preview=1` switches to a fully offline demo fed by hand-written sample fixtures under
 * ./fixtures/ instead of any network call — useful without a Python process running, and
 * clearly banner-labelled so it is never mistaken for a live result. The default (live)
 * view is the one worth watching as the four model routes stop being stubs one at a time:
 * each becomes a genuinely computed, telemetry-derived DERIVED estimate with an interval,
 * served from the SAME route `client.ts` already calls, so nothing here needs to change
 * again as that lands -- only `src/trackshift/serve/` does.
 */
"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { PreviewPanels } from "./PreviewPanels";
import { ScenarioControls } from "./ScenarioControls";
import s from "./decision.module.css";

function DecisionContent() {
  const preview = useSearchParams().get("preview") === "1";

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>Decision engine</p>
        <h1 className={s.title}>
          Live, <em>Honestly</em>
        </h1>
        <p className={s.lede}>
          Where, not just whether, to spend electrical energy in a battle. Pick an event, set the
          state, and ask one of the four routes. Anything still a placeholder comes back badged{" "}
          <strong>STUB</strong>; everything else is the real answer, with the full body behind{" "}
          <em>Raw JSON</em>.
        </p>
      </header>

      {preview ? (
        <PreviewPanels />
      ) : (
        <ScenarioControls />
      )}

      <section className={s.footnote}>
        <h2 className={s.footHead}>Why a stub is badged, never silent</h2>
        <p className={s.footBody}>
          A well-shaped guess is fine to ship, as long as it is labelled loudly enough that nobody
          mistakes it for a result.{" "}
          {preview ? (
            <>
              This <code>?preview=1</code> view is the offline exception: it makes no network
              call at all and is fed entirely by hand-written sample fixtures under{" "}
              <code>frontend/src/api-contract/fixtures/</code>.
            </>
          ) : (
            <>
              Drop <code>?preview=1</code> from the URL for the live view; add it back for an
              offline demo that needs no backend running.
            </>
          )}
        </p>
      </section>
    </main>
  );
}

export default function DecisionView() {
  return (
    <Suspense fallback={null}>
      <DecisionContent />
    </Suspense>
  );
}
