import type { Metadata } from "next";
import DecisionView from "./DecisionView";

export const metadata: Metadata = {
  title: "Decision · SmashKartz",
  description:
    "Shadow price, pass probability, rival belief and the recommended action — the surface the problem statement is about, and the models still needed to serve it.",
};

export default function DecisionPage() {
  return <DecisionView />;
}
