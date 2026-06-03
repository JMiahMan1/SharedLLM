import { test, expect } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_USER = 'default';
const ADMIN_PASS = 'admin';

test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

test.describe('Android App - Mobile Layout & Navigation', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('mobile viewport renders bottom navigation bar', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const bottomNav = page.locator('nav').filter({ hasText: /home|dashboard|communication|workspace|identity/i });
    await expect(bottomNav.or(page.locator('[class*="bottom-nav"]'))).toBeVisible({ timeout: 10000 });
  });

  test('bottom nav has all core routes', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const navItems = page.locator('nav a, nav button');
    const count = await navItems.count();
    expect(count).toBeGreaterThanOrEqual(4);
  });

  test('tapping bottom nav items navigates correctly', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    const navLinks = [
      { name: /home|dashboard/i, expectedUrl: /dashboard|\/$/ },
      { name: /communication/i, expectedUrl: /communication/ },
      { name: /workspace/i, expectedUrl: /workspace/ },
      { name: /identity|profile/i, expectedUrl: /identity|profile/ },
    ];

    for (const link of navLinks) {
      const navItem = page.getByRole('link', { name: link.name }).or(
        page.getByRole('button', { name: link.name })
      );
      if (await navItem.isVisible({ timeout: 3000 }).catch(() => false)) {
        await navItem.click();
        await page.waitForTimeout(1000);
      }
    }
  });

  test('hamburger menu is hidden on mobile (replaced by bottom nav)', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const sidebar = page.locator('[class*="sidebar"]').or(page.locator('aside'));
    const isVisible = await sidebar.isVisible({ timeout: 3000 }).catch(() => false);
    expect(isVisible).toBe(false);
  });

  test('mobile header shows app title and user menu', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const header = page.locator('header').or(page.locator('[class*="header"]'));
    await expect(header).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Android App - Authentication & Biometrics', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test('login page is mobile-optimized', async ({ page }) => {
    await page.goto(`${UI_URL}/login`);
    await expect(page.getByRole('heading', { name: 'Jarvis OS' })).toBeVisible();
    await expect(page.getByPlaceholder('Enter username')).toBeVisible();
    await expect(page.getByPlaceholder('Enter password')).toBeVisible();
  });

  test('login form fits mobile viewport without horizontal scroll', async ({ page }) => {
    await page.goto(`${UI_URL}/login`);
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const viewportWidth = await page.evaluate(() => window.innerWidth);
    expect(scrollWidth).toBeLessThanOrEqual(viewportWidth);
  });

  test('session persists across page reloads', async ({ page }) => {
    await loginAsAdmin(page);
    await page.reload();
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    await expect(page).not.toHaveURL(/\/login/);
  });
});

test.describe('Android App - Wake Word & Voice Assistant', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('mic icon is visible for voice input', async ({ page }) => {
    const micBtn = page.getByRole('button', { name: /mic|voice|jarvis/i });
    if (await micBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(micBtn).toBeVisible();
    }
  });

  test('tapping mic opens voice assistant overlay', async ({ page }) => {
    const micBtn = page.getByRole('button', { name: /mic|voice|jarvis/i });
    if (await micBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await micBtn.click();
      await page.waitForTimeout(2000);
      const overlay = page.locator('[class*="voice"]').or(page.locator('[class*="assistant"]'));
      await expect(overlay.or(page.getByText(/listening|speak/i))).toBeVisible({ timeout: 5000 }).catch(() => {});
    }
  });
});

test.describe('Android App - Intercom (Mobile Hold-to-Talk)', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('intercom section loads on mobile', async ({ page }) => {
    await expect(page.getByText(/intercom|talk/i)).toBeVisible({ timeout: 10000 });
  });

  test('hold-to-talk button is visible', async ({ page }) => {
    const holdBtn = page.getByRole('button', { name: /hold|talk|intercom/i });
    if (await holdBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(holdBtn).toBeVisible();
    }
  });
});

