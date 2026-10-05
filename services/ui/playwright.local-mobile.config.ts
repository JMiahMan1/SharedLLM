import fs from 'fs';
import os from 'os';
import path from 'path';
import { defineConfig, devices } from '@playwright/test';

// The pinned browser build (1217) fails to unzip on this machine, so use the
// newest headless shell that is actually installed. Throwaway local config —
// CI and the committed playwright.config.ts are untouched.
const cache = path.join(os.homedir(), 'Library/Caches/ms-playwright');
const exec = fs.readdirSync(cache)
  .filter((d) => d.startsWith('chromium_headless_shell-'))
  .sort()
  .reverse()
  .map((d) => path.join(cache, d, 'chrome-headless-shell-mac-x64', 'chrome-headless-shell'))
  .find((p) => fs.existsSync(p));
if (!exec) throw new Error(`no chromium headless shell installed under ${cache}`);
console.log(`[local-mobile] using browser ${exec}`);

export default defineConfig({
  testDir: './e2e',
  outputDir: '../../.tmp/pw-out',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: 'list',
  use: {
    ...devices['Pixel 7'],
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
