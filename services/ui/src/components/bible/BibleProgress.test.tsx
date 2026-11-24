import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleProgress from './BibleProgress';
import type { BibleMark, BiblePlanEnrollment, BibleStats } from '../../types/api';
import { localDayKey } from './progressNumbers';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'sam' } }) }));

vi.mock('../../services/api', () => ({
  api: {
    getBibleStats: vi.fn(),
    getBiblePlans: vi.fn(),
    getBibleAchievements: vi.fn().mockResolvedValue({
      username: 'sam',
      points: 65,
      newly_earned: [],
      earned: [
        { id: 'first_chapter', name: 'In the Word', description: 'Finish your first chapter of Scripture.', points: 5, earned_on: '2026-09-01' },
        { id: 'chapters_25', name: 'Steady Reader', description: 'Read 25 chapters.', points: 10, earned_on: '2026-09-15' },
        { id: 'streak_7', name: 'A Full Week', description: 'Read Scripture seven days in a row.', points: 25, earned_on: '2026-09-20' },
      ],
      next_up: [
        { id: 'chapters_100', name: 'One Hundred Chapters', description: 'Read 100 chapters.', points: 25, current: 25, target: 100, remaining: 75, percent: 25, unit: 'chapters' },
        { id: 'streak_14', name: 'Two Weeks Faithful', description: 'Read Scripture fourteen days in a row.', points: 50, current: 5, target: 14, remaining: 9, percent: 35, unit: 'day streak' },
      ],
      stars: { granted: 0, status: 'ok' },
      pending_rules: [],
      announced: { posted: 0, status: 'ok' },
    }),
    getBibleActivityFeed: vi.fn().mockResolvedValue({
      entries: [
        { username: 'parent', read_streak_current: 8, days_read: 30, achievements: [{ id: 'streak_7', name: 'A Full Week' }], points: 80 },
        { username: 'child', read_streak_current: 3, days_read: 10, achievements: [], points: 15 },
      ],
      members: ['parent', 'child'],
      count: 2,
      note: 'Nobody has shared their reading yet. Share it in Settings → Share my activity.',
    }),
  },
}));

import { api } from '../../services/api';

/** The local date `back` days before today, as the stats window keys it. */
function daysAgo(back: number): string {
  const now = new Date();
  return localDayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - back));
}

function stats(overrides: Partial<BibleStats> = {}): BibleStats {
  return {
    username: 'sam',
    read_streak_current: 5,
    read_streak_longest: 12,
    open_streak_current: 7,
    open_streak_longest: 14,
    days_read: 45,
    metrics: { chapters_total: 25, books_read: 3, read_streak: 5, plan_days_total: 30, plan_completed: 1, memory_mastered: 2, quizzes_total: 10, quiz_perfect: 3 },
    days_opened: 50,
    marks: 10,
    highlights: 6,
    bookmarks: 4,
    position: { book: 'John', chapter: 3, verse: 16, reference: 'John 3' },
    last_read_at: '2026-10-13T10:00:00Z',
    // Every event: opening the app and searching count here, so it must not be read as chapters.
    window: { [daysAgo(0)]: 40, [daysAgo(1)]: 40 },
    // Listed out of order and with a day outside the week, which must not count.
    chapters_window: { [daysAgo(0)]: 1, [daysAgo(9)]: 20, [daysAgo(2)]: 3, [daysAgo(6)]: 2 },
    last_plan_id: null,
    ...overrides,
  };
}

function enrollment(overrides: Partial<BiblePlanEnrollment>): BiblePlanEnrollment {
  return {
    plan_id: 'gospels-made-up', name: 'A Made-up Gospel Plan', started_on: '2026-10-01', finished_on: null, days: 40,
    completed: 12, completed_days: [], percent: 30, calendar_day: 13, current_day: 13, next_day: 13, behind: 0,
    finished: false, today: null, ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(api.getBibleStats).mockReset().mockResolvedValue(stats());
  vi.mocked(api.getBiblePlans).mockReset().mockResolvedValue({ catalogue: [], mine: [enrollment({})] });
});

function renderProgress(marks: BibleMark[] = [], onOpenPlans?: () => void) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BibleProgress marks={marks} onDeleteMark={vi.fn()} onOpenRef={vi.fn()} onOpenPlans={onOpenPlans} />
    </QueryClientProvider>,
  );
}

