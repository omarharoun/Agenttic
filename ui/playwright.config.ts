import { defineConfig, devices } from "@playwright/test";

/* SPEC-4 Step 21 — production bar: end-to-end + visual-regression harness.
 *
 * Serves the *built* app (dist/) with `vite preview` on a dedicated test port
 * so E2E runs against the same artifact CI ships. The backend is never started:
 * every spec mocks `**​/api/**` via Playwright route interception (see
 * e2e/mock-api.ts), so the suite is fully hermetic — no live server, no DB.
 *
 * Two chromium projects run the same specs in both themes. The app resolves its
 * theme from localStorage["ascore_theme"] + <html data-theme> (not the OS
 * media query alone), so each project seeds that preference in an init script;
 * `colorScheme` is set too so any media-query-driven CSS matches.
 */

const PORT = 4317;
const BASE_URL = `http://127.0.0.1:${PORT}`;

/** Injected before every page load to pin the theme deterministically. */
function themeInit(theme: "dark" | "light") {
  return `try {
    localStorage.setItem('ascore_theme', '${theme}');
    document.documentElement.setAttribute('data-theme', '${theme}');
  } catch {}`;
}

export default defineConfig({
  testDir: "./e2e",
  // Specs use the `.e2e.ts` suffix (not `.spec.ts`) so the vitest default glob
  // (**/*.{test,spec}.*) never picks them up — the two runners stay isolated
  // without touching the existing vitest setup.
  testMatch: "**/*.e2e.ts",
  // Snapshots live next to the specs, split per project (theme) so a light and
  // dark baseline never collide.
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{testFilePath}/{arg}{ext}",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: {
    // Visual-regression tolerance: allow sub-pixel AA/font-hinting drift, fail
    // on real layout/color changes.
    toHaveScreenshot: { maxDiffPixelRatio: 0.02, animations: "disabled" },
  },
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // Pinned, because a baseline is a photograph of rendered TEXT. Without
    // these the console's timestamps render in whatever zone the machine is
    // in — the committed baselines read 1:00:00 PM where CI reads 10:00:00 AM
    // for the same fixture — so a snapshot refreshed on a laptop can never
    // match CI again. UTC is what CI has; en-US is what the date formatting
    // in the fixtures assumes.
    timezoneId: "UTC",
    locale: "en-US",
  },
  projects: [
    {
      name: "chromium-dark",
      // visual-tokens drives BOTH themes itself, so running it here too would
      // photograph each screen twice and file the light shot under the dark
      // project.
      testIgnore: "**/visual-tokens.e2e.ts",
      use: {
        ...devices["Desktop Chrome"],
        colorScheme: "dark",
      },
      // The theme seed is applied per-project via a fixture (see e2e/fixtures).
      metadata: { themeInit: themeInit("dark") },
    },
    {
      name: "chromium-light",
      // visual-tokens drives BOTH themes itself, so running it here too would
      // photograph each screen twice and file the light shot under the dark
      // project.
      testIgnore: "**/visual-tokens.e2e.ts",
      use: {
        ...devices["Desktop Chrome"],
        colorScheme: "light",
      },
      metadata: { themeInit: themeInit("light") },
    },
    {
      // The M45 token gate. It calls chooseTheme() per test rather than taking
      // the theme from the project, so it gets one project and produces
      // exactly one baseline per screen per theme.
      name: "visual-tokens",
      testMatch: "**/visual-tokens.e2e.ts",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // Serve the production build. `--host 127.0.0.1` pins the bind address so it
    // matches BASE_URL (vite preview otherwise binds localhost/IPv6 only, which
    // 127.0.0.1 health checks can't reach). `--strictPort` makes a port clash a
    // hard failure instead of silently drifting.
    command: `npm run preview -- --port ${PORT} --strictPort --host 127.0.0.1`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
