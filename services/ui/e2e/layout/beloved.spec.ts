import { test, expect as baseExpect, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, CONVERSATIONS } from '../fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload, type Allowed } from '../fixtures/pageAudit';
import { deriveKey, makeVerifier, openRecord, randomBytes, sealRecord, toBase64, PBKDF2_ITERATIONS } from '../../src/lib/privateChatCrypto';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * Beloved, the couple's page inside the private chat, on every form factor:
 * the home page with both pictures and the days-married counter, adding a
 * special day, today's devotion, a love note, a prayer marked prayed for and
 * answered, a date idea suggested, and Our details. The server is mocked and
 * the ciphertext is real: the test checks the server only ever receives sealed
 * records, nothing is readable after a lock, and nothing scrolls sideways. A
 * second test checks the entry point is never there for an administrator who
 * is not one of the couple, nor for a child or a teen.
 */

const PASSWORD = 'a long shared sentence';
const TOKEN = 'roombeloved1';
const SEC = Math.floor(Date.now() / 1000);
const WEDDING = '2016-04-09';

interface Run { mocks: HomeMocks; errors: string[]; found: string[] }

interface SealedRow { id: string; kind: string; at: number; updated_at: number; by: string; own: boolean; by_name: string; sealed: string }

interface Server {
  chat: Record<string, unknown>;
  records: SealedRow[];
  /** Every body the page sent to a Beloved route. */
  sent: Array<{ method: string; path: string; body: string }>;
  belovedCalls: number;
  /** The caller's children as identity knows them (the overview's `children`). */
  children?: Array<{ username: string; display_name: string; birth_date: string | null }>;
}

