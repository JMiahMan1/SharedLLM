import { describe, it, expect, beforeEach, afterEach, afterAll } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import Health from '../pages/Health';

/**
 * The day view charts the hours inside today.
 *
 * The whole point of this feature is that it must not lie during the ramp-up:
 * a phone on the old build has no hour data at all, and the difference between
 * "your phone has not reported hours" and "you did not move for 24 hours" is
 * the difference between an honest message and a false accusation. So the
 * absence case is pinned here as carefully as the data case.
 */

const HOURS = [
  { hour: 7, label: '07:00', steps: 120 },
  { hour: 8, label: '08:00', steps: 640 },
  { hour: 13, label: '13:00', steps: 310 },
];

function rangesPayload(range: string) {
  const today = new Date().toISOString().slice(0, 10);
  const base = {
    user_id: 'default',
    range,
    label: range === 'D' ? 'Today' : 'Last 7 days',
    total: 1070,
    daily_average: 1070,
    days_recorded: range === 'D' ? 1 : 7,
    goal: 10000,
    baseline: null,
    baseline_min_days: 7,
    thin: true,
    has_gaps: false,
    best: { label: 'Today', steps: 1070 },
  };
  if (range === 'D') {
    return {
      ...base,
      buckets: [{ label: 'Today', start: today, end: today, steps: 1070, days_missing: 0, days_recorded: 1, complete: false }],
      hourly: HOURS,
      peak: HOURS[1],
    };
  }
  return {
    ...base,
    buckets: Array.from({ length: 7 }, (_, i) => ({
      label: `Day ${i + 1}`,
      start: today,
      end: today,
      steps: 1000 + i,
      days_missing: 0,
      days_recorded: 1,
      complete: true,
    })),
  };
}

/** Recorded hours and the hours that are still in the future, both anchored to
 *  a real clock so "later today" cannot be tested against a frozen guess. */
const NOW = new Date().getHours();
const IS_LATE = NOW >= 21;

function useGeoHandlers(opts: { hourly: boolean }) {
  server.use(
    http.get('/api/geo/steps', () =>
      HttpResponse.json({
        user_id: 'default',
        daily_steps: { [new Date().toISOString().slice(0, 10)]: 1070 },
        today: 1070,
        goal: 10000,
        sources: {},
        last_synced: Date.now() / 1000,
      })
    ),
    http.get('/api/geo/workouts', () => HttpResponse.json({ workouts: [], total: 0 })),
    http.get('/api/geo/trips', () => HttpResponse.json({ trips: [], total_trips: 0 })),
    http.get('/api/geo/vehicles', () => HttpResponse.json({ vehicles: [] })),
    http.get('/api/geo/people', () => HttpResponse.json({ features: [] })),
    http.get('/api/geo/steps/ranges', ({ request }) => {
      const range = new URL(request.url).searchParams.get('range') || 'W';
      const payload = rangesPayload(range);
      if (range === 'D' && !opts.hourly) {
        const { hourly: _h, peak: _p, ...rest } = payload as Record<string, unknown>;
        return HttpResponse.json(rest);
      }
      return HttpResponse.json(payload);
    })
  );
}

async function openDayView(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByTestId('steps-card');
  await user.click(screen.getByTestId('range-D'));
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: /by the hour/i })).toBeInTheDocument()
  );
}

describe('the day view charts activity by the hour', () => {
  afterAll(() => server.resetHandlers());
  afterEach(() => server.resetHandlers());

  it('draws the hours the phone reported and names the busiest', async () => {
    useGeoHandlers({ hourly: true });
    const user = userEvent.setup();
    renderWithProviders(<Health />);
    await openDayView(user);

    const chart = await screen.findByTestId('step-hour-chart');
    expect(within(chart).getByTestId('hour-bar-8')).toBeInTheDocument();
    expect(within(chart).getByTestId('hour-bar-13')).toBeInTheDocument();
    expect(screen.getByTestId('hour-chart-busiest')).toHaveTextContent('Busiest 8am · 640 steps');
  });

  it('pairs the total with the busiest hour instead of a one-day average', async () => {
    useGeoHandlers({ hourly: true });
    const user = userEvent.setup();
    renderWithProviders(<Health />);
    await openDayView(user);

    expect(screen.getByTestId('history-total')).toHaveTextContent('1,070');
    expect(screen.getByTestId('history-peak-hour')).toHaveTextContent('8am');
    // "Daily avg" and "Best day" of a single day are today's total twice over.
    expect(screen.queryByTestId('history-average')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Best$/)).not.toBeInTheDocument();
  });

  it('says the phone has not reported hours rather than drawing a flat day', async () => {
    useGeoHandlers({ hourly: false });
    const user = userEvent.setup();
    renderWithProviders(<Health />);
    await openDayView(user);

    expect(await screen.findByTestId('hour-chart-missing')).toBeInTheDocument();
    expect(screen.getByTestId('hour-chart-missing')).toHaveTextContent(/no hourly detail yet/i);
    // No chart at all: an empty 24-bar chart reads as "you did not move".
    expect(screen.queryByTestId('step-hour-chart')).not.toBeInTheDocument();
    // And with no hours there is no peak hour to claim either.
    expect(screen.getByTestId('history-peak-hour')).toHaveTextContent('—');
  });

  it('draws the rest of today as still to come', async () => {
    useGeoHandlers({ hourly: true });
    const user = userEvent.setup();
    renderWithProviders(<Health />);
    await openDayView(user);

    const chart = await screen.findByTestId('step-hour-chart');
    // Late in the evening there may be no future hours left to assert on, so
    // this only checks the claim when the day actually has some left.
    if (!IS_LATE) {
      expect(within(chart).getByTestId(`hour-bar-${NOW + 1}`)).toHaveAttribute('data-state', 'ahead');
    }
    expect(within(chart).getByTestId(`hour-bar-${NOW}`)).toHaveAttribute('data-current', 'true');
  });

  it('does not put hours on the week view, where they would be 168 columns', async () => {
    useGeoHandlers({ hourly: true });
    const user = userEvent.setup();
    renderWithProviders(<Health />);

    await screen.findByTestId('step-range-selector');
    await waitFor(() => expect(screen.getByTestId('history-average')).toBeInTheDocument());

    expect(screen.queryByRole('heading', { name: /by the hour/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId('step-hour-chart')).not.toBeInTheDocument();
  });
});