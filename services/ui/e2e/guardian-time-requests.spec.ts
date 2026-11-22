import { test, expect as baseExpect, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from './fixtures/homeMocks';
import { mockHealth } from './fixtures/healthMocks';
import { mockBible } from './fixtures/bibleMocks';
import { mockFamilyPages } from './fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload } from './fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * A child asks for more screen time: on the child's page the parent sees the
 * minutes asked in a field they can change (60 asked, 30 given), or a quick
 * pick, then Approve gives exactly that; Not today declines. On every form
 * factor nothing runs past its card or off the screen, and on phones every
 * control is at least 44px. Hermetic.
 */

interface Run { mocks: HomeMocks; errors: string[]; decided: unknown[]; pending: unknown[] }

function asked(over: Record<string, unknown> = {}) {
  return {
    id: 22, username: 'jordan', day: '2026-11-21', minutes: 60, note: 'one more level, then bed',
    status: 'pending', granted_minutes: 0, decided_by: null,
    created_at: new Date(Date.now() - 60_000).toISOString(), decided_at: null,
    ...over,
  };
}

async function open(page: Page): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const run: Run = { mocks, errors: [], decided: [], pending: [asked()] };
  // Registered last, so these answer before the shared family mocks.
  await page.route('**/api/screen-time/requests?*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const status = new URL(route.request().url()).searchParams.get('status');
    return route.fulfill({ json: { requests: status === 'pending' ? run.pending : [] } });
  });
  await page.route('**/api/screen-time/requests/*', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as { grant: boolean; minutes?: number };
    run.decided.push(body);
    run.pending = [];
    return route.fulfill({ json: { request: asked({ status: body.grant ? 'granted' : 'declined', granted_minutes: body.minutes ?? 0 }) } });
  });
  page.on('pageerror', (e) => run.errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) run.errors.push(m.text().slice(0, 400));
  });
  await page.goto('/family/member/jordan');
  await expect(page.getByTestId('request-time-22')).toBeVisible().catch((e: Error) => {
    throw new Error(`${e.message}\npage errors: ${run.errors.join(' | ')}\nunmocked: ${mocks.unmocked.join(', ')}`);
  });
  return run;
}

async function audit(page: Page, info: TestInfo, run: Run, where: string) {
  const row = page.getByTestId('request-time-22');
  await row.scrollIntoViewIfNeeded();
  await settle(page);
  const found = await screenFindings(page, row, where);
  await shot(page, info, where.replace(/\W+/g, '-'));
  expect(found, found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('a request for more screen time', () => {
  test.describe.configure({ timeout: 120_000 });

  test('a parent edits 60 minutes down to 30 and approves', async ({ page }, info) => {
    const run = await open(page);
    const row = page.getByTestId('request-time-22');
    await expect(row).toContainText('Asks for 60 more minutes');
    await expect(row.getByLabel('Give')).toHaveValue('60');
    await audit(page, info, run, 'time request');

    await row.getByLabel('Give').fill('30');
    await expect(row).toContainText('They asked for 60.');
    await row.getByRole('button', { name: 'Approve 30 min' }).click();
    await expect.poll(() => run.decided).toEqual([{ grant: true, minutes: 30 }]);
    await expect(page.getByTestId('request-time-22')).toHaveCount(0);
  });

  test('a quick pick, and an amount out of range cannot be approved', async ({ page }, info) => {
    const run = await open(page);
    const row = page.getByTestId('request-time-22');
    await row.getByLabel('Give').fill('500');
    await expect(row.getByRole('button', { name: 'Approve', exact: true })).toBeDisabled();
    await expect(row).toContainText('Enter 5 to 240 minutes.');
    await audit(page, info, run, 'time request out of range');

    await row.getByRole('group', { name: 'Quick picks' }).getByRole('button', { name: '45' }).click();
    await row.getByRole('button', { name: 'Approve 45 min' }).click();
    await expect.poll(() => run.decided).toEqual([{ grant: true, minutes: 45 }]);
  });

  test('a parent says not today', async ({ page }, info) => {
    const run = await open(page);
    await page.getByTestId('request-time-22').getByRole('button', { name: 'Not today' }).click();
    await expect.poll(() => run.decided).toEqual([{ grant: false }]);
    await expect(page.getByTestId('request-time-22')).toHaveCount(0);
    await shot(page, info, 'time request declined');
    expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  });
});
