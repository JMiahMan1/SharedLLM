import { test, expect, type Page } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_USER = 'default';
const ADMIN_PASS = 'changeme';

// 1x1 transparent PNG
const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

async function loginAsAdmin(page: Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

async function openIdeFor(page: Page, index = 0) {
  await loginAsAdmin(page);
  await page.goto(`${UI_URL}/workspaces`);
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(4000);
  const btn = page.getByTitle('Open workspace files (IDE)').nth(index);
  await expect(btn).toBeVisible({ timeout: 15000 });
  await btn.click();
  const modal = page.locator('div.fixed.inset-0.z-50').last();
  await expect(modal).toBeVisible({ timeout: 15000 });
  return modal;
}

async function openIdeForName(page: Page, name: string): Promise<Page | null> {
  await page.goto(`${UI_URL}/workspaces`);
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(4000);
  const btns = page.getByTitle('Open workspace files (IDE)');
  const n = await btns.count();
  for (let i = 0; i < n; i++) {
    await btns.nth(i).click();
    const m = page.locator('div.fixed.inset-0.z-50').last();
    await expect(m).toBeVisible({ timeout: 15000 });
    const title = await m.locator('span.font-semibold').first().textContent();
    if (title && title.includes(name)) return m as unknown as Page;
    await m.getByLabel('Close').click().catch(() => {});
    await page.waitForTimeout(300);
  }
  return null;
}

test.describe('Workspace IDE', () => {
  test('opens: activity bar + Source Control / Tools / Raven Chat panels all render', { timeout: 90000 }, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));

    const modal = await openIdeFor(page);
    await expect(modal.getByText('No file open')).toBeVisible({ timeout: 10000 });

    // Activity bar has 4 views
    await expect(modal.getByTitle('Explorer')).toBeVisible();
    await expect(modal.getByTitle('Source Control')).toBeVisible();
    await expect(modal.getByTitle('Tools')).toBeVisible();
    await expect(modal.getByTitle('Raven Chat')).toBeVisible();

    // Source Control
    await modal.getByTitle('Source Control').click();
    await expect(modal.getByRole('button', { name: /commit/i })).toBeVisible();
    await expect(modal.getByRole('button', { name: /push/i })).toBeVisible();
    await expect(modal.getByRole('button', { name: /diff/i })).toBeVisible();

    // Tools
    await modal.getByTitle('Tools').click();
    await expect(modal.getByRole('button', { name: /lint/i })).toBeVisible();
    await expect(modal.getByRole('button', { name: /sync to nextcloud/i })).toBeVisible();

    // Raven Chat — dispatches real missions
    await modal.getByTitle('Raven Chat').click();
    await expect(modal.getByText('Raven Chat')).toBeVisible();
    await expect(modal.getByPlaceholder(/describe a task/i)).toBeVisible();
    await expect(modal.getByTitle('Dispatch Raven mission')).toBeVisible();

    expect(errors).toEqual([]);
  });

  test('file explorer lists files (no user-context error) and opens a file in the editor', { timeout: 90000 }, async ({ page }) => {
    // Try each workspace until one exposes file entries
    let opened = false;
    const ideButtons = 6;
    for (let i = 0; i < ideButtons; i++) {
      const modal = await openIdeFor(page, i);
      const entries = modal.locator('div.cursor-pointer');
      // wait briefly for listing
      await page.waitForTimeout(1500);
      const count = await entries.count();
      if (count > 0) {
        for (let j = 0; j < Math.min(count, 12); j++) {
          await entries.nth(j).click();
          await page.waitForTimeout(700);
          if (await modal.locator('.monaco-editor').isVisible().catch(() => false)) {
            opened = true;
            break;
          }
        }
      }
      // close for next attempt
      await modal.getByLabel('Close').click().catch(() => {});
      await page.waitForTimeout(500);
      if (opened) break;
    }
    expect(opened).toBe(true);
  });

  test('image preview pane renders and Stable Diffusion panel is present', { timeout: 120000 }, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));

    await loginAsAdmin(page);

    // Discover a writable workspace (retry: /api/workspaces is intermittently 500)
    let wsData: { workspaces?: Array<{ id: string; display_name: string; capabilities?: string[] }> } = {};
    for (let attempt = 0; attempt < 5; attempt++) {
      const r = await page.request.get(`${UI_URL}/api/workspaces`);
      if (r.ok()) {
        wsData = await r.json();
        if (wsData.workspaces && wsData.workspaces.length) break;
      }
      await page.waitForTimeout(1500);
    }
    const workspaces = wsData.workspaces || [];
    const target = workspaces.find((w) => (w.capabilities || []).includes('write')) || workspaces[0];
    expect(target).toBeTruthy();

    const fileName = `preview_test_${Date.now()}.png`;
    const writeResp = await page.request.post(`${UI_URL}/api/workspaces/files/write`, {
      data: { workspace_id: target.id, relative_path: fileName, content_base64: PNG_B64, create_parents: true },
    });
    expect(writeResp.ok()).toBe(true);

    const modal = await openIdeForName(page, target.display_name);
    expect(modal).not.toBeNull();
    const m = modal as unknown as Page;

    // Refresh explorer so the new image appears, then open it
    await m.getByTitle('Refresh').click();
    await page.waitForTimeout(1500);
    const fileEntry = m.locator('div.cursor-pointer', { hasText: fileName });
    await expect(fileEntry).toBeVisible({ timeout: 10000 });
    await fileEntry.click();
    await page.waitForTimeout(1500);

    // Preview image + SD task panel
    await expect(m.locator('img').first()).toBeVisible({ timeout: 10000 });
    await expect(m.getByText('Stable Diffusion')).toBeVisible();
    await expect(m.getByRole('button', { name: /generate \(txt2img\)/i })).toBeVisible();

    // Cleanup
    await page.request
      .post(`${UI_URL}/api/workspaces/files/delete`, {
        data: { workspace_id: target.id, relative_path: fileName },
      })
      .catch(() => {});

    expect(errors).toEqual([]);
  });
});
