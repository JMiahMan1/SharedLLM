import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test';

// Override per checkout (HERMETIC_PORT) when several run the suite at once:
// the dev server is reused, so a shared port would serve another checkout's code.
const HERMETIC_PORT = process.env.HERMETIC_PORT || '5179';
const HERMETIC_BASE_URL = `http://localhost:${HERMETIC_PORT}`;

// The media visual baselines are per-platform (`*-darwin.png`) and the fonts a
// Linux runner falls back to are not the ones they were captured with, so CI
// skips that one spec rather than comparing a Mac render with a Linux one.
const SKIP_VISUAL = process.env.SKIP_VISUAL === '1' ? '**/visual.spec.ts' : undefined;

const projects: PlaywrightTestConfig['projects'] = [
  {
    name: 'hermetic-desktop',
    grepInvert: /@live/,
    testIgnore: SKIP_VISUAL,
    fullyParallel: true,
    workers: 4,
    use: {
      ...devices['Desktop Chrome'],
      viewport: { width: 1440, height: 900 },
      baseURL: HERMETIC_BASE_URL,
    },
  },
  {
    name: 'hermetic-mobile',
    grepInvert: /@live/,
    testIgnore: SKIP_VISUAL,
    fullyParallel: true,
    workers: 4,
    use: {
      ...devices['Pixel 7'],
      baseURL: HERMETIC_BASE_URL,
    },
  },
  {
    name: 'hermetic-iphone',
    grepInvert: /@live/,
    testIgnore: SKIP_VISUAL,
    fullyParallel: true,
    workers: 4,
    use: {
      ...devices['iPhone 14'],
      baseURL: HERMETIC_BASE_URL,
    },
  },
];

if (process.env.LIVE === '1') {
  projects.push({
    name: 'live',
    grep: /@live/,
    use: {
      ...devices['Desktop Chrome'],
      baseURL: process.env.UI_URL || 'http://192.168.2.205:8080',
    },
  });
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  webServer: {
    command: `npx vite --port ${HERMETIC_PORT} --strictPort`,
    url: HERMETIC_BASE_URL,
    reuseExistingServer: true,
  },
  use: {
    baseURL: HERMETIC_BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    launchOptions: {
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    },
  },
  projects,
});