async function serve(page: Page, server: Server): Promise<void> {
  await page.route(/\/api\/(private-chat\/|communication\/talk\/(conversations|messages)(\?|$))/, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (p === '/api/private-chat/status') {
      return route.fulfill({ json: { eligible: true, chats: [server.chat], min_iterations: PBKDF2_ITERATIONS, partners: [] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}` && req.method() === 'GET') return route.fulfill({ json: server.chat });
    // The thread's edits and reactions (none here).
    if (p === `/api/private-chat/chats/${TOKEN}/marks`) return route.fulfill({ json: { marks: [] } });
    const beloved = new RegExp(`^/api/private-chat/chats/${TOKEN}/beloved(?:/([a-z]+))?(?:/([0-9a-f]{32}))?$`).exec(p);
    if (beloved) {
      server.belovedCalls += 1;
      const [, kind, id] = beloved;
      if (req.method() !== 'GET') server.sent.push({ method: req.method(), path: p, body: req.postData() ?? '' });
      if (req.method() === 'GET') {
        return route.fulfill({ json: { me: 'parent', records: server.records, children: server.children ?? [], couple: [
          { username: 'parent', display_name: 'Parent', avatar: null, family_role: 'father', me: true },
          { username: 'robin', display_name: 'Robin', avatar: null, family_role: 'mother', me: false },
        ] } });
      }
      if (req.method() === 'POST' && kind) {
        const body = req.postDataJSON() as { sealed: string; notify?: boolean };
        // Numbered by what was sent, so an id is never reused after a delete.
        const row: SealedRow = { id: (server.sent.length + 1).toString(16).padStart(32, 'b'), kind, at: Math.floor(Date.now() / 1000),
          updated_at: Math.floor(Date.now() / 1000), by: 'parent', own: true, by_name: 'Parent', sealed: body.sealed };
        server.records.push(row);
        return route.fulfill({ json: { ...row, notified: Boolean(body.notify) } });
      }
      const row = server.records.find((r) => r.id === id);
      if (!row) return route.fulfill({ status: 404, json: { detail: 'No such record.' } });
      if (req.method() === 'PUT') {
        row.sealed = (req.postDataJSON() as { sealed: string }).sealed;
        row.updated_at += 1;
        return route.fulfill({ json: row });
      }
      server.records = server.records.filter((r) => r.id !== id);
      return route.fulfill({ json: { deleted: id } });
    }
    if (p === '/api/communication/talk/conversations') {
      const own = [{ token: TOKEN, display_name: 'Private chat', type: 2, private: true, last_message: 'Encrypted message',
        last_message_actor: 'Robin', last_message_actor_id: 'robin', last_message_timestamp: SEC - 30, last_activity: SEC - 30,
        unread_messages: 0, last_common_read: 0, last_read: 0, can_moderate: true }];
      return route.fulfill({ json: { status: 'SUCCESS', detail: { conversations: [...own, ...CONVERSATIONS] } } });
    }
    if (p === '/api/communication/talk/messages' && url.searchParams.get('token') === TOKEN) {
      return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [] } } });
    }
    return route.fallback();
  });
  // A short passage, as the Bible service answers a single verse.
  await page.route(/\/api\/bible\/passages/, (route) => route.fulfill({ json: {
    version: 'tst', requested: 'x', reference: 'Made-up 1:1', spans: [], count: 1,
    verses: [{ version: 'tst', osis: 'Gen', book_name: 'Genesis', chapter: 1, verse: 1, reference: 'Genesis 1:1',
      text: 'A made-up verse for the test, about love that is patient and kind.' }],
  } }));
}

async function setup(): Promise<{ server: Server; key: CryptoKey }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(PASSWORD, kdf);
  const row = async (id: string, kind: string, record: object, by = 'robin'): Promise<SealedRow> => ({
    id, kind, at: SEC - 3600, updated_at: SEC - 3600, by, own: by === 'parent', by_name: by === 'parent' ? 'Parent' : 'Robin',
    sealed: await sealRecord(key, record),
  });
  const server: Server = {
    chat: { token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin', created_at: SEC - 86400,
      version: 1, kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key), beloved: true },
    records: [
      await row('a'.repeat(32), 'profile', { b: 'profile', wedding: WEDDING, birthdays: { robin: '1990-07-04' } }),
      await row('c'.repeat(32), 'note', { b: 'note', text: 'Thank you for the coffee this morning.' }),
    ],
    sent: [],
    belovedCalls: 0,
  };
  return { server, key };
}

async function open(page: Page, server: Server, me?: Record<string, unknown>, unlock = true): Promise<Run> {
  await quietHotReload(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await serve(page, server);
  if (me) await page.route('**/api/users/me', (route) => route.fulfill({ json: me }));
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) errors.push(m.text().slice(0, 400));
  });
  await page.goto('/family');
  await expect(page.getByText('Camping trip planning').first()).toBeVisible();
  await page.getByRole('button', { name: /Private chat/ }).first().click();
  if (!unlock) return { mocks, errors, found: [] };
  await page.getByTestId('private-password').fill(PASSWORD);
  await page.getByTestId('private-unlock').click();
  await expect(page.getByTestId('private-chat')).toBeVisible();
  return { mocks, errors, found: [] };
}

async function check(page: Page, info: TestInfo, run: Run, where: string, scope: Locator, allowed: Allowed[] = []): Promise<void> {
  await settle(page);
  run.found.push(...await screenFindings(page, scope, where, allowed));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

function done(run: Run) {
  expect(run.found, run.found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('Beloved on every form factor', () => {
  test.describe.configure({ timeout: 180_000 });

  test('home, special days, today, notes and prayer, date ideas, details; sealed throughout and gone on lock', async ({ page }, info) => {
    const { server, key } = await setup();
    const run = await open(page, server);

    // The heart on the bar: Robin's note has not been seen yet.
    const indicator = page.getByTestId('private-love-indicator');
    await expect(indicator).toHaveText('1');
    await expect(page.getByTestId('private-beloved-open')).toHaveAttribute('aria-label', 'Beloved, 1 new from Robin');
    await check(page, info, run, 'private chat bar with a heart', page.getByTestId('private-chat'));

    await page.getByTestId('private-beloved-open').click();
    const beloved = page.getByTestId('beloved');
    await expect(beloved).toBeVisible();
    // Seen once Beloved opens; your own heart below never lights it.
    await expect(indicator).toHaveCount(0);
    const home = beloved.getByTestId('beloved-home');
    await expect(home).toBeVisible();
    // Both of them, side by side, and how long they have been married.
    await expect(home.getByRole('img', { name: 'Parent' })).toBeVisible();
    await expect(home.getByRole('img', { name: 'Robin' })).toBeVisible();
    await expect(home.getByTestId('beloved-names')).toHaveText('Parent & Robin');
    await expect(home.getByTestId('beloved-counter')).toContainText('days married');
    await expect(home.getByTestId('beloved-counter')).toContainText('years');
    await expect(home.getByTestId('beloved-verse')).toContainText('patient and kind');
    await expect(home.getByTestId('beloved-latest-note')).toContainText('Thank you for the coffee');
    await check(page, info, run, 'beloved home', beloved);

    await home.getByTestId('beloved-heart').click();
    await expect.poll(() => server.records.filter((r) => r.kind === 'heart').length).toBe(1);
    expect(JSON.parse(server.sent.find((s) => s.path.endsWith('/heart'))!.body).notify).toBe(true);
    await beloved.getByRole('button', { name: 'Close Beloved' }).click();
    await expect(beloved).toHaveCount(0);
    await expect(page.getByTestId('private-beloved-open')).toHaveAttribute('aria-label', 'Beloved');
    await expect(indicator).toHaveCount(0);
    // A new heart from Robin lights it again on the next check (every 30 seconds).
    server.records.push({ ...server.records[1], id: 'd'.repeat(32), kind: 'heart', at: Math.floor(Date.now() / 1000) + 5 });
    await expect(indicator).toHaveText('1', { timeout: 40_000 });
    await page.getByTestId('private-beloved-open').click();
    await expect(beloved).toBeVisible();
    await expect(indicator).toHaveCount(0);

    // Special days: the anniversary, Robin's birthday, milestones, and one of their own.
    await beloved.getByTestId('beloved-tab-days').click();
    const days = beloved.getByTestId('beloved-days');
    await expect(days.getByText(/wedding anniversary/)).toBeVisible();
    await expect(days.getByText("Robin's birthday")).toBeVisible();
    await expect(days.getByText(/days married/).first()).toBeVisible();
    await days.getByTestId('beloved-day-name').fill('The day we got engaged');
    await days.getByTestId('beloved-day-date').fill('2015-12-24');
    await days.getByTestId('beloved-day-add').click();
    await expect(days.getByText('The day we got engaged')).toBeVisible();
    await check(page, info, run, 'beloved days', beloved);

    // Today: the verse, a devotion and a question, marked done and answered.
    await beloved.getByTestId('beloved-tab-today').click();
    const today = beloved.getByTestId('beloved-today');
    await expect(today.getByTestId('beloved-verse-text')).toContainText('patient and kind');
    await expect(today.getByTestId('beloved-reflection')).not.toBeEmpty();
    await expect(today.getByTestId('beloved-question')).not.toBeEmpty();
    await today.getByTestId('beloved-answer').fill('Walking together after dinner.');
    await today.getByTestId('beloved-devotion-done').click();
    await expect(today.getByTestId('beloved-devotion-done')).toContainText('Done today');
    await check(page, info, run, 'beloved today', beloved);

    // Notes and prayer.
    await beloved.getByTestId('beloved-tab-notes').click();
    const notes = beloved.getByTestId('beloved-notes');
    await notes.getByTestId('beloved-note-input').fill('Counting the hours until tonight.');
    await notes.getByTestId('beloved-note-send').click();
    await expect(notes.getByText('Counting the hours until tonight.')).toBeVisible();
    await notes.getByTestId('beloved-prayer-input').fill('Wisdom for the new job.');
    await notes.getByTestId('beloved-prayer-add').click();
    const prayer = notes.getByTestId('beloved-prayer').filter({ hasText: 'Wisdom for the new job.' });
    await expect(prayer).toBeVisible();
    await prayer.getByTestId('beloved-prayed').click();
    await expect(prayer).toContainText('prayed 1 time');
    await check(page, info, run, 'beloved notes and prayer', beloved);
    await prayer.getByTestId('beloved-answered-open').click();
    await prayer.getByTestId('beloved-answer-how').fill('The offer came on Friday.');
    await prayer.getByTestId('beloved-answered-save').click();
    const answered = notes.getByTestId('beloved-answered');
    await expect(answered).toContainText('The offer came on Friday.');
    await check(page, info, run, 'beloved answered prayer', beloved);

    // Date ideas: suggest one, then mark it done.
    await beloved.getByTestId('beloved-tab-dates').click();
    const dates = beloved.getByTestId('beloved-dates');
    const idea = dates.getByTestId('beloved-idea').first();
    const ideaTitle = (await idea.locator('span').nth(1).textContent()) ?? '';
    await idea.getByTestId('beloved-idea-suggest').click();
    const suggested = dates.getByTestId('beloved-suggested');
    await expect(suggested).toContainText(ideaTitle);
    expect(JSON.parse(server.sent.filter((s) => s.path.endsWith('/idea')).at(-1)!.body).notify).toBe(true);
    await check(page, info, run, 'beloved date ideas', beloved);
    await suggested.getByTestId('beloved-idea-done').click();
    await expect(dates.getByTestId('beloved-idea-had')).toContainText(ideaTitle);

    // Our details: the shared settings sheet.
    await beloved.getByTestId('beloved-settings-open').click();
    const sheet = page.getByTestId('beloved-settings');
    await expect(sheet.getByTestId('beloved-wedding')).toHaveValue(WEDDING);
    await sheet.getByTestId('beloved-first-date').fill('2014-02-14');
    await check(page, info, run, 'beloved details', sheet);
    await sheet.getByTestId('beloved-settings-save').click();
    await expect(sheet).toHaveCount(0);

    // The server only ever received sealed records: none of what was written is readable in what was sent.
    const sentText = JSON.stringify(server.sent);
    for (const secret of ['engaged', '2015-12-24', 'Counting the hours', 'Wisdom for the new job', 'The offer came', 'Walking together',
      '2014-02-14', WEDDING, ideaTitle]) {
      expect(sentText, secret).not.toContain(secret);
    }
    for (const s of server.sent.filter((x) => x.method !== 'DELETE')) expect(JSON.parse(s.body).sealed).toContain('```jarvis-envelope');
    // And the records do open with the chat's key, to exactly what was written.
    const profiles = server.records.filter((r) => r.kind === 'profile');
    expect(await openRecord(key, profiles[0].sealed)).toMatchObject({ b: 'profile', wedding: WEDDING, firstDate: '2014-02-14' });

    // Going to the background locks the chat: Beloved and everything decrypted are gone.
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.getByTestId('private-lock')).toBeVisible();
    await expect(page.getByTestId('beloved')).toHaveCount(0);
    for (const text of ['Counting the hours', 'Thank you for the coffee', 'Wisdom for the new job', 'days married']) {
      await expect(page.getByText(text)).toHaveCount(0);
    }
    expect(await page.locator('img[src^="blob:"]').count()).toBe(0);
    done(run);
  });

  test('Just us: the private calendar, a plan and its reminders; sealed, generic outside its tab, gone on lock', async ({ page }, info) => {
    const { server, key } = await setup();
    const local = (offset: number) => {
      const d = new Date();
      d.setDate(d.getDate() + offset);
      return { key: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`, month: d.getMonth() };
    };
    const [today, earlier, later] = [local(0), local(-3), local(5)];
    const seeded = async (id: string, kind: string, record: object): Promise<SealedRow> => ({
      id, kind, at: SEC - 60, updated_at: SEC - 60, by: 'robin', own: false, by_name: 'Robin', sealed: await sealRecord(key, record),
    });
    // Robin marked a day three days ago and planned this evening; both arrived after the note.
    server.records.push(
      await seeded('e'.repeat(32), 'together', { b: 'together', day: earlier.key, note: 'A made-up note about a walk' }),
      await seeded('f'.repeat(32), 'plan', { b: 'plan', day: today.key, idea: 'hotel' }),
    );
    // The phone's alarm scheduler, as the Android app has it: every list the page asks for is kept here.
    await page.addInitScript(() => {
      const synced: unknown[] = [];
      (window as unknown as { __synced: unknown[] }).__synced = synced;
      window.__jarvisBelovedRemindersMock = {
        sync: async ({ reminders }) => {
          synced.push(reminders);
          return { scheduled: reminders.length };
        },
        clear: async () => ({ scheduled: 0 }),
      };
    });
    const run = await open(page, server);

    // The bar's heart counts Robin's note only: the calendar's records never light it.
    await expect(page.getByTestId('private-love-indicator')).toHaveText('1');
    await page.getByTestId('private-beloved-open').click();
    const beloved = page.getByTestId('beloved');
    const home = beloved.getByTestId('beloved-home');
    await expect(home).toBeVisible();
    // The home page previews nothing from the tab.
    for (const text of ['A made-up note', 'days since', 'Last time', 'Planned', 'A night away']) await expect(home.getByText(text)).toHaveCount(0);

    await beloved.getByTestId('beloved-tab-justus').click();
    await expect(beloved.getByTestId('beloved-tab-justus')).toHaveText('Just us');
    const tab = beloved.getByTestId('beloved-justus');
    await expect(tab.getByTestId('justus-since-days')).toHaveText('3');
    await expect(tab.getByTestId('justus-counts')).toContainText('This year:');
    const calendar = tab.getByTestId('justus-calendar');
    // Today has Robin's plan; the marked day three days ago may be last month.
    await expect(calendar.locator(`[data-day="${today.key}"]`)).toHaveAttribute('data-planned', 'true');
    if (earlier.month !== new Date().getMonth()) await calendar.getByTestId('justus-prev').click();
    await expect(calendar.locator(`[data-day="${earlier.key}"]`)).toHaveAttribute('data-marked', 'true');
    await calendar.locator(`[data-day="${earlier.key}"]`).click();
    await expect(tab.getByTestId('justus-selected')).toContainText('A made-up note about a walk');
    await expect(tab.getByTestId('justus-selected')).toContainText('Robin marked it');
    // Robin's entry is Robin's: there is nothing to delete it with.
    await expect(tab.getByTestId('justus-entry-delete')).toHaveCount(0);
    const monthName = await calendar.getByTestId('justus-month').textContent();
    await calendar.getByTestId('justus-next').click();
    await expect(calendar.getByTestId('justus-month')).not.toHaveText(monthName ?? '');
    if (earlier.month === new Date().getMonth()) await calendar.getByTestId('justus-prev').click();
    await expect(calendar.locator(`[data-day="${today.key}"]`)).toBeVisible();
    await check(page, info, run, 'just us', beloved);

    // One tap for today.
    await tab.getByTestId('justus-today').click();
    await expect(tab.getByTestId('justus-since-days')).toHaveText('0');
    await expect(tab.getByTestId('justus-today')).toHaveText('Today is marked');
    await expect(tab.getByTestId('justus-today')).toBeDisabled();
    expect(JSON.parse(server.sent.find((s) => s.path.endsWith('/together'))!.body).notify).toBe(false);
    // Remove your own, then mark it again with a private note.
    await calendar.locator(`[data-day="${today.key}"]`).click();
    const selected = tab.getByTestId('justus-selected');
    await expect(selected).toContainText('You marked it');
    await selected.getByTestId('justus-entry-delete').click();
    await expect(tab.getByTestId('justus-since-days')).toHaveText('3');
    await selected.getByTestId('justus-note').fill('Our quiet evening by the fire');
    await selected.getByTestId('justus-mark').click();
    await expect(selected).toContainText('Our quiet evening by the fire');
    await expect(tab.getByTestId('justus-since-days')).toHaveText('0');

    // Robin's plan for today: either of them can mark it done.
    const robinsPlan = tab.getByTestId('justus-plan').filter({ hasText: 'A night away at a hotel' });
    await expect(robinsPlan).toContainText('Robin planned it');
    await robinsPlan.getByTestId('justus-plan-done').click();
    await expect(robinsPlan).toHaveCount(0);

    // Plan an evening: Robin is told only that something is waiting.
    const form = tab.getByTestId('justus-plan-form');
    await form.getByTestId('justus-plan-day').fill(later.key);
    await form.getByTestId('justus-plan-time').fill('19:30');
    await form.getByTestId('justus-idea').filter({ hasText: 'Candlelit dinner at home' }).click();
    await form.getByTestId('justus-plan-note').fill('Book the sitter early');
    await check(page, info, run, 'just us plan form', beloved);
    await form.getByTestId('justus-plan-add').click();
    const mine = tab.getByTestId('justus-plan').filter({ hasText: 'Candlelit dinner at home' });
    await expect(mine).toContainText('at 19:30');
    await expect(mine).toContainText('Book the sitter early');
    // Not until the day comes.
    await expect(mine.getByTestId('justus-plan-done')).toBeDisabled();
    expect(JSON.parse(server.sent.filter((s) => s.path.endsWith('/plan')).at(-1)!.body).notify).toBe(true);

    // This phone's gentle reminder, off by default.
    const nudge = tab.getByTestId('justus-nudge');
    await expect(nudge).toHaveValue('0');
    await nudge.selectOption('7');
    await check(page, info, run, 'just us planned and reminder', beloved);
    type Synced = Array<{ id: number; at: number; title: string; body: string }>;
    const lastSync = async () => ((await page.evaluate(() => (window as unknown as { __synced: Synced[] }).__synced)).at(-1) ?? []);
    await expect.poll(async () => (await lastSync()).filter((r) => r.body === 'A little reminder for the two of you.').length).toBe(2);
    // Every reminder the phone holds is generic: no tab, no idea, no note, no word that says what it is.
    const reminderText = JSON.stringify(await lastSync());
    for (const word of [/intima/i, /\bsex/i, /night/i, /together/i, /\bbed\b/i, /romanc/i, /candle/i, /hotel/i, /sitter/i, /fire/i, /just us/i]) {
      expect(reminderText).not.toMatch(word);
    }

    // Cancel it.
    await mine.getByTestId('justus-plan-cancel').click();
    await expect(mine).toHaveCount(0);
    expect(server.records.filter((r) => r.kind === 'plan' && r.own)).toEqual([]);

    // The server only received sealed records: nothing typed or picked, and no revealing word, in what was sent.
    expect(server.sent.length).toBeGreaterThanOrEqual(6);
    const bodies = JSON.stringify(server.sent.map((s) => s.body));
    for (const secret of ['Our quiet evening', 'by the fire', 'Book the sitter', 'sitter', '19:30', 'candles', 'Candlelit', 'hotel', later.key, today.key,
      earlier.key, 'together', 'night', 'intima', 'Just us']) {
      expect(bodies, secret).not.toContain(secret);
    }
    for (const s of server.sent.filter((x) => x.method !== 'DELETE')) expect(JSON.parse(s.body).sealed).toContain('```jarvis-envelope');
    // And the records open with the chat's key to exactly what was written.
    const marked = server.records.filter((r) => r.kind === 'together' && r.own);
    expect(await openRecord(key, marked[0].sealed)).toEqual({ b: 'together', day: today.key, note: 'Our quiet evening by the fire' });
    const finished = server.records.find((r) => r.id === 'f'.repeat(32))!;
    expect(await openRecord(key, finished.sealed)).toMatchObject({ b: 'plan', day: today.key, idea: 'hotel', doneAt: expect.any(Number) });

    // A lock takes it all away.
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.getByTestId('private-lock')).toBeVisible();
    await expect(page.getByTestId('beloved')).toHaveCount(0);
    for (const text of ['Our quiet evening', 'A made-up note', 'Just us', 'days since']) await expect(page.getByText(text)).toHaveCount(0);
    done(run);
  });

  // The app never even asks a non-parent about private chats, so these never reach an unlocked chat.
  const outsiders: Array<[string, Record<string, unknown>]> = [
    ['an administrator who is not a parent', { id: 9, username: 'admin', display_name: 'Admin', full_name: 'Admin', role: 'admin',
      is_admin: true, family_role: 'other', is_minor: false }],
    ['a teen', { id: 10, username: 'teen', display_name: 'Teen', full_name: 'Teen', role: 'user', is_admin: false, family_role: 'teen', is_minor: true }],
    ['a child', { id: 11, username: 'kid', display_name: 'Kid', full_name: 'Kid', role: 'user', is_admin: false, family_role: 'child', is_minor: true }],
  ];
  for (const [who, me] of outsiders) {
    test(`never shows Beloved to ${who}`, async ({ page }) => {
      const { server } = await setup();
      const run = await open(page, server, me, false);
      await expect(page.getByTestId('private-lock')).toBeVisible();
      await expect(page.getByTestId('private-beloved-open')).toHaveCount(0);
      await expect(page.getByText('Beloved', { exact: true })).toHaveCount(0);
      // Not even asked for: nothing about it reaches the server either.
      expect(server.belovedCalls).toBe(0);
      done(run);
    });
  }

  test("children's birthdays, the weather for a stargazing night, and nothing under the phone's nav bar", async ({ page }, info) => {
    const { server, key } = await setup();
    const local = (offset: number) => {
      const d = new Date();
      d.setDate(d.getDate() + offset);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    };
    // Turning 9 the day after tomorrow, inside the week the forecast covers.
    const soon = local(2);
    const nineYearsAgo = `${Number(soon.slice(0, 4)) - 9}${soon.slice(4)}`;
    server.children = [
      { username: 'kidsky', display_name: 'Sky', birth_date: nineYearsAgo },
      { username: 'kidjamie', display_name: 'Jamie', birth_date: null },
    ];
    // The Android app's edge-to-edge insets, as Capacitor's SystemBars injects them (env() reads 0 there).
    const NAV_BAR = 48;
    const run = await open(page, server);
    // Capacitor sets them on the page once it has loaded.
    await page.evaluate((px) => document.documentElement.style.setProperty('--safe-area-inset-bottom', `${px}px`), NAV_BAR);
    await page.getByTestId('private-beloved-open').click();
    const beloved = page.getByTestId('beloved');
    await expect(beloved.getByTestId('beloved-home')).toBeVisible();

    // Our details: the family's children are offered, Sky with the birth date Jarvis has.
    await beloved.getByTestId('beloved-settings-open').click();
    const sheet = page.getByTestId('beloved-settings');
    const kids = sheet.getByTestId('beloved-children');
    await expect(kids.getByTestId('beloved-child-from-family')).toHaveText(['Sky', 'Jamie']);
    await kids.getByTestId('beloved-child-from-family').filter({ hasText: 'Sky' }).click();
    await expect(kids.getByTestId('beloved-child-birthday').first()).toHaveValue(nineYearsAgo);
    await kids.getByTestId('beloved-child-from-family').filter({ hasText: 'Jamie' }).click();
    await expect(kids.getByTestId('beloved-child-from-family')).toHaveCount(0);
    await expect(kids.getByTestId('beloved-child-incomplete')).toHaveText('Add the birthday to keep this one.');
    await kids.getByTestId('beloved-child-birthday').nth(1).fill('2019-02-28');
    await expect(kids.getByTestId('beloved-child-incomplete')).toHaveCount(0);
    await kids.getByTestId('beloved-child-add').click();
    await kids.getByTestId('beloved-child-name').nth(2).fill('A made-up godchild');
    await kids.getByTestId('beloved-child-birthday').nth(2).fill('2021-06-15');

    // The Save button sits clear of the nav bar the phone draws over the page.
    const save = sheet.getByTestId('beloved-settings-save');
    await save.scrollIntoViewIfNeeded();
    const box = await save.boundingBox();
    const viewport = page.viewportSize();
    expect(box && viewport && box.y + box.height <= viewport.height - NAV_BAR, 'Save is under the nav bar').toBeTruthy();
    await check(page, info, run, 'beloved children birthdays', sheet);
    await save.click();
    await expect(sheet).toHaveCount(0);

    // Sealed: the server got no names or dates, and the sealed profile holds all three.
    const put = server.sent.filter((s) => s.path.includes('/profile')).at(-1);
    expect(put).toBeTruthy();
    for (const plain of ['Sky', 'Jamie', 'godchild', '2019-02-28']) expect(put?.body).not.toContain(plain);
    const profile = server.records.find((r) => r.kind === 'profile') as SealedRow;
    const opened = await openRecord(key, profile.sealed) as { children?: Array<{ name: string; birthday: string }> };
    expect(opened.children?.map((c) => c.name)).toEqual(['Sky', 'Jamie', 'A made-up godchild']);

    // Special days: Sky turns 9 in two days, with that day's weather beside it.
    await beloved.getByTestId('beloved-tab-days').click();
    const sky = beloved.getByTestId('beloved-day').filter({ hasText: "Sky's 9th birthday" });
    await expect(sky).toContainText('In 2 days');
    await expect(sky.getByTestId('beloved-weather')).toHaveAttribute('aria-label', /^Rain, high 72°F, low 49°F$/);
    await expect(beloved.getByTestId('beloved-day').filter({ hasText: /^.*Jamie's \d+\w\w birthday/ })).toHaveCount(1);
    await check(page, info, run, 'beloved days with children', beloved);

    // Just us: picking stargazing two nights out shows that night is cloudy, and the moon.
    await beloved.getByTestId('beloved-tab-justus').click();
    const tab = beloved.getByTestId('beloved-justus');
    await tab.getByTestId('justus-plan-day').fill(soon);
    await tab.getByTestId('justus-idea').filter({ hasText: 'Stargazing' }).click();
    const preview = tab.getByTestId('justus-plan-weather');
    await expect(preview.getByTestId('beloved-weather-day')).toContainText('Rain');
    const night = preview.getByTestId('beloved-weather-night');
    await expect(night).toHaveAttribute('data-look', 'cloudy');
    await expect(night).toContainText('Overnight 90% cloud');
    await expect(night.getByTestId('beloved-weather-moon')).toContainText(/(moon|crescent|quarter|gibbous)\b.*% lit/i);
    await tab.getByTestId('justus-plan-add').click();
    const planned = tab.getByTestId('justus-plan').filter({ hasText: 'Stargazing' });
    await expect(planned.getByTestId('beloved-weather-night')).toContainText('Overnight 90% cloud');
    // Tomorrow is the clearest night left in the forecast but one: 40% is "some clouds".
    await tab.getByTestId('justus-plan-day').fill(local(1));
    await expect(tab.getByTestId('justus-plan-weather').getByTestId('beloved-weather-night')).toHaveAttribute('data-look', 'some-cloud');
    // Past the week the forecast covers, only the moon.
    await tab.getByTestId('justus-plan-day').fill(local(20));
    const far = tab.getByTestId('justus-plan-weather');
    await expect(far.getByTestId('beloved-weather-day')).toHaveCount(0);
    await expect(far.getByTestId('beloved-weather-moon')).toBeVisible();
    await expect(far.getByTestId('beloved-weather-night')).not.toContainText('cloud');
    await check(page, info, run, 'beloved just us stargazing weather', tab);

    // The end of the Beloved page scrolls clear of the nav bar too.
    // Scrolled to the end, as a thumb would: the card may already be "in view" behind the bar.
    const last = tab.getByTestId('justus-reminder');
    await last.evaluate((el) => {
      let box: HTMLElement | null = el.parentElement;
      while (box && getComputedStyle(box).overflowY !== 'auto') box = box.parentElement;
      box?.scrollTo({ top: box.scrollHeight, behavior: 'instant' });
    });
    const end = await last.boundingBox();
    expect(end && viewport && end.y + end.height <= viewport.height - NAV_BAR, 'the last card is under the nav bar').toBeTruthy();
    done(run);
  });

  test('never shows Beloved to an administrator father who is not one of the couple, even with the chat unlocked', async ({ page }) => {
    const { server } = await setup();
    const run = await open(page, server, { id: 12, username: 'otherdad', display_name: 'Other Dad', full_name: 'Other Dad', role: 'admin',
      is_admin: true, family_role: 'father', is_minor: false });
    await expect(page.getByTestId('private-gallery-open')).toBeVisible();
    await expect(page.getByTestId('private-beloved-open')).toHaveCount(0);
    expect(server.belovedCalls).toBe(0);
    done(run);
  });
});
