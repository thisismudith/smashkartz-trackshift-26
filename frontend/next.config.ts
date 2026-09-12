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
        source: "/sim/:file*.:hash([a-f0-9]{10}).:ext(json|bin)",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
};

export default nextConfig;
