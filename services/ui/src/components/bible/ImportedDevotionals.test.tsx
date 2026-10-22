import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ImportedDevotionals from './ImportedDevotionals';
import { dayOfYear, todayOrdinal } from './devotionalCadence';

const apiMock = vi.hoisted(() => ({
  getBibleImportedBooks: vi.fn(),
  getBibleImportedBook: vi.fn(),
  getBibleImportedBookChapter: vi.fn(),
  setBibleDevotionalCadence: vi.fn(),
  askBibleBookQuestion: vi.fn(),
  createNote: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: {
    ...(await importOriginal<typeof import('../../services/api')>()).api,
    ...apiMock,
  },
}));

const BOOK = {
  id: 2,
  title: 'Devotions for a Sacred Marriage',
  author: '',
  kind: 'devotional' as const,
  source: 'upload',
  source_path: '',
  chapter_count: 4,
  created_at: '2026-10-22T05:06:45Z',
};

const CHAPTERS = [
  { ordinal: 1, label: 'Title Page', characters: 60, number: null },
  { ordinal: 2, label: 'One Day at a Time', characters: 120, number: 1 },
  { ordinal: 3, label: 'A Shared Prayer', characters: 130, number: 2 },
  { ordinal: 4, label: 'Grace for Today', characters: 140, number: 3 },
];

function renderShelf() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ImportedDevotionals />
    </QueryClientProvider>,
  );
}

describe('ImportedDevotionals', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    apiMock.getBibleImportedBooks.mockResolvedValue({
      books: [BOOK],
      count: 1,
      kinds: ['devotional'],
    });
    apiMock.getBibleImportedBook.mockResolvedValue({ book: BOOK, chapters: CHAPTERS });
    apiMock.getBibleImportedBookChapter.mockImplementation(
      async (_id: number, ordinal: number) => ({
        book_id: BOOK.id,
        ordinal,
        label: CHAPTERS.find((chapter) => chapter.ordinal === ordinal)?.label ?? '',
        text: `${CHAPTERS.find((chapter) => chapter.ordinal === ordinal)?.label ?? ''} body text`,
        questions: [],
      }),
    );
    apiMock.setBibleDevotionalCadence.mockResolvedValue({ book: BOOK });
  });

  it('lists the imported devotional books', async () => {
    renderShelf();
    expect(await screen.findByText('Devotional books')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: 'Read Devotions for a Sacred Marriage' }),
    ).toBeTruthy();
    expect(apiMock.getBibleImportedBooks).toHaveBeenCalledWith('devotional');
  });

  it('stays out of the way when nothing has been imported', async () => {
    apiMock.getBibleImportedBooks.mockResolvedValue({ books: [], count: 0, kinds: ['devotional'] });
    const { container } = renderShelf();
    await screen.findByText('Devotional books').catch(() => undefined);
    expect(container.firstChild).toBeNull();
  });

  it("opens the reader at today's reading among the printed devotions", async () => {
    const user = userEvent.setup();
    renderShelf();
    await user.click(
      await screen.findByRole('button', { name: 'Read Devotions for a Sacred Marriage' }),
    );
    // Front matter never takes a day, so today lands on one of the three
    // printed devotions (ordinals 2-4), the same way the server rotates.
    const expected = todayOrdinal(CHAPTERS, 'day');
    expect(apiMock.getBibleImportedBook).toHaveBeenCalledWith(BOOK.id);
    expect(await screen.findByTestId('bible-study-reader')).toBeTruthy();
    expect(apiMock.getBibleImportedBookChapter).toHaveBeenCalledWith(BOOK.id, expected);
    expect(await screen.findByText(/body text/)).toBeTruthy();
  });

  it('counts the day of the year from the first of January', () => {
    expect(dayOfYear(new Date(2026, 0, 1))).toBe(1);
    expect(dayOfYear(new Date(2026, 0, 31))).toBe(31);
  });

  it('starts a weekly devotional at its first devotion for the first week', () => {
    // Week 1 is days 1-7, and the front matter never takes a day.
    expect(todayOrdinal(CHAPTERS, 'week', 1)).toBe(2);
    expect(todayOrdinal(CHAPTERS, 'week', 7)).toBe(2);
    expect(todayOrdinal(CHAPTERS, 'week', 8)).toBe(3);
    // 21 Oct is day 294, week 42: (293 // 7) % 3 lands on the third devotion,
    // which is the one a 52-reading book prints as 42.
    expect(todayOrdinal(CHAPTERS, 'week', 294)).toBe(4);
  });

  it('changes the reading cadence from the shelf', async () => {
    const user = userEvent.setup();
    apiMock.setBibleDevotionalCadence.mockImplementation(
      async (_id: number, cadence: 'day' | 'week') => {
        apiMock.getBibleImportedBooks.mockResolvedValue({
          books: [{ ...BOOK, cadence }],
          count: 1,
          kinds: ['devotional'],
        });
        return { book: { ...BOOK, cadence } };
      },
    );
    renderShelf();
    const weekly = await screen.findByRole('button', { name: 'Weekly' });
    expect(weekly.getAttribute('aria-pressed')).toBe('false');

    await user.click(weekly);

    expect(apiMock.setBibleDevotionalCadence).toHaveBeenCalledWith(BOOK.id, 'week');
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'Weekly' }).getAttribute('aria-pressed')).toBe(
        'true',
      );
    });
  });
});
