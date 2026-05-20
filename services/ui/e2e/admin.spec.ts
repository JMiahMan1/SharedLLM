import { test, expect } from '@playwright/test';

test.describe('Admin Page - System Matrix', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.getByPlaceholder('Username').fill('admin');
    await page.getByPlaceholder('Password').fill('admin');
    await page.getByRole('button', { name: /sign in/i }).click();
    await page.waitForURL('**/dashboard');
    await page.goto('/admin');
    await page.waitForLoadState('networkidle');
  });

  test('loads all tabs', async ({ page }) => {
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

  test('Users & Devices tab - loads users list', async ({ page }) => {
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    await expect(page.getByText('User Management')).toBeVisible();
    await expect(page.getByText('Discovery Import')).toBeVisible();
    await expect(page.getByText('Device Assignments')).toBeVisible();
  });

  test('Users & Devices tab - entity search dropdown appears', async ({ page }) => {
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    const searchInput = page.getByPlaceholder('Search Home Assistant entities...');
    await expect(searchInput).toBeVisible();
    await searchInput.click();
    await expect(page.getByText('No entities found').or(page.locator('ul > li').first())).toBeVisible({ timeout: 10000 });
  });

  test('Device Groups tab - media groups section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Device Groups' }).click();
    await expect(page.getByText('Create Media Group')).toBeVisible();
    await expect(page.getByText('Media Groups')).toBeVisible();
  });

  test('Device Groups tab - entity multi-select for media groups', async ({ page }) => {
    await page.getByRole('button', { name: 'Device Groups' }).click();
    const multiSelect = page.getByPlaceholder('Search and add media entities...');
    await expect(multiSelect).toBeVisible();
    await multiSelect.click();
    await expect(page.locator('ul > li').first()).toBeVisible({ timeout: 10000 });
  });

  test('Device Groups tab - light clusters section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Device Groups' }).click();
    await page.getByRole('button', { name: 'Lights' }).click();
    await expect(page.getByText('Create Light Cluster')).toBeVisible();
    await expect(page.getByText('Light Clusters')).toBeVisible();
  });

  test('Telemetry tab - loads enrollment section', async ({ page }) => {
    await page.getByRole('button', { name: 'Telemetry' }).click();
    await expect(page.getByText('Enroll Device')).toBeVisible();
    await expect(page.getByText('Enrolled Devices')).toBeVisible();
  });

  test('Telemetry tab - entity search dropdown for enrollment', async ({ page }) => {
    await page.getByRole('button', { name: 'Telemetry' }).click();
    const searchInput = page.getByPlaceholder('Search HA entities for telemetry...');
    await expect(searchInput).toBeVisible();
    await searchInput.click();
    await expect(page.getByText('No entities found').or(page.locator('ul > li').first())).toBeVisible({ timeout: 10000 });
  });

  test('Intercom tab - sessions section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Intercom' }).click();
    await expect(page.getByText('Active Sessions')).toBeVisible();
  });

  test('Intercom tab - broadcast section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Intercom' }).click();
    await page.getByRole('button', { name: 'Broadcast' }).click();
    await expect(page.getByText('Broadcast Message')).toBeVisible();
  });

  test('Intercom tab - broadcast entity multi-select', async ({ page }) => {
    await page.getByRole('button', { name: 'Intercom' }).click();
    await page.getByRole('button', { name: 'Broadcast' }).click();
    const multiSelect = page.getByPlaceholder('Search and add target entities...');
    await expect(multiSelect).toBeVisible();
  });

  test('Intercom tab - announce section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Intercom' }).click();
    await page.getByRole('button', { name: 'Announce' }).click();
    await expect(page.getByText('TV / Speaker Announcement')).toBeVisible();
  });

  test('Intercom tab - announce entity multi-select', async ({ page }) => {
    await page.getByRole('button', { name: 'Intercom' }).click();
    await page.getByRole('button', { name: 'Announce' }).click();
    const multiSelect = page.getByPlaceholder('Search and add target devices...');
    await expect(multiSelect).toBeVisible();
  });

  test('Raven Ops tab - loads queue', async ({ page }) => {
    await page.getByRole('button', { name: 'Raven Ops' }).click();
    await expect(page.getByText('Raven Mission Queue')).toBeVisible();
  });

  test('LLM & Settings tab - loads settings', async ({ page }) => {
    await page.getByRole('button', { name: 'LLM & Settings' }).click();
    await expect(page.getByText('LLM Configuration')).toBeVisible();
  });

  test('Database & Audit tab - loads stats and logs', async ({ page }) => {
    await page.getByRole('button', { name: 'Database & Audit' }).click();
    await expect(page.getByText('RAG Statistics')).toBeVisible();
    await expect(page.getByText('Recent Logs')).toBeVisible();
  });
});
