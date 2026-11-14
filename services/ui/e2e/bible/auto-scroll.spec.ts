import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { expectFits, expectTapTargets } from '../fixtures/layoutChecks';

/**
 * Auto scroll in the reading surfaces: the Bible reader, focus mode and the
 * Bible studies reader. On every form factor the control has to fit on the
 * screen, clear of the bottom bar, with thumb-sized buttons, and the text has
 * to move once it starts. The passage and study book are made up.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const VERSES = Array.from({ length: 40 }, (_, i) => ({
  version: 'tst',
  osis: 'Gen',
  book_name: 'Genesis',
  chapter: 1,
  verse: i + 1,
  reference: `Genesis 1:${i + 1}`,
  text: `Made-up verse ${i + 1}: a long line of placeholder words so the chapter is taller than any screen it is read on, `
    + 'and the reader has to scroll to reach the end of it.',
}));

const PASSAGE = {
  version: 'tst',
  requested: 'Genesis 1',
  reference: 'Genesis 1',
  spans: [{ book: 'Gen', book_name: 'Genesis', chapter_start: 1, chapter_end: 1, verse_start: 1, verse_end: null,
    whole_book: false, display: 'Genesis 1' }],
  verses: VERSES,
  count: VERSES.length,
};

const STUDY_BOOK = { id: 7, title: 'A Made-up Study', author: 'Test Author', kind: 'study', source: 'upload', source_path: '',
  chapter_count: 2, created_at: null };
const STUDY_TEXT = Array.from({ length: 30 }, (_, i) => `Paragraph ${i + 1}. Placeholder study text, long enough that the `
  + 'session is taller than the screen and auto scroll has somewhere to go.').join('\n\n');

async function mockBible(page: Page) {
  const unknown: string[] = [];
  // The read-aloud panel asks which speakers it could play on.
  await page.route('**/api/communication/timers/devices', (route) => route.fulfill({ json: { devices: [] } }));
  await page.route('**/api/bible/**', (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^\/api\/bible/, '');
    const json = (body: unknown) => route.fulfill({ json: body });
    if (route.request().method() !== 'GET') return json({ status: 'ok' });
    if (p === '/versions') {
      return json({ versions: [{ code: 'tst', name: 'Test Translation', language: 'en', license_class: 'public_domain',
        rights_holder: '', installed: true, verse_count: 40, imported_at: null, editions: 0, primary: true }] });
    }
    if (p === '/books') return json({ version: 'tst', books: [{ osis: 'Gen', name: 'Genesis', order: 1, chapters: 2 }] });
    if (p === '/passages') return json(PASSAGE);
    if (p === '/state') return json({ username: 'parent', position: { book: 'Gen', chapter: 1, verse: 1, version: 'tst' }, preferences: {} });
    if (p === '/marks') return json({ marks: [] });
    if (p === '/editions') return json({ version: 'tst', default: '', editions: [], other_translations: [] });
    if (p === '/imported-books') return json({ books: [STUDY_BOOK], count: 1, kinds: ['study'] });
    if (p === '/imported-books/7') {
      return json({ book: STUDY_BOOK, chapters: [{ ordinal: 1, label: 'Session 1', characters: 4000 }, { ordinal: 2, label: 'Session 2', characters: 4000 }] });
    }
    if (/^\/imported-books\/7\/chapters\/\d+$/.test(p)) {
      return json({ book_id: 7, ordinal: Number(p.split('/').pop()), label: 'Session 1', text: STUDY_TEXT, questions: [] });
    }
    if (p === '/voices') return json({ voices: [], count: 0 });
    if (p === '/study/notes') {
      return json({ version: 'tst', edition: '', edition_name: '', requested: null, reference: 'Genesis 1', kinds: [],
        available_kinds: {}, cross_version: false, other_translations: [], editions: [], notes: [], count: 0, note: null });
    }
    unknown.push(p);
    return json({});
  });
  return unknown;
}

