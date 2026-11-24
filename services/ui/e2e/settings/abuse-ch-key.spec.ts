/**
 * The abuse.ch Auth-Key in Accounts & Integrations (docs/APP_VETTING.md),
 * hermetic: every API call is mocked and nothing reaches a server. Runs in
 * every hermetic project, so it is checked on a desktop, phones and tablets.
 *
 * Settings > Accounts & Integrations: a person with no key of their own sees
 * that the household key is used, and can add their own (identity checks it
 * with abuse.ch first; a refused key is not saved and says why).
 * Admin > Users & Devices > Edit: the default user's key is the household
 * key, and the dialog says so. The key is never fetched back for an eye.
 * Every key here is made up.
 */
import { expect as baseExpect, test, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { quietHotReload, screenFindings, settle } from '../fixtures/pageAudit';
import { expectClearOfBottomBar, expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

const GOOD = 'made-up-good-key';

interface Person {
  id: number; username: string; display_name: string; full_name: string; role: string; is_admin: boolean;
  is_system_default: boolean; saved_credentials: string[]; abuse_ch_status: 'own' | 'household' | 'not_set_up';
}

function person(id: number, username: string, extra: Partial<Person> = {}): Person {
  const name = username[0].toUpperCase() + username.slice(1);
  return { id, username, display_name: name, full_name: name, role: 'user', is_admin: false, is_system_default: false,
    saved_credentials: [], abuse_ch_status: 'household', ...extra };
}

async function mockServer(page: Page, me: Person, others: Person[] = []) {
  const people = new Map([me, ...others].map((p) => [p.username, p]));
  const householdSet = () => people.get('default')?.saved_credentials.includes('malwarebazaar_key') ?? true;
  const patches: Array<{ path: string; body: Record<string, unknown> }> = [];
  const reveals: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  // The rest of each page answers from the shared hermetic mocks; the people and their keys from here.
  await quietHotReload(page);
  const home = await mockHome(page, me.is_admin ? 'admin' : 'member');
  await mockHealth(page);
  await mockBible(page);
  await mockSettingsPages(page);
  await page.addInitScript((p) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(p));
  }, me);

  /** identity's rules: the key is checked before it is saved, and only whether it is set comes back. */
  const patch = (who: Person, body: Record<string, unknown>) => {
    const key = body.malwarebazaar_key;
    if (typeof key === 'string' && key.trim()) {
      if (key.trim() !== GOOD) return { status: 400, json: { detail: 'abuse.ch refused this Auth-Key, so it was not saved. Check it at auth.abuse.ch.' } };
      who.saved_credentials = [...new Set([...who.saved_credentials, 'malwarebazaar_key'])];
    }
    if (Array.isArray(body.clear_fields) && body.clear_fields.includes('malwarebazaar_key')) {
      who.saved_credentials = who.saved_credentials.filter((k) => k !== 'malwarebazaar_key');
    }
    for (const p of people.values()) {
      p.abuse_ch_status = p.saved_credentials.includes('malwarebazaar_key') ? 'own'
        : !p.is_system_default && householdSet() ? 'household' : 'not_set_up';
    }
    return { status: 200, json: who };
  };

  await page.route((u: URL) => /^\/api\/users(\/[^/]+)?(\/reveal)?$/.test(u.pathname),
    async (route: Route) => {
      const req = route.request();
      const p = new URL(req.url()).pathname;
      const method = req.method();
      if (p === '/api/users/me' && method === 'GET') return route.fulfill({ json: people.get(me.username) });
      if (p === '/api/users' && method === 'GET') return route.fulfill({ json: [...people.values()] });
      const reveal = /^\/api\/users\/([^/]+)\/reveal$/.exec(p);
      if (reveal) {
        reveals.push(String((req.postDataJSON() as { field?: string }).field));
        return route.fulfill({ json: { field: 'x', value: 'x' } });
      }
      const one = /^\/api\/users\/([^/]+)$/.exec(p);
      if (one && method === 'PATCH') {
        const body = req.postDataJSON() as Record<string, unknown>;
        patches.push({ path: p, body });
        const who = one[1] === 'me' ? people.get(me.username)! : people.get(decodeURIComponent(one[1]))!;
        return route.fulfill(patch(who, body));
      }
      return route.fallback();
    });
  return { patches, reveals, errors, home, findings: [] as string[] };
}

async function openTile(page: Page) {
  await page.goto('/identity');
  const tile = page.getByTestId('abuse-ch-tile');
  await tile.scrollIntoViewIfNeeded();
  await expect(tile).toBeVisible();
  return tile;
}

type Run = Awaited<ReturnType<typeof mockServer>>;

/** Nothing cut off or past its card, no sideways scroll, and a screenshot. */
async function layout(page: Page, info: TestInfo, run: Run, scope: Locator, name: string) {
  await settle(page);
  run.findings.push(...await screenFindings(page, scope, name));
  await expectNoHorizontalOverflow(page);
  await snap(page, info, name);
}

/** 44px under a finger; a mouse gets the smaller desktop controls (as the rest of the app). */
async function thumbSized(scope: Locator, info: TestInfo) {
  if (info.project.use.hasTouch) await expectTapTargets(scope);
}

function finish(run: Run) {
  expect(run.findings, run.findings.join('\n')).toEqual([]);
  expect(run.errors).toEqual([]);
  expect([...new Set(run.home.unmocked)]).toEqual([]);
}

