import { test, expect, type Page } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';

// Wander's header on a phone: the title, the safety buttons and Refresh sit
// tidily and nothing runs off the right edge.
async function mockApi(page: Page) {
  await page.route((url: URL) => url.pathname.startsWith('/api/'), (route) => {
    const url = route.request().url();
    if (url.includes('/api/users/me')) return route.fallback();
    return route.fulfill({ json: {} });
  });
}

async function overflowing(page: Page) {
  return page.evaluate(() => {
    const limit = window.innerWidth;
    return [...document.querySelectorAll<HTMLElement>('main *')]
      // Map tiles reach past the map's edge; the map clips them.
      .filter((el) => !el.closest('.leaflet-container'))
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.right > limit + 0.5;
      })
      .map((el) => `${el.tagName.toLowerCase()}[${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30)}] right=${Math.round(el.getBoundingClientRect().right)} > ${limit}`);
  });
}

for (const width of [412, 360]) {
  test(`Wander's header fits a ${width}px phone`, async ({ page }, info) => {
    test.skip((info.project.use.viewport?.width ?? 1000) > 500, 'phone layout only');
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await seedAuth(page);
    await page.goto('/wander');
    await expect(page.getByTestId('family-safety').first()).toBeVisible();
    await page.waitForTimeout(800);
    expect(await overflowing(page)).toEqual([]);
    await page.screenshot({ path: `../../.tmp/wander-${width}.png`, fullPage: false });
  });
}
