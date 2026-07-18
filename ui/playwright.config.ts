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
  },
  projects: [
    {
      name: "chromium-dark",
      use: {
        ...devices["Desktop Chrome"],
        colorScheme: "dark",
      },
      // The theme seed is applied per-project via a fixture (see e2e/fixtures).
      metadata: { themeInit: themeInit("dark") },
    },
    {
      name: "chromium-light",
      use: {
        ...devices["Desktop Chrome"],
        colorScheme: "light",
      },
      metadata: { themeInit: themeInit("light") },
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
