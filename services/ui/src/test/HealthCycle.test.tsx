import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HealthCycle from '../components/health/HealthCycle';
import { api } from '../services/api';
import type { HealthCycleState } from '../types/api';

vi.mock('../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getHealthCycle: vi.fn(),
      getFamilyDirectory: vi.fn(),
      getHealthShares: vi.fn(),
      updateHealthCycleSettings: vi.fn(),
      addHealthCyclePeriod: vi.fn(),
      deleteHealthCyclePeriod: vi.fn(),
      saveHealthCycleLog: vi.fn(),
      deleteHealthCycleLog: vi.fn(),
      updateHealthShare: vi.fn(),
    },
  };
});

const getHealthCycle = vi.mocked(api.getHealthCycle);
const getFamilyDirectory = vi.mocked(api.getFamilyDirectory);
const getHealthShares = vi.mocked(api.getHealthShares);
const updateHealthCycleSettings = vi.mocked(api.updateHealthCycleSettings);
const addHealthCyclePeriod = vi.mocked(api.addHealthCyclePeriod);
const saveHealthCycleLog = vi.mocked(api.saveHealthCycleLog);
const updateHealthShare = vi.mocked(api.updateHealthShare);

function cycleState(overrides: Partial<HealthCycleState> = {}): HealthCycleState {
  return {
    applicable: true,
    enabled: false,
    is_minor: false,
    average_cycle_length: null,
    average_period_length: null,
    guardian_username: null,
    summary: null,
    periods: [],
    logs: [],
    ...overrides,
  };
}

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe('HealthCycle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getFamilyDirectory.mockResolvedValue([]);
    getHealthShares.mockResolvedValue({ owner: 'ada', shares: [] });
  });

  it('does not render at all for a male profile', async () => {
    getHealthCycle.mockResolvedValue(
      cycleState({ applicable: false, reason: 'Cycle tracking is not shown for a male profile.' }),
    );
    const { container } = renderWithClient(<HealthCycle />);
    await waitFor(() => expect(getHealthCycle).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('offers an opt-in and keeps the framing non-diagnostic', async () => {
    getHealthCycle.mockResolvedValue(cycleState());
    updateHealthCycleSettings.mockResolvedValue(cycleState({ enabled: true }));
    renderWithClient(<HealthCycle />);

    expect(await screen.findByText('Cycle tracking')).toBeInTheDocument();
    expect(screen.getByText(/not contraception or medical advice/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /turn on cycle tracking/i }));
    await waitFor(() =>
      expect(updateHealthCycleSettings).toHaveBeenCalledWith({ enabled: true }),
    );
  });

  it('shows the phase, cycle day and next-period estimate without a fertility claim', async () => {
    getHealthCycle.mockResolvedValue(
      cycleState({
        enabled: true,
        summary: {
          has_data: true,
          phase: 'follicular',
          phase_label: 'Follicular',
          cycle_day: 5,
          cycle_length_used: 28,
          cycle_length_source: 'observed',
          period_length_used: 5,
          last_period_start: '2026-10-18',
          next_period_start: '2026-11-15',
          days_until_next_period: 24,
          estimated_ovulation_day: 14,
          irregular: false,
          variation_days: 1,
          confidence: 'medium',
          detail: 'Cycle day 5 of an estimated 28-day cycle.',
          disclaimer: 'Estimates only, based on logged dates. Not contraception or medical advice.',
        },
        periods: [
          {
            id: 1,
            start_day: '2026-10-18',
            end_day: '2026-10-22',
            flow: 'medium',
            note: '',
            source: 'manual',
            external_id: null,
            created_at: '2026-10-18T10:00:00+00:00',
          },
        ],
      }),
    );
    renderWithClient(<HealthCycle />);

    expect(await screen.findByText('Follicular')).toBeInTheDocument();
    expect(screen.getByText('Cycle day 5')).toBeInTheDocument();
    expect(screen.getByText(/next period in 24 days/i)).toBeInTheDocument();
    expect(screen.getByText(/not contraception or medical advice/i)).toBeInTheDocument();
    expect(screen.queryByText(/fertile/i)).not.toBeInTheDocument();
  });

  it('logs a period with the chosen flow', async () => {
    getHealthCycle.mockResolvedValue(cycleState({ enabled: true }));
    addHealthCyclePeriod.mockResolvedValue({ period: {} as never, summary: null });
    renderWithClient(<HealthCycle />);

    fireEvent.change(await screen.findByLabelText('Flow'), { target: { value: 'heavy' } });
    fireEvent.click(screen.getByRole('button', { name: /^log$/i }));
    await waitFor(() =>
      expect(addHealthCyclePeriod).toHaveBeenCalledWith(
        expect.objectContaining({ flow: 'heavy' }),
      ),
    );
  });

  it('saves a symptom log for the selected day', async () => {
    getHealthCycle.mockResolvedValue(cycleState({ enabled: true }));
    saveHealthCycleLog.mockResolvedValue({ log: {} as never });
    renderWithClient(<HealthCycle />);

    fireEvent.click(await screen.findByRole('button', { name: 'Cramps' }));
    fireEvent.click(screen.getByRole('button', { name: /save day/i }));
    await waitFor(() =>
      expect(saveHealthCycleLog).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({ symptoms: ['cramps'] }),
      ),
    );
  });

  it('restricts a teen to one guardian instead of an open person picker', async () => {
    getHealthCycle.mockResolvedValue(cycleState({ enabled: true, is_minor: true }));
    renderWithClient(<HealthCycle />);

    expect(await screen.findByText(/mother \/ trusted adult/i)).toBeInTheDocument();
    expect(screen.queryByText('Choose a person…')).not.toBeInTheDocument();
    expect(screen.getByText(/choose a mother or trusted adult above/i)).toBeInTheDocument();
  });

  it('lets an adult share the cycle phase with a chosen family member', async () => {
    getHealthCycle.mockResolvedValue(cycleState({ enabled: true }));
    getFamilyDirectory.mockResolvedValue([
      { username: 'michele', display_name: 'Michele', is_admin: false },
    ] as never);
    updateHealthShare.mockResolvedValue({ owner: 'ada', viewer: 'michele', scope: 'cycle', enabled: true });
    renderWithClient(<HealthCycle />);

    fireEvent.change(await screen.findByDisplayValue('Choose a person…'), {
      target: { value: 'michele' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^share$/i }));
    await waitFor(() =>
      expect(updateHealthShare).toHaveBeenCalledWith({
        viewer: 'michele',
        scope: 'cycle',
        enabled: true,
      }),
    );
  });
});
