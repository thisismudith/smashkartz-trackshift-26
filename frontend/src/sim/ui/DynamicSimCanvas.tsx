"use client";

/**
 * Next 16 requires `dynamic(..., { ssr: false })` to be called from inside a Client
 * Component (a Server Component may not call it directly) — see
 * node_modules/next/dist/docs/01-app/02-guides/lazy-loading.md. This is that wrapper;
 * the server page renders THIS component, never SimCanvas directly.
 */
import dynamic from "next/dynamic";

const SimCanvas = dynamic(() => import("./SimCanvas"), {
  ssr: false,
  loading: () => (
    <div style={{ height: "100dvh", display: "grid", placeItems: "center", color: "var(--haas-grey)" }}>
      Loading simulator…
    </div>
  ),
});

export default function DynamicSimCanvas() {
  return <SimCanvas />;
}
