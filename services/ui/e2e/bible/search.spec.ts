import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { controlsOffScreen as offScreen, expectNoHorizontalOverflow } from '../fixtures/layoutChecks';
import { audit, isPhone } from '../fixtures/screenAudit';

/**
 * The Bible search screen on every form factor: the filter chips, the
 * matches-by-book chart and the results must fit, be thumb-sized and never
 * push the page sideways (the chart scrolls inside itself).
 */
const expect = baseExpect.configure({ timeout: 15_000 });

async function expectSound(page: Page, scope: string, where: string) {
  const findings = await audit(page.locator(scope).first(), isPhone(page));
  expect(findings.small, `${where}: controls shorter than 44px`).toEqual([]);
  expect(findings.clipped, `${where}: text cut off`).toEqual([]);
  expect(await offScreen(page), `${where}: controls past the edge`).toEqual([]);
  await expectNoHorizontalOverflow(page);
}

test('Bible search fits the screen with its filters, chart, tips and context open', async ({ page }, info) => {
  const home = await mockHome(page, 'admin');
  const unknown = await mockBible(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/bible?ref=Genesis+1');
  await expect(page.getByText(/Made-up verse 1:/).first()).toBeVisible();

  await page.getByTestId('bible-browse-open').click();
  await page.getByTestId('bible-ref-input').fill('faith in:1thess');
  await page.getByTestId('bible-ref-go').click();
  const results = page.getByTestId('bible-search-results');
  await expect(results).toBeVisible();
  await expect(page.getByTestId('bible-search-summary')).toContainText('50 verses');
  await expect(results.locator('mark').first()).toHaveText('faith');
  await expectSound(page, '[data-testid="bible-search-results"]', 'search results');

  await page.getByTestId('bible-search-tips-toggle').click();
  await expect(page.getByTestId('bible-search-tips')).toBeVisible();
  await page.getByLabel('Verses around Genesis 4:1').click();
  await expect(page.getByTestId('bible-search-context')).toBeVisible();
  await expectSound(page, '[data-testid="bible-search-results"]', 'search with tips and context');

  await page.getByTestId('bible-search-match-meaning').click();
  await page.getByTestId('bible-search-ask-toggle').click();
  await page.getByTestId('bible-search-ask-go').click();
  await expect(page.getByTestId('bible-search-ask-answer')).toContainText('A made-up answer');
  await expectSound(page, '[data-testid="bible-search-results"]', 'meaning search with an answer');
  await page.getByTestId('bible-search-ask').scrollIntoViewIfNeeded();
  const asked = info.outputPath(`bible-search-ask-${info.project.name}.png`);
  await page.screenshot({ path: asked });
  await info.attach(`bible search ask (${info.project.name})`, { path: asked, contentType: 'image/png' });
  const shot = info.outputPath(`bible-search-${info.project.name}.png`);
  await page.screenshot({ path: shot, fullPage: true });
  await info.attach(`bible search (${info.project.name})`, { path: shot, contentType: 'image/png' });

  // The search is remembered and offered again in the passage panel.
  await page.getByRole('button', { name: 'Clear search' }).click();
  await page.getByTestId('bible-browse-open').click();
  await expect(page.getByTestId('bible-recent-searches')).toContainText('faith in:1thess');

  expect(errors).toEqual([]);
  expect(home.unmocked).toEqual([]);
  expect(unknown).toEqual([]);
});
