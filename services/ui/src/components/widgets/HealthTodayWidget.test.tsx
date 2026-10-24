import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HealthTodayWidget from './HealthTodayWidget';
import { api } from '../../services/api';
import { planDayOf } from '../../lib/mealPlan';
import type { HealthSummary } from '../../types/api';
import type { UserWidgetSettings } from '../../types/widget';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getHealthSummary: vi.fn(),
      getHealthActiveMinutes: vi.fn(),
      readNote: vi.fn(),
      getDailySteps: vi.fn(),
    },
  };
});

const METRICS = {
  readiness: true, checkin: true, sleep: true, nutrition: true, water: true, steps: true, active_minutes: false,
  streaks: true, weekly_digest: true, family_challenge: true, cheers: true, lessons: true, coach: true, fasting: false,
};

function summary(kid = false): HealthSummary {
  return {
    profile: { kid_mode: kid, metrics: METRICS },
    readiness: { score: 78, label: 'ready', detail: '', confidence: 'high', missing: [], components: {} },
    sleep: { last_night: { duration_minutes: 450 }, score: { score: 85 }, need_minutes: 480, history: [] },
    water: { ml: 1000, goal_ml: 2000, percent: 50, logged: true },
    nutrition: { logged: true, entries_count: 2, totals: { calories: 900 } },
  } as unknown as HealthSummary;
}

const SETTINGS = { widget_key: 'health_today', size: 'medium' } as unknown as UserWidgetSettings;

function renderWidget() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HealthTodayWidget settingsButton={null} userSettings={SETTINGS} onTogglePin={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.readNote).mockResolvedValue({
    status: 'SUCCESS',
    message: `#meal-plan\n\n- [ ] ${planDayOf()} · Chicken Chili`,
    service: 'note_read',
  });
});

describe('HealthTodayWidget', () => {
  it("shows readiness, the day so far and tonight's dinner, never steps", async () => {
    vi.mocked(api.getHealthSummary).mockResolvedValue(summary());
    renderWidget();
    expect(await screen.findByText(/Readiness 78/)).toBeInTheDocument();
    expect(screen.getByText('Slept 7h 30m · 85/100')).toBeInTheDocument();
    expect(screen.getByText('1,000 of 2,000 ml water')).toBeInTheDocument();
    expect(screen.getByText('900 kcal logged')).toBeInTheDocument();
    expect(await screen.findByText('Tonight: Chicken Chili')).toBeInTheDocument();
    expect(api.getDailySteps).not.toHaveBeenCalled();
    // Active minutes is switched off here, so it is never fetched.
    expect(api.getHealthActiveMinutes).not.toHaveBeenCalled();
  });

  it('shows a child what was logged, not calories', async () => {
    vi.mocked(api.getHealthSummary).mockResolvedValue(summary(true));
    renderWidget();
    expect(await screen.findByText('2 things logged')).toBeInTheDocument();
    expect(screen.queryByText(/kcal/)).not.toBeInTheDocument();
  });
});
