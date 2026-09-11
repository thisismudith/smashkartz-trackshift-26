import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests target pure modules (physics, particle maths) and run without a DOM.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