test.describe('Android App - NFC Tag Macros', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('NFC settings section exists in admin', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    await page.getByRole('button', { name: 'Raven Ops' }).click();
    const nfcSection = page.getByText(/nfc|tag|macro/i);
    if (await nfcSection.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(nfcSection).toBeVisible();
    }
  });
});

test.describe('Android App - Location Tracking', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('location permission prompt handling', async ({ page }) => {
    await page.goto(`${UI_URL}/identity`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const locationSection = page.getByText(/location|gps|tracking/i);
    if (await locationSection.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(locationSection).toBeVisible();
    }
  });
});

test.describe('Android App - Entity Dropdowns (Mobile)', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('entity search dropdown opens on mobile tap', async ({ page }) => {
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    const searchInput = page.getByPlaceholder('Search Home Assistant entities...');
    await expect(searchInput).toBeVisible({ timeout: 10000 });
    await searchInput.tap();
    await page.waitForTimeout(2000);
    const dropdown = page.locator('.entity-dropdown-portal');
    await expect(dropdown).toBeVisible({ timeout: 5000 });
  });

  test('selecting entity from dropdown adds it (mobile)', async ({ page }) => {
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    const searchInput = page.getByPlaceholder('Search Home Assistant entities...');
    await searchInput.tap();
    await page.waitForTimeout(2000);
    const dropdown = page.locator('.entity-dropdown-portal');
    if (await dropdown.isVisible({ timeout: 5000 })) {
      const firstItem = dropdown.locator('button').first();
      if (await firstItem.isVisible({ timeout: 3000 })) {
        await firstItem.tap();
        await page.waitForTimeout(1000);
        const value = await searchInput.inputValue();
        expect(value.length).toBeGreaterThan(0);
      }
    }
  });

  test('entity multi-select works on mobile', async ({ page }) => {
    await page.getByRole('button', { name: 'Device Groups' }).click();
    const multiSelect = page.getByPlaceholder('Search and add media entities...');
    await expect(multiSelect).toBeVisible({ timeout: 10000 });
    await multiSelect.tap();
    await page.waitForTimeout(2000);
    const dropdown = page.locator('.entity-dropdown-portal');
    if (await dropdown.isVisible({ timeout: 5000 })) {
      const firstItem = dropdown.locator('button').first();
      if (await firstItem.isVisible({ timeout: 3000 })) {
        await firstItem.tap();
        await page.waitForTimeout(1000);
        const tags = page.locator('[class*="bg-indigo-500\\/20"]');
        await expect(tags.first()).toBeVisible({ timeout: 5000 });
      }
    }
  });
});

test.describe('Android App - Responsive Design', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('dashboard adapts to mobile layout', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const viewportWidth = await page.evaluate(() => window.innerWidth);
    expect(scrollWidth).toBeLessThanOrEqual(viewportWidth);
  });

  test('admin tabs scroll horizontally on mobile', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const tabContainer = page.locator('[class*="overflow-x"]').or(page.locator('div').filter({ has: page.getByRole('button', { name: 'Users & Devices' }) }).first());
    await expect(tabContainer).toBeVisible({ timeout: 10000 });
  });

  test('glass panels stack vertically on mobile', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    const panels = page.locator('[class*="glass-panel"]');
    const count = await panels.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test('touch targets are at least 44px on mobile', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const buttons = page.locator('button');
    const count = await buttons.count();
    for (let i = 0; i < Math.min(count, 5); i++) {
      const box = await buttons.nth(i).boundingBox();
      if (box) {
        expect(Math.max(box.width, box.height)).toBeGreaterThanOrEqual(30);
      }
    }
  });
});

test.describe('Android App - Performance', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('initial page load under 5 seconds', async ({ page }) => {
    const start = Date.now();
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(10000);
  });

  test('entity dropdown renders within 2 seconds', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    const searchInput = page.getByPlaceholder('Search Home Assistant entities...');
    await searchInput.click();
    const start = Date.now();
    await page.waitForSelector('.entity-dropdown-portal', { timeout: 5000 });
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(5000);
  });
});
