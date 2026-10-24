import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StreaksCard from './StreaksCard';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getHealthStreaks: vi.fn() } };
});

describe('StreaksCard', () => {
  it('celebrates streaks, names rest days and never says failed', async () => {
    vi.mocked(api.getHealthStreaks).mockResolvedValue({
      day: '2026-10-24',
      rule: 'One missed day in any seven is a rest day and keeps the streak; today counts once it is logged.',
      habits: [
        { key: 'logging', title: 'Showing up', detail: '', current: 6, best: 9, rest_days: 1, today_done: false },
        { key: 'checkin', title: 'Morning check-in', detail: '', current: 0, best: 4, rest_days: 0, today_done: false },
        { key: 'water_goal', title: 'Water goal', detail: '', current: 0, best: 0, rest_days: 0, today_done: false, needs: 'Set a water goal in Health → Health to start this streak.' },
        { key: 'sleep_logged', title: 'Sleep logged', detail: '', current: 2, best: 2, rest_days: 0, today_done: true },
      ],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <StreaksCard />
      </QueryClientProvider>,
    );

    const logging = await screen.findByTestId('streak-logging');
    expect(within(logging).getByText('Today still open · best 9 · 1 rest day')).toBeInTheDocument();
    expect(within(screen.getByTestId('streak-checkin')).getByText(/fine day to start again/)).toBeInTheDocument();
    expect(within(screen.getByTestId('streak-water_goal')).getByText(/Set a water goal/)).toBeInTheDocument();
    expect(screen.queryByText(/fail/i)).not.toBeInTheDocument();
  });
});
