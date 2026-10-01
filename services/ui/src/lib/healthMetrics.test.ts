import { describe, expect, it } from 'vitest';
import {
  DEFAULT_STEP_GOAL,
  dailyStepSeries,
  elapsedLabel,
  formatCount,
  formatMiles,
  paceLabel,
  stepDayLabel,
  stepSourcesLabel,
  stepsSummary,
  workoutTotals,
} from './healthMetrics';
import type { Workout } from '../types/api';

const workout = (over: Partial<Workout>): Workout => ({
  id: 'w1',
  user_id: 'jeremiah',
  activity_type: 'walking',
  start_time: 0,
  status: 'completed',
  ...over,
});

describe('stepsSummary', () => {
  it('uses the server goal when present', () => {
    const s = stepsSummary({ daily_steps: { '2026-10-01': 5000 }, today: 5000, goal: 10000 });
    expect(s.goal).toBe(10000);
    expect(s.percent).toBe(50);
    expect(s.remaining).toBe(5000);
    expect(s.reachedGoal).toBe(false);
  });

  it('falls back to the default goal when the server omitted it', () => {
    // geo's no-redis early return sends no `goal` at all.
    const s = stepsSummary({ daily_steps: {}, today: 0 });
    expect(s.goal).toBe(DEFAULT_STEP_GOAL);
  });

  it('treats a zero goal as missing rather than dividing by zero', () => {
    const s = stepsSummary({ daily_steps: {}, today: 100, goal: 0 });
    expect(s.goal).toBe(DEFAULT_STEP_GOAL);
    expect(Number.isFinite(s.percent)).toBe(true);
  });

  it('caps percent at 100 so an overachiever cannot overflow a ring', () => {
    const s = stepsSummary({ daily_steps: {}, today: 25000, goal: 10000 });
    expect(s.percent).toBe(100);
    expect(s.reachedGoal).toBe(true);
    expect(s.remaining).toBe(0);
  });

  it('reports no data when the pedometer has never reported', () => {
    expect(stepsSummary({ daily_steps: {}, today: 0 }).hasData).toBe(false);
    expect(stepsSummary({ daily_steps: { a: 1 }, today: 0 }).hasData).toBe(true);
  });

  it('survives a null response', () => {
    const s = stepsSummary(null);
    expect(s.today).toBe(0);
    expect(s.hasData).toBe(false);
  });

  it('never yields a negative today for a malformed reading', () => {
    expect(stepsSummary({ daily_steps: {}, today: -5 }).today).toBe(0);
    expect(stepsSummary({ daily_steps: {}, today: Number.NaN }).today).toBe(0);
  });
});

