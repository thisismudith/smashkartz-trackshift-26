import type { Metadata } from "next";
import DynamicNewRaceCanvas from "@/sim/ui/DynamicNewRaceCanvas";

export const metadata: Metadata = {
  title: "New Race · SmashKartz",
};

export default function NewRacePage() {
  return <DynamicNewRaceCanvas />;
}
