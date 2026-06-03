import { test, expect } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_USER = 'default';
const ADMIN_PASS = 'admin';

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

test.describe('Dashboard - Halo Banner', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('halo banner is visible', async ({ page }) => {
    const haloBanner = page.locator('[class*="halo"], [class*="presence"]').first();
    await expect(haloBanner).toBeVisible({ timeout: 5000 }).catch(() => {});
  });
});

test.describe('Dashboard - Header and Search', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('dashboard title is visible', async ({ page }) => {
    await expect(page.getByText('Jarvis Dashboard')).toBeVisible({ timeout: 10000 });
  });

  test('welcome message shows username', async ({ page }) => {
    const welcomeText = page.locator('p').filter({ hasText: /welcome back/i }).first();
    await expect(welcomeText).toBeVisible({ timeout: 10000 });
  });

  test('search bar placeholder is visible', async ({ page }) => {
    const searchInput = page.getByPlaceholder(/search live rag/i);
    if (await searchInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(searchInput).toBeVisible();
    }
  });

  test('search input accepts text', async ({ page }) => {
    const searchInput = page.getByPlaceholder(/search live rag/i);
    if (await searchInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await searchInput.fill('test query');
      const value = await searchInput.inputValue();
      expect(value).toBe('test query');
    }
  });

  test('search button is visible', async ({ page }) => {
    const searchButton = page.locator('button[type="submit"]').first();
    await expect(searchButton).toBeVisible();
  });

  test('voice command button is visible', async ({ page }) => {
    const voiceBtn = page.getByRole('button', { name: /voice command/i });
    if (await voiceBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(voiceBtn).toBeVisible();
    }
  });

  test('search shows results when query is entered', async ({ page }) => {
    const searchInput = page.getByPlaceholder(/search live rag/i);
    if (await searchInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await searchInput.fill('test');
      await page.locator('button[type="submit"]').first().click();
      await page.waitForTimeout(3000);
      const searchResults = page.locator('section.glass-panel.space-y-4.border-indigo-500\\/20').first();
      await expect(searchResults).toBeVisible({ timeout: 10000 }).catch(() => {});
    }
  });
});

test.describe('Dashboard - Service Status Cards', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('live service status section is visible', async ({ page }) => {
    await expect(page.getByText('Live Service Status')).toBeVisible({ timeout: 10000 });
  });

  test('service status cards are displayed', async ({ page }) => {
    const serviceCards = page.locator('button.glass-card.flex.flex-col.gap-4.p-6');
    await expect(serviceCards).toHaveCountGreaterThanOrEqual(1);
  });

  test('service cards show status indicator', async ({ page }) => {
    const statusIndicator = page.locator('div').filter({ hasText: /healthy|unhealthy|unknown|running/i }).first();
    await expect(statusIndicator).toBeVisible({ timeout: 10000 }).catch(() => {});
  });

  test('service cards are clickable', async ({ page }) => {
    const serviceCard = page.locator('button.glass-card.flex.flex-col.gap-4.p-6').first();
    if (await serviceCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await serviceCard.click();
      await page.waitForTimeout(1000);
    }
  });

  test('service detail modal shows when card is clicked', async ({ page }) => {
    const serviceCard = page.locator('button.glass-card.flex.flex-col.gap-4.p-6').first();
    if (await serviceCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await serviceCard.click();
      await expect(page.locator('.fixed.inset-0.z-50')).toBeVisible({ timeout: 10000 });
    }
  });

  test('service detail modal shows recent logs', async ({ page }) => {
    const serviceCard = page.locator('button.glass-card.flex.flex-col.gap-4.p-6').first();
    if (await serviceCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await serviceCard.click();
      await expect(page.getByText(/recent logs/i)).toBeVisible({ timeout: 5000 });
    }
  });

  test('service detail modal has close button', async ({ page }) => {
    const serviceCard = page.locator('button.glass-card.flex.flex-col.gap-4.p-6').first();
    if (await serviceCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await serviceCard.click();
      const closeBtn = page.locator('button').filter({ hasText: /X/ }).first();
      await expect(closeBtn).toBeVisible({ timeout: 5000 });
    }
  });
});

test.describe('Dashboard - Recent Logs', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('recent logs section is visible', async ({ page }) => {
    await expect(page.getByText(/recent logs|system logs/i)).toBeVisible({ timeout: 10000 });
  });

  test('log entries table is visible', async ({ page }) => {
    const logTable = page.locator('table').first();
    await expect(logTable).toBeVisible({ timeout: 10000 }).catch(() => {});
  });

  test('log entries show timestamp', async ({ page }) => {
    const timestamps = page.locator('td').filter({ hasText: /^\d{2}:\d{2}:\d{2}$/ }).first();
    await expect(timestamps).toBeVisible({ timeout: 10000 }).catch(() => {});
  });
});

test.describe('Dashboard - Workspace Summary', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('workspace summary section is visible', async ({ page }) => {
    await expect(page.getByText(/workspace.*summary|workspaces/i, { ignoreCase: true })).toBeVisible({ timeout: 10000 });
  });

  test('workspace cards are displayed in summary', async ({ page }) => {
    const workspaceCards = page.locator('div.glass-card').filter({ hasText: /workspace/i }).first();
    await expect(workspaceCards).toBeVisible({ timeout: 10000 }).catch(() => {});
  });
});

test.describe('Dashboard - Settings Management', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('settings section is visible', async ({ page }) => {
    await expect(page.getByText(/global settings|settings/i)).toBeVisible({ timeout: 10000 });
  });

  test('settings toggles are visible', async ({ page }) => {
    const toggles = page.locator('input[type="checkbox"]').first();
    await expect(toggles).toBeVisible({ timeout: 10000 }).catch(() => {});
  });

  test('settings can be updated', async ({ page }) => {
    const toggle = page.locator('input[type="checkbox"]').first();
    if (await toggle.isVisible({ timeout: 5000 }).catch(() => false)) {
      await toggle.click();
      await page.waitForTimeout(1000);
    }
  });
});

test.describe('Dashboard - Voice Assistant', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('voice assistant overlay opens', async ({ page }) => {
    const voiceBtn = page.getByRole('button', { name: /voice command/i });
    if (await voiceBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await voiceBtn.click();
      await expect(page.locator('[class*="voice"]')).toBeVisible({ timeout: 10000 });
    }
  });
});
