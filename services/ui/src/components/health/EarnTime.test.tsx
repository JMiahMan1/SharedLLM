/**
 * Earned time (docs/HEALTH_HUB.md, "Earning time and chores first"): the parent sets what
 * steps, exercise and approved chores are worth; the child sees what they
 * earned, what carried over and which chores wait for a parent's OK.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import ScreenTimeCard from './ScreenTimeCard';
import EarnTimeControls from './EarnTimeControls';
import { api } from '../../services/api';
import { earnedLine, lockedLine } from '../../lib/screenTimeText';
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
      setScreenRules: vi.fn(),
    },
  };
});

const EARN = { steps_every: 1000, steps_min: 5, chore_min: 10, max_min: 60 };
const status = (overrides: Partial<ScreenTimeStatus> = {}): ScreenTimeStatus => ({
  username: 'kid', day: '2026-10-21', rules: { daily_limit_min: 60, earn: EARN, chores_first: true },
  can_manage: false, limit_min: 60, used_min: 30, bonus_min: 0, remaining_min: 85, bedtime_active: false,
  school_active: false, paused: false, locked: false, reason: null, until: null, resets_at: '2026-10-22T00:00:00-07:00',
  earned_min: 25, banked_min: 30, chores_first: true, chores_left: 0,
  earned: { total_min: 25, uncapped_min: 25, max_min: 60, parts: {
    steps: { count: 3000, every: 1000, minutes: 5, earned_min: 15 },
    chores: { count: 1, every: 1, minutes: 10, earned_min: 10 },
  } },
  chores_waiting: [{ id: 4, title: 'Trash', day: '2026-10-21' }],
  ...overrides,
});

const wrap = (ui: ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getScreenTimeRequests).mockResolvedValue({ requests: [] });
});

describe('the words', () => {
  it('names what earned the time, and the day cap when it bit', () => {
    expect(earnedLine(status())).toBe('Earned 25 min today from 3,000 steps and 1 approved chore.');
    const capped = status({ earned_min: 60, earned: { total_min: 60, uncapped_min: 90, max_min: 60, parts: { steps: { count: 18000, every: 1000, minutes: 5, earned_min: 90 } } } });
    expect(earnedLine(capped)).toBe("Earned 60 min today from 18,000 steps (today's most is 60 min).");
    expect(earnedLine(status({ earned_min: 0 }))).toBe('');
  });

  it('says what to do when chores come first', () => {
    expect(lockedLine(status({ locked: true, reason: 'chores', chores_left: 2 }))).toBe(
      'Chores first: 2 chores left today. Tick them off on the chore chart to unlock.',
    );
    expect(lockedLine(status({ locked: true, reason: 'chores', chores_left: 1 }))).toMatch(/^Chores first: 1 chore left/);
  });
});

describe('ScreenTimeCard (the child)', () => {
  it('shows earned and saved time, chores waiting on a parent and how to earn more', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status());
    wrap(<ScreenTimeCard />);
    const box = await screen.findByTestId('screen-time-earned');
    expect(box).toHaveTextContent('Earned 25 min today from 3,000 steps and 1 approved chore.');
    expect(screen.getByTestId('screen-time-banked')).toHaveTextContent('30 min saved from earlier this week');
    expect(screen.getByTestId('screen-time-chores-waiting')).toHaveTextContent("Waiting for a parent's OK: Trash.");
    expect(box).toHaveTextContent('every 1,000 steps earns 5 min; each chore a parent approves earns 10 min');
  });

  it('explains a chores-first lock', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ locked: true, reason: 'chores', chores_left: 1 }));
    wrap(<ScreenTimeCard />);
    expect(await screen.findByTestId('screen-time-locked')).toHaveTextContent('Chores first: 1 chore left today.');
  });
});

describe('EarnTimeControls (a parent)', () => {
  it('saves what steps, exercise and chores earn, keeping the other rules', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ can_manage: true, rules: { daily_limit_min: 60, bedtime_start: '21:00', bedtime_end: '07:00', paused: true } }));
    vi.mocked(api.setScreenRules).mockResolvedValue({ rules: {} });
    wrap(<EarnTimeControls username="kid" name="Jordan" />);
    fireEvent.change(await screen.findByLabelText('Steps for each block'), { target: { value: '2000' } });
    fireEvent.change(screen.getByLabelText('Minutes each block of steps earns'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Minutes each approved chore earns'), { target: { value: '15' } });
    fireEvent.change(screen.getByLabelText('Most minutes a day can earn'), { target: { value: '90' } });
    fireEvent.click(screen.getByTestId('earn-chores-first'));
    fireEvent.click(screen.getByTestId('earn-save'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledWith('kid', {
      daily_limit_min: 60, bedtime_start: '21:00', bedtime_end: '07:00',
      earn: { steps_every: 2000, steps_min: 10, chore_min: 15, max_min: 90 }, chores_first: true,
    }));
  });

  it('will not save half a line', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ can_manage: true, rules: {} }));
    wrap(<EarnTimeControls username="kid" name="Jordan" />);
    fireEvent.change(await screen.findByLabelText('Minutes of exercise for each block'), { target: { value: '15' } });
    expect(screen.getByRole('alert')).toHaveTextContent('Fill in both numbers for exercise');
    expect(screen.getByTestId('earn-save')).toBeDisabled();
  });

  it('turns earning and chores first off when cleared', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ can_manage: true, rules: { earn: { chore_min: 10 }, chores_first: true } }));
    vi.mocked(api.setScreenRules).mockResolvedValue({ rules: {} });
    wrap(<EarnTimeControls username="kid" name="Jordan" />);
    fireEvent.change(await screen.findByLabelText('Minutes each approved chore earns'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('earn-chores-first'));
    fireEvent.click(screen.getByTestId('earn-save'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledWith('kid', { earn: undefined, chores_first: undefined }));
  });

  it('is read-only for someone who cannot manage the child', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ can_manage: false }));
    wrap(<EarnTimeControls username="kid" name="Jordan" />);
    expect(await screen.findByText('Only their parents can change this.')).toBeInTheDocument();
  });
});
