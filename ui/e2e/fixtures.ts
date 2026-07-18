import { test as base, expect } from "@playwright/test";

/* Shared E2E fixtures. Derives the target theme from the project name
 * ("chromium-dark" | "chromium-light") and exposes it, and pins <html
 * data-theme> before any page navigation via an init script. Specs import
 * `test`/`expect` from here instead of @playwright/test directly. */

type Fixtures = {
  theme: "dark" | "light";
};

export const test = base.extend<Fixtures>({
  theme: async ({}, use, testInfo) => {
    const theme = testInfo.project.name.includes("light") ? "light" : "dark";
    await use(theme);
  },
});

export { expect };
