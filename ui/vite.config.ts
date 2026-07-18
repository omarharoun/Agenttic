import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/** Only the public content routes are emitted as static HTML. /scan stays
 *  interactive (client-rendered) and /app/* is a pure client SPA — neither is
 *  prerendered, so the heavy React Flow console never runs in the build. */
const PRERENDER = new Set(["/", "/methodology", "/pricing", "/certified", "/api-docs", "/status",
  "/playground", "/playground/gate"]);

export default defineConfig({
  plugins: [react()],
  // consumed by `vite-react-ssg build` (see build script in package.json)
  ssgOptions: {
    // Leave the entry as a default (deferred) module script. Forcing `async`
    // lets it run before the inline script that sets __VITE_REACT_SSG_HASH__,
    // so the loader-data manifest is fetched as "…manifest-undefined.json"
    // (404 → the router's error boundary). Deferred preserves document order.
    // formatting stays "none" so renderToString output is byte-for-byte and
    // hydration doesn't trip on collapsed whitespace.
    formatting: "none",
    includedRoutes(paths: string[]) {
      return paths.filter((p) => PRERENDER.has(p));
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8700",
    },
  },
  test: {
    // Two runners, one file extension. Vitest's default glob claims every
    // *.spec.ts, including the Playwright specs under e2e/ — which then fail
    // with "test() was not expected to be called here" because they are being
    // run by the wrong runner entirely. Each owns a directory: vitest takes
    // src/, playwright takes e2e/ (see playwright.config.ts testDir).
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"],
  },
});
