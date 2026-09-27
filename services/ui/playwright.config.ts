import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test';

const HERMETIC_BASE_URL = 'http://localhost:5179';

const projects: PlaywrightTestConfig['projects'] = [
  {
    name: 'hermetic-desktop',
    grepInvert: /@live/,
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
    command: 'npx vite --port 5179 --strictPort',
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
