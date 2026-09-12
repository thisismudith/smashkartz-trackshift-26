import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
