import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FastingCard from './FastingCard';
import { api } from '../../services/api';
import type { HealthFast } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getHealthFasting: vi.fn(),
      startHealthFast: vi.fn(),
      stopHealthFast: vi.fn(),
    },
  };
});

const RUNNING: HealthFast = {
  id: 4,
  started_at: '2026-10-21T20:00:00-07:00',
  ended_at: null,
  target_hours: 16,
  elapsed_minutes: 600,
  target_minutes: 960,
  percent: 63,
  remaining_minutes: 360,
  reached: false,
};

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FastingCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('FastingCard', () => {
  it('shows a running fast against its target and ends it', async () => {
    vi.mocked(api.getHealthFasting).mockResolvedValue({ allowed: true, active: RUNNING, recent: [], presets: [12, 14, 16] });
    vi.mocked(api.stopHealthFast).mockResolvedValue({ ...RUNNING, ended_at: '2026-10-22T06:00:00-07:00' });
    renderCard();

    expect(await screen.findByText('10h')).toBeInTheDocument();
    expect(screen.getByText(/6h to your 16 h target/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('fasting-stop'));
    await waitFor(() => expect(api.stopHealthFast).toHaveBeenCalled());
  });

  it('starts a fast with the chosen target', async () => {
    vi.mocked(api.getHealthFasting).mockResolvedValue({ allowed: true, active: null, recent: [], presets: [12, 14, 16] });
    vi.mocked(api.startHealthFast).mockResolvedValue(RUNNING);
    renderCard();

    fireEvent.click(await screen.findByRole('button', { name: '14 h' }));
    fireEvent.click(screen.getByTestId('fasting-start'));
    await waitFor(() => expect(api.startHealthFast).toHaveBeenCalledWith({ target_hours: 14 }));
  });

  it('renders nothing for a minor', async () => {
    vi.mocked(api.getHealthFasting).mockResolvedValue({ allowed: false, active: null, recent: [], presets: [12, 14, 16] });
    const { container } = renderCard();
    await waitFor(() => expect(api.getHealthFasting).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[data-testid="fasting-card"]')).toBeNull());
    expect(container.querySelector('.skeleton')).toBeNull();
  });
});
