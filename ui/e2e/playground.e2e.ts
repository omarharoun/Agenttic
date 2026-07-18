import { test, expect } from "./fixtures";
import AxeBuilder from "@axe-core/playwright";

/* SPEC-5 Step 24 — the public Gate playground.
 *
 * No API, no signup — sim-core only. Asserts the acceptance criteria: axe-clean,
 * keyboard-operable, the "lobotomy" preset shows the production fail-closed
 * reason string, and slider state is reproducible from URL params (shareable).
 * Runs under both theme projects. */

test.describe("playground — The Gate", () => {
  test("presets render the real production receipts", async ({ page }) => {
    await page.goto("/playground/gate");
    await expect(page.getByRole("heading", { name: "The Gate" })).toBeVisible();

    await page.getByRole("button", { name: /the lobotomy/i }).click();
    await expect(page.locator(".pg-verdict-badge")).toHaveText("REJECT");
    await expect(page.locator(".pg-receipt")).toHaveText(
      "rejected: candidate scorecard is missing baseline criteria ['safety'] " +
        "— unpaired criteria cannot be verified as non-regressing",
    );

    await page.getByRole("button", { name: /the clean win/i }).click();
    await expect(page.locator(".pg-verdict-badge")).toHaveText("PROMOTE");
  });

  test("URL params reproduce a slider state exactly (shareable)", async ({ page }) => {
    await page.goto("/playground/gate?tone=0.95&acc=0.92&saf=0.7&rate=0.8");
    // safety candidate 0.70 vs baseline 0.95 -> epsilon floor rejects
    await expect(page.locator(".pg-verdict-badge")).toHaveText("REJECT");
    await expect(page.locator(".pg-receipt")).toContainText("dropped beyond epsilon");
  });

  test("is keyboard operable (a slider takes focus and responds to arrows)", async ({ page }) => {
    await page.goto("/playground/gate");
    const slider = page.getByRole("slider").first();
    await slider.focus();
    await expect(slider).toBeFocused();
    const before = await slider.inputValue();
    await slider.press("ArrowRight");
    expect(await slider.inputValue()).not.toBe(before);
  });

  test("axe — no critical violations", async ({ page }, testInfo) => {
    await page.goto("/playground/gate");
    await expect(page.getByRole("heading", { name: "The Gate" })).toBeVisible();
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const critical = results.violations.filter((v) => v.impact === "critical");
    for (const v of results.violations) {
      testInfo.annotations.push({ type: `axe-${v.impact}`, description: `${v.id}: ${v.help}` });
    }
    expect(critical, JSON.stringify(critical.map((v) => v.id))).toEqual([]);
  });
});

test.describe("playground — Drift Watch & The Deferral", () => {
  test("drift fires a re-eval as degradation rises (real sim-core)", async ({ page }) => {
    await page.goto("/playground/drift?deg=0.1");
    await expect(page.locator(".pg-verdict-badge")).toHaveText("STABLE");
    await page.goto("/playground/drift?deg=0.5");
    await expect(page.locator(".pg-verdict-badge")).toHaveText("RE-EVAL FIRED");
    await expect(page.locator(".pg-receipt")).toContainText("batch re-evaluation recommended");
  });

  test("deferral scores 1.0 for a correct escalation, 0.0 for over-confidence", async ({ page }) => {
    await page.goto("/playground/deferral?amb=0.8&thr=0.5");
    await expect(page.locator(".pg-verdict-badge")).toHaveText("SCORE 1.0");
    await page.goto("/playground/deferral?amb=0.6&thr=0.9");
    await expect(page.locator(".pg-verdict-badge")).toHaveText("SCORE 0.0");
  });

  for (const [name, path] of [["Drift Watch", "/playground/drift"], ["The Deferral", "/playground/deferral"]] as const) {
    test(`${name} — keyboard + axe (no critical)`, async ({ page }, testInfo) => {
      await page.goto(path);
      const slider = page.getByRole("slider").first();
      await slider.focus();
      await expect(slider).toBeFocused();
      const before = await slider.inputValue();
      await slider.press("ArrowRight");
      expect(await slider.inputValue()).not.toBe(before);
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      const critical = results.violations.filter((v) => v.impact === "critical");
      for (const v of results.violations) {
        testInfo.annotations.push({ type: `axe-${v.impact}`, description: `${v.id}: ${v.help}` });
      }
      expect(critical, JSON.stringify(critical.map((v) => v.id))).toEqual([]);
    });
  }
});
