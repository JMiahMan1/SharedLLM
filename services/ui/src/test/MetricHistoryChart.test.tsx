import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import MetricHistoryChart from '../components/health/MetricHistoryChart';
import type { MetricRangeBucket } from '../types/api';

const bucket = (i: number, over: Partial<MetricRangeBucket> = {}): MetricRangeBucket => ({
  label: `D${i}`,
  start: `2025-09-${String(20 + i).padStart(2, '0')}`,
  end: `2025-09-${String(20 + i).padStart(2, '0')}`,
  value: 0,
  active_days: 0,
  quiet: true,
  ...over,
});

const buckets = [
  bucket(0, { value: 0 }),
  bucket(1, { value: 3, active_days: 1, quiet: false }),
  bucket(2, { value: 0 }),
  bucket(3, { value: 7, active_days: 1, quiet: false }),
];

describe('MetricHistoryChart', () => {
  it('renders nothing for a single bucket', () => {
    // One bar stretched across the full width reads as a block, not a trend.
    const { container } = render(
      <MetricHistoryChart buckets={[buckets[1]]} format="count" metricLabel="Workouts" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders a bar per bucket', () => {
    render(<MetricHistoryChart buckets={buckets} format="count" metricLabel="Workouts" />);
    buckets.forEach((b) => expect(screen.getByTestId(`metric-bar-${b.start}`)).toBeInTheDocument());
  });

  // The core distinction from the steps chart: a quiet day here is a real zero,
  // drawn as a visible baseline, not hatched as missing data.
  it('draws a quiet day as a real zero rather than hiding it', () => {
    render(<MetricHistoryChart buckets={buckets} format="count" metricLabel="Workouts" />);
    const quiet = screen.getByTestId('metric-bar-2025-09-20');
    expect(quiet).toHaveAttribute('data-quiet', 'true');
  });

  it('scales an active bar against the window maximum', () => {
    render(<MetricHistoryChart buckets={buckets} format="count" metricLabel="Workouts" />);
    // The 7 bucket is the tallest, so it fills; the 3 is roughly half.
    const tall = screen.getByTestId('metric-bar-2025-09-23').firstElementChild as HTMLElement;
    const half = screen.getByTestId('metric-bar-2025-09-21').firstElementChild as HTMLElement;
    expect(tall.style.height).toBe('100%');
    expect(half.style.height).toBe('43%');
  });

  it('counts active days in the caption', () => {
    render(<MetricHistoryChart buckets={buckets} format="count" metricLabel="Workouts" />);
    expect(screen.getByTestId('metric-chart-days')).toHaveTextContent('2 active days');
  });

  it('uses the singular for a single active day', () => {
    const one = [bucket(0, { value: 2, active_days: 1, quiet: false }), bucket(1)];
    render(<MetricHistoryChart buckets={one} format="count" metricLabel="Workouts" />);
    expect(screen.getByTestId('metric-chart-days')).toHaveTextContent('1 active day');
  });

  it('never announces a quiet day as a measured zero', () => {
    render(<MetricHistoryChart buckets={buckets} format="count" metricLabel="Workouts" />);
    const list = screen.getByRole('list');
    const items = within(list).getAllByRole('listitem').map((li) => li.textContent);
    expect(items).toContain('D0: no activity');
    expect(items).toContain('D3: 7');
    expect(items.some((t) => /: 0$/.test(t ?? ''))).toBe(false);
  });

  it('labels the chart group for screen readers', () => {
    render(<MetricHistoryChart buckets={buckets} format="count" metricLabel="Workouts" />);
    expect(screen.getByRole('group')).toHaveAttribute('aria-label', 'Workouts across 4 buckets');
  });
});
