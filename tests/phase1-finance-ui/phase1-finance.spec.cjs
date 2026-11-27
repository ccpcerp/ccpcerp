const path = require('node:path');
const { test, expect } = require('@playwright/test');

// Execute the application's real Page JavaScript in an isolated browser fixture.
// There is no HTTP server, live Frappe session, database or financial API.
const pageScript = path.resolve(
  __dirname,
  '../../ccpcerp/ccpc_erp/page/ccpc_phase1_finance/ccpc_phase1_finance.js',
);
const jqueryScript = require.resolve('jquery/dist/jquery.min.js');

const COMPANIES = [
  'Cagayan Corn Products Corporation',
  'Ricor Mills Corporation',
];
const KINDS = [
  { value: 'Expense', source: 'Item Group', proposal: 'Expense Account' },
  { value: 'Center', source: 'Beneficiary Department', proposal: 'Cost Center' },
];

async function mountFinancePage(page, { canPropose, canReview, companies = COMPANIES, allowedKinds }) {
  await page.setContent('<!doctype html><html lang="en"><body><div id="fixture"></div></body></html>');
  await page.addScriptTag({ path: jqueryScript });
  await page.evaluate(({ companies, canPropose, canReview, allowedKinds }) => {
    window.__ = (value) => value;
    window.__financeApiCalls = [];
    window.frappe = {
      pages: { 'ccpc-phase1-finance': {} },
      session: { user: canPropose ? 'preparer@example.invalid' : 'controller@example.invalid' },
      ui: {
        make_app_page({ parent }) {
          const body = document.createElement('div');
          parent.appendChild(body);
          return { body };
        },
      },
      utils: { debounce: (fn) => fn },
      call: async (request) => {
        window.__financeApiCalls.push(request.method);
        if (request.method !== 'ccpcerp.ccpc_erp.phase1_finance_onboarding.get_pilot_context'
            || request.type !== 'POST') {
          throw new Error(`Unexpected Finance API request in read-only fixture: ${request.method}`);
        }
        return {
          message: {
            companies,
            can_propose: canPropose,
            can_review: canReview,
            allowed_kinds: allowedKinds,
          },
        };
      },
    };
  }, { companies, canPropose, canReview, allowedKinds });
  await page.addScriptTag({ path: pageScript });
  await page.evaluate(async () => {
    await window.frappe.pages['ccpc-phase1-finance'].on_page_load(
      document.getElementById('fixture'),
    );
  });
}

async function assertNoBusinessApiCalls(page) {
  expect(await page.evaluate(() => window.__financeApiCalls)).toEqual([
    'ccpcerp.ccpc_erp.phase1_finance_onboarding.get_pilot_context',
  ]);
}

for (const actor of [
  { name: 'Finance Preparer', canPropose: true, canReview: false },
  { name: 'independent Finance Controller', canPropose: false, canReview: true },
]) {
  test(`${actor.name}: both Companies and mapping types render without writes`, async ({ page }) => {
    await mountFinancePage(page, actor);
    const company = page.locator('#ccpc-pilot-company');
    const kind = page.locator('#ccpc-pilot-kind');
    await expect(company).toBeVisible();
    await expect(kind).toBeVisible();
    expect(await company.locator('option').allTextContents()).toEqual(COMPANIES);
    expect(await kind.locator('option').allTextContents()).toEqual(['Expense Account', 'Cost Center']);

    for (const companyName of COMPANIES) {
      await company.selectOption({ label: companyName });
      await expect(company).toHaveValue(companyName);
      for (const mapping of KINDS) {
        await kind.selectOption(mapping.value);
        await expect(page.locator('label[for="ccpc-pilot-source"]')).toHaveText(mapping.source);
        await expect(page.locator('#ccpc-pilot-source')).toBeVisible();
        await expect(page.getByRole('button', { name: 'Check coverage', exact: true })).toBeVisible();
        const proposeButton = page.getByRole('button', { name: 'Propose mapping', exact: true });
        if (actor.canPropose) {
          await expect(page.locator('label[for="ccpc-pilot-value"]')).toHaveText(mapping.proposal);
          await expect(page.locator('#ccpc-pilot-value')).toBeVisible();
          await expect(proposeButton).toBeVisible();
        } else {
          await expect(page.locator('#ccpc-pilot-value')).toHaveCount(0);
          await expect(proposeButton).toHaveCount(0);
        }
        await assertNoBusinessApiCalls(page);
      }
    }
  });

  test(`${actor.name}: changing Company or mapping type clears stale selections`, async ({ page }) => {
    await mountFinancePage(page, actor);
    // Direct DOM assignment avoids search/focus events and their associated API calls.
    await page.evaluate((canPropose) => {
      document.getElementById('ccpc-pilot-source').value = 'old source';
      if (canPropose) document.getElementById('ccpc-pilot-value').value = 'old proposal';
    }, actor.canPropose);
    await page.locator('#ccpc-pilot-kind').selectOption('Center');
    await expect(page.locator('#ccpc-pilot-source')).toHaveValue('');
    if (actor.canPropose) await expect(page.locator('#ccpc-pilot-value')).toHaveValue('');

    await page.evaluate((canPropose) => {
      document.getElementById('ccpc-pilot-source').value = 'other source';
      if (canPropose) document.getElementById('ccpc-pilot-value').value = 'other proposal';
    }, actor.canPropose);
    await page.locator('#ccpc-pilot-company').selectOption(COMPANIES[1]);
    await expect(page.locator('#ccpc-pilot-source')).toHaveValue('');
    if (actor.canPropose) await expect(page.locator('#ccpc-pilot-value')).toHaveValue('');
    await assertNoBusinessApiCalls(page);
  });
}

test('Stockroom preparer: only CCPC Cost Center choice and correct initial captions', async ({ page }) => {
  await mountFinancePage(page, {
    canPropose: true,
    canReview: false,
    companies: [COMPANIES[0]],
    allowedKinds: ['Center'],
  });
  const company = page.locator('#ccpc-pilot-company');
  const kind = page.locator('#ccpc-pilot-kind');
  expect(await company.locator('option').allTextContents()).toEqual([COMPANIES[0]]);
  expect(await kind.locator('option').allTextContents()).toEqual(['Cost Center']);
  await expect(kind).toHaveValue('Center');
  await expect(page.locator('label[for="ccpc-pilot-source"]')).toHaveText('Beneficiary Department');
  await expect(page.locator('label[for="ccpc-pilot-value"]')).toHaveText('Cost Center');
  await expect(page.getByRole('button', { name: 'Propose mapping' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign and approve' })).toHaveCount(0);
  await assertNoBusinessApiCalls(page);
});
