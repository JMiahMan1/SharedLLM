import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import ScreenTimeCard from './ScreenTimeCard';
import ScreenTimeControls from './ScreenTimeControls';
import { api } from '../../services/api';
import type { ScreenTimeStatus } from '../../types/api';

vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'kid', family_role: 'child', is_minor: true } }) }));
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getScreenTimeStatus: vi.fn(),
      getScreenTimeRequests: vi.fn(),
      askForScreenTime: vi.fn(),
      setScreenRules: vi.fn(),
      pauseScreenTime: vi.fn(),
      giveScreenTimeBonus: vi.fn(),
      decideScreenTimeRequest: vi.fn(),
    },
  };
});

const status = (overrides: Partial<ScreenTimeStatus> = {}): ScreenTimeStatus => ({
  username: 'kid', day: '2026-10-21', rules: { daily_limit_min: 120, bedtime_start: '21:00', bedtime_end: '07:00' },
  can_manage: false, protected: true, limit_min: 120, used_min: 75, bonus_min: 0, remaining_min: 45, bedtime_active: false,
  school_active: false, paused: false, locked: false, reason: null, until: null, resets_at: '2026-10-22T00:00:00-07:00',
  ...overrides,
});

const wrap = (ui: ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

describe('ScreenTimeCard (the member)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getScreenTimeRequests).mockResolvedValue({ requests: [] });
  });

  it('shows the time left and lets the member ask for more', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status());
    vi.mocked(api.askForScreenTime).mockResolvedValue({ request: { id: 1 } as never });
    wrap(<ScreenTimeCard />);

    expect(await screen.findByTestId('screen-time-left')).toHaveTextContent('45 min left today');
    fireEvent.change(screen.getByLabelText('What is the extra time for?'), { target: { value: 'homework video' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask for 30 min' }));
    await waitFor(() => expect(api.askForScreenTime).toHaveBeenCalledWith(30, 'homework video'));
  });

  it('explains a lock and when it lifts', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ locked: true, reason: 'bedtime', until: '2026-10-22T07:00:00-07:00' }));
    wrap(<ScreenTimeCard />);
    expect(await screen.findByTestId('screen-time-locked')).toHaveTextContent(/^Bedtime\. The phone is back on at/);
  });

  it('says so once a request is waiting', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status());
    vi.mocked(api.getScreenTimeRequests).mockResolvedValue({ requests: [{ id: 2, username: 'kid' } as never] });
    wrap(<ScreenTimeCard />);
    expect(await screen.findByTestId('screen-time-waiting')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Ask for/ })).toBeNull();
  });

  it('stays out of the way when no rules are set', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ rules: {}, limit_min: null, remaining_min: null }));
    const { container } = wrap(<ScreenTimeCard />);
    await waitFor(() => expect(api.getScreenTimeStatus).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

describe('ScreenTimeControls (a parent)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ can_manage: true }));
    vi.mocked(api.getScreenTimeRequests).mockResolvedValue({
      requests: [{ id: 7, username: 'kid', minutes: 30, note: 'one more level' } as never, { id: 8, username: 'sib', minutes: 15 } as never],
    });
    vi.mocked(api.decideScreenTimeRequest).mockResolvedValue({ request: { id: 7 } as never });
    vi.mocked(api.setScreenRules).mockResolvedValue({ rules: {} });
  });

  it("answers only this child's requests", async () => {
    wrap(<ScreenTimeControls username="kid" name="Zachary" />);
    const requests = await screen.findAllByTestId('screen-time-request');
    expect(requests).toHaveLength(1);
    expect(requests[0]).toHaveTextContent('one more level');
    fireEvent.click(screen.getByRole('button', { name: 'Grant' }));
    await waitFor(() => expect(api.decideScreenTimeRequest).toHaveBeenCalledWith(7, true));
  });

  it('saves edited rules, clearing what was emptied', async () => {
    wrap(<ScreenTimeControls username="kid" name="Zachary" />);
    fireEvent.change(await screen.findByLabelText('Daily limit (min)'), { target: { value: '90' } });
    fireEvent.change(screen.getByLabelText('Bedtime starts'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('screen-time-save'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalled());
    const [who, rules] = vi.mocked(api.setScreenRules).mock.calls[0];
    expect(who).toBe('kid');
    expect(rules.daily_limit_min).toBe(90);
    expect(rules.bedtime_start).toBeUndefined();
  });

  it('renders nothing for someone who cannot manage the child', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ can_manage: false }));
    const { container } = wrap(<ScreenTimeControls username="kid" name="Zachary" />);
    await waitFor(() => expect(api.getScreenTimeStatus).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
