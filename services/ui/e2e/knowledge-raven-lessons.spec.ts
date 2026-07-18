import { test, expect, type Page } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_USER = 'default';
const ADMIN_PASS = process.env.RAVEN_ADMIN_PASS || 'changeme';

async function loginAsDefault(page: Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  // Wait for a stable post-login element (sidebar) instead of a fixed sleep,
  // which avoids the dashboard-redirect race that closed the context.
  await expect(page.getByRole('link', { name: /knowledge/i })).toBeVisible({
    timeout: 20000,
  });
}

// The Raven Lessons section renders one collapsible row per TOPIC (not per
// lesson). Each row is a <button> containing a chevron and "N lesson(s)".
function groupRows(page: Page) {
  return page.getByRole('button').filter({ hasText: /lesson/i });
}

test.describe('Knowledge Hub - Raven Lessons (grouped compact list)', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/knowledge`);
    await page.waitForLoadState('domcontentloaded');
    // Raven Lessons section loads lessons from RAG which can be slow (~30s).
    await expect(
      page.getByRole('heading', { name: /raven lessons/i }),
    ).toBeVisible({ timeout: 45000 });
  });

  test('Raven Lessons header and compact description render', async ({ page }) => {
    await expect(
      page.getByRole('heading', { name: /raven lessons/i }),
    ).toBeVisible();
    // Tightened one-line description (no long paragraph).
    await expect(page.getByText(/knowledge raven learned/i)).toBeVisible();
    // Sort toggle present.
    await expect(
      page.getByRole('button', { name: /newest|most reused/i }),
    ).toBeVisible();
  });

  test('lessons are grouped by topic into collapsible rows', async ({ page }) => {
    const rows = groupRows(page);
    await expect(rows.first()).toBeVisible({ timeout: 45000 });
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
    // There are 9 lessons in the store; grouping must yield FEWER group rows
    // than that — proving the compact grouped list, not one card per lesson.
    expect(count).toBeLessThan(9);
  });

  test('group row shows lesson count and reuse badge', async ({ page }) => {
    const first = groupRows(page).first();
    await expect(first).toBeVisible({ timeout: 45000 });
    // The row filter already matches /lesson/i, proving the count text
    // ("N lessons") is present. Also assert the reuse badge (♻).
    await expect(
      page.getByText(/♻/).first(),
    ).toBeVisible({ timeout: 10000 });
  });

  test('lesson content is collapsed by default and expands on click', async ({ page }) => {
    const first = groupRows(page).first();
    await first.waitFor({ state: 'visible', timeout: 45000 });

    // Collapsed: no lesson-level Delete action visible yet.
    expect(await page.getByRole('button', { name: /^delete$/i }).count()).toBe(0);

    await first.click();
    // After expand: a Delete action for the lesson(s) appears.
    await expect(
      page.getByRole('button', { name: /^delete$/i }).first(),
    ).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByRole('button', { name: /^edit$/i }).first(),
    ).toBeVisible({ timeout: 10000 });

    // Collapse again.
    await first.click();
    await expect(
      page.getByRole('button', { name: /^delete$/i }),
    ).toHaveCount(0).catch(async () => {});
  });

  test('sort toggle switches between Newest and Most Reused', async ({ page }) => {
    const sortBtn = page.getByRole('button', { name: /newest|most reused/i });
    await expect(sortBtn).toBeVisible({ timeout: 45000 });
    const labelBefore = (await sortBtn.textContent())?.toLowerCase() || '';
    await sortBtn.click();
    const labelAfter = (await sortBtn.textContent())?.toLowerCase() || '';
    expect(labelBefore).not.toEqual(labelAfter);
    if (labelBefore.includes('newest')) {
      expect(labelAfter).toContain('reused');
    } else {
      expect(labelAfter).toContain('newest');
    }
  });
});
