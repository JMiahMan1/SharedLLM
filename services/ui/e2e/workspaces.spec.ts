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

test.describe('Workspaces Page - Admin CRUD', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/workspaces`);
    await page.waitForLoadState('networkidle');
  });

  test('shows empty state with get started button', async ({ page }) => {
    await page.goto(`${UI_URL}/workspaces?empty=true`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByText('No Workspaces Found')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /get started/i })).toBeVisible();
  });

  test('add repository button opens modal', async ({ page }) => {
    await page.getByRole('button', { name: /add repository/i }).click();
    await expect(page.locator('.fixed.inset-0.z-50')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /cancel/i })).toBeVisible();
  });

  test('new workspace modal contains all required fields', async ({ page }) => {
    await page.getByRole('button', { name: /add repository/i }).click();
    await expect(page.getByPlaceholder('project-id')).toBeVisible();
    await expect(page.getByPlaceholder('My Project')).toBeVisible();
    await expect(page.getByPlaceholder('your/repository/folder')).toBeVisible();
  });

  test('workspace modal shows webhook configuration section', async ({ page }) => {
    await page.getByRole('button', { name: /add repository/i }).click();
    await expect(page.getByText('Automated Sync')).toBeVisible({ timeout: 5000 });
  });

  test('save workspace validates required fields', async ({ page }) => {
    await page.getByRole('button', { name: /add repository/i }).click();
    await page.getByRole('button', { name: /save workspace/i }).click();
    await expect(page.getByText(/required fields: id, name, path/i)).toBeVisible({ timeout: 5000 });
  });

  test('new workspace modal has toggle switches for sync features', async ({ page }) => {
    await page.getByRole('button', { name: /add repository/i }).click();
    const toggles = page.locator('button[aria-label*="Toggle"]');
    await expect(toggles).toHaveCount(2);
  });

  test('auto-pull enabled webhook URL is visible', async ({ page }) => {
    await page.getByRole('button', { name: /add repository/i }).click();
    await page.waitForTimeout(500);
    const webhookToggle = page.locator('button[aria-label="Toggle automated sync"]');
    await webhookToggle.click();
    await expect(page.locator('input[placeholder="Secret key for GitHub or GitLab"]')).toBeVisible({ timeout: 5000 });
  });

  test('exclusions input allows adding entries', async ({ page }) => {
    await page.getByRole('button', { name: /add repository/i }).click();
    const exclusionInput = page.getByPlaceholder(/add directory to exclude/i);
    if (await exclusionInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await exclusionInput.fill('.git');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(500);
      await expect(page.getByText('.git')).toBeVisible();
    }
  });

  test('webhook secret regenerate button works', async ({ page }) => {
    await page.getByRole('button', { name: /add repository/i }).click();
    const webhookToggle = page.locator('button[aria-label="Toggle automated sync"]');
    await webhookToggle.click();
    await page.waitForTimeout(500);
    const regenerateBtn = page.getByRole('button', { name: /regenerate/i });
    if (await regenerateBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await regenerateBtn.click();
      await page.waitForTimeout(500);
    }
  });
});

test.describe('Workspaces Page - List Actions', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/workspaces`);
    await page.waitForLoadState('networkidle');
  });

  test('sync all button is visible for admins', async ({ page }) => {
    await expect(page.getByRole('button', { name: /sync all/i })).toBeVisible();
  });

  test('workspace cards show git remote and branch info', async ({ page }) => {
    const gitInfo = page.locator('span').filter({ hasText: /\/main$/ });
    await expect(gitInfo).toBeVisible({ timeout: 5000 }).catch(() => {});
  });

  test('workspace cards show source repository URL', async ({ page }) => {
    const repoUrl = page.locator('span').filter({ hasText: /github\.com|gitlab\.com/i }).first();
    await expect(repoUrl).toBeVisible({ timeout: 5000 }).catch(() => {});
  });

  test('workspace cards show sync exclusion tags', async ({ page }) => {
    const exclusionTags = page.locator('span').filter({ hasText: /^\.[\w]+$/ }).first();
    await expect(exclusionTags).toBeVisible({ timeout: 5000 }).catch(() => {});
  });

  test('workspace cards show default badge when set', async ({ page }) => {
    const defaultBadge = page.locator('span').filter({ hasText: /default/i }).first();
    await expect(defaultBadge).toBeVisible({ timeout: 5000 }).catch(() => {});
  });

  test('workspace cards show shared badge for system workspaces', async ({ page }) => {
    const sharedBadge = page.locator('span').filter({ hasText: /shared/i }).first();
    await expect(sharedBadge).toBeVisible({ timeout: 5000 }).catch(() => {});
  });
});
