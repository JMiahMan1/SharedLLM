import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { controlsOffScreen as offScreen, expectNoHorizontalOverflow } from '../fixtures/layoutChecks';

/**
 * Nothing on the Bible pages may sit off the screen or under its edge, on any
 * form factor: the reader's header icons, the section tabs, the auto scroll
 * control. Checked with the longest book name and with a large system font
 * (Android's font size setting scales the whole app's text), which is where
 * the header's icons used to be pushed off the right edge of a phone.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** Back to the top, where a phone shows the reader's header again (it slides away while reading). */
async function toTop(page: Page) {
  await page.evaluate(() => {
    const main = document.querySelector('main');
    if (main) { main.style.scrollBehavior = 'auto'; main.scrollTop = 0; }
    window.scrollTo(0, 0);
  });
  await expect(page.getByTestId('bible-page')).toBeVisible();
  await page.waitForTimeout(400);  // the header's slide back in
}

for (const [label, scale] of [['normal text', 1], ['large system font', 1.3]] as const) {
  for (const ref of ['Genesis 1', '1 Thessalonians 4']) {
    test(`the Bible reader fits the screen: ${ref}, ${label}`, async ({ page }) => {
      await mockHome(page, 'admin');
      const unknown = await mockBible(page);
      const errors: string[] = [];
      page.on('pageerror', (e) => errors.push(e.message));
      page.on('console', (m) => { if (m.type() === 'error' && /React UI error|Uncaught/.test(m.text())) errors.push(m.text().slice(0, 500)); });
      const fail = () => `page errors: ${errors.join(' | ')}; unmocked Bible calls: ${unknown.join(', ')}`;
      if (scale !== 1) {
        await page.addInitScript((s) => {
          document.addEventListener('DOMContentLoaded', () => { document.documentElement.style.fontSize = `${s * 100}%`; });
        }, scale);
      }
      await page.goto(`/bible?ref=${encodeURIComponent(ref)}`);
      await expect(page.getByText(/Made-up verse 1:/).first()).toBeVisible();
      await expect(page.getByTestId('bible-auto-scroll')).toBeInViewport({ ratio: 1 });
      await toTop(page).catch((e) => { throw new Error(`${e}\n${fail()}`); });
      await expect(page.getByTestId('bible-reader-header')).not.toHaveAttribute('data-hidden', 'true');
      await expect(page.getByTestId('bible-display-toggle')).toBeInViewport({ ratio: 1 });
      await expect(page.getByTestId('bible-listen-toggle')).toBeInViewport({ ratio: 1 });
      expect(await offScreen(page)).toEqual([]);
      await expectNoHorizontalOverflow(page);
      // Every section of the Bible page, too.
      for (const tab of ['today', 'play', 'memorize', 'studies', 'progress', 'read']) {
        await toTop(page).catch((e) => { throw new Error(`${e}\n${fail()}`); });
        await page.getByTestId(`bible-tab-${tab}`).click();
        await page.waitForTimeout(300);
        expect(await offScreen(page), `the ${tab} section`).toEqual([]);
      }
      expect(errors, fail()).toEqual([]);
    });
  }
}
