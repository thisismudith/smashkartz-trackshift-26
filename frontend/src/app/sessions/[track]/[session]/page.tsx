import type { Metadata } from "next";
import { Suspense } from "react";
import SessionView from "./SessionView";

export { generateStaticParams } from "../../staticParams";

export const metadata: Metadata = {
  title: "Session · SmashKartz",
};

/**
 * `?view=` is read on the CLIENT, in SessionView.
 *
 * It used to be resolved here so the first paint already showed the requested view. A
 * static export cannot do that: one HTML file is built per (track, session) and served for
 * every query string, so a server-rendered view would be whichever value happened to be
 * baked in. The page therefore hands over the plain default and SessionView corrects it
 * from the URL on mount — the same validation, the same fallback to energy for an unknown
 * value, just a frame later.
 */
export default async function SessionPage({
  params,
}: {
  params: Promise<{ track: string; session: string }>;
}) {
  const { track, session } = await params;
  // Suspense because SessionView calls useSearchParams, which Next requires a boundary
  // around in a prerendered route. The fallback is never seen in practice -- the component
  // renders its own loading state -- but without the boundary the export fails outright.
  return (
    <Suspense>
      <SessionView
        track={track}
        session={decodeURIComponent(session)}
        initialView="energy"
      />
    </Suspense>
  );
}
