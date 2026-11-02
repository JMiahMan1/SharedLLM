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
      libraryInspectBook: vi.fn(),
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

  it('fills in a chosen book, keeps every field editable, and adds it', async () => {
    vi.mocked(api.libraryInspectBook).mockResolvedValue({
      title: 'A Hunger for God', authors: ['John Piper'], comments: 'Desiring God through fasting and prayer.',
      publisher: 'Crossway', published: '1997', isbn: '9781581349184', language: 'en', series: '', subjects: ['Fasting'],
      cover_url: 'https://books.google.com/cover.jpg', cover_preview: '',
      tags: [{ name: 'fasting', why: 'mentions “fasting”', existing: false }, { name: 'prayer', why: 'mentions “prayer”', existing: true }],
      sources: { title: 'the file', comments: 'Google Books', cover: 'Google Books' },
    });
    vi.mocked(api.libraryAddBook).mockResolvedValue({ book_id: 12, title: 'A Hunger for God', tags: ['fasting'], path: 'x' });
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    const form = screen.getByTestId('library-add');
    fireEvent.change(within(form).getByTestId('library-add-file'), { target: { files: [new File(['x'], 'piper.epub')] } });

    await waitFor(() => expect(within(form).getByTestId('library-add-title')).toHaveValue('A Hunger for God'));
    expect(within(form).getByTestId('library-add-authors')).toHaveValue('John Piper');
    expect(within(form).getByTestId('library-add-tags')).toHaveValue('fasting, prayer');
    expect(within(form).getByTestId('library-found')).toHaveTextContent('description, cover from Google Books');
    expect(within(form).getByTestId('library-add-cover')).toHaveAttribute('src', 'https://books.google.com/cover.jpg');

    // Edit a field and drop a suggested tag before adding.
    fireEvent.change(within(form).getByTestId('library-add-title'), { target: { value: 'A Hunger for God (2nd ed.)' } });
    fireEvent.click(within(within(form).getByTestId('library-suggested-tags')).getByRole('button', { name: /prayer/ }));
    fireEvent.click(within(form).getByTestId('library-add-submit'));
    await waitFor(() => expect(api.libraryAddBook).toHaveBeenCalled());
    const sent = vi.mocked(api.libraryAddBook).mock.calls[0][0];
    expect((sent.get('file') as File).name).toBe('piper.epub');
    expect(sent.get('title')).toBe('A Hunger for God (2nd ed.)');
    expect(sent.get('tags')).toBe('fasting');
    expect(sent.get('isbn')).toBe('9781581349184');
    expect(sent.get('cover_url')).toBe('https://books.google.com/cover.jpg');
  });

  it('adds by hand when auto-fill is off', async () => {
    vi.mocked(api.libraryAddBook).mockResolvedValue({ book_id: 13, title: 'Notes', tags: [], path: 'x' });
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    const form = screen.getByTestId('library-add');
    fireEvent.click(within(form).getByLabelText('Auto-fill and tag'));
    fireEvent.change(within(form).getByTestId('library-add-file'), { target: { files: [new File(['x'], 'notes.pdf')] } });
    fireEvent.click(within(form).getByTestId('library-add-submit'));
    await waitFor(() => expect(api.libraryAddBook).toHaveBeenCalled());
    expect(api.libraryInspectBook).not.toHaveBeenCalled();
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
