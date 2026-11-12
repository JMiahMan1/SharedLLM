import { expect, type Locator, type Page, type TestInfo } from '@playwright/test';

/**
 * Layout checks every home page runs in every project (desktop, phones,
 * tablets): nothing wider than the screen, primary controls a thumb can hit,
 * nothing hidden under the bottom bar, dialogs that fit, and a screenshot.
 */

export const MIN_TARGET = 44;

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const o = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, inner: window.innerWidth }));
  expect(o.doc, 'the page is wider than the screen').toBeLessThanOrEqual(o.inner + 1);
}

/** Every visible button and link under `scope` is at least 44px tall. */
export async function expectTapTargets(scope: Locator, min = MIN_TARGET): Promise<void> {
  const small = await scope.evaluateAll((els, m) => els.flatMap((root) =>
    Array.from(root.querySelectorAll<HTMLElement>('button, a[href], select, input:not([type="hidden"])')).flatMap((el) => {
      const b = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (!b.width || !b.height || style.visibility === 'hidden') return [];
      // A ::before hit area (the shared Toggle) widens the target past its box.
      const before = getComputedStyle(el, '::before');
      const extra = before.content !== 'none' && before.position === 'absolute' ? 20 : 0;
      return b.height + extra + 0.5 < m
        ? [`${(el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 30)} (${Math.round(b.height)}px)`]
        : [];
    })), min);
  expect(small, `controls shorter than 44px: ${small.join('; ')}`).toEqual([]);
}

/** Whether this project shows the phone shell (bottom bar) rather than the sidebar. */
export async function isPhoneShell(page: Page): Promise<boolean> {
  return (await page.getByRole('button', { name: 'More pages' }).count()) > 0;
}

/**
 * With the page scrolled to the end, its last content still ends above the
 * bottom bar, so nothing is stuck underneath it.
 */
export async function expectClearOfBottomBar(page: Page): Promise<void> {
  if (!(await isPhoneShell(page))) return;
  const r = await page.evaluate(() => {
    const main = document.querySelector('main');
    const bar = Array.from(document.querySelectorAll('nav')).find((n) => n.querySelector('[aria-label="More pages"]'));
    if (!main || !bar) return null;
    // The shell scrolls smoothly; jump instead so the measurement is of the end.
    main.scrollTo({ top: main.scrollHeight, behavior: 'instant' as ScrollBehavior });
    const content = main.firstElementChild as HTMLElement | null;
    const kids = content ? Array.from(content.querySelectorAll<HTMLElement>('*')) : [];
    let bottom = 0;
    for (const k of kids) {
      const b = k.getBoundingClientRect();
      if (b.height > 0 && getComputedStyle(k).position !== 'fixed') bottom = Math.max(bottom, b.bottom);
    }
    return { bottom, barTop: bar.getBoundingClientRect().top, inner: window.innerHeight };
  });
  if (!r) return;
  expect(r.bottom, 'content ends under the bottom bar').toBeLessThanOrEqual(r.barTop + 1);
}

/** The nav offers the admin pages to an admin and hides them from a member, with the Home section. */
export async function expectNavFor(page: Page, admin: boolean): Promise<void> {
  if (await isPhoneShell(page)) {
    await page.getByRole('button', { name: 'More pages' }).click();
    const sheet = page.getByTestId('more-sheet');
    await expect(sheet.getByTestId('more-section-home')).toBeVisible();
    await expect(sheet.getByRole('link', { name: 'Admin' })).toHaveCount(admin ? 1 : 0);
    await expectFits(page, sheet.locator('> div').last());
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
  } else {
    await expect(page.getByTestId('nav-section-home')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Admin', exact: true })).toHaveCount(admin ? 1 : 0);
  }
}

/** A sheet or dialog sits inside the screen; when its content is longer, it scrolls inside. */
export async function expectFits(page: Page, panel: Locator): Promise<void> {
  const r = await panel.evaluate((el) => {
    const b = el.getBoundingClientRect();
    const scrollers = [el, ...Array.from(el.querySelectorAll<HTMLElement>('*'))]
      .filter((n) => /(auto|scroll)/.test(getComputedStyle(n).overflowY));
    const overflowing = el.scrollHeight > el.clientHeight + 1;
    return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, w: window.innerWidth, h: window.innerHeight,
      overflowing, scrolls: scrollers.length > 0 };
  });
  expect(r.top, 'the panel starts above the screen').toBeGreaterThanOrEqual(-1);
  expect(r.bottom, 'the panel runs below the screen').toBeLessThanOrEqual(r.h + 1);
  expect(r.left).toBeGreaterThanOrEqual(-1);
  expect(r.right).toBeLessThanOrEqual(r.w + 1);
  if (r.overflowing) expect(r.scrolls, 'a long panel must scroll inside').toBe(true);
}

/** One screenshot per page per project, attached to the report. */
export async function snap(page: Page, info: TestInfo, name: string): Promise<void> {
  const path = info.outputPath(`${name}-${info.project.name}.png`);
  await page.screenshot({ path });
  await info.attach(`${name} (${info.project.name})`, { path, contentType: 'image/png' });
}

/** Everything a page checks once it has drawn. */
export async function expectLayoutSound(page: Page, info: TestInfo, name: string): Promise<void> {
  await expectNoHorizontalOverflow(page);
  await expectTapTargets(page.getByTestId('page-header'));
  await expectClearOfBottomBar(page);
  await snap(page, info, name);
}
