import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AchievementsPanel from '../components/wander/AchievementsPanel';
import { renderWithProviders } from './render';
import { server } from './setup';
import { http, HttpResponse } from 'msw';

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

describe('AchievementsPanel', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows points, earned badges and next-up progress', async () => {
    renderWithProviders(<AchievementsPanel />);

    expect(await screen.findByTestId('achievement-points')).toHaveTextContent('2 unlocked');
    expect(screen.getByTestId('achievement-points')).toHaveTextContent('4 pts');
    expect(await screen.findByText('First Steps')).toBeInTheDocument();
    expect(screen.getByText('Goal Day')).toBeInTheDocument();

    const nextUp = await screen.findByTestId('next-up');
    expect(nextUp).toHaveTextContent('Week of Wins');
    expect(nextUp).toHaveTextContent('3 / 7');
    expect(nextUp).toHaveTextContent('4 to go');
  });

  it('says so when nothing is unlocked yet, instead of looking broken', async () => {
    server.use(
      http.get('/api/geo/achievements', () =>
        HttpResponse.json({
          user_id: 'default',
          earned: [],
          next_up: [],
          points: 0,
          goals: { daily_steps: 10000, weekly_steps: 70000, workouts_per_week: 4, weekly_distance_miles: 15 },
        })
      )
    );
    renderWithProviders(<AchievementsPanel />);

    expect(await screen.findByText(/no badges yet/i)).toBeInTheDocument();
  });

  it('edits weekly goals with validation', async () => {
    const user = userEvent.setup();
    renderWithProviders(<AchievementsPanel />);

    await user.click(await screen.findByRole('button', { name: /goals/i }));
    const weekly = await screen.findByLabelText('Weekly step goal');
    await user.clear(weekly);
    await user.type(weekly, '90000');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.queryByTestId('goal-editor')).not.toBeInTheDocument();
    });
  });

  it('refuses an out-of-range weekly goal', async () => {
    const user = userEvent.setup();
    renderWithProviders(<AchievementsPanel />);

    await user.click(await screen.findByRole('button', { name: /goals/i }));
    const weekly = await screen.findByLabelText('Weekly step goal');
    await user.clear(weekly);
    await user.type(weekly, '10');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    // Editor stays open: the value was rejected, not silently saved
    expect(await screen.findByTestId('goal-editor')).toBeInTheDocument();
  });
});
