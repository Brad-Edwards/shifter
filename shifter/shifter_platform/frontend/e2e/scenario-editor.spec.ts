import { expect, test } from "@playwright/test";

/**
 * End-to-end happy path: list -> canonical RAES pack detail.
 *
 * Preconditions (see frontend/README.md):
 *  - SPA_E2E_BASE_URL points at a running Django + built SPA stack whose inbox
 *    catalog has been bootstrapped.
 *  - The browser context is an authenticated session with CMS authoring
 *    (threat-research) access. When the stack requires login, provide the
 *    session to Playwright via `storageState` (playwright.config.ts / a global
 *    setup).
 */
test("authoring user can inspect the canonical Polaris pack", async ({ page }) => {
  await page.goto("/scenario-editor/");

  await expect(page.getByRole("heading", { level: 1, name: "Scenarios" })).toBeVisible();
  await page.getByRole("link", { name: /Operation NORTHSTORM/i }).click();
  await expect(page.getByRole("heading", { level: 1, name: /Operation NORTHSTORM/i })).toBeVisible();
  await expect(page.getByText("RAES", { exact: true })).toBeVisible();
});
