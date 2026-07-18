import { test, expect } from "./fixtures";
import { mockApi, seedSession } from "./mock-api";

/* SPEC-4 Step 21 — visual-regression baselines.
 *
 * toHaveScreenshot() snapshots of the five core console screens. Runs under
 * BOTH theme projects (chromium-dark / chromium-light), so
 * each screen gets a light and a dark baseline stored per-project (see
 * snapshotPathTemplate in playwright.config.ts).
 *
 * Generate/refresh baselines locally with:  npm run e2e:update
 * CI then fails on any pixel drift beyond the configured tolerance. */

const SCREENS: [string, string][] = [
  ["dashboard", "/app"],
  ["runs", "/app/executions"],
  ["results", "/app/results"],
  ["scorecard-detail", "/app/scorecards/sc_001"],
  ["issues", "/app/issues"],
];

test.describe("visual regression — core screens in both themes", () => {
  test.beforeEach(async ({ page, theme }) => {
    await mockApi(page);
    await seedSession(page, theme);
  });

  for (const [name, path] of SCREENS) {
    test(name, async ({ page, theme }) => {
      await page.goto(path);
      // Wait for the shell/content to paint before snapshotting.
      await expect(page.getByRole("navigation").first()).toBeVisible();
      // Give lazy chunks + fonts a beat to settle for a stable pixel baseline.
      await page.waitForLoadState("networkidle").catch(() => {});
      await expect(page).toHaveScreenshot(`${name}-${theme}.png`, {
        fullPage: true,
      });
    });
  }
});
