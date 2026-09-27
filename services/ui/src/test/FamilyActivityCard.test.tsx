import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import FamilyActivityCard from '../components/health/FamilyActivityCard';

const summaryByWindow: Record<string, unknown> = {
  week: {
    status: 'SUCCESS',
    user_id: 'jeremiah',
    window: 'week',
    days: 7,
    steps_total: 32000,
    steps_average: 4571,
    workout_count: 3,
    workout_distance_miles: 8.4,
    points: 12,
    achievements_earned: 4,
  },
  month: {
    status: 'SUCCESS',
    user_id: 'jeremiah',
    window: 'month',
    days: 30,
    steps_total: 141000,
    steps_average: 4700,
    workout_count: 11,
    workout_distance_miles: 31.2,
    points: 26,
    achievements_earned: 7,
  },
};

let feedWindows: string[] = [];
let summaryWindows: string[] = [];

function useHandlers(feedUsers: unknown[] = []) {
  server.use(
    http.get('/api/geo/activity/summary', ({ request }) => {
      const window = new URL(request.url).searchParams.get('window') ?? 'week';
      summaryWindows.push(window);
      return HttpResponse.json(summaryByWindow[window] ?? summaryByWindow.week);
    }),
    http.get('/api/geo/activity/feed', ({ request }) => {
      const window = new URL(request.url).searchParams.get('window') ?? 'week';
      feedWindows.push(window);
      return HttpResponse.json({ status: 'SUCCESS', viewer: 'jeremiah', window, users: feedUsers });
    })
  );
}

beforeEach(() => {
  feedWindows = [];
  summaryWindows = [];
});

describe('FamilyActivityCard', () => {
  it('shows your totals plus each person\u2019s shared scopes only', async () => {
    useHandlers([
      {
        username: 'sam',
        window: 'week',
        steps_total: 41000,
        workout_count: 2,
        workout_distance_miles: 5.2,
      },
    ]);
    renderWithProviders(<FamilyActivityCard />);

    await waitFor(() => expect(screen.getByTestId('feed-user-jeremiah')).toBeInTheDocument());
    expect(screen.getByTestId('feed-user-jeremiah').textContent).toContain('32,000 steps');
    expect(screen.getByTestId('feed-user-jeremiah').textContent).toContain('12 pts');
    expect(screen.getByTestId('feed-user-jeremiah').textContent).toContain('4 badges');

    const sam = screen.getByTestId('feed-user-sam');
    expect(sam.textContent).toContain('41,000 steps');
    expect(sam.textContent).toContain('2 workouts');
    // Sam shared totals + workouts only — no points/badges leak into the row
    expect(sam.textContent).not.toContain('pts');
    expect(sam.textContent).not.toContain('badges');
    // You always lead; everyone else ranks by shared steps
    const rows = screen.getAllByTestId(/^feed-user-/);
    expect(rows[0]).toHaveAttribute('data-testid', 'feed-user-jeremiah');
    expect(rows[1]).toHaveAttribute('data-testid', 'feed-user-sam');
  });

  it('refetches both endpoints when the window changes', async () => {
    useHandlers([]);
    const user = userEvent.setup();
    renderWithProviders(<FamilyActivityCard />);

    await waitFor(() => expect(summaryWindows).toContain('week'));
    await user.click(screen.getByTestId('feed-window-month'));

    await waitFor(() => expect(feedWindows).toContain('month'));
    expect(summaryWindows).toContain('month');
    expect(screen.getByTestId('feed-user-jeremiah').textContent).toContain('141,000 steps');
  });

  it('explains opt-in sharing when nobody else shares', async () => {
    useHandlers([]);
    renderWithProviders(<FamilyActivityCard />);

    await waitFor(() => expect(screen.getByTestId('feed-empty')).toBeInTheDocument());
    expect(screen.getByTestId('feed-empty').textContent).toContain('Settings');
  });
});
