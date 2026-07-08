import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/** Only the public content routes are emitted as static HTML. /scan stays
 *  interactive (client-rendered) and /app/* is a pure client SPA — neither is
 *  prerendered, so the heavy React Flow console never runs in the build. */
const PRERENDER = new Set(["/", "/methodology", "/certified", "/api-docs"]);

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
});