test.describe('the abuse.ch key in Accounts & Integrations', () => {
  // The Admin dialog audits every integration's fieldset (Google Books joined it): ~50 s on a small phone.
  test.describe.configure({ timeout: 90_000 });

  test('a person without their own key uses the household key, and can add their own', async ({ page }, info) => {
    const run = await mockServer(page, person(7, 'kim'), [person(1, 'default', { is_system_default: true, is_admin: true,
      saved_credentials: ['malwarebazaar_key'], abuse_ch_status: 'own' })]);
    const tile = await openTile(page);
    await expect(tile.getByTestId('integration-status-abuse.ch')).toHaveText('Using the household key');
    await thumbSized(tile, info);
    await layout(page, info, run, page.locator('main'), 'abuse-ch-tile');
    await expectClearOfBottomBar(page);
    await tile.scrollIntoViewIfNeeded();

    await tile.getByRole('button', { name: 'Manage abuse.ch' }).click();
    const dialog = page.getByTestId('abuse-ch-dialog');
    await expect(dialog.getByTestId('abuse-ch-status')).toContainText('Using the household key');
    await expect(dialog.getByTestId('abuse-ch-status')).toContainText('An admin set a key for the whole household');
    await expect(dialog).toContainText('auth.abuse.ch');
    await expect(dialog).toContainText('SHA-256');
    await settle(page);
    await expectFits(page, page.getByRole('dialog'));
    await thumbSized(dialog, info);
    await layout(page, info, run, page.getByRole('dialog'), 'abuse-ch-dialog-household');

    // A key abuse.ch refuses is not saved, and the reason is shown.
    await dialog.getByRole('textbox', { name: 'abuse.ch Auth-Key' }).fill('made-up-wrong-key');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('abuse.ch refused this Auth-Key, so it was not saved.')).toBeVisible();
    await expect(dialog).toBeVisible();

    await dialog.getByRole('textbox', { name: 'abuse.ch Auth-Key' }).fill(GOOD);
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toHaveCount(0);
    await expect(tile.getByTestId('integration-status-abuse.ch')).toHaveText('Using your own key');
    expect(run.patches.map((p) => p.body)).toEqual([{ malwarebazaar_key: 'made-up-wrong-key' }, { malwarebazaar_key: GOOD }]);

    // Saved: the eye never fetches it back, and removing it is explicit.
    await tile.getByRole('button', { name: 'Manage abuse.ch' }).click();
    await expect(dialog.getByTestId('abuse-ch-status')).toContainText('Using your own key');
    await expect(dialog).toContainText('Saved');
    await dialog.getByRole('button', { name: 'Remove my key' }).click();
    await thumbSized(dialog, info);
    await layout(page, info, run, page.getByRole('dialog'), 'abuse-ch-dialog-own');
    await page.getByRole('dialog', { name: 'abuse.ch' }).getByRole('button', { name: 'Remove', exact: true }).click();
    await expect(tile.getByTestId('integration-status-abuse.ch')).toHaveText('Using the household key');
    expect(run.patches.at(-1)?.body).toEqual({ clear_fields: ['malwarebazaar_key'] });
    expect(run.reveals).toEqual([]);
    finish(run);
  });

  test('without a household key the card says it is not set up', async ({ page }, info) => {
    const run = await mockServer(page, person(7, 'kim', { abuse_ch_status: 'not_set_up' }),
      [person(1, 'default', { is_system_default: true, is_admin: true, abuse_ch_status: 'not_set_up' })]);
    const tile = await openTile(page);
    await expect(tile.getByTestId('integration-status-abuse.ch')).toHaveText('Not set up');
    await tile.getByRole('button', { name: 'Manage abuse.ch' }).click();
    await expect(page.getByTestId('abuse-ch-status')).toContainText('No household key is set yet');
    await layout(page, info, run, page.getByRole('dialog'), 'abuse-ch-dialog-none');
    finish(run);
  });

  test("Admin's Edit user dialog sets the household key on the default user", async ({ page }, info) => {
    const admin = person(2, 'robin', { is_admin: true, role: 'admin', abuse_ch_status: 'not_set_up' });
    const run = await mockServer(page, admin, [
      person(1, 'default', { is_system_default: true, is_admin: true, abuse_ch_status: 'not_set_up' }),
      person(7, 'kim', { abuse_ch_status: 'not_set_up' }),
    ]);
    await page.goto('/admin/users');
    const edit = page.getByRole('button', { name: 'Edit default', exact: true });
    await edit.scrollIntoViewIfNeeded();
    await edit.click();
    const dialog = page.getByRole('dialog');
    const field = dialog.getByRole('group', { name: 'abuse.ch' });
    await field.scrollIntoViewIfNeeded();
    await expect(field.getByTestId('household-default-abuse_ch')).toHaveText(
      'This is the household key: everyone who has not set their own uses it.');
    await expect(field).toContainText('auth.abuse.ch');
    await field.getByRole('textbox', { name: 'abuse.ch Auth-Key' }).fill(GOOD);
    await thumbSized(field, info);
    await settle(page);
    await expectFits(page, dialog);
    await layout(page, info, run, dialog, 'abuse-ch-admin-edit-default');
    await dialog.getByRole('button', { name: /Save User/ }).click();
    await expect.poll(() => run.patches.find((p) => p.path === '/api/users/default')?.body.malwarebazaar_key).toBe(GOOD);
    expect(run.reveals).toEqual([]);

    // Another person's dialog: blank means the household key.
    await page.getByRole('button', { name: 'Edit kim', exact: true }).click();
    const kim = page.getByRole('dialog').getByRole('group', { name: 'abuse.ch' });
    await kim.scrollIntoViewIfNeeded();
    await expect(kim.getByTestId('household-default-abuse_ch')).toHaveText('Leave it blank to use the household key (set on the default user).');
    await layout(page, info, run, page.getByRole('dialog'), 'abuse-ch-admin-edit-member');
    finish(run);
  });
});
