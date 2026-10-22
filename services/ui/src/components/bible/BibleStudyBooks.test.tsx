import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import BibleStudyBooks from './BibleStudyBooks';

const apiMock = vi.hoisted(() => ({
  getBibleImportedBooks: vi.fn(),
  getBibleImportedBook: vi.fn(),
  getBibleImportedBookChapter: vi.fn(),
  askBibleBookQuestion: vi.fn(),
  createNote: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

function renderShelf() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BibleStudyBooks />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  apiMock.getBibleImportedBooks.mockReset();
  apiMock.getBibleImportedBook.mockReset();
  apiMock.getBibleImportedBookChapter.mockReset();
});

describe('study book shelf', () => {
  it('lists an imported book that has no author metadata', async () => {
    apiMock.getBibleImportedBooks.mockResolvedValue({
      books: [
        {
          id: 1,
          title: 'Walk With Jesus PDF - 1st Edition',
          author: '',
          kind: 'study',
          source: 'upload',
          source_path: '/data/imports/book-1-Walk With Jesus.pdf',
          chapter_count: 122,
          created_at: '2026-10-21T21:19:16',
        },
      ],
      count: 1,
      kinds: ['study'],
    });

    renderShelf();

    expect(await screen.findByText('Walk With Jesus PDF - 1st Edition')).toBeInTheDocument();
    expect(screen.getByText('122 sections')).toBeInTheDocument();
    expect(screen.queryByText('Unknown author')).not.toBeInTheDocument();
  });

  it('opens a book into a page reader instead of a list of page buttons', async () => {
    const user = userEvent.setup();
    apiMock.getBibleImportedBooks.mockResolvedValue({
      books: [
        {
          id: 1,
          title: 'Walk With Jesus PDF - 1st Edition',
          author: '',
          kind: 'study',
          source: 'upload',
          source_path: '/data/imports/book-1-Walk With Jesus.pdf',
          chapter_count: 1,
          created_at: '2026-10-21T21:19:16',
        },
      ],
      count: 1,
      kinds: ['study'],
    });
    apiMock.getBibleImportedBook.mockResolvedValue({
      book: {
        id: 1,
        title: 'Walk With Jesus PDF - 1st Edition',
        author: '',
        kind: 'study',
        source: 'upload',
        source_path: '',
        chapter_count: 1,
        created_at: null,
      },
      chapters: [{ ordinal: 1, label: 'Page 1', characters: 40 }],
    });
    apiMock.getBibleImportedBookChapter.mockResolvedValue({
      book_id: 1,
      ordinal: 1,
      label: 'Page 1',
      text: '1\n\nWeek 1: Start\n\nFollow Jesus today.',
      questions: [],
    });

    renderShelf();
    await user.click(await screen.findByRole('button', { name: /Walk With Jesus PDF/ }));

    expect(await screen.findByTestId('bible-study-reader')).toBeInTheDocument();
    expect(screen.getByText('Follow Jesus today.')).toBeInTheDocument();
    expect(apiMock.getBibleImportedBookChapter).toHaveBeenCalledWith(1, 1);
  });

  it('does not offer a book with no readable sections', async () => {
    apiMock.getBibleImportedBooks.mockResolvedValue({
      books: [
        {
          id: 2,
          title: 'Empty Import',
          author: 'A Writer',
          kind: 'study',
          source: 'upload',
          source_path: '',
          chapter_count: 0,
          created_at: null,
        },
      ],
      count: 1,
      kinds: ['study'],
    });

    renderShelf();

    expect(await screen.findByText('No study books yet. Import one in Admin › Bible.')).toBeInTheDocument();
    expect(screen.queryByText('Empty Import')).not.toBeInTheDocument();
  });
});
