/**
 * Google Books and Open Library in Accounts & Integrations (docs/INTEGRATIONS.md),
 * hermetic: every API call is mocked and nothing reaches a server. Runs in every
 * hermetic project, so it is checked on a desktop, phones and tablets.
 *
 * Settings > Accounts & Integrations: Google Books starts "Not set up"; the key
 * goes in a show/hide password field, Test shows exactly what Google answered
 * (a 429 here), and saving the key turns Google Books on. Open Library is on
 * with no key and can be turned off. Knowledge > Family library > Add a book
 * says which catalogues fill a book in and why one is not used, with a link to
 * the settings. Every key and address here is made up.
 */
import { expect as baseExpect, test, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { mockLibraryPages } from '../fixtures/libraryPagesMocks';
import { quietHotReload, screenFindings, settle } from '../fixtures/pageAudit';
import { expectClearOfBottomBar, expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

const KEY = 'made-up-books-key';
const QUOTA = "Google Books answered 429: the API key's daily quota is used up. It resets at midnight Pacific time, or raise the quota in Google Cloud Console.";

interface Source {
  name: string; enabled: boolean; enabled_from: 'own' | 'household' | 'default'; ready: boolean; reason: string;
  key_set?: boolean; key_source?: 'own' | 'household' | 'absent'; contact_email?: string;
}

/** identity's rules for the book catalogues, kept in the browser. */
function catalogues(state: { key: boolean; google: boolean | null; open: boolean | null }) {
  const googleOn = state.google ?? false;
  const openOn = state.open ?? true;
  const google: Source = {
    name: 'Google Books', enabled: googleOn, enabled_from: state.google === null ? 'default' : 'own',
    ready: googleOn && state.key, key_set: state.key, key_source: state.key ? 'own' : 'absent',
    reason: googleOn && state.key ? '' : googleOn
      ? 'Google Books: turned on but no API key is set in Settings > Accounts & Integrations'
      : state.key ? 'Google Books: turned off in Settings > Accounts & Integrations'
        : 'Google Books: not set up (add an API key in Settings > Accounts & Integrations)',
  };
  const open: Source = {
    name: 'Open Library', enabled: openOn, enabled_from: state.open === null ? 'default' : 'own', ready: openOn,
    reason: openOn ? '' : 'Open Library: turned off in Settings > Accounts & Integrations', contact_email: '',
  };
  return { order: ['google_books', 'open_library'], sources: { google_books: google, open_library: open } };
}

async function mockServer(page: Page) {
  const state = { key: false, google: null as boolean | null, open: null as boolean | null };
  const me = () => ({
    id: 7, username: 'kim', display_name: 'Kim', full_name: 'Kim', role: 'admin', is_admin: true, is_system_default: false,
    saved_credentials: state.key ? ['google_books_key'] : [], abuse_ch_status: 'household', book_metadata_status: catalogues(state),
  });
  const patches: Record<string, unknown>[] = [];
  const tests: Record<string, unknown>[] = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await quietHotReload(page);
  const home = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockSettingsPages(page);
  await mockLibraryPages(page);
  await page.addInitScript((p) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(p));
  }, me());

  await page.route((u: URL) => u.pathname === '/api/users/me' || u.pathname === '/api/users/me/book-metadata/test'
    || u.pathname === '/api/library/catalogues', async (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/api/library/catalogues') return route.fulfill({ json: catalogues(state) });
    if (p === '/api/users/me/book-metadata/test') {
      const body = req.postDataJSON() as Record<string, unknown>;
      tests.push(body);
      return route.fulfill({ json: body.source === 'google_books'
        ? { ok: false, source: 'google_books', status: 429, message: QUOTA }
        : { ok: true, source: 'open_library', status: 200, message: 'Open Library works: it found “The Brothers Karamazov” (ISBN 9780140449136).' } });
    }
    if (req.method() === 'GET') return route.fulfill({ json: me() });
    const body = req.postDataJSON() as Record<string, unknown>;
    patches.push(body);
    if (typeof body.google_books_key === 'string' && body.google_books_key) state.key = true;
    const settings = (body.book_metadata ?? {}) as Record<string, boolean | null>;
    if ('google_books_enabled' in settings) state.google = settings.google_books_enabled;
    if ('open_library_enabled' in settings) state.open = settings.open_library_enabled;
    return route.fulfill({ json: me() });
  });
  return { patches, tests, errors, home, findings: [] as string[] };
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

async function openTile(page: Page, testId: string) {
  await page.goto('/identity');
  const tile = page.getByTestId(testId);
  await tile.scrollIntoViewIfNeeded();
  await expect(tile).toBeVisible();
  return tile;
}

test.describe('the book catalogues in Accounts & Integrations', () => {
  test.describe.configure({ timeout: 90_000 });

  test('Google Books: paste a key, test it, see the exact error, save it', async ({ page }, info) => {
    const run = await mockServer(page);
    const tile = await openTile(page, 'google-books-tile');
    await expect(tile.getByTestId('integration-status-google-books')).toHaveText('Not set up');
    await thumbSized(tile, info);
    await layout(page, info, run, page.locator('main'), 'book-catalogue-tiles');
    await expectClearOfBottomBar(page);

    await tile.getByRole('button', { name: 'Manage Google Books' }).click();
    const dialog = page.getByTestId('google-books-dialog');
    await expect(dialog.getByTestId('google-books-status')).toContainText('not set up (add an API key in Settings > Accounts & Integrations)');
    const field = dialog.getByLabel('Google Books API key', { exact: true });
    await expect(field).toHaveAttribute('type', 'password');
    await field.fill(KEY);
    await dialog.getByRole('button', { name: 'Show password' }).click();
    await expect(field).toHaveAttribute('type', 'text');
    await expect(field).toHaveValue(KEY);

    await dialog.getByRole('button', { name: /Test/ }).click();
    await expect(dialog.getByTestId('google-books-test-result')).toHaveText(QUOTA);
    expect(run.tests).toEqual([{ source: 'google_books', api_key: KEY }]);
    await settle(page);
    await expectFits(page, page.getByRole('dialog'));
    await thumbSized(dialog, info);
    await layout(page, info, run, page.getByRole('dialog'), 'google-books-dialog-test-failed');

    await dialog.getByRole('button', { name: /Save key/ }).click();
    await expect.poll(() => run.patches.at(-1)).toEqual({ google_books_key: KEY, book_metadata: { google_books_enabled: true } });
    await expect(dialog.getByTestId('google-books-status')).toContainText('On, with your own key');
    await expect(dialog.getByLabel('Google Books API key', { exact: true })).toHaveValue('');
    await page.keyboard.press('Escape');
    await expect(tile.getByTestId('integration-status-google-books')).toHaveText('On, with your own key');
    finish(run);
  });

  test('Open Library: on with no key, tested, and turned off', async ({ page }, info) => {
    const run = await mockServer(page);
    const tile = await openTile(page, 'open-library-tile');
    await expect(tile.getByTestId('integration-status-open-library')).toHaveText('On');
    await tile.getByRole('button', { name: 'Manage Open Library' }).click();
    const dialog = page.getByTestId('open-library-dialog');
    await dialog.getByRole('button', { name: /Test/ }).click();
    await expect(dialog.getByTestId('open-library-test-result')).toContainText('Open Library works');
    await settle(page);
    await expectFits(page, page.getByRole('dialog'));
    await thumbSized(dialog, info);
    await layout(page, info, run, page.getByRole('dialog'), 'open-library-dialog');

    await dialog.getByRole('switch', { name: 'Use Open Library for book details' }).click();
    await expect.poll(() => run.patches.at(-1)).toEqual({ book_metadata: { open_library_enabled: false } });
    await page.keyboard.press('Escape');
    await expect(tile.getByTestId('integration-status-open-library')).toHaveText('Off');
    finish(run);
  });

  test('the library says which catalogues fill a book in, and why one is not used', async ({ page }, info) => {
    const run = await mockServer(page);
    await page.goto('/knowledge');
    const panel = page.getByTestId('library-panel');
    await panel.scrollIntoViewIfNeeded();
    await panel.getByTestId('library-add-button').click();
    const note = panel.getByTestId('library-catalogues');
    await expect(note).toContainText('Book details from Open Library.');
    await expect(note).toContainText('Not used: Google Books: not set up (add an API key in Settings > Accounts & Integrations).');
    const link = note.getByRole('link', { name: 'Settings > Accounts & Integrations' }).first();
    await link.scrollIntoViewIfNeeded();
    await thumbSized(note, info);
    await layout(page, info, run, panel, 'library-catalogues-note');
    await link.click();
    await expect(page).toHaveURL(/\/identity$/);
    await expect(page.getByTestId('google-books-tile')).toBeVisible();
    finish(run);
  });
});
