import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from './fixtures/homeMocks';
import { mockHealth } from './fixtures/healthMocks';
import { mockBible } from './fixtures/bibleMocks';
import { mockFamilyPages } from './fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload } from './fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * App vetting on every form factor (docs/APP_VETTING.md): a child's Apps card
 * with the questionable app first, an app opened to its review and actions,
 * the age rules, the "not used lately" list, and the verdict on an app
 * request. Nothing may run past its card, no text is cut off, nothing sits off
 * the screen, and on phones every control is at least 44px. Hermetic: the
 * mocks answer every call and mockHome's catch-all stays empty.
 */

interface Run { mocks: HomeMocks; errors: string[]; found: string[] }

async function open(page: Page, path: string): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) errors.push(m.text().slice(0, 400));
  });
  await page.goto(path);
  await expect(page.getByTestId('apps-card')).toBeVisible().catch((e: Error) => {
    throw new Error(`${e.message}\npage errors: ${errors.join(' | ')}\nunmocked: ${mocks.unmocked.join(', ')}`);
  });
  return { mocks, errors, found: [] };
}

async function check(page: Page, info: TestInfo, run: Run, where: string, scope: Locator) {
  await settle(page);
  run.found.push(...await screenFindings(page, scope, where));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

function done(run: Run) {
  expect(run.found, run.found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('app vetting', () => {
  test.describe.configure({ timeout: 120_000 });

  test('the Apps card: flagged first, an app opened, the rules and unused apps', async ({ page }, info) => {
    const run = await open(page, '/family/member/jordan?section=apps');
    const card = page.getByTestId('apps-card');
    await card.scrollIntoViewIfNeeded();
    await expect(card.getByTestId('apps-limit')).toHaveText('Age 10 · apps up to Everyone 10+');
    const attention = card.getByTestId('apps-attention');
    await expect(attention).toContainText('Block World Builders');
    await expect(attention).toContainText('Rated Teen, above the Everyone 10+ limit');
    await expect(card.getByTestId('apps-all')).toContainText('Checking its Play listing and reviews');
    await expect(card.getByTestId('apps-all')).toContainText('Not on Google Play: judged on its permissions');
    await check(page, info, run, 'apps card', card);

    // Open the flagged app: its review, the quote, and every action.
    await card.getByRole('button', { name: /Block World Builders/ }).click();
    const detail = card.getByTestId('app-detail-com.example.blockworld');
    await expect(detail).toContainText('Random people kept sending my son friend requests');
    for (const name of ['Reviewed, keep', 'Block', 'Remove', 'Always allow', 'Always flag']) {
      await expect(detail.getByRole('button', { name, exact: true })).toBeVisible();
    }
    await expect(detail).toContainText('Block keeps it from opening');
    await check(page, info, run, 'apps card app open', card);

    // The age rules.
    await card.getByTestId('apps-rules-toggle').click();
    const rules = card.getByTestId('apps-rules');
    await expect(rules.getByTestId('apps-birth-date')).toHaveValue('2016-01-01');
    await expect(rules.getByTestId('apps-max-rating')).toContainText('By age (Everyone 10+)');
    await check(page, info, run, 'apps card rules', card);

    // Not used lately, with what each button does.
    const unused = card.getByTestId('apps-unused');
    await expect(unused).toContainText('Old Racer Turbo Championship Edition');
    await expect(unused).toContainText('Last used 3 months ago · 1.3 GB');
    await expect(unused.getByRole('button', { name: 'Keep' })).toBeVisible();

    // An action answers with a toast and refreshes the card.
    await detail.getByRole('button', { name: 'Reviewed, keep', exact: true }).click();
    await expect(page.getByText(/kept; it leaves the top/)).toBeVisible();
    done(run);
  });

  test('the Guardian overview: the request verdict and the Apps card', async ({ page }, info) => {
    const run = await open(page, '/guardian?member=jordan&area=overview');
    const request = page.getByTestId('request-verdict-3');
    await request.scrollIntoViewIfNeeded();
    await expect(request).toContainText('Rated Teen · above the Everyone 10+ limit');
    await expect(request).toContainText('Contact with strangers or chat');
    await expect(request).toContainText('Ads · In-app purchases · Users Interact');
    await check(page, info, run, 'guardian request verdict', page.getByTestId('open-requests'));
    await check(page, info, run, 'guardian apps card', page.getByTestId('apps-card'));
    done(run);
  });
});
