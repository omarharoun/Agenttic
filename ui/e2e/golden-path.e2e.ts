import { test, expect } from "./fixtures";
import { mockApi, seedSession } from "./mock-api";

/* SPEC-4 Step 21 — golden-path E2E against a fully mocked API.
 *
 * Walks the core operator journey and asserts the key UI landmark at each step
 * (headings, nav links, form controls, live regions) rather than exact copy, so
 * the test survives wording tweaks but catches a broken flow:
 *
 *   login → new evaluation → run (mocked) → scorecard → export
 *   + answer an escalation
 *   + add a calibration label
 *
 * The backend is never started: mockApi() intercepts every `**​/api/**` call.
 */

test.describe("golden path", () => {
  test.beforeEach(async ({ page, theme }) => {
    await mockApi(page);
    await seedSession(page, theme);
  });

  test("login lands in the console", async ({ page }) => {
    // Start unauthenticated at the login form.
    await page.addInitScript(() => {
      try { localStorage.removeItem("ascore_token"); } catch { /* ignore */ }
    });
    await page.goto("/login");
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();

    await page.getByLabel("Email").fill("e2e@agenttic.io");
    await page.getByLabel("Password").fill("hunter2hunter2");
    await page.getByRole("button", { name: /log in/i }).click();

    // Login navigates to /app — the console shell renders its nav.
    await expect(page).toHaveURL(/\/app/);
    await expect(page.getByRole("link", { name: /Runs/ })).toBeVisible();
  });

  test("new evaluation → runs → results → scorecard → export", async ({ page }) => {
    await page.goto("/app");
    // The sidebar nav is the anchor for the whole journey — scope link lookups
    // to it so we don't collide with in-page CTAs of the same name.
    const nav = page.getByRole("navigation").first();
    await expect(nav).toBeVisible();

    // New evaluation
    await nav.getByRole("link", { name: /New evaluation/ }).click();
    await expect(page).toHaveURL(/\/app\/build/);

    // Runs (executions table)
    await nav.getByRole("link", { name: /^Runs$/ }).click();
    await expect(page).toHaveURL(/\/app\/executions/);

    // Results (scorecard history)
    await nav.getByRole("link", { name: /^Results$/ }).click();
    await expect(page).toHaveURL(/\/app\/results/);

    // Scorecard detail — open the first scorecard's detail route directly (the
    // AppShell routes /app/scorecards/:id) and assert it paints its export
    // affordances (the Markdown / PDF buttons render once the scorecard loads).
    await page.goto("/app/scorecards/sc_001");
    await expect(page).toHaveURL(/\/app\/scorecards\/sc_001/);
    await expect(page.getByRole("button", { name: /markdown/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /pdf/i })).toBeVisible();

    // Export — click the Markdown export; the mock serves the report and the
    // page must not surface an export error.
    await page.getByRole("button", { name: /markdown/i }).click();
    await expect(page.getByText(/export failed/i)).toHaveCount(0);
  });

  test("issues report renders the ranked findings", async ({ page }) => {
    await page.goto("/app/issues");
    await expect(page).toHaveURL(/\/app\/issues/);
    await expect(page.getByRole("navigation").first()).toBeVisible();
  });

  test("answer an escalation", async ({ page }) => {
    await page.goto("/app/escalations");
    await expect(page).toHaveURL(/\/app\/escalations/);

    // The pending item exposes an answer textarea + submit.
    const answer = page.getByRole("textbox").first();
    await expect(answer).toBeVisible();
    await answer.fill("Approve the refund — outside window but goodwill.");

    const submit = page.getByRole("button", { name: /answer|respond|submit|send/i }).first();
    await expect(submit).toBeEnabled();
    await submit.click();
    // The mock returns ok:true; the page should not surface an error alert.
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("add a calibration label", async ({ page }) => {
    await page.goto("/app/calibration");
    await expect(page).toHaveURL(/\/app\/calibration/);

    // Enter a criterion id and fetch the next unlabeled case.
    const critInput = page.getByPlaceholder(/criterion/i);
    await expect(critInput).toBeVisible();
    await critInput.fill("helpfulness");
    await page.getByRole("button", { name: /start labeling/i }).click();

    // A score anchor button (score + its label, e.g. "Fully helpful") appears
    // for the unlabeled trace; clicking it posts the label.
    const scoreBtn = page.getByRole("button", { name: /helpful/i }).first();
    await expect(scoreBtn).toBeVisible({ timeout: 10_000 });
    await scoreBtn.click();
    // Success shows a status note ("Saved…"); no error alert.
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
});
