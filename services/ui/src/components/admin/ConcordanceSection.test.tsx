import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ConcordanceSection from './ConcordanceSection';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getConcordances: vi.fn(), installConcordance: vi.fn() } };
});

beforeEach(() => vi.clearAllMocks());

describe('ConcordanceSection', () => {
  it('lists tagged concordances and installs one', async () => {
    vi.mocked(api.getConcordances).mockResolvedValue({
      installed: { installed: false, entries: 0, occurrences: 0, source: '' },
      books: [{ id: 7, title: "The Strongest Strong's", authors: ['James Strong'], file: '/Books/S.pdf', state: 'ready', reason: '' }],
      job: { state: 'idle', book_id: null, title: '', message: '' },
      tags: ['Concordance', "Strong's"],
    });
    vi.mocked(api.installConcordance).mockResolvedValue({ state: 'running', title: "The Strongest Strong's" });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ConcordanceSection onFailed={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId('bible-concordance-7')).toHaveTextContent('Ready to install');
    fireEvent.click(screen.getByTestId('bible-concordance-install-7'));
    await waitFor(() => expect(api.installConcordance).toHaveBeenCalledWith(7));
  });
});