describe('BibleProgress', () => {
  it('shows streak, days read, and chapters stats', async () => {
    renderProgress([]);
    const card = (label: string) => screen.getByText(label).closest('.glass-panel');
    await waitFor(() => expect(card('Read streak')).toHaveTextContent('5'));
    expect(card('Best streak')).toHaveTextContent('12');
    expect(card('Days read')).toHaveTextContent('45');
    expect(card('Chapters')).toHaveTextContent('25');
  });

  it('never prints markup as text in any card', async () => {
    renderProgress([]);
    const card = await screen.findByTestId('bible-progress-week');
    await waitFor(() => expect(card).toHaveTextContent('6 chapters'));
    const progress = screen.getByTestId('bible-progress');
    expect(progress.textContent).not.toMatch(/<\/?span|className=/);
    // The unit is a real element, styled apart from the number.
    expect(within(card).getByText(/chapters/).tagName).toBe('SPAN');
  });

  it('counts this week from the last seven local days of chapters, not every event', async () => {
    renderProgress([]);
    const card = await screen.findByTestId('bible-progress-week');
    await waitFor(() => expect(card).toHaveTextContent('6 chapters'));
  });

  it('shows days done of the plan under way, with its name', async () => {
    renderProgress([]);
    const card = await screen.findByTestId('bible-progress-plan');
    await waitFor(() => expect(card).toHaveTextContent('12 of 40 days'));
    expect(card).toHaveTextContent('A Made-up Gospel Plan');
    expect(card).not.toHaveTextContent('%');
  });

  it('shows the plan the reader last opened when several are under way', async () => {
    vi.mocked(api.getBibleStats).mockResolvedValue(stats({ last_plan_id: 'psalms-made-up' }));
    vi.mocked(api.getBiblePlans).mockResolvedValue({
      catalogue: [],
      mine: [
        enrollment({ started_on: '2026-10-05' }),
        enrollment({ plan_id: 'psalms-made-up', name: 'Made-up Psalms', days: 150, completed: 7, started_on: '2026-09-01' }),
      ],
    });
    renderProgress([]);
    const card = await screen.findByTestId('bible-progress-plan');
    await waitFor(() => expect(card).toHaveTextContent('7 of 150 days'));
    expect(card).toHaveTextContent('Made-up Psalms');
  });

  it('says No plan, with a way to start one, when no plan is under way', async () => {
    vi.mocked(api.getBiblePlans).mockResolvedValue({ catalogue: [], mine: [] });
    const onOpenPlans = vi.fn();
    renderProgress([], onOpenPlans);
    const card = await screen.findByTestId('bible-progress-plan');
    await waitFor(() => expect(card).toHaveTextContent('No plan'));
    fireEvent.click(within(card).getByTestId('bible-progress-start-plan'));
    expect(onOpenPlans).toHaveBeenCalledTimes(1);
  });

  it('shows memorized verses and books finished, and no second chapter total', async () => {
    renderProgress([]);
    const memorized = await screen.findByTestId('bible-progress-memorized');
    await waitFor(() => expect(memorized).toHaveTextContent('2 verses'));
    expect(screen.getByTestId('bible-progress-books')).toHaveTextContent('3');
    expect(screen.queryByText('Chapters total')).not.toBeInTheDocument();
  });

  it('shows earned badges and progress toward next', async () => {
    renderProgress([]);
    await waitFor(() => expect(screen.getByText('In the Word')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('Steady Reader')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('A Full Week')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('One Hundred Chapters')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('25/100')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('Two Weeks Faithful')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('5/14')).toBeInTheDocument());
  });

  it('shows family encouragement note', async () => {
    renderProgress([]);
    await waitFor(() => expect(screen.getByText(/Encourage each other/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/Dad read today/)).toBeInTheDocument());
  });

  it('shows family activity feed', async () => {
    renderProgress([]);
    await waitFor(() => expect(screen.getByText('parent')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/8 day streak/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('child')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/3 day streak/)).toBeInTheDocument());
  });

  it('shows empty state when no family activity shared', async () => {
    vi.mocked(api.getBibleActivityFeed).mockResolvedValue({
      entries: [],
      members: [],
      note: 'Nobody has shared their reading yet. Share it in Settings → Share my activity.',
    });
    renderProgress([]);
    await waitFor(() => expect(screen.getByText(/Nobody has shared their reading yet/)).toBeInTheDocument());
  });
});