import type { Metadata } from "next";
import LabView from "./LabView";

export const metadata: Metadata = {
  title: "Parameter lab · SmashKartz",
  description:
    "Every input the race model takes, with the uncertainty it was fitted to — and what is still needed to make them interactive.",
};

export default function LabPage() {
  return <LabView />;
}
