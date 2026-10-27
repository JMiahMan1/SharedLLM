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
      askAParent: vi.fn(),
      setScreenRules: vi.fn(),
      pauseScreenTime: vi.fn(),
      giveScreenTimeBonus: vi.fn(),
      decideScreenTimeRequest: vi.fn(),
      getUsage: vi.fn().mockResolvedValue({ status: 'SUCCESS', days: [] }),
      getOfflineCode: vi.fn().mockResolvedValue({ set: false, updated_at: null, can_set: true }),
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

  it('shows the minutes left on each app with its own limit', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({
      rules: { app_limits: { 'com.google.android.youtube': 30 } }, limit_min: null, remaining_min: null,
      app_minutes_left: { 'com.google.android.youtube': 0 },
    }));
    wrap(<ScreenTimeCard />);
    expect(await screen.findByTestId('screen-time-apps')).toHaveTextContent('YouTube: used up for today');
  });

  it('lets the child ask a parent to unblock a blocked app and shows their own weekly report', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({
      blocked_apps: ['com.roblox.client'],
      week: {
        days_reported: 3,
        total_min: 180,
        daily_avg_min: 60,
        days: [
          { day: '2026-10-19', minutes: 45 },
          { day: '2026-10-20', minutes: 60 },
          { day: '2026-10-21', minutes: 75 },
        ],
        top_apps: [{ package: 'com.google.android.youtube', minutes: 90 }],
      },
    }));
    vi.mocked(api.askAParent).mockResolvedValue({ request: { id: 9 } as never });
    wrap(<ScreenTimeCard />);

    expect(await screen.findByTestId('screen-time-week')).toHaveTextContent('This week: 1 h a day (3 h across 3 days)');
    expect(screen.getByTestId('screen-time-week-apps')).toHaveTextContent('Most used: YouTube (1 h 30 min)');
    fireEvent.change(screen.getByLabelText('What is the extra time for?'), { target: { value: 'playing with cousins' } });
    fireEvent.click(screen.getByTestId('ask-unblock-com.roblox.client'));
    await waitFor(() => expect(api.askAParent).toHaveBeenCalledWith('app_unblock', 'com.roblox.client', 'playing with cousins', 'Roblox'));
    expect(await screen.findByTestId('asked-unblock-com.roblox.client')).toHaveTextContent('Asked a parent');
  });

  it('stays out of the way when no rules are set', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ rules: {}, limit_min: null, remaining_min: null }));
    const { container } = wrap(<ScreenTimeCard />);
    await waitFor(() => expect(api.getScreenTimeStatus).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

describe('ScreenTimeControls (a parent): the weekly schedule', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(
      status({ can_manage: true, rules: { daily_limit_min: 120, weekend_limit_min: 180, bedtime_start: '21:00', bedtime_end: '07:00', day_limits: { fri: 150 } } }),
    );
    vi.mocked(api.setScreenRules).mockResolvedValue({ rules: {} });
  });

  it('shows a row per day with what each day falls back to', async () => {
    wrap(<ScreenTimeControls username="kid" name="Zachary" />);
    expect(await screen.findByLabelText('Monday limit in minutes')).toHaveAttribute('placeholder', '120 (2 h)');
    expect(screen.getByLabelText('Saturday limit in minutes')).toHaveAttribute('placeholder', '180 (3 h)');
    expect(screen.getByLabelText('Friday limit in minutes')).toHaveValue(150);
    expect(screen.getByRole('button', { name: 'Saturday is a school day' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('saves a day of its own, school days and cleared routines, keeping the rest', async () => {
    wrap(<ScreenTimeControls username="kid" name="Zachary" />);
    fireEvent.change(await screen.findByLabelText('Wednesday limit in minutes'), { target: { value: '60' } });
    fireEvent.change(screen.getByLabelText('Every day (min)'), { target: { value: '90' } });
    fireEvent.click(screen.getByRole('button', { name: 'Friday is a school day' }));
    fireEvent.change(screen.getByLabelText('Bedtime starts'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('screen-time-save'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalled());
    const [who, rules] = vi.mocked(api.setScreenRules).mock.calls[0];
    expect(who).toBe('kid');
    expect(rules).toMatchObject({ daily_limit_min: 90, weekend_limit_min: 180, day_limits: { wed: 60, fri: 150 }, school_days: ['mon', 'tue', 'wed', 'thu'] });
    expect(rules.bedtime_start).toBeUndefined();
  });

  it('says so to someone who cannot manage the child', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ can_manage: false }));
    wrap(<ScreenTimeControls username="kid" name="Zachary" />);
    expect(await screen.findByTestId('screen-time-controls-empty')).toHaveTextContent('Only their parents');
  });
});
