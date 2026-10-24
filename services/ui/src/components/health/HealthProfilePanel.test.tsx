import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HealthProfilePanel from './HealthProfilePanel';
import { api } from '../../services/api';
import type { HealthProfile } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getHealthProfile: vi.fn(),
      getHealthSummary: vi.fn().mockResolvedValue(null),
      getHealthShares: vi.fn().mockResolvedValue([]),
      getFamilyDirectory: vi.fn().mockResolvedValue([]),
    },
  };
});

function profile(overrides: Partial<HealthProfile> = {}): HealthProfile {
  return {
    user_id: 'emma',
    exists: true,
    display_name: 'Emma',
    birth_year: null,
    height_cm: null,
    sex: 'female',
    activity_level: 'moderate',
    goal: 'maintain',
    units: 'imperial',
    kid_mode: false,
    sleep_need_minutes: null,
    water_goal_ml: null,
    weekly_active_minutes: null,
    metrics: { sleep: true },
    metric_catalog: [{ key: 'sleep', label: 'Sleep', default: true }],
    ...overrides,
  } as HealthProfile;
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HealthProfilePanel />
    </QueryClientProvider>,
  );
}

describe('HealthProfilePanel kid mode', () => {
  beforeEach(() => vi.mocked(api.getHealthProfile).mockReset());

  it('shows kid mode on and locked for a minor', async () => {
    vi.mocked(api.getHealthProfile).mockResolvedValue(profile({ kid_mode: true, kid_mode_locked: true }));
    renderPanel();
    expect(await screen.findByText('Always on for anyone under 18.')).toBeInTheDocument();
    const box = screen.getByRole('checkbox', { name: /kid mode/i });
    expect(box).toBeChecked();
    expect(box).toBeDisabled();
  });

  it('leaves an adult free to choose', async () => {
    vi.mocked(api.getHealthProfile).mockResolvedValue(profile({ kid_mode_locked: false }));
    renderPanel();
    const box = await screen.findByRole('checkbox', { name: /kid mode/i });
    expect(box).not.toBeChecked();
    expect(box).toBeEnabled();
    expect(screen.queryByText('Always on for anyone under 18.')).not.toBeInTheDocument();
  });
});
