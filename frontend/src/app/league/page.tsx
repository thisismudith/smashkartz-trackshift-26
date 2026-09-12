import type { Metadata } from "next";
import LeagueView from "./LeagueView";

export const metadata: Metadata = {
  title: "Energy league · SmashKartz",
  description:
    "Estimated ERS deploy, harvest and balance per lap across every built 2026 session, ranked by circuit, team and driver — with the twin's unclosed balance stated up front.",
};

export default function LeaguePage() {
  return <LeagueView />;
}
