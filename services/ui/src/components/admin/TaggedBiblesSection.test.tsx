import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import TaggedBiblesSection from './TaggedBiblesSection';
import { api } from '../../services/api';
import type { TaggedBible } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getTaggedBibles: vi.fn(), installTaggedBible: vi.fn() } };
});

const BOOK = (over: Partial<TaggedBible>): TaggedBible => ({
  id: 1, title: 'ESV Study Bible', authors: ['Crossway'], file: '/Books/Text/Crossway/ESV Study Bible (1)/ESV Study Bible.epub',
  format: 'EPUB', code: 'esv', edition: 'esv-study', edition_name: 'ESV Study Bible', state: 'ready', reason: '', installed_at: null, ...over,
});

function renderSection(onReport = vi.fn(), onFailed = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TaggedBiblesSection knownCodes={['esv', 'kjv', 'nkjv']} onReport={onReport} onFailed={onFailed} />
    </QueryClientProvider>,
  );
  return { onReport, onFailed };
}

beforeEach(() => vi.clearAllMocks());

describe('TaggedBiblesSection', () => {
  it('lists each tagged Bible with what it installs as, and installs one', async () => {
    vi.mocked(api.getTaggedBibles).mockResolvedValue({ books: [BOOK({}), BOOK({ id: 2, title: 'NKJV Bible', code: 'nkjv', edition: 'nkjv', state: 'installed' })], count: 2, auto_sync_hours: 6 });
    vi.mocked(api.installTaggedBible).mockResolvedValue({ status: 'succeeded', message: 'Installed', log: [] } as never);
    const { onReport } = renderSection();
    expect(await screen.findByTestId('bible-tagged-1')).toHaveTextContent('ESV · ESV Study Bible · EPUB');
    expect(screen.getByTestId('bible-tagged-2')).toHaveTextContent('In the Bible app');
    fireEvent.click(screen.getByTestId('bible-tagged-install-1'));
    await waitFor(() => expect(api.installTaggedBible).toHaveBeenCalledWith(1, undefined));
    await waitFor(() => expect(onReport).toHaveBeenCalled());
  });

  it('asks which translation when the title does not say', async () => {
    vi.mocked(api.getTaggedBibles).mockResolvedValue({ books: [BOOK({ id: 3, title: "Grandma's Bible", code: null, edition: null, state: 'needs attention', reason: 'Could not tell which translation this is from its title.' })], count: 1, auto_sync_hours: 6 });
    vi.mocked(api.installTaggedBible).mockResolvedValue({ status: 'succeeded', message: 'Installed', log: [] } as never);
    renderSection();
    const install = await screen.findByTestId('bible-tagged-install-3');
    expect(install).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Translation of Grandma's Bible"), { target: { value: 'kjv' } });
    fireEvent.click(install);
    await waitFor(() => expect(api.installTaggedBible).toHaveBeenCalledWith(3, 'kjv'));
  });
});
