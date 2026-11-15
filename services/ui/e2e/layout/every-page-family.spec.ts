import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, LONG_NAME } from '../fixtures/familyPagesMocks';
import { openAllDetails, screenFindings, settle, shot, type Allowed, quietHotReload } from '../fixtures/pageAudit';
import { isPhone } from '../fixtures/screenAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The family pages (the Family hub's every tab with a conversation open, its
 * new chat sheet and conversation info; a child's member page, every section;
 * Wander with its trip, share and map dialogs, the place form and all trips;
 * the Calendar in every view with its sources and an event) on every form
 * factor: nothing drawn past its card or dialog, no text cut off, no control
 * off the screen, no sideways scroll, and 44px targets on phones. Hermetic:
 * fixtures/familyPagesMocks.ts answers these pages and mockHome's catch-all
 * stays empty. The made-up family has long names and long text on purpose.
 */

const ALLOWED: Allowed[] = [
  { where: /^(member|wander)/, finding: /under 44px: (Leaflet|OpenStreetMap)\b/,
    why: 'the map credit Leaflet draws in its corner: an attribution, not a control the family uses' },
];

interface Run { mocks: HomeMocks; errors: string[]; found: string[] }

async function open(page: Page, path: string, ready: (p: Page) => Locator): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  // A render error the app's error boundary catches never reaches pageerror; it is logged instead.
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) errors.push(m.text().slice(0, 400));
  });
  await page.goto(path);
  // A page that never shows its content says why: a page error, or a call nothing answered.
  await expect(ready(page)).toBeVisible().catch((e: Error) => {
    throw new Error(`${e.message}\npage errors: ${errors.join(' | ')}\nunmocked: ${mocks.unmocked.join(', ')}`);
  });
  return { mocks, errors, found: [] };
}

