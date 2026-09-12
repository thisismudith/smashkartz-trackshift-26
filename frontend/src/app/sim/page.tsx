import type { Metadata } from "next";
import DynamicSimCanvas from "@/sim/ui/DynamicSimCanvas";

export const metadata: Metadata = {
  title: "Race Simulator · SmashKartz",
};

export default function SimPage() {
  return <DynamicSimCanvas />;
}
