/**
 * Music Assistant in Accounts & Integrations (docs/INTEGRATIONS.md, "Music Assistant"),
 * hermetic: every API call is mocked and nothing reaches a server. Runs in every
 * hermetic project, so it is checked on a desktop, phones and tablets.
 *
 * A person on the household server (HomeHub's Music Assistant integration) sees it
 * is their own account there and that favourites are shared. A person whose own
 * server does not answer sees it as an error on the card, and switches to the
 * household server only by pressing "Use the household Music Assistant". With no
 * Music Assistant anywhere the card says how to set one up. Every value is made up.
 */
import { expect as baseExpect, test, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { quietHotReload, screenFindings, settle } from '../fixtures/pageAudit';
import { expectClearOfBottomBar, expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

const HOUSEHOLD = 'http://music.example.test:8095';
const MINE = 'http://mine.example.test:8095';
const NOTE = 'Music Assistant keeps play history, resume points and playback speed for each person; '
  + 'favourites, the library and playlists are shared by everyone on the same server.';
const NONE = 'No Music Assistant is set up: add the Music Assistant integration in HomeHub or your own in Accounts & Integrations.';

interface Person {
  id: number; username: string; display_name: string; full_name: string; role: string; is_admin: boolean;
  is_system_default: boolean; saved_credentials: string[]; mass_url: string | null;
}

function person(extra: Partial<Person> = {}): Person {
  return { id: 7, username: 'kim', display_name: 'Kim', full_name: 'Kim', role: 'user', is_admin: false,
    is_system_default: false, saved_credentials: ['mass_token'], mass_url: null, ...extra };
}

/** identity's answer for this person, following its rules (services/identity/music_assistant.py). */
function statusFor(me: Person, household: string | null, ownAnswers: boolean) {
  const base = { household_url: household, household_error: household ? null : NONE, note: NOTE, ma_username: null as string | null };
  if (me.mass_url) {
    return ownAnswers
      ? { ...base, server: 'own', login: 'own', url: me.mass_url, own_url: me.mass_url, can_use_household: !!household,
        state: 'ok', message: 'Connected as kim.', ma_username: 'kim' }
      : { ...base, server: 'own', login: 'own', url: me.mass_url, own_url: me.mass_url, can_use_household: !!household,
        state: 'unreachable',
        message: `Your own Music Assistant at ${me.mass_url} did not answer (No answer (URLError).). Fix it in Accounts & Integrations, or switch to the household Music Assistant there.` };
  }
  if (!household) {
    return { ...base, server: 'absent', login: 'absent', url: null, own_url: null, can_use_household: false, state: 'not_set_up', message: NONE };
  }
  return { ...base, server: 'household', login: 'provisioned', url: household, own_url: null, can_use_household: false,
    state: 'ok', message: 'Connected as kim.', ma_username: 'kim' };
}

async function mockServer(page: Page, me: Person, household: string | null, ownAnswers = false) {
  const patches: Array<Record<string, unknown>> = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await quietHotReload(page);
  const home = await mockHome(page, 'member');
  await mockHealth(page);
  await mockBible(page);
  await mockSettingsPages(page);
  await page.addInitScript((p) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(p));
  }, me);

  await page.route((u: URL) => u.pathname === '/api/auth/music-assistant/status',
    (route: Route) => route.fulfill({ json: statusFor(me, household, ownAnswers) }));
  await page.route((u: URL) => u.pathname === '/api/users/me', async (route: Route) => {
    const req = route.request();
    if (req.method() === 'GET') return route.fulfill({ json: me });
    if (req.method() === 'PATCH') {
      const body = req.postDataJSON() as Record<string, unknown>;
      patches.push(body);
      const cleared = Array.isArray(body.clear_fields) ? body.clear_fields as string[] : [];
      if (cleared.includes('mass_url')) me.mass_url = null;
      if (typeof body.mass_url === 'string') me.mass_url = body.mass_url;
      return route.fulfill({ json: me });
    }
    return route.fallback();
  });
  return { patches, errors, home, findings: [] as string[] };
}

type Run = Awaited<ReturnType<typeof mockServer>>;

async function openTile(page: Page) {
  await page.goto('/identity');
  const tile = page.getByTestId('music-assistant-tile');
  await tile.scrollIntoViewIfNeeded();
  await expect(tile).toBeVisible();
  return tile;
}

