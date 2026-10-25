import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import FamilyChallengeCard from './FamilyChallengeCard';
import { api } from '../../services/api';
import type { HealthChallenge, HealthChallengeWeek } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getMe: vi.fn(),
      getHealthChallenges: vi.fn(),
      createHealthChallenge: vi.fn(),
      joinHealthChallenge: vi.fn(),
      leaveHealthChallenge: vi.fn(),
      deleteHealthChallenge: vi.fn(),
      getTalkConversations: vi.fn(),
      sendTalkMessage: vi.fn(),
    },
  };
});

vi.mock('../chat/familyDirectory', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../chat/familyDirectory')>()),
  useFamilyDirectory: () => (id?: string | null) =>
    id === 'emma' ? { username: 'emma', display_name: 'Emma' } : undefined,
}));

const KINDS: HealthChallengeWeek['kinds'] = [
  { kind: 'water_days', title: 'Water goal days', unit: 'days', detail: 'Days your logged water reaches your water goal.', taken: false },
  { kind: 'sleep_nights', title: 'Sleep goal nights', unit: 'nights', detail: 'Nights your logged sleep reaches your sleep need.', taken: false },
  { kind: 'cook_nights', title: 'Cook nights', unit: 'nights', detail: 'Days a dinner is logged from a family recipe.', taken: true },
];

const COOK: HealthChallenge = {
  id: 7,
  kind: 'cook_nights',
  title: 'Cook nights',
  unit: 'nights',
  detail: 'Days a dinner is logged from a family recipe.',
  week_start: '2026-10-19',
  week_end: '2026-10-25',
  target_days: 3,
  created_by: 'mom',
  joined: false,
  leaderboard: [
    { user_id: 'emma', count: 3, days: ['2026-10-19', '2026-10-20', '2026-10-21'], reached: true, guideline_goal: false },
    { user_id: 'mom', count: 1, days: ['2026-10-19'], reached: false, guideline_goal: false },
  ],
};

const week = (overrides: Partial<HealthChallengeWeek> = {}): HealthChallengeWeek => ({
  week_start: '2026-10-19',
  week_end: '2026-10-25',
  can_create: true,
  kinds: KINDS,
  challenges: [COOK],
  ...overrides,
});

