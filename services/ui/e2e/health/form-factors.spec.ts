import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockHealth, LONG_NAME } from '../fixtures/healthMocks';
import { controlsOffScreen, expectClearOfBottomBar, expectNoHorizontalOverflow } from '../fixtures/layoutChecks';
import { audit, isPhone } from '../fixtures/screenAudit';

/**
 * Every Health tab on every form factor, with a full made-up day (sleep, food,
 * water, steps, a check-in, a fast, a challenge with a long-named member):
 * nothing wider than the screen, nothing past its right edge, thumb-sized
 * controls on a phone, no text cut off, and the end of the page clear of the
 * bottom bar. Hermetic: every call is answered and the catch-all stays empty.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const TABS = [
  { id: 'today', ready: 'Readiness' },
  { id: 'fitness', ready: 'Bonus stars' },
  { id: 'sleep', ready: 'Log a night' },
  { id: 'nutrition', ready: 'Breakfast' },
  { id: 'health', ready: 'Measurements' },
] as const;

async function open(page: Page) {
  const home = await mockHome(page, 'admin');
  const unknownHealth = await mockHealth(page);
  const unknownBible = await mockBible(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error' && /React UI error|Uncaught/.test(m.text())) errors.push(m.text().slice(0, 300)); });
  await page.goto('/fitness');
  await expect(page.getByTestId('health-tab-today')).toBeVisible();
  return { home, unknownHealth, unknownBible, errors };
}

async function showTab(page: Page, id: string) {
  const tab = page.getByTestId(`health-tab-${id}`);
  // The page keeps a step-sync timer, so a first tap can land mid-render: tap until it takes.
  await expect(async () => {
    await tab.click();
    await expect(tab).toHaveAttribute('aria-selected', 'true', { timeout: 1000 });
  }).toPass({ timeout: 15_000 });
}

async function expectSound(page: Page, where: string) {
  const findings = await audit(page.getByTestId('health-page'), isPhone(page));
  expect(findings.small, `${where}: controls shorter than 44px`).toEqual([]);
  expect(findings.clipped, `${where}: text cut off`).toEqual([]);
  expect(await controlsOffScreen(page), `${where}: controls past the edge`).toEqual([]);
  await expectNoHorizontalOverflow(page);
}

test.describe('health on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('every tab fits, with thumb-sized controls and nothing cut off', async ({ page }, info) => {
    const run = await open(page);
    // All five tabs sit side by side inside the strip: none scrolled out of sight.
    const strip = (await page.getByTestId('health-tabs').boundingBox())!;
    for (const { id } of TABS) {
      const box = (await page.getByTestId(`health-tab-${id}`).boundingBox())!;
      expect(box.x, `the ${id} tab starts inside the strip`).toBeGreaterThanOrEqual(strip.x - 1);
      expect(box.x + box.width, `the ${id} tab ends inside the strip`).toBeLessThanOrEqual(strip.x + strip.width + 1);
      if (isPhone(page)) expect(box.height).toBeGreaterThanOrEqual(44);
    }
    for (const { id, ready } of TABS) {
      await showTab(page, id);
      await expect(page.getByTestId('health-page').getByText(ready, { exact: true }).first()).toBeVisible();
      await page.waitForTimeout(400);
      await expectSound(page, `the ${id} tab`);
      const shot = info.outputPath(`health-${id}-${info.project.name}.png`);
      await page.screenshot({ path: shot });
      await info.attach(`health ${id} (${info.project.name})`, { path: shot, contentType: 'image/png' });
      await expectClearOfBottomBar(page);
    }
    expect(run.errors).toEqual([]);
    expect(run.unknownHealth).toEqual([]);
    expect(run.home.unmocked).toEqual([]);
  });

  test('a long family name is shown whole, and the check-in rows line up', async ({ page }) => {
    await open(page);
    const shared = page.getByTestId('health-page').getByText(LONG_NAME, { exact: true }).first();
    await shared.scrollIntoViewIfNeeded();
    await expect(shared).toBeVisible();
    expect(await shared.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    const lefts: number[] = [];
    for (const row of ['energy', 'mood', 'soreness']) {
      lefts.push((await page.getByTestId(`checkin-${row}`).getByRole('button').first().boundingBox())!.x);
    }
    expect(Math.max(...lefts) - Math.min(...lefts), 'the 1 buttons line up').toBeLessThanOrEqual(1);
    // A reminder's hint is read across the card, not a word a line beside the time.
    const hint = page.getByTestId('reminder-bedtime').locator('p').nth(1);
    await hint.scrollIntoViewIfNeeded();
    const lineHeight = await hint.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight));
    expect((await hint.boundingBox())!.height).toBeLessThan(lineHeight * 2.5);
    // Sources are named, never shown as ids.
    await expect(page.getByTestId('health-page')).not.toContainText('health_connect');
  });

  test('adding food and editing a night stay on the screen', async ({ page }, info) => {
    const run = await open(page);
    await showTab(page, 'nutrition');
    await page.getByTestId('meal-dinner').getByRole('button', { name: 'Add' }).click();
    const panel = page.getByTestId('add-food-panel');
    await expect(panel).toBeVisible();
    await panel.scrollIntoViewIfNeeded();
    await expectSound(page, 'the add food panel');
    await panel.screenshot({ path: info.outputPath(`health-add-food-${info.project.name}.png`) });

    await showTab(page, 'sleep');
    await page.getByRole('button', { name: /^Edit sleep for / }).first().click();
    await page.waitForTimeout(300);
    await expectSound(page, 'editing a night');

    await showTab(page, 'health');
    const profile = page.getByTestId('health-page');
    await expect(profile.getByText('Measurements', { exact: true })).toBeVisible();
    await profile.getByRole('button', { name: 'History' }).first().click();
    await page.waitForTimeout(300);
    await expectSound(page, 'a measurement history');
    expect(run.errors).toEqual([]);
    expect(run.home.unmocked).toEqual([]);
  });
});
