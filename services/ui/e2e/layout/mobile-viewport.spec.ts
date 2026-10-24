import { test, expect, type Page } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';

// The phone app is one fixed shell: the header at the top, the bottom bar at
// the bottom, and only <main> scrolls. If the document itself can scroll, a
// drag slides the whole app under the status bar and the navigation buttons.
// Pages that render on a generic empty mock (Health and Settings need real
// fixtures; the shell is the same component on every page).
const PAGES = ['/', '/family', '/notes', '/media', '/calendar', '/bible', '/recipes', '/wander'];

async function documentDrift(page: Page) {
  return page.evaluate(() => {
    window.scrollTo(400, 4000);
    const moved = { x: window.scrollX, y: window.scrollY };
    window.scrollTo(0, 0);
    const shell = document.querySelector('[data-testid="mobile-shell"]')?.getBoundingClientRect();
    return {
      moved,
      locked: getComputedStyle(document.body).overflow === 'hidden',
      shell: shell ? { top: shell.top, height: Math.round(shell.height), width: Math.round(shell.width) } : null,
      viewport: { height: window.innerHeight, width: window.innerWidth },
    };
  });
}

test.describe('mobile shell fits the screen', () => {
  for (const path of PAGES) {
    test(`${path} cannot be dragged off the screen`, async ({ page }, info) => {
      test.skip(info.project.name !== 'hermetic-mobile', 'phone layout only');
      await page.route('**/api/**', (route) => {
        const url = route.request().url();
        if (url.includes('/api/users/me')) return route.fallback();
        return route.fulfill({ json: {} });
      });
      await seedAuth(page);
      await page.goto(path);
      await page.waitForTimeout(1500);

      const drift = await documentDrift(page);
      expect(drift.moved, `document scrolled on ${path}`).toEqual({ x: 0, y: 0 });
      expect(drift.locked).toBe(true);
      expect(drift.shell).toEqual({ top: 0, ...drift.viewport });
    });
  }
});
