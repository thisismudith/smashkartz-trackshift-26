import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export: `next build` writes a plain directory of HTML, JS and assets
  // to out/ with no Node server behind it. That is what lets the site sit on a
  // CDN while the model service runs as a container on its own origin.
  //
  // Two consequences the rest of the tree has to honour, because the export
  // refuses to build otherwise: there can be no route handlers under
  // src/app/api/, and no server component may read `searchParams` (a static file
  // is built once and cannot vary per query string, so the URL has to be read on
  // the client instead).
  output: "export",
  // Every route becomes <route>/index.html rather than <route>.html, so a static
  // host resolves /insights/ without needing per-file rewrite rules.
  trailingSlash: true,
  // The optimiser is a server at request time; there is none to run here.
  images: { unoptimized: true },

  // INERT UNDER `output: "export"`. Next only applies these headers when it is
  // serving the response itself, and an exported build never is -- the CDN or
  // static host does. Kept because it is the exact policy the artifacts need and
  // the rules are load-bearing, not advisory; it now has to be configured ON THE
  // CDN instead:
  //
  //   /sim/index.json                     public, max-age=0, must-revalidate
  //   /sim/*.<sha10>.{json,bin,glb}       public, max-age=31536000, immutable
  //
  // Getting the second one wrong is expensive rather than merely untidy: the
  // circuit models are 97-158 MB, so without an immutable rule the browser
  // revalidates and re-downloads Silverstone on every page load.
  async headers() {
    return [
      {
        // index.json is a stable, un-hashed pointer to the latest build (see
        // scripts/build_sim_data.py) and must always be revalidated.
        source: "/sim/index.json",
        headers: [{ key: "Cache-Control", value: "public, max-age=0, must-revalidate" }],
      },
      {
        // every other artifact under /sim/ is content-hashed by build_sim_data.py, so
        // it is safe to cache forever; public/ files default to max-age=0 otherwise
        // (see node_modules/next/dist/docs/.../public-folder.md).
        //
        // `glb` belongs here for the same reason json and bin do, and belongs here
        // MUCH more urgently: the circuit models published at
        // /sim/glb/<slug>.<sha10>.glb are 97-158 MB, so without this rule the browser
        // revalidates and re-downloads Silverstone on EVERY page load. `:file*` spans
        // path segments, so the nested glb/ directory matches this same pattern
        // (/sim/glb/british-grand-prix.228c897e5c.glb -> file "glb/british-grand-prix",
        // hash, ext), and the hashed name is what makes an immutable cache safe: new
        // bytes get a new sha10 and therefore a new URL.
        source: "/sim/:file*.:hash([a-f0-9]{10}).:ext(json|bin|glb)",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
};

export default nextConfig;
