import { describe, it, expect } from 'vitest';
import {
  formatMetricValue,
  metricAverageLabel,
  metricBarPercent,
  metricMax,
  metricBucketAriaLabel,
  metricSummary,
  hasMetricActivity,
} from '../lib/healthMetricsRanges';
import type { MetricRangeBucket, MetricRangeResponse } from '../types/api';

const bucket = (over: Partial<MetricRangeBucket> = {}): MetricRangeBucket => ({
  label: 'Mon',
  start: '2025-09-29',
  end: '2025-09-29',
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
  buckets: [bucket()],
  total: 0,
  per_active_day: 0,
  active_days: 0,
  empty: true,
  best: null,
  ...over,
});

describe('formatMetricValue', () => {
  it('formats counts as whole numbers', () => {
    expect(formatMetricValue(4, 'count')).toBe('4');
    expect(formatMetricValue(3.6, 'count')).toBe('4');
  });

  it('formats sub-hour durations in minutes', () => {
    expect(formatMetricValue(45, 'duration')).toBe('45 min');
  });

  it('formats durations over an hour', () => {
    expect(formatMetricValue(90, 'duration')).toBe('1h 30m');
    expect(formatMetricValue(120, 'duration')).toBe('2h');
  });

  it('formats distances to one decimal', () => {
    expect(formatMetricValue(39.28, 'distance')).toBe('39.3 mi');
  });

  it('refuses to render a non-finite value', () => {
    expect(formatMetricValue(NaN, 'count')).toBe('—');
    expect(formatMetricValue(Infinity, 'distance')).toBe('—');
  });
});

describe('metricBarPercent', () => {
  it('scales against the tallest bar', () => {
    expect(metricBarPercent(5, 10)).toBe(50);
    expect(metricBarPercent(10, 10)).toBe(100);
  });

  it('floors a small value so it stays visible', () => {
    // 1% would render as a hairline, reading as "nothing happened".
    expect(metricBarPercent(1, 1000)).toBe(4);
  });

  it('returns zero for a quiet bucket so quiet and tiny stay distinct', () => {
    expect(metricBarPercent(0, 10)).toBe(0);
  });

  it('returns zero rather than dividing by zero when nothing happened', () => {
    expect(metricBarPercent(0, 0)).toBe(0);
  });
});

describe('metricMax', () => {
  it('finds the tallest bucket', () => {
    expect(metricMax([bucket({ value: 2 }), bucket({ value: 9 }), bucket()])).toBe(9);
  });

  it('is zero for an all-quiet window', () => {
    expect(metricMax([bucket(), bucket()])).toBe(0);
  });
});

describe('metricBucketAriaLabel', () => {
  // The distinction from steps: a missing day there is a gap, here it is a real
  // zero. So this must never announce a measured-looking zero for a quiet day.
  it('says no activity for a quiet bucket, not zero', () => {
    expect(metricBucketAriaLabel(bucket(), 'count')).toBe('Mon: no activity');
  });

  it('states the value for an active bucket', () => {
    const b = bucket({ value: 3, active_days: 1, quiet: false });
    expect(metricBucketAriaLabel(b, 'count')).toBe('Mon: 3');
  });
});

describe('metricAverageLabel', () => {
  it('averages over active days, and says so', () => {
    const s = series({ per_active_day: 1.5, active_days: 2, empty: false });
    expect(metricAverageLabel(s)).toBe('2 per active day');
  });

  it('never averages across the calendar', () => {
    // 3 workouts over 2 active days, in a 30-day window. The calendar average
    // would be 0.1, which reads as inactivity rather than as three real workouts.
    const s = series({ total: 3, per_active_day: 1.5, active_days: 2, empty: false, range: 'M' });
    expect(metricAverageLabel(s)).toBe('2 per active day');
  });

  it('says so plainly when there is no activity', () => {
    expect(metricAverageLabel(series())).toBe('No activity yet');
  });
});

describe('metricSummary', () => {
  it('returns null for an empty window rather than a zero total', () => {
    // "0 workouts" would read as a measurement that came back empty.
    expect(metricSummary(series())).toBeNull();
  });

  it('summarises total and active days', () => {
    const s = series({ total: 3, active_days: 2, empty: false });
    expect(metricSummary(s)).toBe('3 across 2 active days');
  });

  it('uses the singular for one active day', () => {
    const s = series({ total: 1, active_days: 1, empty: false });
    expect(metricSummary(s)).toBe('1 across 1 active day');
  });
});

describe('hasMetricActivity', () => {
  it('is false for undefined and for an empty series', () => {
    expect(hasMetricActivity(undefined)).toBe(false);
    expect(hasMetricActivity(series())).toBe(false);
  });

  it('is true once something happened', () => {
    expect(hasMetricActivity(series({ empty: false, active_days: 1 }))).toBe(true);
  });
});
