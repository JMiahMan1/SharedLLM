import fs from 'fs';
import os from 'os';
import path from 'path';
import { defineConfig, devices } from '@playwright/test';

// Desktop counterpart to playwright.local-mobile.config.ts: same throwaway
// browser workaround, 1440x900. Local harness only; CI never sees these.
const cache = path.join(os.homedir(), 'Library/Caches/ms-playwright');
const exec = fs.readdirSync(cache)
  .filter((d) => d.startsWith('chromium_headless_shell-'))
  .sort()
  .reverse()
  .map((d) => path.join(cache, d, 'chrome-headless-shell-mac-x64', 'chrome-headless-shell'))
  .find((p) => fs.existsSync(p));
if (!exec) throw new Error(`no chromium headless shell installed under ${cache}`);
console.log(`[local-desktop] using browser ${exec}`);

export default defineConfig({
  testDir: './e2e',
  outputDir: '../../.tmp/pw-out-desktop',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    viewport: { width: 1440, height: 900 },
    baseURL: 'http://localhost:5179',
    trace: 'off',
    screenshot: 'only-on-failure',
    launchOptions: { executablePath: exec },
  },
  webServer: {
    command: 'npx vite --port 5179 --strictPort',
    url: 'http://localhost:5179',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});