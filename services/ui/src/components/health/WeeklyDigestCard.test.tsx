import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import WeeklyDigestCard from './WeeklyDigestCard';
import { api } from '../../services/api';
import type { HealthWeekly } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getHealthWeekly: vi.fn() } };
});

const weekly = (overrides: Partial<HealthWeekly> = {}): HealthWeekly => ({
  user_id: 'jeremiah',
  start: '2026-10-15',
  end: '2026-10-21',
  metrics: ['sleep_minutes', 'sleep_score', 'calories', 'water_ml', 'energy', 'mood'],
  days_logged: 3,
  averages: { sleep_minutes: 450, sleep_score: 82, calories: null, water_ml: 1500, energy: 3.5, mood: null },
  last_week: { sleep_minutes: 390 },
  best_sleep: { day: '2026-10-21', minutes: 480, score: 90 },
  changes: [{ metric: 'sleep_minutes', this_week: 450, last_week: 390, delta: 60, direction: 'up' }],
  days: [],
  ...overrides,
});

const wrap = (ui: ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

describe('WeeklyDigestCard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows logged averages, the best night and what changed', async () => {
    vi.mocked(api.getHealthWeekly).mockResolvedValue(weekly());
    wrap(<WeeklyDigestCard />);

    expect(await screen.findByTestId('weekly-avg-sleep_minutes')).toHaveTextContent('7h 30m');
    expect(screen.getByTestId('weekly-avg-energy')).toHaveTextContent('3.5/5');
    expect(screen.queryByTestId('weekly-avg-calories')).toBeNull();
    expect(screen.queryByTestId('weekly-avg-mood')).toBeNull();
    expect(screen.getByTestId('weekly-best-sleep')).toHaveTextContent('8h (score 90)');
    expect(screen.getByTestId('weekly-changes')).toHaveTextContent('Sleep up from 6h 30m to 7h 30m');
  });

  it('says so when the week is steady', async () => {
    vi.mocked(api.getHealthWeekly).mockResolvedValue(weekly({ changes: [] }));
    wrap(<WeeklyDigestCard />);
    expect(await screen.findByTestId('weekly-steady')).toBeInTheDocument();
  });

  it('shows an empty state, not zeros, when nothing was logged', async () => {
    vi.mocked(api.getHealthWeekly).mockResolvedValue(weekly({ days_logged: 0, averages: {}, best_sleep: null, changes: [] }));
    wrap(<WeeklyDigestCard />);
    expect(await screen.findByTestId('weekly-digest-empty')).toBeInTheDocument();
  });

  it('shows an error when the store cannot be reached', async () => {
    vi.mocked(api.getHealthWeekly).mockRejectedValue(new Error('down'));
    wrap(<WeeklyDigestCard />);
    expect(await screen.findByTestId('weekly-digest-error')).toBeInTheDocument();
  });
});
