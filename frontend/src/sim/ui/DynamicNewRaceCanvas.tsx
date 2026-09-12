"use client";

import dynamic from "next/dynamic";

const NewRaceCanvas = dynamic(() => import("./NewRaceCanvas"), {
  ssr: false,
  loading: () => (
    <div style={{ height: "100dvh", display: "grid", placeItems: "center", color: "var(--haas-grey)" }}>
      Loading configurator…
    </div>
  ),
});

export default function DynamicNewRaceCanvas() {
  return <NewRaceCanvas />;
}
