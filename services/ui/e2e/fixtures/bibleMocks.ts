import type { Page } from '@playwright/test';

/**
 * A made-up Bible for the hermetic screen tests: one translation, a book with
 * a long name and one with a short one, chapters taller than any screen, and
 * one study book. Register after mockHome so these answer first. Returns the
 * Bible paths asked for that nothing here answers.
 */

export const VERSES = Array.from({ length: 40 }, (_, i) => ({
  version: 'tst',
  osis: 'Gen',
  book_name: 'Genesis',
  chapter: 1,
  verse: i + 1,
  reference: `Genesis 1:${i + 1}`,
  text: `Made-up verse ${i + 1}: a long line of placeholder words so the chapter is taller than any screen it is read on, `
    + 'and the reader has to scroll to reach the end of it.',
}));

export const PASSAGE = {
  version: 'tst',
  requested: 'Genesis 1',
  reference: 'Genesis 1',
  spans: [{ book: 'Gen', book_name: 'Genesis', chapter_start: 1, chapter_end: 1, verse_start: 1, verse_end: null,
    whole_book: false, display: 'Genesis 1' }],
  verses: VERSES,
  count: VERSES.length,
};

const LONG_VERSES = VERSES.map((v) => ({ ...v, osis: '1Thess', book_name: '1 Thessalonians', chapter: 4,
  reference: `1 Thessalonians 4:${v.verse}` }));
export const LONG_PASSAGE = {
  ...PASSAGE,
  requested: '1 Thessalonians 4',
  reference: '1 Thessalonians 4',
  spans: [{ ...PASSAGE.spans[0], book: '1Thess', book_name: '1 Thessalonians', chapter_start: 4, chapter_end: 4, display: '1 Thessalonians 4' }],
  verses: LONG_VERSES,
};

const TODAY = new Date().toISOString().slice(0, 10);
export const DAILY = {
  day: TODAY,
  verse_of_day: { day: TODAY, day_of_year: 1, version: 'tst', scope: 'all', osis: 'Gen', book: 'Gen', book_name: 'Genesis',
    chapter: 1, verse: 3, reference: 'Genesis 1:3', text: VERSES[2].text, context: null },
  devotional: { day: TODAY, day_of_year: 1, source: 'test', skipped: [], stored: false,
    entry: { source: 'test', work: 'A Made-up Devotional', title: 'A morning reading', day_of_year: 1, kind: 'text',
      reference: 'Genesis 1:3', text: 'Placeholder devotional text for the day, a few sentences long so it wraps on a phone.', url: '' } },
  sources: [],
  streaks: { read_streak_current: 3, read_streak_longest: 9, open_streak_current: 4, days_read: 20, chapters_read: 41 },
  position: { book: 'Gen', chapter: 1, verse: 1, version: 'tst', reference: 'Genesis 1' },
  plans: [],
  memory_due: 2,
};

export const STUDY_BOOK = { id: 7, title: 'A Made-up Study', author: 'Test Author', kind: 'study', source: 'upload', source_path: '',
  chapter_count: 2, created_at: null };
export const STUDY_TEXT = Array.from({ length: 30 }, (_, i) => `Paragraph ${i + 1}. Placeholder study text, long enough that the `
  + 'session is taller than the screen and auto scroll has somewhere to go.').join('\n\n');

export async function mockBible(page: Page) {
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
    if (p === '/books') {
      return json({ version: 'tst', books: [{ osis: 'Gen', name: 'Genesis', order: 1, chapters: 2 },
        { osis: '1Thess', name: '1 Thessalonians', order: 52, chapters: 5 }] });
    }
    if (p === '/passages') {
      // The longest book name a reader meets, for the header's narrowest case.
      return json((url.searchParams.get('ref') ?? '').startsWith('1 Thessalonians') ? LONG_PASSAGE : PASSAGE);
    }
    if (p === '/state') {
      return json({ username: 'parent', position: { book: 'Gen', chapter: 1, verse: 1, version: 'tst' },
        preferences: { reminder_time: '07:30' } });
    }
    if (p === '/daily') return json(DAILY);
    if (p === '/devotional/works') return json({ works: [] });
    if (p === '/plans') return json({ catalogue: [], mine: [] });
    if (p === '/memorize') return json({ verses: [], due_count: 0, mastered: 0, sets: [] });
    if (p === '/study/sessions') return json({ sessions: [], count: 0 });
    if (p === '/stats') {
      return json({ username: 'parent', read_streak_current: 3, read_streak_longest: 9, open_streak_current: 4, open_streak_longest: 12,
        days_read: 20, metrics: {}, days_opened: 30, marks: 0, highlights: 0, bookmarks: 0, position: null, last_read_at: null, window: {} });
    }
    if (p === '/achievements') {
      return json({ username: 'parent', points: 120, newly_earned: [], earned: [], pending_rules: [],
        next_up: [{ id: 'streak-7', name: 'A week in the Word', description: 'Read seven days in a row', points: 50, current: 3,
          target: 7, remaining: 4, percent: 43, unit: 'days' }],
        stars: { granted: 0, status: 'ok' }, announced: { posted: 0, status: 'ok' } });
    }
    if (p === '/activity/feed') return json({ entries: [], members: [] });
    if (p === '/plans/groups') return json({ groups: [] });
    if (p === '/games') {
      return json({ today: TODAY, modes: [
        { mode: 'daily', name: 'Daily Bible Quiz', blurb: 'Ten questions, the same for the whole family.', count: 10, daily: true, seconds: null },
        { mode: 'verse_race', name: 'Verse Race', blurb: 'Finish the verse before the clock runs out.', count: 10, daily: false, seconds: 60 },
      ] });
    }
    if (p === '/games/daily') {
      return json({ mode: 'daily', name: 'Daily Bible Quiz', seed: TODAY, version: 'tst', seconds: null, questions: [],
        played_today: { correct: 8, total: 10, duration_ms: 95_000 } });
    }
    if (p === '/games/leaderboard') {
      return json({ day: TODAY, entries: [
        { attempt_id: 1, username: 'parent', correct: 8, total: 10, duration_ms: 95_000, you: true },
        { attempt_id: 2, username: 'a-family-member-with-a-very-long-username', correct: 7, total: 10, duration_ms: 120_000, you: false },
      ] });
    }
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