/** Audit `scope` (the page's main area unless a dialog is given), with every section opened. */
async function check(page: Page, info: TestInfo, run: Run, where: string, scope?: Locator, allowed: Allowed[] = []): Promise<void> {
  const target = scope ?? page.locator('main');
  await settle(page);
  await openAllDetails(target);
  run.found.push(...await screenFindings(page, target, where, [...ALLOWED, ...allowed]));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

function done(run: Run) {
  expect(run.found, run.found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('every family page on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('Family: chat list, an open conversation, its info, a new chat, and every tab', async ({ page }, info) => {
    const run = await open(page, '/family', (p) => p.getByText('Camping trip planning').first());
    await check(page, info, run, 'family chat list');

    await page.getByText('The Family Group With A Long Name For Small Phones').first().click();
    await expect(page.getByTestId('chat-feed').getByText('Who is picking up the groceries?')).toBeVisible();
    // Below lg an open conversation is the whole screen (position: fixed, over the shell), yet in
    // the page it still sits inside the rounded chat panel, so the audit measures the header and
    // composer against the panel's edges. Those are not this screen's edges: anything really past
    // the screen is still caught by the off-screen and sideways-scroll checks.
    const thread = page.getByTestId('chat-thread');
    const fixedThread = await thread.evaluate((el) => getComputedStyle(el).position === 'fixed');
    const panel = (await page.getByTestId('chat-panel').boundingBox())!;
    const panelEdges = `outside ${Math.round(panel.x)}..${Math.round(panel.x + panel.width)})`;
    const overPanel: Allowed[] = fixedThread ? [{ where: /^family conversation$/,
      finding: new RegExp(`outside its card: .* ${panelEdges.replace(/[.)]/g, '\\$&')}$`),
      why: 'the full-screen thread measured against the panel it covers' }] : [];
    await check(page, info, run, 'family conversation', thread, overPanel);

    await page.getByTestId('chat-info-open').click();
    const infoSheet = page.getByRole('dialog').last();
    await expect(infoSheet.getByText(LONG_NAME).first()).toBeVisible();
    await check(page, info, run, 'family conversation info', infoSheet);
    // The sheet can still be sliding in when the first Escape lands: press until it has gone.
    await expect(async () => {
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0, { timeout: 1000 });
    }).toPass({ timeout: 15_000 });

    if (await page.getByRole('button', { name: 'Back to chats' }).isVisible()) {
      await page.getByRole('button', { name: 'Back to chats' }).click();
    }
    await page.getByTestId('chat-new').click();
    const sheet = page.getByRole('dialog').last();
    await expect(sheet).toBeVisible();
    await check(page, info, run, 'family new chat', sheet);
    await page.keyboard.press('Escape');

    for (const tab of ['Calls', 'Announce', 'Games', 'Create']) {
      await page.getByRole('tab', { name: tab }).click();
      await expect(page.getByRole('tab', { name: tab })).toHaveAttribute('aria-selected', 'true');
      await page.waitForLoadState('networkidle').catch(() => {});
      await check(page, info, run, `family ${tab.toLowerCase()}`);
      if (tab === 'Announce') {
        await page.getByRole('button', { name: 'Configure alarm, sound, emoji and devices' }).click();
        await check(page, info, run, 'family announce settings');
      }
    }
    done(run);
  });

  test('A child\'s member page, every section', async ({ page }, info) => {
    const run = await open(page, '/family/member/jordan', (p) => p.getByTestId('member-header'));
    await expect(page.getByTestId('member-header')).toContainText('Jordan');
    const mobile = (page.viewportSize()?.width ?? 1280) < 1024;
    for (const id of ['overview', 'comms', 'time', 'apps', 'activity', 'location', 'health', 'faith', 'devices']) {
      const button = page.getByTestId(`member-${mobile ? 'tab' : 'rail'}-${id}`);
      if (!(await button.count())) continue;
      await button.click();
      await expect(page.getByTestId(`member-panel-${id}`)).toBeVisible();
      await page.waitForLoadState('networkidle').catch(() => {});
      await check(page, info, run, `member ${id}`);
    }
    done(run);
  });

  test('Wander: presence, places, trips, the trip dialogs and all trips', async ({ page }, info) => {
    const run = await open(page, '/wander', (p) => p.getByTestId('trip-card').first());
    await expect(page.getByTestId('places-panel')).toBeVisible();
    await check(page, info, run, 'wander');

    await page.getByTestId('add-place').click();
    await expect(page.getByTestId('place-form')).toBeVisible();
    await check(page, info, run, 'wander place form', page.getByTestId('places-panel'));
    await page.getByTestId('place-form').getByRole('button', { name: 'Close' }).click();

    const card = page.getByTestId('trip-card').first();
    await card.getByTitle('Open route map').first().click();
    let dialog = page.getByRole('dialog').last();
    await expect(dialog).toBeVisible();
    await check(page, info, run, 'wander trip map', dialog);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);

    await card.getByRole('button', { name: /edit/i }).first().click();
    dialog = page.getByRole('dialog').last();
    await expect(dialog).toBeVisible();
    await check(page, info, run, 'wander edit trip', dialog);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);

    await card.getByRole('button', { name: /share/i }).first().click();
    dialog = page.getByRole('dialog').last();
    await expect(dialog).toBeVisible();
    await check(page, info, run, 'wander share trip', dialog);
    await page.keyboard.press('Escape');

    await page.getByTestId('see-all-trips').click();
    await expect(page.getByTestId('all-trips')).toBeVisible();
    await check(page, info, run, 'wander all trips');
    done(run);
  });

  test('Calendar: every view, the sources, and an event', async ({ page }, info) => {
    const run = await open(page, '/calendar', (p) => p.getByRole('group', { name: 'Calendar view' }));
    for (const view of ['Agenda', 'Day', 'Week', 'Month']) {
      await page.getByRole('group', { name: 'Calendar view' }).getByRole('button', { name: view }).click();
      await page.waitForLoadState('networkidle').catch(() => {});
      await check(page, info, run, `calendar ${view.toLowerCase()}`);
    }
    await page.getByRole('button', { name: 'Sources' }).click();
    await expect(page.getByRole('button', { name: 'Close sources' })).toBeVisible();
    await check(page, info, run, 'calendar sources');
    await page.getByRole('button', { name: 'Close sources' }).click();

    await page.getByRole('group', { name: 'Calendar view' }).getByRole('button', { name: 'Agenda' }).click();
    await page.getByText('Dentist').first().click();
    const dialog = page.getByRole('dialog').last();
    await expect(dialog).toBeVisible();
    await check(page, info, run, 'calendar edit event', dialog);
    await page.keyboard.press('Escape');
    if (isPhone(page)) await expect(page.getByRole('dialog')).toHaveCount(0);
    done(run);
  });
});
