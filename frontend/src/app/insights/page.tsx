import type { Metadata } from "next";
import { parseFilters } from "@/components/filters";
import InsightsView from "./InsightsView";

export const metadata: Metadata = {
  title: "Fitted parameters · SmashKartz",
  description:
    "Cross-circuit league tables from the fitted lap model: overtaking difficulty, pace trend and driver offsets — each with its confidence interval.",
};

/** Filters are resolved on the server so a shared link renders filtered on first paint, with no
 * read-the-URL-then-correct flash on the client. */
export default async function InsightsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(sp)) {
    if (typeof v === "string") q.set(k, v);
    else if (Array.isArray(v) && v.length) q.set(k, v.join(","));
  }
  return <InsightsView initialFilters={parseFilters(q)} />;
}
