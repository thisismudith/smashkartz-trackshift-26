import type { Metadata } from "next";
import RulesView from "./RulesView";

export const metadata: Metadata = {
  title: "Regulation · SmashKartz",
  description:
    "The 2026 speed-dependent electrical power envelope and the three energy quantities, as the rule engine holds them — with every unverified value marked.",
};

export default function RulesPage() {
  return <RulesView />;
}