async function openReader(page: Page) {
  const mocks = await mockHome(page, 'admin');
  const unknown = await mockBible(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)); });
  await page.goto('/bible?ref=Genesis+1');
  await expect(page.getByText(/Made-up verse 1:/).first()).toBeVisible();
  return { mocks, unknown, errors };
}

/** The scroll position of the reader's own scrolling element. */
async function readerTop(page: Page): Promise<number> {
  return page.evaluate(() => {
    const main = document.querySelector('main');
    return main ? main.scrollTop : 0;
  });
}

test.describe('auto scroll', () => {
  test.describe.configure({ timeout: 90_000 });

  test('the Bible reader scrolls by itself, at a pace the reader sets, and the control fits', async ({ page }) => {
    const run = await openReader(page);
    const control = page.getByTestId('bible-auto-scroll');
    await expect(control).toBeVisible();
    await expectFits(page, control);
    await control.getByRole('button', { name: 'Auto scroll' }).click();
    await expect(control.getByRole('button', { name: 'Pause auto scroll' })).toBeVisible();
    await expectFits(page, control);
    await expectTapTargets(control);
    await control.getByRole('button', { name: 'Faster' }).click();
    await expect(control.getByTestId('bible-auto-scroll-speed')).toContainText('Speed 4');
    const before = await readerTop(page);
    await expect.poll(() => readerTop(page), { timeout: 5000 }).toBeGreaterThan(before + 20);
    await control.getByRole('button', { name: 'Pause auto scroll' }).click();
    const paused = await readerTop(page);
    await page.waitForTimeout(600);
    expect(await readerTop(page)).toBe(paused);
    await control.getByRole('button', { name: 'Close auto scroll' }).click();
    await expect(control.getByRole('button', { name: 'Auto scroll' })).toBeVisible();
    expect(run.errors).toEqual([]);
    expect(run.unknown, `unmocked Bible calls: ${run.unknown.join(', ')}`).toEqual([]);
  });

  test('focus mode has its own control, and it moves the page', async ({ page }) => {
    await openReader(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    if (await page.getByTestId('bible-focus-open').isVisible()) {
      await page.getByTestId('bible-focus-open').click();
    } else {
      await page.getByTestId('bible-display-toggle').click();
      await page.getByTestId('bible-focus-open-narrow').click();
    }
    await expect(page.getByTestId('bible-focus')).toBeVisible();
    await expect(page.getByTestId('bible-auto-scroll')).toHaveCount(0);
    const control = page.getByTestId('bible-focus-auto-scroll');
    await control.getByRole('button', { name: 'Auto scroll' }).click();
    await expectFits(page, control);
    await expectTapTargets(control);
    const top = () => page.getByTestId('bible-focus').evaluate((el) => el.scrollTop);
    await expect.poll(top, { timeout: 5000 }).toBeGreaterThan(10);
  });

  test('the Bible studies reader scrolls its page above its own footer', async ({ page }) => {
    await mockHome(page, 'admin');
    const unknown = await mockBible(page);
    await page.goto('/bible?tab=studies');
    await page.getByRole('button', { name: /A Made-up Study/ }).click();
    await expect(page.getByTestId('bible-study-reader-text')).toContainText('Paragraph 1.');
    const control = page.getByTestId('bible-study-auto-scroll');
    await control.getByRole('button', { name: 'Auto scroll' }).click();
    await expectFits(page, control);
    await expectTapTargets(control);
    // The control sits above the footer's page buttons, never over them.
    const footer = await page.getByRole('button', { name: 'Next page' }).boundingBox();
    const box = await control.boundingBox();
    expect(box!.y + box!.height).toBeLessThanOrEqual(footer!.y);
    const top = () => page.getByTestId('bible-study-page').evaluate((el) => el.scrollTop);
    await expect.poll(top, { timeout: 5000 }).toBeGreaterThan(10);
    expect(unknown.filter((p) => !p.startsWith('/study')), unknown.join(', ')).toEqual([]);
  });
});
