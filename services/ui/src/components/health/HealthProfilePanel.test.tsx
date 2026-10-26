import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HealthProfilePanel from './HealthProfilePanel';
import { api } from '../../services/api';
import { confirmDialog } from '../ui/confirmDialog';
import type { HealthProfile } from '../../types/api';

// Destructive actions draw the app's own dialog, not the browser's.
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
const confirmMock = vi.mocked(confirmDialog);

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
      getHealthMetrics: vi.fn(),
      deleteHealthMetric: vi.fn(),
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

function sample(id: number, metric: string, value: number, unit: string) {
  return {
    id,
    user_id: 'emma',
    metric,
    value,
    unit,
    recorded_at: '2026-10-26T09:00:00Z',
    source: 'manual',
    external_id: null,
    metadata: {},
  };
}

function summaryWith(metrics: Record<string, ReturnType<typeof sample>>) {
  return { user_id: 'emma', day: '2026-10-26', metrics, resting_hr_baseline: 58 } as never;
}

describe('HealthProfilePanel measurements', () => {
  beforeEach(() => {
    vi.mocked(api.getHealthProfile).mockReset();
    vi.mocked(api.getHealthSummary).mockReset();
    vi.mocked(api.getHealthMetrics).mockReset();
    vi.mocked(api.deleteHealthMetric).mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('shows every measure the summary has, not just weight and resting heart rate', async () => {
    vi.mocked(api.getHealthProfile).mockResolvedValue(profile());
    vi.mocked(api.getHealthSummary).mockResolvedValue(
      summaryWith({
        weight: sample(1, 'weight', 70.5, 'kg'),
        body_fat: sample(2, 'body_fat', 22.4, '%'),
        resting_hr: sample(3, 'resting_hr', 58, 'bpm'),
        hrv: sample(4, 'hrv', 45, 'ms'),
      }),
    );
    renderPanel();

    // An imported body fat and HRV used to be stored and never shown.
    expect(await screen.findByText('Body fat')).toBeInTheDocument();
    expect(screen.getByText('22.4 %')).toBeInTheDocument();
    expect(screen.getByText('70.5 kg')).toBeInTheDocument();
    expect(screen.getByText('45 ms')).toBeInTheDocument();
    expect(screen.getByText(/baseline 58 bpm/)).toBeInTheDocument();
  });

  it('hides weight for a child, as the rest of the panel does', async () => {
    vi.mocked(api.getHealthProfile).mockResolvedValue(profile({ kid_mode: true, kid_mode_locked: true }));
    vi.mocked(api.getHealthSummary).mockResolvedValue(
      summaryWith({ weight: sample(1, 'weight', 40, 'kg'), hrv: sample(2, 'hrv', 60, 'ms') }),
    );
    renderPanel();

    expect(await screen.findByText('HRV')).toBeInTheDocument();
    expect(screen.queryByText('Weight')).not.toBeInTheDocument();
  });

  it('shows a measure history and deletes a reading after asking', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getHealthProfile).mockResolvedValue(profile());
    vi.mocked(api.getHealthSummary).mockResolvedValue(
      summaryWith({ weight: sample(1, 'weight', 70.5, 'kg') }),
    );
    vi.mocked(api.getHealthMetrics).mockResolvedValue({
      user_id: 'emma',
      metric: 'weight',
      unit: 'kg',
      days: 30,
      samples: [sample(1, 'weight', 70.5, 'kg'), sample(2, 'weight', 71.2, 'kg')],
      latest: sample(1, 'weight', 70.5, 'kg'),
    });
    vi.mocked(api.deleteHealthMetric).mockResolvedValue({ deleted: 1 });
    renderPanel();

    await user.click(await screen.findByRole('button', { name: 'History' }));
    expect(await screen.findByTestId('measure-history')).toBeInTheDocument();
    expect(api.getHealthMetrics).toHaveBeenCalledWith('weight', 30);

    await user.click(screen.getByRole('button', { name: 'Delete the 71.2 kg reading' }));
    await waitFor(() => expect(api.deleteHealthMetric).toHaveBeenCalledWith(2));
    expect(confirmMock).toHaveBeenCalled();
  });

  it('leaves the reading alone when the dialog is declined', async () => {
    const user = userEvent.setup();
    confirmMock.mockResolvedValue(false);
    vi.mocked(api.getHealthProfile).mockResolvedValue(profile());
    vi.mocked(api.getHealthSummary).mockResolvedValue(
      summaryWith({ weight: sample(1, 'weight', 70.5, 'kg') }),
    );
    vi.mocked(api.getHealthMetrics).mockResolvedValue({
      user_id: 'emma',
      metric: 'weight',
      unit: 'kg',
      days: 30,
      samples: [sample(1, 'weight', 70.5, 'kg')],
      latest: sample(1, 'weight', 70.5, 'kg'),
    });
    renderPanel();

    await user.click(await screen.findByRole('button', { name: 'History' }));
    await user.click(await screen.findByRole('button', { name: 'Delete the 70.5 kg reading' }));
    expect(api.deleteHealthMetric).not.toHaveBeenCalled();
  });
});