const wrap = (ui: ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

describe('FamilyChallengeCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getMe).mockResolvedValue({ username: 'mom', is_admin: false } as never);
    vi.mocked(api.getTalkConversations).mockResolvedValue({
      status: 'SUCCESS',
      detail: { conversations: [{ token: 'fam', display_name: 'Family', type: 2 }] },
    } as never);
    vi.mocked(api.sendTalkMessage).mockResolvedValue({ status: 'SUCCESS' } as never);
  });

  it('shows the standings from the server, by name', async () => {
    vi.mocked(api.getHealthChallenges).mockResolvedValue(week());
    wrap(<FamilyChallengeCard />);
    expect(await screen.findByTestId('standing-cook_nights-emma')).toHaveTextContent('Emma');
    expect(screen.getByTestId('standing-cook_nights-emma')).toHaveTextContent('3 of 3 nights');
    expect(screen.getByTestId('standing-cook_nights-mom')).toHaveTextContent('1 of 3 nights');
    expect(screen.getByTestId('family-challenge')).not.toHaveTextContent(/kcal|calorie|weight/i);
  });

  it('joins a challenge', async () => {
    vi.mocked(api.getHealthChallenges).mockResolvedValue(week());
    vi.mocked(api.joinHealthChallenge).mockResolvedValue({ ...COOK, joined: true });
    wrap(<FamilyChallengeCard />);
    fireEvent.click(await screen.findByTestId('challenge-join-cook_nights'));
    await waitFor(() => expect(api.joinHealthChallenge).toHaveBeenCalledWith(7));
  });

  it('lets an adult start one of the kinds not taken this week', async () => {
    vi.mocked(api.getHealthChallenges).mockResolvedValue(week());
    vi.mocked(api.createHealthChallenge).mockResolvedValue({ ...COOK, kind: 'water_days', title: 'Water goal days' });
    wrap(<FamilyChallengeCard />);
    const challenge = await screen.findByLabelText('Challenge');
    expect([...(challenge as HTMLSelectElement).options].map((option) => option.value)).toEqual(['water_days', 'sleep_nights']);
    fireEvent.change(challenge, { target: { value: 'sleep_nights' } });
    fireEvent.change(screen.getByLabelText('Days to aim for'), { target: { value: '4' } });
    fireEvent.click(screen.getByTestId('family-challenge-start'));
    await waitFor(() => expect(api.createHealthChallenge).toHaveBeenCalled());
    expect(vi.mocked(api.createHealthChallenge).mock.calls[0][0]).toMatchObject({ kind: 'sleep_nights', target_days: 4 });
  });

  it('a child sees the empty state and no start form', async () => {
    vi.mocked(api.getHealthChallenges).mockResolvedValue(week({ can_create: false, challenges: [] }));
    wrap(<FamilyChallengeCard />);
    expect(await screen.findByTestId('family-challenge-empty')).toHaveTextContent('A parent can start one.');
    expect(screen.queryByTestId('family-challenge-create')).toBeNull();
  });

  it('shares the leaderboard to a chat', async () => {
    vi.mocked(api.getHealthChallenges).mockResolvedValue(week());
    wrap(<FamilyChallengeCard />);
    fireEvent.click(await screen.findByTestId('challenge-share-cook_nights'));
    fireEvent.click(await screen.findByTestId('bible-share-chat-target'));
    await waitFor(() => expect(api.sendTalkMessage).toHaveBeenCalled());
    const message = vi.mocked(api.sendTalkMessage).mock.calls[0][0].message;
    expect(message).toContain('🏆 Family challenge: Cook nights');
    expect(message).toContain('1. Emma · 3 of 3 nights ✓');
    expect(message).toContain('2. mom · 1 of 3 nights');
  });

  it('shows a step challenge, and a member whose steps could not be read is not shown at zero', async () => {
    const steps: HealthChallenge = {
      ...COOK,
      id: 9,
      kind: 'step_days',
      title: 'Step goal days',
      unit: 'days',
      detail: 'Days your steps reach your daily step goal.',
      target_days: 4,
      leaderboard: [
        { user_id: 'emma', count: 4, days: ['2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22'], reached: true, guideline_goal: false, unavailable: false },
        { user_id: 'mom', count: 0, days: [], reached: false, guideline_goal: false, unavailable: true },
      ],
    };
    vi.mocked(api.getHealthChallenges).mockResolvedValue(
      week({ challenges: [steps], kinds: [...KINDS, { kind: 'step_days', title: 'Step goal days', unit: 'days', detail: steps.detail, taken: true }] }),
    );
    wrap(<FamilyChallengeCard />);
    expect(await screen.findByTestId('standing-step_days-emma')).toHaveTextContent('4 of 4 days');
    expect(screen.getByTestId('standing-step_days-mom')).toHaveTextContent('not available right now');
    expect(screen.getByTestId('standing-step_days-mom')).not.toHaveTextContent('0 of 4');
  });

  it('offers step goal days when it is free this week', async () => {
    vi.mocked(api.getHealthChallenges).mockResolvedValue(
      week({ kinds: [...KINDS, { kind: 'step_days', title: 'Step goal days', unit: 'days', detail: 'Days your steps reach your daily step goal.', taken: false }] }),
    );
    wrap(<FamilyChallengeCard />);
    const challenge = await screen.findByLabelText('Challenge');
    expect([...(challenge as HTMLSelectElement).options].map((option) => option.value)).toContain('step_days');
  });

  it('shows an error when the store cannot be reached', async () => {
    vi.mocked(api.getHealthChallenges).mockRejectedValue(new Error('down'));
    wrap(<FamilyChallengeCard />);
    expect(await screen.findByTestId('family-challenge-error')).toBeInTheDocument();
  });
});
