import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import JarvisUseCard from './JarvisUseCard';
import { api } from '../../services/api';
import type { JarvisUse } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), getJarvisUse: vi.fn() },
  };
});

function use(overrides: Partial<JarvisUse> = {}): JarvisUse {
  return {
    days: 7,
    window_start: '2026-10-19',
    has_data: true,
    total: 5,
    top: [
      { label: 'Music, video and podcasts', count: 3 },
      { label: 'Bible', count: 2 },
    ],
    ...overrides,
  };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <JarvisUseCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('JarvisUseCard', () => {
  it('shows the most-used in-app features', async () => {
    vi.mocked(api.getJarvisUse).mockResolvedValue({ status: 'SUCCESS', use: use() });
    renderCard();

    expect(await screen.findByText('Music, video and podcasts')).toBeInTheDocument();
    expect(screen.getByText('Bible')).toBeInTheDocument();
    expect(screen.getByTestId('jarvis-use-list').textContent).toContain('3');
  });

  it('says so when nothing was recorded, never a zero', async () => {
    vi.mocked(api.getJarvisUse).mockResolvedValue({ status: 'SUCCESS', use: use({ has_data: false, total: 0, top: [] }) });
    renderCard();

    expect(await screen.findByTestId('jarvis-use-empty')).toHaveTextContent('Nothing recorded in the last 7 days');
  });

  it('offers a retry when the summary could not load', async () => {
    vi.mocked(api.getJarvisUse).mockRejectedValue(new Error('down'));
    renderCard();

    expect(await screen.findByTestId('jarvis-use-error')).toBeInTheDocument();
    vi.mocked(api.getJarvisUse).mockResolvedValue({ status: 'SUCCESS', use: use() });
    fireEvent.click(screen.getByText('Try again'));
    await waitFor(() => expect(screen.getByTestId('jarvis-use-list')).toBeInTheDocument());
  });

  it('asks for a longer window when the person picks 30 days', async () => {
    vi.mocked(api.getJarvisUse).mockResolvedValue({ status: 'SUCCESS', use: use() });
    renderCard();
    await screen.findByTestId('jarvis-use-list');

    fireEvent.click(screen.getByText('30 d'));

    await waitFor(() => expect(api.getJarvisUse).toHaveBeenCalledWith(30));
  });
});
