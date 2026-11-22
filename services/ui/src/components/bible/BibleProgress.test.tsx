import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleProgress from './BibleProgress';
import type { BibleMark } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'sam' } }) }));

vi.mock('../../services/api', () => ({
  api: {
    getBibleStats: vi.fn().mockResolvedValue({
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
      position: { book: 'John', chapter: 3, verse: 16, reference: 'John 3', label: 'John 3' },
      last_read_at: '2026-10-13T10:00:00Z',
      window: { '2026-10-07': 2, '2026-10-08': 1, '2026-10-09': 3, '2026-10-10': 0, '2026-10-11': 2, '2026-10-12': 1, '2026-10-13': 1 },
    }),
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

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'sam' } }) }));

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleProgress from './BibleProgress';
import type { BibleMark } from '../../types/api';

function renderProgress(marks: BibleMark[] = []) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <BibleProgress marks={marks} onDeleteMark={vi.fn()} onOpenRef={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe('BibleProgress', () => {
  it('shows streak, days read, and chapters stats', async () => {
    renderProgress([]);
    expect(await screen.findByText('Read streak')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('Best streak')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('Days read')).toBeInTheDocument();
    expect(screen.getByText('45')).toBeInTheDocument();
    expect(screen.getByText('Chapters')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
  });

  it('shows this week chapters and plan progress', async () => {
    renderProgress([]);
    expect(screen.getByText(/10/)).toBeInTheDocument();
    expect(screen.getByText('Plan progress')).toBeInTheDocument();
    expect(screen.getByText(/30/)).toBeInTheDocument();
    expect(screen.getByText('Books finished')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('shows earned badges and progress toward next', async () => {
    renderProgress([]);
    expect(await screen.findByText('In the Word')).toBeInTheDocument();
    expect(screen.getByText('Steady Reader')).toBeInTheDocument();
    expect(screen.getByText('A Full Week')).toBeInTheDocument();
    expect(await screen.findByText('One Hundred Chapters')).toBeInTheDocument();
    expect(screen.getByText('25/100')).toBeInTheDocument();
    expect(screen.getByText('Two Weeks Faithful')).toBeInTheDocument();
    expect(screen.getByText('5/14')).toBeInTheDocument();
  });

  it('shows family encouragement note', async () => {
    renderProgress([]);
    expect(await screen.findByText(/Encourage each other/)).toBeInTheDocument();
    expect(screen.getByText(/Dad read today/)).toBeInTheDocument();
  });

  it('shows family activity feed', async () => {
    renderProgress([]);
    expect(await screen.findByText('parent')).toBeInTheDocument();
    expect(screen.getByText(/8 day streak/)).toBeInTheDocument();
    expect(screen.getByText('child')).toBeInTheDocument();
    expect(screen.getByText(/3 day streak/)).toBeInTheDocument();
  });

  it('shows empty state when no family activity shared', async () => {
    api.getBibleActivityFeed.mockResolvedValue({
      entries: [],
      members: [],
      note: 'Nobody has shared their reading yet. Share it in Settings → Share my activity.',
    });
    renderProgress([]);
    expect(await screen.findByText(/Nobody has shared their reading yet/)).toBeInTheDocument();
  });
});