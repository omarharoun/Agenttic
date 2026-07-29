import { expect, test } from "@playwright/test";
import { freezeClock, loaded, settle, stubApi, chooseTheme } from "./support/console";

/* M45 acceptance: "Visual regression snapshots of existing console screens are
 * unchanged or their diffs are explained in RECONCILIATION.md."
 *
 * The baselines committed alongside this file ARE the record of what the token
 * migration produced. From here, any change to design/tokens.css that moves a
 * pixel on these screens has to be looked at and either accepted (update the
 * baseline, explain it in RECONCILIATION.md) or fixed. That is the whole
 * mechanism: the token layer can no longer be edited invisibly.
 *
 * Both themes, because a token that resolves in one and not the other is
 * exactly the failure this milestone exists to prevent.
 */

/** Console screens that render meaningfully from stubbed data. */
const CONSOLE = [
  { name: "dashboard", path: "/app" },
  { name: "results", path: "/app/results" },
  { name: "build", path: "/app/build" },
  { name: "capabilities", path: "/app/capabilities" },
  { name: "settings", path: "/app/settings" },
];

/** Public routes — prerendered, no stubbing needed. */
const PUBLIC = [
  { name: "landing", path: "/" },
  { name: "pricing", path: "/pricing" },
  { name: "methodology", path: "/methodology" },
];

for (const theme of ["dark", "light"] as const) {
  for (const screen of CONSOLE) {
    test(`console ${screen.name} — ${theme}`, async ({ page }) => {
      await chooseTheme(page, theme);
      await freezeClock(page);
      await stubApi(page);
      await page.goto(screen.path);
      await loaded(page);
      await settle(page, theme);
      await expect(page).toHaveScreenshot(`${screen.name}-${theme}.png`, {
        fullPage: true,
      });
    });
  }

  for (const screen of PUBLIC) {
    test(`public ${screen.name} — ${theme}`, async ({ page }) => {
      await chooseTheme(page, theme);
      await freezeClock(page);
      await page.goto(screen.path);
      await loaded(page);
      await settle(page, theme);
      await expect(page).toHaveScreenshot(`${screen.name}-${theme}.png`, {
        fullPage: true,
      });
    });
  }
}

test("a token change moves the pixels on both surfaces", async ({ page }) => {
  /* The mechanism check. If overriding a token changed nothing, these
   * snapshots would be decorative — passing whether or not the surfaces
   * actually draw from the token layer. Console and landing are asserted
   * together because the point of M45 is that they share one source. */
  const shot = async (path: string, override?: string) => {
    await chooseTheme(page, "dark");
    await freezeClock(page);
    await stubApi(page);
    await page.goto(path);
    await loaded(page);
    await settle(page, "dark");
    if (override) {
      await page.addStyleTag({ content: `:root { ${override} }` });
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r(null))));
    }
    return page.screenshot({ fullPage: false });
  };

  const paintItPink = "--bg: #ff00aa !important; --panel: #ff00aa !important;";

  const consoleBefore = await shot("/app");
  const consoleAfter = await shot("/app", paintItPink);
  expect(Buffer.compare(consoleBefore, consoleAfter),
    "console did not respond to a token override").not.toBe(0);

  const landingBefore = await shot("/");
  const landingAfter = await shot("/", paintItPink);
  expect(Buffer.compare(landingBefore, landingAfter),
    "landing did not respond to a token override").not.toBe(0);
});
