import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import MetricDetailCards from '../components/health/MetricDetailCards';
import { renderWithProviders } from './render';
import type { MetricRangeBucket, MetricRangeResponse } from '../types/api';

const mocks = vi.hoisted(() => ({
  getMetricRanges: vi.fn(),
  getMetricCatalog: vi.fn(),
}));

vi.mock('../services/api', () => ({
  api: {
    getMetricRanges: mocks.getMetricRanges,
    getMetricCatalog: mocks.getMetricCatalog,
  },
}));

const bucket = (i: number, over: Partial<MetricRangeBucket> = {}): MetricRangeBucket => ({
  label: `D${i}`,
  start: `2025-09-${String(20 + i).padStart(2, '0')}`,
  end: `2025-09-${String(20 + i).padStart(2, '0')}`,
  value: 0,
  active_days: 0,
  quiet: true,
  ...over,
});

const series = (over: Partial<MetricRangeResponse> = {}): MetricRangeResponse => ({
  user_id: 'jeremiah',
  metric: 'workouts',
  label: 'Workouts',
  unit: 'count',
  format: 'count',
  range: 'W',
  range_label: 'This week',
  buckets: [bucket(0), bucket(1)],
  total: 0,
  per_active_day: 0,
  active_days: 0,
  empty: true,
  best: null,
  ...over,
});

const CATALOG = {
  available: ['workouts', 'workout_minutes', 'workout_miles', 'drive_miles'],
  unavailable: {
    calories: 'Never recorded: the calories field is written as None and nothing fills it in.',
  },
};

describe('MetricDetailCards', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getMetricCatalog.mockResolvedValue(CATALOG);
    mocks.getMetricRanges.mockResolvedValue(series());
  });

  it('renders one card per recorded metric', async () => {
    renderWithProviders(<MetricDetailCards range="W" />);
    expect(await screen.findByTestId('metric-card-workouts')).toBeInTheDocument();
    expect(screen.getByTestId('metric-card-workout_minutes')).toBeInTheDocument();
    expect(screen.getByTestId('metric-card-workout_miles')).toBeInTheDocument();
    expect(screen.getByTestId('metric-card-drive_miles')).toBeInTheDocument();
  });

  it('does not render a card for a metric the server does not record', async () => {
    renderWithProviders(<MetricDetailCards range="W" />);
    await screen.findByTestId('metric-card-workouts');
    expect(screen.queryByTestId('metric-card-calories')).not.toBeInTheDocument();
  });

  // A missing Calories card reads as a broken feature. Saying why is the point.
  it('explains an untracked metric instead of hiding it', async () => {
    renderWithProviders(<MetricDetailCards range="W" />);
    const panel = await screen.findByTestId('metric-unavailable');
    const items = within(panel).getAllByRole('listitem');
    expect(items.some((li) => /calories/i.test(li.textContent ?? ''))).toBe(true);
    expect(items.some((li) => /nothing fills it in/i.test(li.textContent ?? ''))).toBe(true);
  });

  it('states an empty window rather than charting zeros', async () => {
    renderWithProviders(<MetricDetailCards range="W" />);
    expect(await screen.findByTestId('metric-empty-workouts')).toHaveTextContent('None yet');
  });

  it('shows the total and the per-active-day average when there is activity', async () => {
    mocks.getMetricRanges.mockImplementation(async (metric: string) =>
      metric === 'workouts'
        ? series({
            total: 4,
            per_active_day: 2,
            active_days: 2,
            empty: false,
            best: { label: 'D3', value: 3 },
            buckets: [
              bucket(0, { value: 3, active_days: 1, quiet: false }),
              bucket(1, { value: 1, active_days: 1, quiet: false }),
            ],
          })
        : series(),
    );
    renderWithProviders(<MetricDetailCards range="W" />);
    expect(await screen.findByTestId('metric-total-workouts')).toHaveTextContent('4');
    expect(screen.getByText('2 per active day')).toBeInTheDocument();
    expect(screen.getByText(/Best: 3 on D3/)).toBeInTheDocument();
  });

  it('formats a distance metric in miles', async () => {
    mocks.getMetricRanges.mockResolvedValue(
      series({
        metric: 'drive_miles',
        label: 'Driving distance',
        format: 'distance',
        total: 39.28,
        per_active_day: 9.82,
        active_days: 4,
        empty: false,
      }),
    );
    renderWithProviders(<MetricDetailCards range="W" />);
    expect(await screen.findByTestId('metric-total-drive_miles')).toHaveTextContent('39.3 mi');
  });

  it('requests the range it is given', async () => {
    renderWithProviders(<MetricDetailCards range="3M" />);
    await screen.findByTestId('metric-card-workouts');
    expect(mocks.getMetricRanges).toHaveBeenCalledWith('workouts', undefined, '3M');
  });

  it('asks for a specific user when one is given', async () => {
    renderWithProviders(<MetricDetailCards range="W" userId="michele" />);
    await screen.findByTestId('metric-card-workouts');
    expect(mocks.getMetricRanges).toHaveBeenCalledWith('workouts', 'michele', 'W');
  });

  it('leaves the target to the server when the user is "all"', async () => {
    renderWithProviders(<MetricDetailCards range="W" userId="all" />);
    await screen.findByTestId('metric-card-workouts');
    expect(mocks.getMetricRanges).toHaveBeenCalledWith('workouts', undefined, 'W');
  });

  it('skips the unavailable panel when nothing is untracked', async () => {
    mocks.getMetricCatalog.mockResolvedValue({ available: ['workouts'], unavailable: {} });
    renderWithProviders(<MetricDetailCards range="W" />);
    await screen.findByTestId('metric-card-workouts');
    expect(screen.queryByTestId('metric-unavailable')).not.toBeInTheDocument();
  });
});
