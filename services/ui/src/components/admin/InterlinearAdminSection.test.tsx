import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import InterlinearAdminSection from './InterlinearAdminSection';

const apiMock = vi.hoisted(() => ({ getBibleInterlinearCoverage: vi.fn(), uploadBibleInterlinear: vi.fn() }));
vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

function renderSection() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <InterlinearAdminSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('InterlinearAdminSection', () => {
  it('says plainly when nothing is installed', async () => {
    apiMock.getBibleInterlinearCoverage.mockResolvedValue({ books: [], verse_count: 0, attribution: '' });
    renderSection();
    expect(await screen.findByText(/None installed/)).toBeInTheDocument();
  });

  it('imports each picked file and reports what each one did', async () => {
    apiMock.getBibleInterlinearCoverage.mockResolvedValue({ books: [], verse_count: 0, attribution: '' });
    apiMock.uploadBibleInterlinear
      .mockResolvedValueOnce({ status: 'succeeded', message: 'Imported 3768 verses (68736 words) across 4 books.' })
      .mockRejectedValueOnce({ response: { data: { detail: 'notes.txt is not UTF-8 text.' } } });
    renderSection();
    const nt = new File(['Jhn.3.16#01=NKO\t...'], 'TAGNT Mat-Jhn.txt', { type: 'text/plain' });
    const bad = new File(['x'], 'notes.txt', { type: 'text/plain' });
    await userEvent.upload(screen.getByTestId('bible-admin-interlinear-file'), [nt, bad]);
    apiMock.getBibleInterlinearCoverage.mockResolvedValue({
      books: [{ book: 'Matt', language: 'greek', source: 'TAGNT Mat-Jhn.txt', verses: 1071 }],
      verse_count: 1071,
      attribution: '',
    });
    await userEvent.click(screen.getByTestId('bible-admin-interlinear-go'));
    await waitFor(() => expect(screen.getByTestId('bible-admin-interlinear-log')).toHaveTextContent('notes.txt is not UTF-8 text.'));
    expect(screen.getByTestId('bible-admin-interlinear-log')).toHaveTextContent('Imported 3768 verses');
    expect(apiMock.uploadBibleInterlinear).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/1,071 verses · 1 NT and 0 OT books/)).toBeInTheDocument();
  });
});
