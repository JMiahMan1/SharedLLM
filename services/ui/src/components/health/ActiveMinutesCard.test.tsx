import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ActiveMinutesCard from './ActiveMinutesCard';
import { api } from '../../services/api';
import { activeMinutesLine } from '../../lib/activeMinutes';
import type { HealthActiveMinutes } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getHealthActiveMinutes: vi.fn() } };
});

function week(overrides: Partial<HealthActiveMinutes> = {}): HealthActiveMinutes {
  return {
    week_start: '2026-10-19',
    week_end: '2026-10-25',
    target_minutes: 150,
    total_minutes: 70,
    percent: 47,
    days: [
      { day: '2026-10-19', minutes: 30, sources: ['workouts'] },
      { day: '2026-10-20', minutes: 40, sources: ['steps'] },
      { day: '2026-10-21', minutes: null, sources: [] },
      { day: '2026-10-22', minutes: null, sources: [] },
      { day: '2026-10-23', minutes: null, sources: [] },
      { day: '2026-10-24', minutes: null, sources: [] },
      { day: '2026-10-25', minutes: null, sources: [] },
    ],
    sources: ['steps', 'workouts'],
    estimate: true,
    notes: ['Step minutes are an estimate: steps beyond 1,000 in an hour count at 100 a minute.'],
    unavailable: [],
    ...overrides,
  };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ActiveMinutesCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('activeMinutesLine', () => {
  it('reads the week against the target', () => {
    expect(activeMinutesLine(week())).toBe('70 active minutes of 150 · 80 to go this week.');
    expect(activeMinutesLine(week({ total_minutes: 160 }))).toBe('160 active minutes: your 150-minute week is done.');
    expect(activeMinutesLine(week({ target_minutes: null }))).toBe('70 active minutes so far this week.');
    expect(activeMinutesLine(week({ total_minutes: null }))).toBe('Nothing recorded this week yet.');
  });
});

describe('ActiveMinutesCard', () => {
  it('shows the week, its sources and the estimate note', async () => {
    vi.mocked(api.getHealthActiveMinutes).mockResolvedValue(week());
    renderCard();
    expect(await screen.findByTestId('active-minutes-line')).toHaveTextContent('70 active minutes of 150');
    expect(screen.getByText(/brisk-walking hours \(estimate\)/)).toBeInTheDocument();
    expect(screen.getByText(/Step minutes are an estimate/)).toBeInTheDocument();
  });

  it('asks for a target instead of assuming one', async () => {
    vi.mocked(api.getHealthActiveMinutes).mockResolvedValue(week({ target_minutes: null, percent: null }));
    renderCard();
    expect(await screen.findByText(/Set a weekly target in Health/)).toBeInTheDocument();
  });

  it('says when geo could not be read', async () => {
    vi.mocked(api.getHealthActiveMinutes).mockResolvedValue(week({ unavailable: ['steps'] }));
    renderCard();
    expect(await screen.findByText('Steps could not be read just now.')).toBeInTheDocument();
  });
});
