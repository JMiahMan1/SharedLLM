import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import LibraryPanel from './LibraryPanel';
import { api } from '../../services/api';
import type { BookCatalogues, LibraryBook } from '../../types/api';
import toast from 'react-hot-toast';

vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

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
      libraryCatalogues: vi.fn(),
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
      <MemoryRouter>
        <LibraryPanel isAdmin={isAdmin} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.libraryBooks).mockResolvedValue({ total: 2, books: [LENT, WESLEY] });
  vi.mocked(api.libraryTags).mockResolvedValue({ tags: [{ name: 'Lent', books: 1 }] });
  vi.mocked(api.libraryCatalogues).mockResolvedValue(OPEN_LIBRARY_ONLY);
});

/** Open Library on; Google Books turned on with no key anywhere. */
const OPEN_LIBRARY_ONLY: BookCatalogues = {
  order: ['google_books', 'open_library'],
  sources: {
    google_books: { name: 'Google Books', enabled: true, enabled_from: 'own', ready: false, key_set: false, key_source: 'absent',
      reason: 'Google Books: turned on but no API key is set in Settings > Accounts & Integrations' },
    open_library: { name: 'Open Library', enabled: true, enabled_from: 'default', ready: true, reason: '',
      last: { ok: false, error: 'Open Library answered 429: too many requests. Try again in a few minutes.', at: '2026-01-01T00:00:00' } },
  },
};

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

  it('shows the chosen file, and takes one dropped on it', async () => {
    vi.mocked(api.libraryInspectBook).mockResolvedValue({
      title: 'Great Lent', authors: [], comments: '', publisher: '', published: '', isbn: '', language: '', series: '',
      subjects: [], cover_url: '', cover_preview: '', tags: [], sources: { title: 'the file name' },
    });
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    expect(screen.getByTestId('library-dropzone')).toHaveTextContent('Choose a book file, or drop it here');
    fireEvent.drop(screen.getByTestId('library-dropzone'), { dataTransfer: { files: [new File(['x'], 'Great Lent.epub')] } });
    expect(await screen.findByTestId('library-chosen-file')).toHaveTextContent('Great Lent.epub');
    await waitFor(() => expect(api.libraryInspectBook).toHaveBeenCalled());
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
    expect(vi.mocked(api.libraryAddBook).mock.calls[0][0].get('online')).toBe('false');
  });

  it('says so when the book could not be identified', async () => {
    vi.mocked(api.libraryAddBook).mockResolvedValue({
      book_id: 14, title: 'book', tags: [], path: 'x', identified: false,
      message: 'Added “book”, but I could not identify it in the book catalogues. Please check the title and author.',
    });
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    const form = screen.getByTestId('library-add');
    fireEvent.click(within(form).getByLabelText('Auto-fill and tag'));
    fireEvent.change(within(form).getByTestId('library-add-file'), { target: { files: [new File(['x'], 'book.pdf')] } });
    fireEvent.click(within(form).getByTestId('library-add-submit'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining('could not identify it'), expect.anything()));
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


describe('LibraryPanel book catalogues', () => {
  it('says where book details come from, why a catalogue is not used, and links to the settings', async () => {
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    const note = await screen.findByTestId('library-catalogues');
    expect(note).toHaveTextContent('Book details from Open Library.');
    expect(note).toHaveTextContent('Not used: Google Books: turned on but no API key is set in Settings > Accounts & Integrations.');
    expect(note).toHaveTextContent('Last lookup: Open Library answered 429');
    expect(within(note).getByRole('link', { name: 'Settings > Accounts & Integrations' })).toHaveAttribute('href', '/identity');
  });

  it('says plainly when the settings could not be read', async () => {
    vi.mocked(api.libraryCatalogues).mockResolvedValue({ order: [], sources: {},
      error: 'the catalogue integrations in Settings > Accounts & Integrations could not be read (identity did not answer)' });
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    expect(await screen.findByTestId('library-catalogues')).toHaveTextContent('could not be read (identity did not answer). No catalogue is asked');
  });

  it("shows a lookup's catalogues: asked, errors and skipped", async () => {
    vi.mocked(api.libraryInspectBook).mockResolvedValue({
      title: 'Harbor Lights', authors: [], comments: '', publisher: '', published: '', isbn: '', language: '', series: '',
      subjects: [], cover_url: '', cover_preview: '', tags: [], sources: {},
      catalogues: { asked: ['Google Books', 'Open Library'], used: ['Open Library'],
        errors: [{ source: 'Google Books', error: "Google Books answered 429: the API key's daily quota is used up." }],
        skipped: [] },
    });
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    fireEvent.change(screen.getByTestId('library-add-file'), { target: { files: [new File(['x'], 'harbor.epub')] } });
    const lines = await screen.findByTestId('library-found-catalogues');
    expect(lines).toHaveTextContent('Asked Google Books and Open Library.');
    expect(within(lines).getByText("Google Books answered 429: the API key's daily quota is used up.")).toHaveClass('text-rose-300');
  });

  it('tells the person when a catalogue answered with an error while adding', async () => {
    vi.mocked(api.libraryAddBook).mockResolvedValue({
      book_id: 15, title: 'Harbor Lights', tags: [], path: 'x', identified: true,
      message: 'Added “Harbor Lights”. Filled in from the file and the catalogues: comments. Google Books answered 429.',
      catalogues: { asked: ['Google Books'], used: [], skipped: [], errors: [{ source: 'Google Books', error: 'Google Books answered 429.' }] },
    });
    renderPanel(true);
    fireEvent.click(await screen.findByTestId('library-add-button'));
    fireEvent.click(screen.getByLabelText('Auto-fill and tag'));
    fireEvent.change(screen.getByTestId('library-add-file'), { target: { files: [new File(['x'], 'harbor.epub')] } });
    fireEvent.submit(screen.getByTestId('library-add'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining('Google Books answered 429'), { duration: 10_000 }));
  });
});
