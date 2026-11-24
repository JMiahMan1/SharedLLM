import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import BookCatalogueTile from './BookCatalogueTile';
import { catalogueLine } from '../../lib/bookCatalogues';
import { api } from '../../services/api';
import type { UserProfile } from '../../services/api';
import type { BookCatalogues } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), updateProfile: vi.fn(), testBookCatalogue: vi.fn() },
  };
});

const KEY = 'made-up-books-key';

const catalogues = (google: Partial<NonNullable<BookCatalogues['sources']['google_books']>> = {}): BookCatalogues => ({
  order: ['google_books', 'open_library'],
  sources: {
    google_books: { name: 'Google Books', enabled: false, enabled_from: 'default', ready: false, key_set: false, key_source: 'absent',
      reason: 'Google Books: not set up (add an API key in Settings > Accounts & Integrations)', ...google },
    open_library: { name: 'Open Library', enabled: true, enabled_from: 'default', ready: true, reason: '',
      contact_email: 'library@example.com', contact_email_from: 'household' },
  },
});

const user = (extra: Partial<UserProfile> = {}): UserProfile =>
  ({ username: 'sam', full_name: 'Sam', role: 'user', is_admin: false, saved_credentials: [],
    book_metadata_status: catalogues(), ...extra }) as UserProfile;

const wrap = (id: 'google_books' | 'open_library', u: UserProfile = user()) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <BookCatalogueTile id={id} userData={u} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('BookCatalogueTile', () => {
  beforeEach(() => vi.clearAllMocks());

  it('words each state the way the library page does', () => {
    expect(catalogueLine('google_books', catalogues().sources.google_books)).toEqual({ text: 'Not set up', problem: false });
    expect(catalogueLine('google_books', catalogues({ enabled: true, reason: 'x' }).sources.google_books))
      .toEqual({ text: 'On, but no API key is set', problem: true });
    expect(catalogueLine('google_books', catalogues({ enabled: true, ready: true, key_set: true, key_source: 'household' }).sources.google_books).text)
      .toBe('On, with the household key');
    expect(catalogueLine('open_library', { ...catalogues().sources.open_library!, enabled: false, ready: false }).text).toBe('Off');
  });

  it('saves a pasted Google Books key through a show/hide password field, and turns Google Books on', async () => {
    vi.mocked(api.updateProfile).mockResolvedValue(user());
    wrap('google_books');
    expect(screen.getByTestId('integration-status-google-books')).toHaveTextContent('Not set up');
    fireEvent.click(screen.getByRole('button', { name: 'Manage Google Books' }));
    const dialog = await screen.findByTestId('google-books-dialog');
    const field = within(dialog).getByLabelText('Google Books API key');
    expect(field).toHaveAttribute('type', 'password');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Show password' }));
    expect(field).toHaveAttribute('type', 'text');
    fireEvent.change(field, { target: { value: ` ${KEY} ` } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Save key/ }));
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ google_books_key: KEY, book_metadata: { google_books_enabled: true } }));
  });

  it('Test shows exactly what Google Books answered, with the key being typed', async () => {
    vi.mocked(api.testBookCatalogue).mockResolvedValue({ ok: false, source: 'google_books', status: 429,
      message: "Google Books answered 429: the API key's daily quota is used up." });
    wrap('google_books');
    fireEvent.click(screen.getByRole('button', { name: 'Manage Google Books' }));
    const dialog = await screen.findByTestId('google-books-dialog');
    fireEvent.change(within(dialog).getByLabelText('Google Books API key'), { target: { value: KEY } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Test/ }));
    expect(await within(dialog).findByTestId('google-books-test-result')).toHaveTextContent('answered 429');
    expect(api.testBookCatalogue).toHaveBeenCalledWith({ source: 'google_books', api_key: KEY });
  });

  it('turned on without a key, the card and the dialog say so', async () => {
    wrap('google_books', user({ book_metadata_status: catalogues({ enabled: true, enabled_from: 'own',
      reason: 'Google Books: turned on but no API key is set in Settings > Accounts & Integrations' }) }));
    expect(screen.getByTestId('integration-status-google-books')).toHaveClass('text-rose-300');
    fireEvent.click(screen.getByRole('button', { name: 'Manage Google Books' }));
    expect(await screen.findByTestId('google-books-status')).toHaveTextContent('turned on but no API key is set');
  });

  it('Open Library: the toggle, the contact email and the order are saved as settings', async () => {
    vi.mocked(api.updateProfile).mockResolvedValue(user());
    wrap('open_library');
    expect(screen.getByTestId('integration-status-open-library')).toHaveTextContent('On');
    fireEvent.click(screen.getByRole('button', { name: 'Manage Open Library' }));
    const dialog = await screen.findByTestId('open-library-dialog');
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Use Open Library for book details' }));
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ book_metadata: { open_library_enabled: false } }));
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Contact email (optional)' }), { target: { value: 'me@example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Save email/ }));
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ book_metadata: { open_library_email: 'me@example.com' } }));
    fireEvent.change(within(dialog).getByLabelText('Which catalogue to ask first'), { target: { value: 'open_library' } });
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ book_metadata: { order: ['open_library', 'google_books'] } }));
  });
});
