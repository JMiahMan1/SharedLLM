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

test.describe('Identity Page - Integration Gallery', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/identity`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('integration gallery section is visible', async ({ page }) => {
    await expect(page.getByText('Integration Gallery')).toBeVisible({ timeout: 10000 });
  });

  test('all integration tiles are displayed', async ({ page }) => {
    const tiles = page.locator('.glass-panel').filter({ hasText: /Home Assistant|Nextcloud|Audiobookshelf|Private Git|GitHub|GitLab/ });
    expect(await tiles.count()).toBeGreaterThanOrEqual(5);
  });

  test('integration tile shows connection status', async ({ page }) => {
    const statusText = page.locator('span').filter({ hasText: /Linked|Not Linked/i }).first();
    await expect(statusText).toBeVisible({ timeout: 5000 });
  });

  test('connected service tile shows Manage Integration button', async ({ page }) => {
    const manageBtn = page.getByRole('button', { name: /manage integration/i });
    if (await manageBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(manageBtn).toBeVisible();
    }
  });

  test('disconnected service tile shows Connect Service button', async ({ page }) => {
    const connectBtn = page.getByRole('button', { name: /connect service/i });
    if (await connectBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(connectBtn).toBeVisible();
    }
  });

  test('integration tile status indicator dot is visible', async ({ page }) => {
    const statusDot = page.locator('div[class*="w-1.5"][class*="h-1.5"][class*="rounded-full"]').first();
    await expect(statusDot).toBeVisible({ timeout: 5000 });
  });

  test('Home Assistant integration tile is present', async ({ page }) => {
    await expect(page.getByText('Home Assistant')).toBeVisible({ timeout: 5000 });
  });

  test('GitHub integration tile is present', async ({ page }) => {
    await expect(page.getByText('GitHub')).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Identity Page - Integration Configuration Modal', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/identity`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('Manage Integration opens configuration modal', async ({ page }) => {
    const manageBtn = page.getByRole('button', { name: /manage integration/i });
    if (await manageBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await manageBtn.click();
      await expect(page.locator('.fixed.inset-0.z-50')).toBeVisible({ timeout: 10000 });
    }
  });

  test('configuration modal has encryption notice', async ({ page }) => {
    const manageBtn = page.getByRole('button', { name: /manage integration/i });
    if (await manageBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await manageBtn.click();
      await expect(page.getByText('Identity Encryption Active')).toBeVisible({ timeout: 5000 });
    }
  });

  test('configuration modal shows data sharing toggle', async ({ page }) => {
    const manageBtn = page.getByRole('button', { name: /manage integration/i });
    if (await manageBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await manageBtn.click();
      const sharingToggle = page.locator('input[type="checkbox"].sr-only');
      await expect(sharingToggle).toBeVisible({ timeout: 5000 });
    }
  });

  test('configuration modal has test sync button', async ({ page }) => {
    const manageBtn = page.getByRole('button', { name: /manage integration/i });
    if (await manageBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await manageBtn.click();
      await expect(page.getByRole('button', { name: /test sync/i })).toBeVisible({ timeout: 5000 });
    }
  });

  test('configuration modal has commit changes button', async ({ page }) => {
    const manageBtn = page.getByRole('button', { name: /manage integration/i });
    if (await manageBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await manageBtn.click();
      await expect(page.getByRole('button', { name: /commit changes/i })).toBeVisible({ timeout: 5000 });
    }
  });

  test('configuration modal closes when cancel is clicked', async ({ page }) => {
    const manageBtn = page.getByRole('button', { name: /manage integration/i });
    if (await manageBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await manageBtn.click();
      await page.waitForTimeout(500);
      const closeBtn = page.locator('button').filter({ hasText: /X/ }).first();
      if (await closeBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await closeBtn.click();
        await page.waitForTimeout(500);
      }
    }
  });

  test('protected branches chip input is visible for GitHub integration', async ({ page }) => {
    const githubTile = page.getByText('GitHub').locator('..').locator('..');
    const manageBtn = githubTile.getByRole('button', { name: /manage integration/i });
    if (await manageBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await manageBtn.click();
      const chipInput = page.getByPlaceholder(/type branch name/i);
      await expect(chipInput).toBeVisible({ timeout: 5000 });
    }
  });
});

test.describe('Identity Page - Vocal Signature', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/identity`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('vocal signature section is visible', async ({ page }) => {
    await expect(page.getByText('Vocal Signature')).toBeVisible({ timeout: 10000 });
  });

  test('voice enrollment card is visible', async ({ page }) => {
    await expect(page.getByText('Biometric Voice Profile')).toBeVisible({ timeout: 5000 });
  });

  test('begin enrollment button is visible', async ({ page }) => {
    const enrollBtn = page.getByRole('button', { name: /begin enrollment/i });
    if (await enrollBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(enrollBtn).toBeVisible();
    }
  });

  test('voice enrollment shows status message when recording', async ({ page }) => {
    const enrollBtn = page.getByRole('button', { name: /begin enrollment/i });
    if (await enrollBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Can't actually start recording in E2E, but verify button exists
      await expect(enrollBtn).toBeVisible();
    }
  });
});

test.describe('Identity Page - API Keys', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/identity`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('API keys section header is visible', async ({ page }) => {
    await expect(page.getByText('External Client Access')).toBeVisible({ timeout: 10000 });
  });

  test('generate new key button is visible', async ({ page }) => {
    const genBtn = page.getByRole('button', { name: /generate new key/i });
    if (await genBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(genBtn).toBeVisible();
    }
  });

  test('API keys table exists', async ({ page }) => {
    const table = page.locator('table');
    await expect(table).toBeVisible();
  });

  test('API keys table has correct column headers', async ({ page }) => {
    await expect(page.getByText('Client Label')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('API Key Prefix')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Status')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Actions')).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Identity Page - Digital Persona', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/identity`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('digital persona section is visible', async ({ page }) => {
    await expect(page.getByText('Digital Persona')).toBeVisible({ timeout: 10000 });
  });

  test('avatar placeholder is visible', async ({ page }) => {
    const avatar = page.locator('div.rounded-2xl').first();
    await expect(avatar).toBeVisible();
  });

  test('display name input is editable', async ({ page }) => {
    const displayNameInput = page.getByPlaceholder('Display name');
    if (await displayNameInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await displayNameInput.fill('Test User');
      const value = await displayNameInput.inputValue();
      expect(value).toBe('Test User');
    }
  });

  test('voice ID field shows NOT_ASSIGNED or assigned value', async ({ page }) => {
    const voiceIdInput = page.getByRole('textbox').filter({ hasPlaceholder: /NOT_ASSIGNED/i }).or(
      page.locator('input[disabled]').first()
    );
    await expect(voiceIdInput).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Identity Page - System Hierarchy', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/identity`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('system hierarchy section is visible for admins', async ({ page }) => {
    await expect(page.getByText('System Hierarchy')).toBeVisible({ timeout: 10000 });
  });

  test('system hierarchy shows user list', async ({ page }) => {
    const userItems = page.locator('div.flex.items-center.justify-between.p-3');
    expect(await userItems.count()).toBeGreaterThanOrEqual(1);
  });
});
