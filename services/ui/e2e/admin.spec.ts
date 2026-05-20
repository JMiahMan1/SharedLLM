import { test, expect } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';

test.describe('Admin Page - System Matrix', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${UI_URL}/login`);
    await page.getByPlaceholder('Enter username').fill('default');
    await page.getByPlaceholder('Enter password').fill('admin');
    await page.getByRole('button', { name: /sign in/i }).click();
    await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2000);
  });

  test('loads admin page with all tabs', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');

    const tabs = [
      'Users & Devices',
      'Device Groups',
      'Telemetry',
      'Intercom',
      'Raven Ops',
      'LLM & Settings',
      'Database & Audit',
    ];
    for (const tab of tabs) {
      await expect(page.getByRole('button', { name: tab })).toBeVisible();
    }
  });

  test('Users & Devices tab - loads users from API', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Users & Devices' }).click();

    await expect(page.getByText('User Management')).toBeVisible();
    // Should show at least the default user
    await expect(page.getByText('@default', { exact: true })).toBeVisible({ timeout: 10000 });
  });

  test('Users & Devices tab - entity search dropdown loads entities', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Users & Devices' }).click();

    const searchInput = page.getByPlaceholder('Search Home Assistant entities...');
    await expect(searchInput).toBeVisible();
    // Click to open dropdown
    await searchInput.click();
    // Should show entities or "No entities found" after loading
    await page.waitForTimeout(3000);
    const dropdown = page.locator('.absolute.z-50.mt-1');
    await expect(dropdown).toBeVisible();
  });

  test('Users & Devices tab - discovery import loads', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Users & Devices' }).click();

    await expect(page.getByText('Discovery Import')).toBeVisible();
    // Should show refresh button
    await expect(page.getByLabel('Refresh discovered users')).toBeVisible();
  });

  test('Device Groups tab - media groups section loads', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Device Groups' }).click();

    await expect(page.getByText('Create Media Group')).toBeVisible();
    await expect(page.getByText('Media Groups')).toBeVisible();
  });

  test('Device Groups tab - entity multi-select for media groups', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Device Groups' }).click();

    const multiSelect = page.getByPlaceholder('Search and add media entities...');
    await expect(multiSelect).toBeVisible();
    // Click to open dropdown
    await multiSelect.click();
    await page.waitForTimeout(3000);
    // Should show dropdown with entities or message
    const dropdown = page.locator('.absolute.z-50.mt-1');
    await expect(dropdown).toBeVisible();
  });

  test('Device Groups tab - light clusters section loads', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Device Groups' }).click();
    await page.getByRole('button', { name: 'Lights' }).click();

    await expect(page.getByText('Create Light Cluster')).toBeVisible();
    await expect(page.getByText('Light Clusters')).toBeVisible();
  });

  test('Telemetry tab - loads enrollment section', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Telemetry' }).click();

    await expect(page.getByText('Enroll Device')).toBeVisible();
    await expect(page.getByText('Enrolled Devices')).toBeVisible();
  });

  test('Telemetry tab - entity search dropdown for enrollment', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Telemetry' }).click();

    const searchInput = page.getByPlaceholder('Search HA entities for telemetry...');
    await expect(searchInput).toBeVisible();
    await searchInput.click();
    await page.waitForTimeout(3000);
    const dropdown = page.locator('.absolute.z-50.mt-1');
    await expect(dropdown).toBeVisible();
  });

  test('Intercom tab - sessions section loads', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Intercom' }).click();

    await expect(page.getByText('Active Sessions')).toBeVisible();
  });

  test('Intercom tab - broadcast section with multi-select', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Intercom' }).click();
    await page.getByRole('button', { name: 'Broadcast' }).click();

    await expect(page.getByText('Broadcast Message')).toBeVisible();
    const multiSelect = page.getByPlaceholder('Search and add target entities...');
    await expect(multiSelect).toBeVisible();
  });

  test('Intercom tab - announce section with multi-select', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Intercom' }).click();
    await page.getByRole('button', { name: 'Announce' }).click();

    await expect(page.getByText('TV / Speaker Announcement')).toBeVisible();
    const multiSelect = page.getByPlaceholder('Search and add target devices...');
    await expect(multiSelect).toBeVisible();
  });

  test('Raven Ops tab - loads queue', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Raven Ops' }).click();

    await expect(page.getByText('Raven Mission Queue')).toBeVisible();
  });

  test('LLM & Settings tab - loads settings', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'LLM & Settings' }).click();

    await expect(page.getByText('LLM Configuration')).toBeVisible();
  });

  test('Database & Audit tab - loads stats and logs', async ({ page }) => {
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Database & Audit' }).click();

    await expect(page.getByText('RAG Statistics')).toBeVisible();
    await expect(page.getByText('Recent Logs')).toBeVisible();
  });
});
