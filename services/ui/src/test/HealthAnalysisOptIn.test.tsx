import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import Health from '../pages/HealthFitness';

const trendsRequests: string[] = [];

function useGeoHandlers() {
  server.use(
    http.get('/api/geo/trips', () =>
      HttpResponse.json({ trips: [], total_trips: 0 })
    ),
    http.get('/api/geo/vehicles', () => HttpResponse.json({ vehicles: [] })),
    http.get('/api/geo/people', () => HttpResponse.json({ features: [] })),
    http.get('/api/geo/steps', () =>
      HttpResponse.json({
        user_id: 'default',
        daily_steps: {},
        today: 0,
        goal: 10000,
      })
    ),
    http.get('/api/geo/workouts', () =>
      HttpResponse.json({ workouts: [], total: 0 })
    ),
    http.post('/api/geo/trends/activity/analyze', () => {
      trendsRequests.push('trends');
      return HttpResponse.json({
        user_id: 'default',
        days: 7,
        steps_today: 6400,
        steps_goal: 10000,
        daily_steps: {},
        steps_avg: 7000,
        steps_best: { date: '2026-10-17', steps: 12400 },
        workout_count: 2,
        workouts_by_type: { walk: 2 },
        workout_distance_miles: 3.2,
        trip_count: 1,
        trip_distance_miles: 12.5,
        drive_fuel_gallons: 1.1,
        drive_cost_usd: 4.25,
        analysis: 'Your pace is steady: about 7,000 steps per day this week.',
        analysis_available: true,
        generated_at: 1700000000,
      });
    })
  );
}

describe('Health health/fitness analysis is opt-in', () => {
  beforeEach(() => {
    trendsRequests.length = 0;
    useGeoHandlers();
  });

  afterEach(() => {
    server.resetHandlers();
  });

  it('does not request or render analysis on page load', async () => {
    renderWithProviders(<Health />);

    expect(await screen.findByTestId('analysis-opt-in')).toBeInTheDocument();
    expect(
      screen.getByText(/analysis only runs when you ask/i)
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(trendsRequests).toHaveLength(0);
    });
    expect(screen.queryByText(/AI Insight/i)).not.toBeInTheDocument();
  });

  it('renders the Health page shell with steps and workouts', async () => {
    renderWithProviders(<Health />);

    expect(await screen.findByTestId('health-fitness')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /fitness/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /workouts/i })).toBeInTheDocument();
  });

  it('runs analysis only after the user explicitly requests it', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Health />);

    await screen.findByTestId('analysis-opt-in');
    expect(trendsRequests).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: /analyze my activity/i }));

    await waitFor(() => {
      expect(trendsRequests).toHaveLength(1);
    });
    expect(await screen.findByText(/AI Insight/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Your pace is steady/i)
    ).toBeInTheDocument();
    expect(screen.getByText('7,000')).toBeInTheDocument();
    expect(screen.getByText('Best Day')).toBeInTheDocument();
    expect(screen.getByText('12,400')).toBeInTheDocument();
    expect(screen.queryByText('Drive Cost')).not.toBeInTheDocument();
  });

  it('supports re-running analysis from the panel action', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Health />);

    await screen.findByTestId('analysis-opt-in');
    await user.click(screen.getByRole('button', { name: /analyze my activity/i }));
    await waitFor(() => expect(trendsRequests).toHaveLength(1));

    const reanalyze = await screen.findByRole('button', { name: /re-analyze/i });
    await user.click(reanalyze);

    await waitFor(() => {
      expect(trendsRequests.length).toBeGreaterThanOrEqual(2);
    });
  });
});

describe('auto-detected workouts', () => {
  it('lets the person dismiss a workout they did not do', async () => {
    const user = userEvent.setup();
    useGeoHandlers();
    let dismissed = false;
    server.use(
      http.get('/api/geo/workouts', () => HttpResponse.json({
        workouts: [{
          id: 'auto_workout_jeremiah_1',
          user_id: 'jeremiah',
          activity_type: 'walking',
          start_time: 1_760_000_000,
          end_time: 1_760_000_900,
          duration_seconds: 900,
          distance_miles: 0.8,
          steps: 1600,
          steps_source: 'gps_estimate',
          status: 'completed',
          auto_detected: true,
        }],
        total: 1,
      })),
      http.post('/api/geo/workouts/auto_workout_jeremiah_1/dismiss', () => {
        dismissed = true;
        return HttpResponse.json({ status: 'dismissed', id: 'auto_workout_jeremiah_1' });
      }),
    );

    renderWithProviders(<Health />);

    expect(await screen.findByText('Auto-detected')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /not a workout/i }));
    await waitFor(() => expect(dismissed).toBe(true));
  });
});
