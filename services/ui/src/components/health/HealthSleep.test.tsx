import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import HealthSleep from './HealthSleep';
import { api } from '../../services/api';
import type { SleepEstimate, SleepSession } from '../../types/api';

vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getSleep: vi.fn(),
      getHealthSummary: vi.fn(),
      logSleep: vi.fn(),
      updateSleep: vi.fn(),
      deleteSleep: vi.fn(),
      getSleepEstimate: vi.fn(),
    },
  };
});

// Built from local clock times so the inputs read the same in any test timezone.
const at = (day: string, time: string) => new Date(`${day}T${time}:00`).toISOString();

const estimate: SleepEstimate = {
  night: '2026-10-21',
  bedtime: at('2026-10-20', '22:40'),
  wake_time: at('2026-10-21', '06:30'),
  duration_minutes: 466,
  wakeups: 1,
  awake_minutes: 4,
  devices: 2,
};

const saved: SleepSession = {
  id: 7, user_id: 'jeremiah', night: '2026-10-19', bedtime: at('2026-10-18', '23:00'), wake_time: at('2026-10-19', '06:00'),
  duration_minutes: 420, efficiency: null, quality: 3, stages: {}, source: 'usage_estimate', external_id: null, note: '',
};

const wrap = (ui: ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

const bedtimeInput = () => screen.getByLabelText('Bedtime') as HTMLInputElement;
const wakeInput = () => screen.getByLabelText('Wake') as HTMLInputElement;

describe('HealthSleep: estimate from device use', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSleep).mockResolvedValue({ user_id: 'jeremiah', sessions: [saved] });
    vi.mocked(api.getHealthSummary).mockRejectedValue(new Error('not needed'));
    vi.mocked(api.getSleepEstimate).mockResolvedValue({ estimate, devices: 2 });
    vi.mocked(api.logSleep).mockResolvedValue({ ...saved, duplicate: false });
    vi.mocked(api.updateSleep).mockResolvedValue(saved);
  });

  it('fills the form and saves an untouched estimate as an estimate', async () => {
    wrap(<HealthSleep />);
    fireEvent.change(screen.getByLabelText('Woke on'), { target: { value: '2026-10-21' } });
    fireEvent.click(screen.getByTestId('estimate-sleep'));

    await waitFor(() => expect(bedtimeInput().value).toBe('22:40'));
    expect(wakeInput().value).toBe('06:30');
    expect(screen.getByTestId('sleep-estimate-note')).toHaveTextContent('2 devices, 1 wake-up (4 min)');
    expect(api.getSleepEstimate).toHaveBeenCalledWith('2026-10-21');

    fireEvent.click(screen.getByTestId('save-sleep'));
    await waitFor(() => expect(api.logSleep).toHaveBeenCalled());
    expect(vi.mocked(api.logSleep).mock.calls[0][0]).toMatchObject({
      night: '2026-10-21', bedtime: '22:40', wake_time: '06:30', duration_minutes: 466, source: 'usage_estimate',
    });
  });

  it('an edited estimate is saved as the person’s own log', async () => {
    wrap(<HealthSleep />);
    fireEvent.change(screen.getByLabelText('Woke on'), { target: { value: '2026-10-21' } });
    fireEvent.click(screen.getByTestId('estimate-sleep'));
    await waitFor(() => expect(bedtimeInput().value).toBe('22:40'));

    fireEvent.change(bedtimeInput(), { target: { value: '23:15' } });
    fireEvent.click(screen.getByTestId('save-sleep'));
    await waitFor(() => expect(api.logSleep).toHaveBeenCalled());
    expect(vi.mocked(api.logSleep).mock.calls[0][0]).toMatchObject({ bedtime: '23:15', duration_minutes: null, source: 'manual' });
  });

  it('edits a saved night in place', async () => {
    wrap(<HealthSleep />);
    fireEvent.click(await screen.findByTestId('edit-sleep-7'));
    expect(bedtimeInput().value).toBe('23:00');
    expect(wakeInput().value).toBe('06:00');

    fireEvent.change(wakeInput(), { target: { value: '06:45' } });
    fireEvent.click(screen.getByTestId('save-sleep'));
    await waitFor(() => expect(api.updateSleep).toHaveBeenCalled());
    const [id, body] = vi.mocked(api.updateSleep).mock.calls[0];
    expect(id).toBe(7);
    expect(body).toMatchObject({ night: '2026-10-19', wake_time: '06:45', duration_minutes: null, source: 'manual' });
    expect(api.logSleep).not.toHaveBeenCalled();
  });

  it('labels an estimated night in the history', async () => {
    wrap(<HealthSleep />);
    expect(await screen.findByTestId('sleep-row-7')).toHaveTextContent('estimated from device use');
  });

  it('leaves the form alone when there is nothing to estimate from', async () => {
    vi.mocked(api.getSleepEstimate).mockResolvedValue({ estimate: null, devices: 1 });
    wrap(<HealthSleep />);
    fireEvent.click(screen.getByTestId('estimate-sleep'));
    await waitFor(() => expect(api.getSleepEstimate).toHaveBeenCalled());
    expect(bedtimeInput().value).toBe('');
    expect(screen.queryByTestId('sleep-estimate-note')).toBeNull();
  });
});
