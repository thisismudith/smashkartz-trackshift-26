import type { Metadata } from "next";
import { Suspense } from "react";
import InsightsView from "./InsightsView";

export const metadata: Metadata = {
  title: "Fitted parameters · SmashKartz",
  description:
    "Cross-circuit league tables from the fitted lap model: overtaking difficulty, pace trend and driver offsets — each with its confidence interval.",
};

/**
 * Filters are read on the CLIENT, in InsightsView.
 *
 * They used to be resolved here so a shared link rendered filtered on first paint. A static
 * export builds this page once and serves the same HTML for every query string, so a
 * server-resolved filter set would be whichever one was baked in — wrong for every shared
 * link but the first. Suspense because InsightsView calls useSearchParams.
 */
export default function InsightsPage() {
  return (
    <Suspense>
      <InsightsView />
    </Suspense>
  );
}
