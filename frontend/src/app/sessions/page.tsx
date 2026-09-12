import type { Metadata } from "next";
import SessionsView from "./SessionsView";

export const metadata: Metadata = {
  title: "Sessions · SmashKartz",
  description:
    "Every 2026 session in the mirror, what has been built from it, and where the telemetry has a measured fault.",
};

export default function SessionsPage() {
  return <SessionsView />;
}
