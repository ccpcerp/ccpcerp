const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: ['*.spec.cjs'],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    browserName: 'chromium',
    headless: true,
    // These tests must not store authenticated pages, Company records, or cookies.
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
});
