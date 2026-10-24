import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CoachCard from './CoachCard';
import { api } from '../../services/api';
import { HEALTH_REMINDERS, existingReminders, reminderPayload } from '../../lib/healthReminders';
import type { HealthGoalsWeek } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getHealthGoals: vi.fn(),
      createHealthGoal: vi.fn(),
      deleteHealthGoal: vi.fn(),
      getTimers: vi.fn(),
      createTimer: vi.fn(),
      deleteTimer: vi.fn(),
    },
  };
});

vi.mock('../../lib/healthHub', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/healthHub')>()),
  localDay: () => '2026-10-22',
}));

const KINDS: HealthGoalsWeek['kinds'] = [
  { kind: 'water_days', title: 'Water goal days', unit: 'days', detail: '', taken: true },
  { kind: 'sleep_nights', title: 'Sleep goal nights', unit: 'nights', detail: '', taken: false },
  { kind: 'cook_nights', title: 'Cook nights', unit: 'nights', detail: '', taken: false },
];

function week(overrides: Partial<HealthGoalsWeek> = {}): HealthGoalsWeek {
  return {
    week_start: '2026-10-19',
    week_end: '2026-10-25',
    goals: [
      {
        id: 1,
        kind: 'water_days',
        title: 'Water goal days',
        unit: 'days',
        detail: '',
        target_days: 4,
        count: 2,
        days: ['2026-10-19', '2026-10-20'],
        reached: false,
        guideline_goal: false,
      },
    ],
    max_goals: 2,
    kinds: KINDS,
    suggestion: { kind: 'cook_nights', target_days: 2, title: 'Cook nights', unit: 'nights', reason: 'Last week you managed 1. One more is a small step.' },
    ...overrides,
  };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CoachCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getTimers).mockResolvedValue([]);
});

describe('health reminders', () => {
  it('are daily reminders that open Health', () => {
    expect(reminderPayload(HEALTH_REMINDERS[0], '21:30')).toEqual({
      title: 'Bedtime: time to start winding down',
      type: 'reminder',
      time_str: '21:30',
      recurrence: 'daily',
      scope: 'user',
      emoji: '🌙',
      url: '/health',
    });
  });

  it('are found among the timers by title', () => {
    const found = existingReminders([
      { id: 'a', type: 'reminder', title: 'Water break: have a glass of water', expires_at: '2026-10-22T15:00:00Z', active: true },
      { id: 'b', type: 'timer', title: 'Pasta', expires_at: '2026-10-22T15:00:00Z', active: true },
    ]);
    expect(Object.keys(found)).toEqual(['water']);
  });
});

describe('CoachCard', () => {
  it('shows goal progress and sets the suggested goal', async () => {
    vi.mocked(api.getHealthGoals).mockResolvedValue(week());
    vi.mocked(api.createHealthGoal).mockResolvedValue(week({ suggestion: null }));
    renderCard();

    expect(await screen.findByText('Water goal days: 2 of 4 days')).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId('goal-suggestion')).getByText('Set this goal'));
    await waitFor(() =>
      expect(api.createHealthGoal).toHaveBeenCalledWith({ kind: 'cook_nights', target_days: 2, day: '2026-10-22' }),
    );
  });

  it('sets a daily bedtime reminder through the timers pipeline', async () => {
    vi.mocked(api.getHealthGoals).mockResolvedValue(week());
    vi.mocked(api.createTimer).mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'timer_add' });
    renderCard();

    const row = await screen.findByTestId('reminder-bedtime');
    fireEvent.change(within(row).getByLabelText('Bedtime reminder time'), { target: { value: '21:15' } });
    fireEvent.click(within(row).getByText('Remind me'));
    await waitFor(() =>
      expect(api.createTimer).toHaveBeenCalledWith(expect.objectContaining({ time_str: '21:15', recurrence: 'daily', type: 'reminder' })),
    );
  });
});
