import type { Metadata } from "next";
import IntegrationView from "./IntegrationView";

export const metadata: Metadata = {
  title: "Model API integration · SmashKartz",
  description:
    "Every route the UI consumes, read through one source toggle: the static replay bundle or the live development service, rendered by the same components.",
};

export default function IntegrationPage() {
  return <IntegrationView />;
}
