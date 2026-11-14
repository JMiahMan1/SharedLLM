import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { expectFits, expectTapTargets } from '../fixtures/layoutChecks';

/**
 * Auto scroll in the reading surfaces: the Bible reader, focus mode and the
 * Bible studies reader. On every form factor the control has to fit on the
 * screen, clear of the bottom bar, with thumb-sized buttons, and the text has
 * to move once it starts. The passage and study book are made up.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

async function openReader(page: Page) {
  const mocks = await mockHome(page, 'admin');
  const unknown = await mockBible(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)); });
  await page.goto('/bible?ref=Genesis+1');
  await expect(page.getByText(/Made-up verse 1:/).first()).toBeVisible();
  return { mocks, unknown, errors };
}

/** The scroll position of the reader's own scrolling element. */
async function readerTop(page: Page): Promise<number> {
  return page.evaluate(() => {
    const main = document.querySelector('main');
    return main ? main.scrollTop : 0;
  });
}

test.describe('auto scroll', () => {
  test.describe.configure({ timeout: 90_000 });

  test('the Bible reader scrolls by itself, at a pace the reader sets, and the control fits', async ({ page }) => {
    const run = await openReader(page);
    const control = page.getByTestId('bible-auto-scroll');
    await expect(control).toBeVisible();
    await expectFits(page, control);
    await control.getByRole('button', { name: 'Auto scroll' }).click();
    await expect(control.getByRole('button', { name: 'Pause auto scroll' })).toBeVisible();
    await expectFits(page, control);
    await expectTapTargets(control);
    await control.getByRole('button', { name: 'Faster' }).click();
    await expect(control.getByTestId('bible-auto-scroll-speed')).toContainText('Speed 4');
    const before = await readerTop(page);
    await expect.poll(() => readerTop(page), { timeout: 5000 }).toBeGreaterThan(before + 20);
    await control.getByRole('button', { name: 'Pause auto scroll' }).click();
    const paused = await readerTop(page);
    await page.waitForTimeout(600);
    expect(await readerTop(page)).toBe(paused);
    await control.getByRole('button', { name: 'Close auto scroll' }).click();
    await expect(control.getByRole('button', { name: 'Auto scroll' })).toBeVisible();
    expect(run.errors).toEqual([]);
    expect(run.unknown, `unmocked Bible calls: ${run.unknown.join(', ')}`).toEqual([]);
  });

  test('focus mode has its own control, and it moves the page', async ({ page }) => {
    await openReader(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    if (await page.getByTestId('bible-focus-open').isVisible()) {
      await page.getByTestId('bible-focus-open').click();
    } else {
      await page.getByTestId('bible-display-toggle').click();
      await page.getByTestId('bible-focus-open-narrow').click();
    }
    await expect(page.getByTestId('bible-focus')).toBeVisible();
    await expect(page.getByTestId('bible-auto-scroll')).toHaveCount(0);
    const control = page.getByTestId('bible-focus-auto-scroll');
    await control.getByRole('button', { name: 'Auto scroll' }).click();
    await expectFits(page, control);
    await expectTapTargets(control);
    const top = () => page.getByTestId('bible-focus').evaluate((el) => el.scrollTop);
    await expect.poll(top, { timeout: 5000 }).toBeGreaterThan(10);
  });

  test('the Bible studies reader scrolls its page above its own footer', async ({ page }) => {
    await mockHome(page, 'admin');
    const unknown = await mockBible(page);
    await page.goto('/bible?tab=studies');
    await page.getByRole('button', { name: /A Made-up Study/ }).click();
    await expect(page.getByTestId('bible-study-reader-text')).toContainText('Paragraph 1.');
    const control = page.getByTestId('bible-study-auto-scroll');
    await control.getByRole('button', { name: 'Auto scroll' }).click();
    await expectFits(page, control);
    await expectTapTargets(control);
    // The control sits above the footer's page buttons, never over them.
    const footer = await page.getByRole('button', { name: 'Next page' }).boundingBox();
    const box = await control.boundingBox();
    expect(box!.y + box!.height).toBeLessThanOrEqual(footer!.y);
    const top = () => page.getByTestId('bible-study-page').evaluate((el) => el.scrollTop);
    await expect.poll(top, { timeout: 5000 }).toBeGreaterThan(10);
    expect(unknown.filter((p) => !p.startsWith('/study')), unknown.join(', ')).toEqual([]);
  });
});
