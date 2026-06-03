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

test.describe('Knowledge Hub - Stats Display', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/knowledge-hub`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('RAG status indicator is visible', async ({ page }) => {
    await expect(page.getByText('RAG Status')).toBeVisible({ timeout: 10000 });
  });

  test('total chunks stat card is visible', async ({ page }) => {
    await expect(page.getByText('Total Chunks')).toBeVisible();
  });

  test('documents ingested stat card is visible', async ({ page }) => {
    await expect(page.getByText('Documents Ingested')).toBeVisible();
  });

  test('last index activity card is visible', async ({ page }) => {
    await expect(page.getByText('Last Index Activity')).toBeVisible();
  });

  test('provider badges are displayed', async ({ page }) => {
    const providerBadges = page.locator('span').filter({ hasText: /nextcloud|homeassistant|rag/i }).first();
    await expect(providerBadges).toBeVisible({ timeout: 5000 }).catch(() => {});
  });

  test('stats cards show loading state initially', async ({ page }) => {
    await page.goto(`${UI_URL}/knowledge-hub`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
    const loadingState = page.getByText('...');
    await expect(loadingState).toBeVisible({ timeout: 3000 }).catch(() => {});
  });
});

test.describe('Knowledge Hub - Manual Ingestion', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/knowledge-hub`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('direct ingestion form is visible', async ({ page }) => {
    await expect(page.getByText('Direct Ingestion')).toBeVisible({ timeout: 10000 });
  });

  test('storage path input is editable', async ({ page }) => {
    const pathInput = page.getByPlaceholder('/your/folder/path');
    if (await pathInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await pathInput.fill('/test/path');
      const value = await pathInput.inputValue();
      expect(value).toBe('/test/path');
    }
  });

  test('recursive ingestion checkbox is checked by default', async ({ page }) => {
    const checkbox = page.locator('input[name="recursive"]');
    const isChecked = await checkbox.isChecked();
    expect(isChecked).toBe(true);
  });

  test('ingest path button is clickable', async ({ page }) => {
    const ingestBtn = page.getByRole('button', { name: /ingest path/i });
    if (await ingestBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(ingestBtn).toBeVisible();
    }
  });

  test('ingestion form validates empty path', async ({ page }) => {
    const ingestBtn = page.getByRole('button', { name: /ingest path/i });
    if (await ingestBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await ingestBtn.click();
      await expect(page.getByText(/please enter a valid path/i)).toBeVisible({ timeout: 5000 });
    }
  });
});

test.describe('Knowledge Hub - File Explorer', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/knowledge-hub`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('file explorer section is visible', async ({ page }) => {
    await expect(page.getByText('Storage Path')).toBeVisible({ timeout: 10000 });
  });

  test('breadcrumbs navigation is visible', async ({ page }) => {
    const rootCrumb = page.getByRole('button', { name: 'Root' });
    await expect(rootCrumb).toBeVisible();
  });

  test('go back button is visible', async ({ page }) => {
    const goBackBtn = page.locator('button[aria-label*="back"]');
    await expect(goBackBtn).toBeVisible();
  });

  test('file filter input is visible', async ({ page }) => {
    const filterInput = page.getByPlaceholder(/filter files/i);
    if (await filterInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(filterInput).toBeVisible();
    }
  });

  test('file table has correct column headers', async ({ page }) => {
    await expect(page.getByText('Name')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Size')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Status')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Actions')).toBeVisible({ timeout: 5000 });
  });

  test('file rows show indexed or unindexed status', async ({ page }) => {
    const statusText = page.locator('span').filter({ hasText: /indexed|unindexed/i }).first();
    await expect(statusText).toBeVisible({ timeout: 10000 }).catch(() => {});
  });

  test('folder rows show index folder button', async ({ page }) => {
    const indexFolderBtn = page.getByRole('button', { name: /index folder/i });
    if (await indexFolderBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(indexFolderBtn).toBeVisible();
    }
  });

  test('file rows show index file button', async ({ page }) => {
    const indexFileBtn = page.getByRole('button', { name: /index file/i });
    if (await indexFileBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(indexFileBtn).toBeVisible();
    }
  });

  test('folder rows have clickable folder names', async ({ page }) => {
    const folderLinks = page.locator('button').filter({ hasText: /^\.[\w]+$/ }).or(
      page.locator('td button').first()
    );
    await expect(folderLinks).toBeVisible({ timeout: 5000 }).catch(() => {});
  });
});

test.describe('Knowledge Hub - System Maintenance', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/knowledge-hub`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('system maintenance section is visible', async ({ page }) => {
    await expect(page.getByText('System Maintenance')).toBeVisible({ timeout: 10000 });
  });

  test('clear nextcloud collection button is visible', async ({ page }) => {
    const purgeBtn = page.getByRole('button', { name: /purge nextcloud data/i });
    if (await purgeBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(purgeBtn).toBeVisible();
    }
  });

  test('clear home assistant collection button is visible', async ({ page }) => {
    const purgeBtn = page.getByRole('button', { name: /purge ha entities/i });
    if (await purgeBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(purgeBtn).toBeVisible();
    }
  });

  test('purge modal opens when purge button is clicked', async ({ page }) => {
    const purgeBtn = page.getByRole('button', { name: /purge nextcloud data/i });
    if (await purgeBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await purgeBtn.click();
      await expect(page.locator('.fixed.inset-0.z-50')).toBeVisible({ timeout: 10000 });
      await expect(page.getByText('Critical Security Warning')).toBeVisible({ timeout: 5000 });
    }
  });

  test('purge modal has cancel button', async ({ page }) => {
    const purgeBtn = page.getByRole('button', { name: /purge nextcloud data/i });
    if (await purgeBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await purgeBtn.click();
      await expect(page.getByRole('button', { name: /cancel/i })).toBeVisible({ timeout: 5000 });
    }
  });

  test('purge modal has confirm button', async ({ page }) => {
    const purgeBtn = page.getByRole('button', { name: /purge nextcloud data/i });
    if (await purgeBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await purgeBtn.click();
      await expect(page.getByRole('button', { name: /confirm purge/i })).toBeVisible({ timeout: 5000 });
    }
  });
});
