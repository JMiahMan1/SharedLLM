import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockBible } from '../fixtures/bibleMocks';

/**
 * The Bible page's six section tabs (Read, Today, Play, Memorize, Studies,
 * Progress) must all be in view with their whole name on every screen: no
 * label cut off with an ellipsis, no tab past the edge, no sideways scroll of
 * the page, and every tab tall enough for a thumb. Checked at each common
 * phone width (down to 320 px), tablet widths and desktop, with normal text
 * and with a large system font (Android's font size setting scales the root).
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const TABS = ['read', 'today', 'play', 'memorize', 'studies', 'progress'] as const;
const FULL_NAMES = ['Read', 'Today', 'Play', 'Memorize', 'Studies', 'Progress'];

/** Widths each project checks besides its own: phones go down to 320, tablets both ways round. */
function widthsFor(projectWidth: number): number[] {
  if (projectWidth < 600) return [320, 360, 375, 390, 412];
  if (projectWidth < 1300) return [768, 1024];
  return [1024, 1440];
}

interface TabReport {
  id: string;
  label: string;
  aria: string | null;
  truncated: boolean;
  left: number;
  right: number;
  top: number;
  bottom: number;
  height: number;
}

async function measure(page: Page): Promise<{ tabs: TabReport[]; docWidth: number; innerWidth: number }> {
  return page.evaluate((ids) => {
    const tabs = ids.map((id) => {
      const el = document.querySelector<HTMLElement>(`[data-testid="bible-tab-${id}"]`);
      if (!el) throw new Error(`no tab ${id}`);
      const b = el.getBoundingClientRect();
      const spans = Array.from(el.querySelectorAll<HTMLElement>('span')).filter((s) => s.offsetParent !== null && s.getClientRects().length > 0);
      const truncated = spans.some((s) => s.scrollWidth > s.clientWidth + 0.5 || s.scrollHeight > s.clientHeight + 1);
      return {
        id,
        label: spans.map((s) => s.innerText.replace(/\s+/g, ' ').trim()).join(' ').trim(),
        aria: el.getAttribute('aria-label'),
        truncated,
        left: b.left,
        right: b.right,
        top: b.top,
        bottom: b.bottom,
        height: b.height,
      };
    });
    return { tabs, docWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth };
  }, [...TABS]);
}

for (const [label, scale] of [['normal text', 1], ['large system font', 1.3]] as const) {
  test(`every Bible section tab fits with its full name, ${label}`, async ({ page }) => {
    test.setTimeout(90_000);
    await mockHome(page, 'admin');
    await mockBible(page);
    if (scale !== 1) {
      await page.addInitScript((s) => {
        document.addEventListener('DOMContentLoaded', () => { document.documentElement.style.fontSize = `${s * 100}%`; });
      }, scale);
    }
    const own = page.viewportSize() ?? { width: 1440, height: 900 };
    await page.goto('/bible?ref=Genesis+1');
    await expect(page.getByTestId('bible-tab-progress')).toBeVisible();

    const problems: string[] = [];
    for (const width of [own.width, ...widthsFor(own.width)]) {
      await page.setViewportSize({ width, height: own.height });
      await page.waitForTimeout(150);
      const { tabs, docWidth, innerWidth } = await measure(page);
      if (docWidth > innerWidth + 1) problems.push(`${width}px: the page scrolls sideways (${docWidth} > ${innerWidth})`);
      tabs.forEach((t, i) => {
        const where = `${width}px ${t.id}`;
        if (t.truncated) problems.push(`${where}: label "${t.label}" is cut off`);
        // The full name is either the visible label or, for a short label, the tab's accessible name.
        const name = FULL_NAMES[i];
        if (t.label !== name && t.aria !== name) problems.push(`${where}: shows "${t.label}" with no full name "${name}"`);
        if (t.left < -0.5 || t.right > innerWidth + 0.5) problems.push(`${where}: past the edge (${t.left}..${t.right} of ${innerWidth})`);
        if (t.height < 44) problems.push(`${where}: only ${t.height}px tall`);
      });
      // Six tabs side by side, never wrapped onto a second row of tabs.
      const tops = new Set(tabs.map((t) => Math.round(t.top)));
      if (tops.size !== 1) problems.push(`${width}px: tabs are on ${tops.size} rows`);
    }
    expect(problems).toEqual([]);

    // Each tab still works by its full name.
    for (const name of FULL_NAMES) {
      await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
    }
  });
}
