import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LibraryPanel from './LibraryPanel';
import { api } from '../../services/api';
import type { LibraryBook } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      libraryBooks: vi.fn(),
      libraryTags: vi.fn(),
      libraryAudit: vi.fn(),
      libraryTagBook: vi.fn(),
      libraryAddBook: vi.fn(),
    },
  };
});

const LENT: LibraryBook = {
  id: 7, title: 'Great Lent', authors: ['Alexander Schmemann'], tags: ['Lent'], series: null, series_index: null,
  formats: ['EPUB'], readable: true, path: 'Alexander Schmemann/Great Lent (7)',
};
const WESLEY: LibraryBook = { ...LENT, id: 9, title: 'Works of John Wesley', authors: ['John Wesley'], tags: [], formats: ['PDF'], readable: false };

function renderPanel(isAdmin: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LibraryPanel isAdmin={isAdmin} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.libraryBooks).mockResolvedValue({ total: 2, books: [LENT, WESLEY] });
  vi.mocked(api.libraryTags).mockResolvedValue({ tags: [{ name: 'Lent', books: 1 }] });
});

describe('LibraryPanel', () => {
  it('lists books with their tags and says when Jarvis can only see a title', async () => {
    renderPanel(false);
    const lent = await screen.findByTestId('library-book-7');
    expect(lent).toHaveTextContent('Great Lent');
    expect(lent).toHaveTextContent('Lent');
    expect(screen.getByTestId('library-book-9')).toHaveTextContent('Jarvis sees the title only');
    // Not an admin: no tag editing, no adding.
    expect(screen.queryByLabelText('Add a tag to Great Lent')).toBeNull();
    expect(screen.queryByTestId('library-add-button')).toBeNull();
  });

  it('filters by a tag', async () => {
    renderPanel(false);
    fireEvent.click(await screen.findByRole('button', { name: /Lent 1/ }));
    await waitFor(() => expect(api.libraryBooks).toHaveBeenLastCalledWith(expect.objectContaining({ tag: 'Lent' })));
  });

  it('lets an admin tag a book and remove a tag', async () => {
    vi.mocked(api.libraryTagBook).mockResolvedValue({ book_id: 7, tags: ['fasting', 'Lent'] });
    renderPanel(true);
    const input = await screen.findByLabelText('Add a tag to Great Lent');
    fireEvent.change(input, { target: { value: 'fasting' } });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(api.libraryTagBook).toHaveBeenCalledWith(7, ['fasting'], []));
    fireEvent.click(screen.getByLabelText('Remove tag Lent from Great Lent'));
    await waitFor(() => expect(api.libraryTagBook).toHaveBeenCalledWith(7, [], ['Lent']));
  });

  it('adds a book with its tags', async () => {
    vi.mocked(api.libraryAddBook).mockResolvedValue({ book_id: 12, title: 'A Hunger for God', tags: ['fasting'], path: 'x' });
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    const form = screen.getByTestId('library-add');
    fireEvent.change(within(form).getByTestId('library-add-file'), { target: { files: [new File(['x'], 'piper.epub')] } });
    fireEvent.change(within(form).getByTestId('library-add-tags'), { target: { value: 'fasting' } });
    fireEvent.click(within(form).getByTestId('library-add-submit'));
    await waitFor(() => expect(api.libraryAddBook).toHaveBeenCalled());
    const sent = vi.mocked(api.libraryAddBook).mock.calls[0][0];
    expect((sent.get('file') as File).name).toBe('piper.epub');
    expect(sent.get('tags')).toBe('fasting');
  });

  it('audits the shelf and only suggests', async () => {
    vi.mocked(api.libraryAudit).mockResolvedValue({
      books: 2, counts: { copies: 1, editions: 0 },
      copies: [{ why: 'Same title twice.', keep: LENT, others: [{ ...LENT, id: 8, formats: ['PDF'] }] }],
      editions: [],
      no_file: { count: 0, books: [], why: '' },
      unreadable: { count: 1, books: [WESLEY], why: 'Only PDF.' },
      untagged: { count: 1, books: [WESLEY], why: 'No tags.' },
    });
    renderPanel(false);
    fireEvent.click(await screen.findByTestId('library-audit-button'));
    const audit = await screen.findByTestId('library-audit');
    await waitFor(() => expect(audit).toHaveTextContent('could remove id 8 (PDF)'));
    expect(audit).toHaveTextContent('nothing is removed');
  });
});
