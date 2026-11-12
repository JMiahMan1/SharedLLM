import { defineConfig, devices } from '@playwright/test';

/**
 * Dedicated config for the live Calendar end-to-end run.
 * Uses the system Google Chrome (channel: 'chrome') so no browser download
 * is needed on the deploy box.
 *
 * Requires UI_URL (the server under test); there is no default address, and
 * the live specs skip themselves when it is missing.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-calendar-report' }]],
  use: {
    baseURL: process.env.UI_URL || undefined,
    channel: 'chrome',
    headless: true,
    launchOptions: {
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
    },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chrome', use: { ...devices['Desktop Chrome'] } }],
});
