import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

/**
 * The cleaned-up study reader on every form factor: structured sections
 * (headings, lists, a quote, a table, footnotes, questions with answer space),
 * scripture references that open the Bible at the verse, a link that opens a
 * section at a paragraph, and read aloud with its controls on screen and the
 * paragraph being read lit up. The book and its audio are made up.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const BOOK = {
  id: 8,
  title: 'A Made-up Structured Study With a Long Title',
  author: 'Test Author',
  kind: 'study',
  source: 'upload',
  source_path: '',
  chapter_count: 2,
  created_at: null,
  format_version: 2,
  reformattable: true,
};

const CHAPTERS = [
  { ordinal: 1, section_id: 's-lesson-1-the-vine', label: 'Lesson 1: The Vine and the Branches', characters: 3000, number: null },
  { ordinal: 2, section_id: 's-lesson-2-the-shepherd', label: 'Lesson 2: The Good Shepherd', characters: 3000, number: null },
];

function blocks(ordinal: number) {
  const prose = Array.from({ length: 20 }, (_, i) => {
    const text = `Paragraph ${i + 1} of lesson ${ordinal}. Placeholder study prose, long enough to wrap over several lines on a small phone, citing John 15:5 along the way.`;
    const start = text.indexOf('John 15:5');
    return {
      id: `p-${ordinal}-${i + 1}`,
      type: 'paragraph',
      text,
      refs: [{ start, end: start + 'John 15:5'.length, ref: 'John 15:5' }],
      ...(i === 0 ? { notes: ['1'] } : {}),
    };
  });
  return [
    { id: `h-${ordinal}`, type: 'heading', level: 1, text: CHAPTERS[ordinal - 1].label },
    ...prose.slice(0, 3),
    { id: `q-${ordinal}`, type: 'quote', text: 'A quoted line of placeholder text.' },
    { id: `l-${ordinal}`, type: 'list', ordered: false, items: ['First point to remember', 'Second point to remember'],
      text: 'First point to remember\nSecond point to remember' },
    { id: `t-${ordinal}`, type: 'table', text: '', rows: [['Day', 'Reading', 'A long column heading for a narrow screen'],
      ['1', 'Psalm 1', 'Placeholder'], ['2', 'Psalm 23', 'Placeholder']] },
    ...prose.slice(3),
    { id: `h-${ordinal}-q`, type: 'heading', level: 2, text: 'Questions for Discussion' },
    { id: `k-${ordinal}-1`, type: 'question', text: 'What does it mean to abide?' },
    { id: `k-${ordinal}-2`, type: 'question', text: 'Where do you see fruit in your week?' },
    { id: `n-${ordinal}`, type: 'note', marker: '1', text: 'A placeholder footnote at the end of the lesson.' },
  ];
}

/** Half a second of silence as a WAV, so the browser really plays something. */
function silentWav(): string {
  const rate = 8000;
  const samples = rate / 2;
  const data = Buffer.alloc(44 + samples * 2);
  data.write('RIFF', 0);
  data.writeUInt32LE(36 + samples * 2, 4);
  data.write('WAVE', 8);
  data.write('fmt ', 12);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24);
  data.writeUInt32LE(rate * 2, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write('data', 36);
  data.writeUInt32LE(samples * 2, 40);
  return data.toString('base64');
}

function plan(ordinal: number) {
  const ids = blocks(ordinal).filter((b) => !['note', 'table'].includes(b.type)).map((b) => b.id);
  const chunks = [ids.slice(0, 3), ids.slice(3, 8), ids.slice(8)].map((part, index) => ({
    index,
    characters: 400,
    cached: false,
    blocks: part.map((id, n) => ({ block_id: id, start: n * 100, end: n * 100 + 99 })),
  }));
  return { book_id: 8, ordinal, section_id: CHAPTERS[ordinal - 1].section_id, voice: 'default', count: chunks.length,
    characters: 1200, cached_count: 0, all_cached: false, chunks };
}

async function mockStudy(page: Page) {
  const unknown = await mockBible(page);
  const wav = silentWav();
  const asked: string[] = [];
  await page.route('**/api/bible/imported-books**', async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^\/api\/bible/, '');
    const json = (body: unknown) => route.fulfill({ json: body });
    if (p === '/imported-books') return json({ books: [BOOK], count: 1, kinds: ['study'] });
    if (!p.startsWith('/imported-books/8')) return route.fallback();
    asked.push(`${p}${url.search}`);
    if (p === '/imported-books/8') return json({ book: BOOK, chapters: CHAPTERS, aliases: [] });
    const chapter = p.match(/^\/imported-books\/8\/chapters\/(\d+)$/);
    if (chapter) {
      const ordinal = Number(chapter[1]);
      return json({ book_id: 8, ordinal, section_id: CHAPTERS[ordinal - 1].section_id, label: CHAPTERS[ordinal - 1].label,
        text: 'plain text', questions: [], blocks: blocks(ordinal) });
    }
    const planned = p.match(/^\/imported-books\/8\/chapters\/(\d+)\/narration\/plan$/);
    if (planned) return json(plan(Number(planned[1])));
    const piece = p.match(/^\/imported-books\/8\/chapters\/(\d+)\/narration\/chunk$/);
    if (piece) {
      const ordinal = Number(piece[1]);
      const index = Number(url.searchParams.get('index'));
      const chunk = plan(ordinal).chunks[index];
      return json({ ...chunk, book_id: 8, ordinal, section_id: '', voice: 'default', count: 3, mime_type: 'audio/wav',
        length_bytes: 8044, audio_base64: wav });
    }
    return route.fallback();
  });
  await page.route('**/api/bible/voices', (route) => route.fulfill({ json: { voices: ['af_heart', 'bm_george'], count: 2 } }));
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  return { unknown, asked, errors };
}