describe('dailyStepSeries', () => {
  it('sorts by date rather than trusting the hash field order', () => {
    // Redis hash field order is undefined. The old slice(-7) trusted it.
    const series = dailyStepSeries(
      {
        '2026-09-29': 300,
        '2026-10-01': 100,
        '2026-09-30': 200,
        '2026-10-02': 400,
      },
      4,
      '2026-10-02',
    );
    expect(series.map((p) => p.steps)).toEqual([300, 200, 100, 400]);
  });

  it('fills gaps with zeros so a missing day reads as a gap', () => {
    const series = dailyStepSeries({ '2026-10-01': 100 }, 3, '2026-10-03');
    expect(series.map((p) => p.date)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03']);
    expect(series.map((p) => p.steps)).toEqual([100, 0, 0]);
  });

  it('runs oldest-first ending today', () => {
    const series = dailyStepSeries({ '2026-10-02': 2, '2026-10-01': 1 }, 7, '2026-10-02');
    expect(series).toHaveLength(7);
    expect(series[6].date).toBe('2026-10-02');
    expect(series[5].date).toBe('2026-10-01');
  });

  it('defaults the end date to the newest bucket it was given', () => {
    const series = dailyStepSeries({ '2026-10-02': 7 }, 2);
    expect(series[1]).toMatchObject({ date: '2026-10-02', steps: 7 });
  });

  it('ignores keys that are not dates instead of rendering garbage', () => {
    const series = dailyStepSeries({ today: 999, '2026-10-01': 5 }, 3, '2026-10-01');
    expect(series.every((p) => /^\d{4}-\d{2}-\d{2}$/.test(p.date))).toBe(true);
    expect(series.reduce((sum, p) => sum + p.steps, 0)).toBe(5);
  });

  it('returns empty for no data rather than a fake axis', () => {
    expect(dailyStepSeries({}, 7)).toEqual([]);
    expect(dailyStepSeries(null, 7)).toEqual([]);
    expect(dailyStepSeries(undefined, 7)).toEqual([]);
  });

  it('coerces a malformed step count to zero', () => {
    const series = dailyStepSeries({ '2026-10-01': Number.NaN }, 1, '2026-10-01');
    expect(series[0].steps).toBe(0);
  });

  it('gives every point a single-letter weekday', () => {
    const series = dailyStepSeries({ '2026-10-01': 1 }, 1, '2026-10-01');
    expect(series[0].weekday.length).toBeGreaterThan(0);
  });
});

describe('stepDayLabel', () => {
  it('renders a human weekday and date', () => {
    expect(stepDayLabel('2026-10-01')).toMatch(/Thu/);
  });

  it('returns the raw key when the date cannot be parsed', () => {
    expect(stepDayLabel('not-a-date')).toBe('not-a-date');
  });
});

describe('stepSourcesLabel', () => {
  it('names the single contributing device', () => {
    expect(stepSourcesLabel({ phone: 368 })).toBe('phone');
  });

  it('joins multiple devices', () => {
    expect(stepSourcesLabel({ phone: 10, watch: 20 })).toBe('phone + watch');
  });

  it('is null when nothing reported today, so the page can say so', () => {
    expect(stepSourcesLabel({})).toBeNull();
    expect(stepSourcesLabel({ phone: 0 })).toBeNull();
    expect(stepSourcesLabel(null)).toBeNull();
  });
});

describe('paceLabel', () => {
  it('formats min/mile', () => {
    expect(paceLabel(2, 1800)).toBe('15:00');
  });

  it('pads seconds', () => {
    expect(paceLabel(1, 500)).toBe('8:20');
  });

  it('carries rounded seconds into the minute instead of printing 0:60', () => {
    // 0.9996 min/mile -> 59.976s -> rounds to 60.
    expect(paceLabel(1, 59.976)).toBe('1:00');
  });

  it('is null rather than 0:00 when pace is not measurable', () => {
    expect(paceLabel(0, 1800)).toBeNull();
    expect(paceLabel(null, 1800)).toBeNull();
    expect(paceLabel(2, 0)).toBeNull();
    expect(paceLabel(2, null)).toBeNull();
    expect(paceLabel(undefined, undefined)).toBeNull();
  });
});

describe('workoutTotals', () => {
  it('sums miles, minutes and steps', () => {
    const totals = workoutTotals([
      workout({ distance_miles: 1.5, duration_seconds: 1800, steps: 2000 }),
      workout({ distance_miles: 0.5, duration_seconds: 600, steps: 700 }),
    ]);
    expect(totals.count).toBe(2);
    expect(totals.miles).toBeCloseTo(2);
    expect(totals.minutes).toBe(40);
    expect(totals.steps).toBe(2700);
  });

  it('treats missing metrics as zero', () => {
    const totals = workoutTotals([workout({})]);
    expect(totals).toMatchObject({ miles: 0, minutes: 0, steps: 0 });
  });

  it('handles no workouts at all', () => {
    expect(workoutTotals([]).count).toBe(0);
    expect(workoutTotals(null).count).toBe(0);
  });
});

describe('formatters', () => {
  it('keeps one decimal below ten miles, rounds above', () => {
    expect(formatMiles(2.34)).toBe('2.3');
    expect(formatMiles(42.6)).toBe('43');
  });

  it('formats counts with separators', () => {
    expect(formatCount(12345)).toBe('12,345');
  });
});

describe('elapsedLabel', () => {
  it('formats minutes and seconds', () => {
    expect(elapsedLabel(1000, 1090)).toBe('1:30');
  });

  it('adds an hours field past 60 minutes', () => {
    expect(elapsedLabel(0, 3725)).toBe('1:02:05');
  });

  it('never renders a negative duration when the clock skews', () => {
    expect(elapsedLabel(1100, 1000)).toBe('0:00');
  });
});