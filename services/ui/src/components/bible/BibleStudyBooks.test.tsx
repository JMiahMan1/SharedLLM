import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import BibleStudyBooks from './BibleStudyBooks';

const apiMock = vi.hoisted(() => ({
  getBibleImportedBooks: vi.fn(),
  getBibleImportedBook: vi.fn(),
  getBibleImportedBookChapter: vi.fn(),
  askBibleBookQuestion: vi.fn(),
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
  apiMock.getBibleImportedBooks.mockReset();
  apiMock.getBibleImportedBook.mockReset();
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
