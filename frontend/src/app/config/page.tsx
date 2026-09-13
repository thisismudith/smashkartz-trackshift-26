import type { Metadata } from "next";
import ConfigExplorerView from "./ConfigExplorerView";

export const metadata: Metadata = {
  title: "Config · SmashKartz",
  description:
    "Every configurable, non-learned variable that feeds a TrackShift decision — rule thresholds, physics priors, the DP value grid, state discretization, baselines, and more — searchable and categorized in one place.",
};

export default function ConfigPage() {
  return <ConfigExplorerView />;
}
