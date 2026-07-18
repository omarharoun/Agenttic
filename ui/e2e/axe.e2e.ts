import { test, expect } from "./fixtures";
import { mockApi, seedSession } from "./mock-api";
import AxeBuilder from "@axe-core/playwright";

/* SPEC-4 Step 21 — axe-in-browser smoke over the five core console routes.
 *
 * THE AUTHORITATIVE a11y GATE is the jsdom vitest jest-axe suite,
 * src/a11y.axe.test.tsx, which runs in `npm test` and FAILS the build on any
 * critical/serious violation across these same five routes. That gate is owned
 * by the app/a11y pass and is where component-level fixes land.
 *
 * This browser variant is complementary: it scans the SAME routes in a real
 * Chromium DOM against the built app, so issues that only exist with real CSS
 * + fonts (notably `color-contrast`, which jsdom cannot compute) surface here.
 *
 * It FAILS the CI job on `critical` violations. `color-contrast` (serious) is
 * reported as a test annotation but does NOT fail the job: those thresholds
 * live in theme.css / component styles owned by a separate pass, and gating CI
 * on another pass's tokens here would be wrong. Flip GATE_SERIOUS=1 (or the
 * AXE_GATE_SERIOUS env var) once the contrast tokens are AA-clean to promote
 * this to a hard critical+serious gate. */

const GATE_SERIOUS = process.env.AXE_GATE_SERIOUS === "1";

const ROUTES: [string, string][] = [
  ["Dashboard", "/app"],
  ["Runs", "/app/executions"],
  ["Results", "/app/results"],
  ["Scorecard detail", "/app/scorecards/sc_001"],
  ["Issues", "/app/issues"],
];

test.describe("axe (browser) — five core routes", () => {
  test.beforeEach(async ({ page, theme }) => {
    await mockApi(page);
    await seedSession(page, theme);
  });

  for (const [name, path] of ROUTES) {
    test(name, async ({ page }, testInfo) => {
      await page.goto(path);
      // Let the console shell settle (nav painted = shell mounted).
      await expect(page.getByRole("navigation").first()).toBeVisible();
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa"])
        .analyze();

      const critical = results.violations.filter((v) => v.impact === "critical");
      const serious = results.violations.filter((v) => v.impact === "serious");

      // Serious findings (theme.css contrast tokens, owned elsewhere) are
      // annotated for visibility but non-blocking unless explicitly gated.
      for (const v of serious) {
        testInfo.annotations.push({
          type: "axe-serious",
          description: `${v.id} (${v.nodes.length} node(s)) — ${v.help}`,
        });
      }

      const blocking = GATE_SERIOUS ? [...critical, ...serious] : critical;
      expect(
        blocking,
        JSON.stringify(blocking.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length })), null, 2),
      ).toHaveLength(0);
    });
  }
});
