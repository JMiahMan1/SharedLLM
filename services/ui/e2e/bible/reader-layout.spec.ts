import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { controlsOffScreen as offScreen, expectClearOfBottomBar, expectFits, expectNoHorizontalOverflow } from '../fixtures/layoutChecks';
import { audit, isPhone } from '../fixtures/screenAudit';

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

/** Thumb-sized controls (on a phone), no text cut off, nothing past the edge, inside `scope`. */
async function expectSound(page: Page, scope: string, where: string) {
  const findings = await audit(page.locator(scope).first(), isPhone(page));
  expect(findings.small, `${where}: controls shorter than 44px`).toEqual([]);
  expect(findings.clipped, `${where}: text cut off`).toEqual([]);
  expect(await offScreen(page), `${where}: controls past the edge`).toEqual([]);
  await expectNoHorizontalOverflow(page);
}

test.describe('the Bible on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('the verse sheet, the header panels and read aloud fit and are thumb-sized', async ({ page }, info) => {
    const home = await mockHome(page, 'admin');
    const unknown = await mockBible(page);
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('/bible?ref=1+Thessalonians+4');
    await expect(page.getByText(/Made-up verse 1:/).first()).toBeVisible();
    // With no translation saved, the pill names the one on the page instead of a cut-off "Version".
    await expect(page.getByTestId('bible-version-open')).toContainText('TST');
    await expectSound(page, '[data-testid="bible-page"]', 'the reader');

    await page.getByTestId('bible-verse-4-3').click();
    const sheet = page.getByTestId('bible-verse-sheet');
    await expect(sheet).toBeVisible();
    await expect(page.getByTestId('bible-action-interlinear')).toBeVisible();
    await expectFits(page, sheet);
    await expectSound(page, '[data-testid="bible-verse-sheet"]', 'the verse sheet');
    const shot = info.outputPath(`bible-verse-sheet-${info.project.name}.png`);
    await page.screenshot({ path: shot });
    await info.attach(`verse sheet (${info.project.name})`, { path: shot, contentType: 'image/png' });
    await sheet.getByRole('button', { name: 'Close verse actions' }).click();
    await expect(sheet).toHaveCount(0);

    for (const [button, name] of [['bible-browse-open', 'Choose a passage'], ['bible-version-open', 'Version'],
      ['bible-display-toggle', 'Text settings']] as const) {
      await toTop(page);
      await page.getByTestId(button).click();
      const panel = page.getByRole('dialog', { name });
      await expect(panel).toBeVisible();
      await expectFits(page, panel);
      await expectSound(page, '[role="dialog"]', `the ${name} panel`);
      await page.keyboard.press('Escape');
      await expect(panel).toHaveCount(0);
    }

    await toTop(page);
    await page.getByTestId('bible-listen-toggle').click();
    const player = page.getByTestId('bible-read-aloud');
    await expect(player).toBeVisible();
    await expectSound(page, '[data-testid="bible-read-aloud"]', 'read aloud');
    expect(errors).toEqual([]);
    expect(home.unmocked).toEqual([]);
    expect(unknown).toEqual([]);
  });

  test('every section is thumb-sized, uncut and clear of the bottom bar', async ({ page }, info) => {
    const home = await mockHome(page, 'admin');
    const unknown = await mockBible(page);
    await page.goto('/bible?ref=Genesis+1');
    await expect(page.getByText(/Made-up verse 1:/).first()).toBeVisible();
    for (const tab of ['today', 'play', 'memorize', 'studies', 'progress']) {
      await toTop(page);
      await page.getByTestId(`bible-tab-${tab}`).click();
      await page.waitForTimeout(800);
      await expectSound(page, '[data-testid="bible-page"]', `the ${tab} section`);
      const shot = info.outputPath(`bible-${tab}-${info.project.name}.png`);
      await page.screenshot({ path: shot });
      await info.attach(`bible ${tab} (${info.project.name})`, { path: shot, contentType: 'image/png' });
      await expectClearOfBottomBar(page);
    }
    expect(home.unmocked).toEqual([]);
    expect(unknown).toEqual([]);
  });
});
