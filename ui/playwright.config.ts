import { defineConfig, devices } from "@playwright/test";

/* Browser-level gates for the design system.
 *
 * SPEC-11 recorded these as deliberately missing: "Full axe + visual-regression
 * are browser-runner (Playwright) CI gates and are NOT set up in this
 * environment." Everything checkable without a browser was gated; the rest was
 * left honestly unproven. This is that runner.
 *
 * It serves the real production build (`vite-react-ssg build` output) rather
 * than the dev server, so what is asserted is what ships — prerendered HTML,
 * hashed assets, the same CSS pipeline.
 */
export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],

  // Snapshots are the M45 evidence that migrating to tokens changed nothing, so
  // they live beside the specs and are committed.
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFileName}/{arg}{ext}",

  use: {
    baseURL: "http://127.0.0.1:4319",
    trace: "on-first-retry",
    // Deterministic rendering: an animation mid-capture is the classic source
    // of a snapshot that fails for no reason.
    reducedMotion: "reduce",
  },

  expect: {
    // Font antialiasing differs by a hair between machines; a hard zero here
    // makes the suite flap without catching anything real.
    toHaveScreenshot: { maxDiffPixelRatio: 0.002, animations: "disabled" },
  },

  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],

  webServer: {
    // `--host 127.0.0.1` is load-bearing: vite preview otherwise binds ::1 only,
    // so a 127.0.0.1 baseURL never connects and the runner just waits out its
    // whole timeout with no useful error.
    command: "npm run build && npx vite preview --port 4319 --strictPort --host 127.0.0.1",
    url: "http://127.0.0.1:4319/",
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,   // the SSG build runs first (~30s) plus tsc and lint
  },
});
