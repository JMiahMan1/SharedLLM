import { test, expect as baseExpect, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from './fixtures/homeMocks';
import { mockHealth } from './fixtures/healthMocks';
import { mockBible } from './fixtures/bibleMocks';
import { mockFamilyPages } from './fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload } from './fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * A locked device asking a parent to unlock it (the family PC's or the
 * child's phone's lock screen, no password typed there): the parent sees the
 * request on the child's page, picks how long and approves, or denies. On
 * every form factor nothing runs past its card or off the screen, and on
 * phones every control is at least 44px. Hermetic.
 */

interface Run { mocks: HomeMocks; errors: string[]; decided: unknown[]; pending: unknown[] }

function waiting(over: Record<string, unknown> = {}) {
  const now = Date.now();
  return {
    id: 21, username: 'jordan', device: 'PC', lock_reason: 'bedtime',
    note: 'I need to finish the science project, it is due first thing tomorrow morning and I am almost done',
    minutes_asked: 30, status: 'pending', minutes_granted: null, until: null, decided_by: null,
    created_at: new Date(now - 60_000).toISOString(), expires_at: new Date(now + 9 * 60_000).toISOString(), decided_at: null,
    ...over,
  };
}

async function open(page: Page): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const run: Run = { mocks, errors: [], decided: [], pending: [waiting()] };
  // Registered last, so these answer before the shared family mocks.
  await page.route('**/api/guard/unlock-requests*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({ json: { requests: run.pending } });
  });
  await page.route('**/api/guard/unlock-requests/*', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as { approve: boolean; minutes?: number };
    run.decided.push(body);
    run.pending = [];
    return route.fulfill({ json: { request: waiting({ status: body.approve ? 'approved' : 'denied', minutes_granted: body.minutes ?? null }) } });
  });
  page.on('pageerror', (e) => run.errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) run.errors.push(m.text().slice(0, 400));
  });
  await page.goto('/family/member/jordan');
  await expect(page.getByTestId('unlock-requests')).toBeVisible().catch((e: Error) => {
    throw new Error(`${e.message}\npage errors: ${run.errors.join(' | ')}\nunmocked: ${mocks.unmocked.join(', ')}`);
  });
  return run;
}

async function audit(page: Page, info: TestInfo, run: Run, where: string) {
  const card = page.getByTestId('unlock-requests');
  await card.scrollIntoViewIfNeeded();
  await settle(page);
  const found = await screenFindings(page, card, where);
  await shot(page, info, where.replace(/\W+/g, '-'));
  expect(found, found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('unlock requests from a locked device', () => {
  test.describe.configure({ timeout: 120_000 });

  test('a parent picks how long and approves', async ({ page }, info) => {
    const run = await open(page);
    const card = page.getByTestId('unlock-requests');
    const row = card.getByTestId('unlock-request-21');
    await expect(row).toContainText('Asks to unlock their PC for 30 minutes at bedtime');
    await expect(row).toContainText('science project');
    await expect(row).toContainText(/expires in \d+ min/);
    await expect(row.getByRole('radio', { name: '30 min' })).toHaveAttribute('aria-checked', 'true');
    await audit(page, info, run, 'unlock request');

    await row.getByRole('radio', { name: '1 h' }).click();
    await row.getByRole('button', { name: 'Approve for 1 h' }).click();
    await expect.poll(() => run.decided).toEqual([{ approve: true, minutes: 60 }]);
    await expect(page.getByTestId('unlock-requests')).toHaveCount(0);
  });

  test('a parent denies', async ({ page }, info) => {
    const run = await open(page);
    const row = page.getByTestId('unlock-request-21');
    await row.getByRole('button', { name: 'Deny' }).click();
    await expect.poll(() => run.decided).toEqual([{ approve: false }]);
    await expect(page.getByTestId('unlock-requests')).toHaveCount(0);
    await shot(page, info, 'unlock request denied');
    expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  });
});
