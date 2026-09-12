import type { Metadata } from "next";
import SessionView from "./SessionView";

export const metadata: Metadata = {
  title: "Session · SmashKartz",
};

const VIEWS = ["energy", "overtake", "pace"] as const;
type View = (typeof VIEWS)[number];

/** ?view= is resolved on the server so the first paint already shows the requested view and the
 * client never has to read the URL and correct itself. An unknown value falls back to energy
 * rather than erroring — a bad link should still land somewhere useful. */
export default async function SessionPage({
  params,
  searchParams,
}: {
  params: Promise<{ track: string; session: string }>;
  searchParams: Promise<{ view?: string }>;
}) {
  const { track, session } = await params;
  const { view } = await searchParams;
  const initialView: View = (VIEWS as readonly string[]).includes(view ?? "")
    ? (view as View)
    : "energy";
  return (
    <SessionView
      track={track}
      session={decodeURIComponent(session)}
      initialView={initialView}
    />
  );
}