async function layout(page: Page, info: TestInfo, run: Run, scope: Locator, name: string) {
  await settle(page);
  run.findings.push(...await screenFindings(page, scope, name));
  await expectNoHorizontalOverflow(page);
  await snap(page, info, name);
}

async function thumbSized(scope: Locator, info: TestInfo) {
  if (info.project.use.hasTouch) await expectTapTargets(scope);
}

function finish(run: Run) {
  expect(run.findings, run.findings.join('\n')).toEqual([]);
  expect(run.errors).toEqual([]);
  expect([...new Set(run.home.unmocked)]).toEqual([]);
}

test.describe('Music Assistant in Accounts & Integrations', () => {
  test('on the household server a person has their own account, and favourites are said to be shared', async ({ page }, info) => {
    const run = await mockServer(page, person(), HOUSEHOLD);
    const tile = await openTile(page);
    await expect(tile.getByTestId('integration-status-Music Assistant')).toHaveText('Household server, your account');
    await thumbSized(tile, info);
    await layout(page, info, run, page.locator('main'), 'music-assistant-tile');
    await expectClearOfBottomBar(page);

    await tile.getByRole('button', { name: 'Manage Music Assistant' }).click();
    const dialog = page.getByTestId('music-assistant-dialog');
    await expect(dialog.getByTestId('music-assistant-status')).toContainText('Connected as kim.');
    await expect(dialog.getByTestId('music-assistant-status')).toContainText('music.example.test:8095');
    await expect(dialog.getByTestId('music-assistant-note')).toContainText('favourites, the library and playlists are shared');
    await expect(dialog.getByRole('button', { name: 'Use the household Music Assistant' })).toHaveCount(0);
    await settle(page);
    await expectFits(page, page.getByRole('dialog'));
    await thumbSized(dialog, info);
    await layout(page, info, run, page.getByRole('dialog'), 'music-assistant-dialog-household');
    expect(run.patches).toEqual([]);
    finish(run);
  });

  test('an own server that does not answer is an error, and switching is the person\'s choice', async ({ page }, info) => {
    const run = await mockServer(page, person({ mass_url: MINE }), HOUSEHOLD, false);
    const tile = await openTile(page);
    await expect(tile.getByTestId('integration-status-Music Assistant')).toHaveText('Your server is not answering');
    await expect(tile.getByRole('button', { name: 'Manage Music Assistant' })).toHaveText('Fix');
    await layout(page, info, run, page.locator('main'), 'music-assistant-tile-own-down');

    await tile.getByRole('button', { name: 'Manage Music Assistant' }).click();
    const dialog = page.getByTestId('music-assistant-dialog');
    await expect(dialog.getByRole('alert')).toContainText(`Your own Music Assistant at ${MINE} did not answer`);
    await expect(dialog.getByLabel('Your own server URL (optional)')).toHaveValue(MINE);
    await settle(page);
    await expectFits(page, page.getByRole('dialog'));
    await thumbSized(dialog, info);
    await layout(page, info, run, page.getByRole('dialog'), 'music-assistant-dialog-own-down');
    expect(run.patches).toEqual([]); // nothing switched by itself

    await dialog.getByRole('button', { name: 'Use the household Music Assistant' }).click();
    await expect.poll(() => run.patches).toEqual([{ clear_fields: ['mass_url'] }]);
    await expect(dialog.getByTestId('music-assistant-status')).toContainText('Connected as kim.');
    await expect(tile.getByTestId('integration-status-Music Assistant')).toHaveText('Household server, your account');
    finish(run);
  });

  test('with no Music Assistant anywhere the card says how to set one up', async ({ page }, info) => {
    const run = await mockServer(page, person({ saved_credentials: [] }), null);
    const tile = await openTile(page);
    await expect(tile.getByTestId('integration-status-Music Assistant')).toHaveText('Not set up');
    await tile.getByRole('button', { name: 'Manage Music Assistant' }).click();
    const dialog = page.getByTestId('music-assistant-dialog');
    await expect(dialog.getByRole('alert')).toContainText('add the Music Assistant integration in HomeHub');
    await settle(page);
    await expectFits(page, page.getByRole('dialog'));
    await layout(page, info, run, page.getByRole('dialog'), 'music-assistant-dialog-none');
    finish(run);
  });
});
