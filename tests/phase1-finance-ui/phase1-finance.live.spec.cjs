const fs = require('node:fs');
const { test, expect } = require('@playwright/test');

// Explicit opt-in only; never permit these checks against production.
const enabled = process.env.CCPC_FINANCE_UI_LIVE === '1';
const baseUrl = process.env.CCPC_FINANCE_UI_DEV_URL;
const allowedBaseUrl = 'http://development.localhost:8000';
const preparerState = process.env.CCPC_FINANCE_UI_PREPARER_STATE;
const checkerState = process.env.CCPC_FINANCE_UI_CHECKER_STATE;
if (enabled) {
  if (baseUrl !== allowedBaseUrl) {
    throw new Error(`Refusing live browser tests. DEV URL must be exactly ${allowedBaseUrl}`);
  }
  for (const [role, file] of [['preparer', preparerState], ['checker', checkerState]]) {
    if (!file || !fs.existsSync(file)) {
      throw new Error(`Missing locally secured Playwright storage state for ${role}`);
    }
  }
}

const COMPANIES = ['Cagayan Corn Products Corporation', 'Ricor Mills Corporation'];
const KINDS = [
  { value: 'Expense', source: 'Item Group', proposed: 'Expense Account' },
  { value: 'Center', source: 'Beneficiary Department', proposed: 'Cost Center' },
];

for (const actor of [
  { role: 'preparer', storageState: preparerState, canPropose: true },
  { role: 'checker', storageState: checkerState, canPropose: false },
]) {
  test.describe(`@live ${actor.role} authenticated MAIN DEVELOPMENT UI`, () => {
    test.skip(!enabled, 'Requires explicit CCPC_FINANCE_UI_LIVE=1');
    test.use({ storageState: actor.storageState });

    test('two-Company × two-mapping-type read-only matrix', async ({ page }) => {
      // Any non-DEV origin is blocked, including redirects to production.
      await page.route('**/*', async (route) => {
        const target = new URL(route.request().url());
        if (target.origin !== allowedBaseUrl) return route.abort();
        // Only the context endpoint is needed; no coverage, proposals or approvals.
        if (target.pathname.startsWith('/api/method/ccpcerp.ccpc_erp.phase1_finance_onboarding.')
            && target.pathname !== '/api/method/ccpcerp.ccpc_erp.phase1_finance_onboarding.get_pilot_context') {
          return route.abort();
        }
        return route.continue();
      });
      await page.goto(`${allowedBaseUrl}/app/ccpc-phase1-finance`);
      const company = page.locator('#ccpc-pilot-company');
      const kind = page.locator('#ccpc-pilot-kind');
      await expect(company).toBeVisible();
      await expect(kind).toBeVisible();
      for (const companyName of COMPANIES) {
        await company.selectOption({ label: companyName });
        for (const mapping of KINDS) {
          await kind.selectOption(mapping.value);
          await expect(page.locator('label[for="ccpc-pilot-source"]')).toHaveText(mapping.source);
          await expect(page.getByRole('button', { name: 'Check coverage', exact: true })).toBeVisible();
          const propose = page.getByRole('button', { name: 'Propose mapping', exact: true });
          if (actor.canPropose) {
            await expect(page.locator('label[for="ccpc-pilot-value"]')).toHaveText(mapping.proposed);
            await expect(propose).toBeVisible();
          } else {
            await expect(page.locator('#ccpc-pilot-value')).toHaveCount(0);
            await expect(propose).toHaveCount(0);
          }
        }
      }
    });
  });
}