async function openBook(page: Page) {
  await page.goto('/bible?tab=studies');
  await page.getByRole('button', { name: /A Made-up Structured Study/ }).click();
  await expect(page.getByRole('heading', { name: 'Lesson 1: The Vine and the Branches' })).toBeVisible();
}

test.describe('study reader', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a structured section reads cleanly and fits the screen', async ({ page }, info) => {
    await mockHome(page, 'admin');
    const run = await mockStudy(page);
    await openBook(page);
    const reader = page.getByTestId('bible-study-reader');
    await expect(reader.locator('[data-block-type="quote"]')).toContainText('A quoted line');
    await expect(reader.getByRole('listitem').filter({ hasText: 'First point to remember' })).toBeVisible();
    await expect(reader.getByRole('region', { name: 'Notes' })).toContainText('A placeholder footnote');
    await expect(reader.getByRole('region', { name: 'Question 1' }).getByTestId('study-answer')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    // A wide table scrolls inside its own box rather than widening the page.
    await reader.locator('[data-block-type="table"]').scrollIntoViewIfNeeded();
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(reader.locator('header'));
    await expectTapTargets(reader.locator('footer'));
    await snap(page, info, 'study-reader-structured');
    expect(run.errors).toEqual([]);
  });

  test('a scripture reference opens the Bible at that verse', async ({ page }) => {
    await mockHome(page, 'admin');
    await mockStudy(page);
    await openBook(page);
    await page.getByRole('button', { name: 'Open John 15:5 in the Bible' }).first().click();
    await expect(page).toHaveURL(/ref=John\+15%3A5|ref=John%2015%3A5|ref=John\+15:5/);
    await expect(page.getByTestId('bible-study-reader')).toHaveCount(0);
  });

  test('a link opens the section at its paragraph', async ({ page }) => {
    await mockHome(page, 'admin');
    await mockStudy(page);
    await page.goto('/bible?tab=studies&book=8&section=s-lesson-2-the-shepherd#p-2-7');
    await expect(page.getByRole('heading', { name: 'Lesson 2: The Good Shepherd' })).toBeVisible();
    const target = page.locator('#p-2-7');
    await expect(target).toBeInViewport();
    await expect(target).toHaveAttribute('data-block-id', 'p-2-7');
  });

  test('read aloud plays from the paragraph on screen, lights it up, and stops', async ({ page }, info) => {
    await mockHome(page, 'admin');
    const run = await mockStudy(page);
    await openBook(page);
    // Scroll so the reader is looking at the later paragraphs.
    await page.getByTestId('bible-study-page').evaluate((el) => {
      const target = el.querySelector('#p-1-5') as HTMLElement;
      el.scrollTo({ top: el.scrollTop + target.getBoundingClientRect().top - el.getBoundingClientRect().top - 4, behavior: 'instant' });
    });
    // The reader's place has settled on paragraph 5 before Listen is pressed.
    await expect
      .poll(() =>
        page.getByTestId('bible-study-page').evaluate((el) => {
          const target = el.querySelector('#p-1-5') as HTMLElement;
          return Math.abs(target.getBoundingClientRect().top - el.getBoundingClientRect().top);
        }),
      )
      .toBeLessThan(30);
    await page.getByTestId('bible-study-listen').click();
    const bar = page.getByTestId('study-read-aloud');
    await expect(bar).toBeVisible();
    await expectFits(page, bar);
    await expectTapTargets(bar);
    await bar.getByTestId('study-read-aloud-play').click();
    await expect(bar.getByRole('button', { name: 'Pause reading' })).toBeVisible();
    // The piece holding paragraph 5 is asked for first, not the top of the section.
    await expect.poll(() => run.asked.find((p) => p.includes('narration/chunk'))).toContain('index=1');
    await expect(page.locator('[data-active="true"]')).toHaveCount(1);
    await snap(page, info, 'study-reader-read-aloud');
    await expectTapTargets(bar);
    await expectNoHorizontalOverflow(page);
    // The page's own footer is still reachable below the controls.
    const footer = await page.getByRole('button', { name: 'Next page' }).boundingBox();
    const box = await bar.boundingBox();
    expect(box!.y + box!.height).toBeLessThanOrEqual(footer!.y + 1);
    await bar.getByTestId('study-read-aloud-stop').click();
    await expect(page.locator('[data-active="true"]')).toHaveCount(0);
    await expect(bar.getByRole('button', { name: 'Read aloud from here' })).toBeVisible();
    // Leaving the book stops everything.
    await page.getByRole('button', { name: 'Back to books' }).click();
    const playing = await page.evaluate(() => Array.from(document.querySelectorAll('audio')).some((a) => !a.paused));
    expect(playing).toBe(false);
    expect(run.errors).toEqual([]);
  });
});
