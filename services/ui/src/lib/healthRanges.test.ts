import { describe, it, expect } from 'vitest';
import {
  bucketAriaLabel,
  bucketHeightPercent,
  heroInsight,
  isGap,
  peakBucket,
  rangeOptions,
  ringSpec,
  stepZone,
  volumeTrend,
  ZONE_STYLES,
} from './healthRanges';
import type { StepRangeBucket, StepRangeResponse } from '../types/api';

const bucket = (over: Partial<StepRangeBucket> = {}): StepRangeBucket => ({
  label: 'Oct 1',
  start: '2026-10-01',
  end: '2026-10-01',
  steps: 8000,
  days_missing: 0,
  days_recorded: 1,
  complete: true,
  ...over,
});

const series = (over: Partial<StepRangeResponse> = {}): StepRangeResponse => ({
  user_id: 'jeremiah',
  range: 'W',
  label: 'Week',
  buckets: [bucket()],
  total: 8000,
  daily_average: 8000,
  days_recorded: 7,
  goal: 10000,
  baseline: 8000,
  baseline_min_days: 7,
  thin: false,
  has_gaps: false,
  best: { label: 'Oct 1', steps: 8000 },
  ...over,
});

describe('rangeOptions', () => {
  it('offers every range on a wide screen', () => {
    expect(rangeOptions(false).map((r) => r.id)).toEqual(['D', 'W', 'M', '3M', 'Y']);
  });

  it('keeps every range reachable on a phone', () => {
    // Dropping 3M and Y outright would hide data rather than defer it.
    expect(rangeOptions(true).map((r) => r.id)).toEqual(['D', 'W', 'M', '3M', 'Y']);
  });

  it('puts the common ranges first on a phone', () => {
    expect(rangeOptions(true).slice(0, 3).map((r) => r.id)).toEqual(['D', 'W', 'M']);
  });
});

describe('stepZone', () => {
  it('bands relative to the user own baseline, not a fixed number', () => {
    expect(stepZone(4000, 9000)).toBe('under');
    expect(stepZone(7000, 9000)).toBe('low');
    expect(stepZone(9000, 9000)).toBe('on');
    expect(stepZone(12000, 9000)).toBe('high');
  });

  it('gives a person with a low normal a different answer for the same day', () => {
    const steps = 9000;
    expect(stepZone(steps, 14000)).toBe('low');
    expect(stepZone(steps, 4000)).toBe('high');
  });

  it('refuses to guess when there is no baseline', () => {
    expect(stepZone(9000, null)).toBe('on');
    expect(stepZone(9000, 0)).toBe('on');
  });

  it('has a style for every zone it can return', () => {
    for (const z of ['under', 'low', 'on', 'high'] as const) {
      expect(ZONE_STYLES[z].label).toBeTruthy();
      expect(ZONE_STYLES[z].ring).toMatch(/^stroke-/);
    }
  });
});

describe('ringSpec', () => {
  it('reports progress against the goal', () => {
    const r = ringSpec(5000, 10000, 10000);
    expect(r.percent).toBe(50);
    expect(r.remaining).toBe(5000);
    expect(r.reached).toBe(false);
  });

  it('caps at 100 rather than overflowing the ring', () => {
    expect(ringSpec(30000, 10000, 10000).percent).toBe(100);
  });

  it('marks the goal reached even when overshooting', () => {
    const r = ringSpec(12000, 10000, 10000);
    expect(r.reached).toBe(true);
    expect(r.remaining).toBe(0);
  });

  it('falls back to the default goal when the server sends none', () => {
    expect(ringSpec(1000, null, 10000).goal).toBe(10000);
    expect(ringSpec(1000, 0, 8000).goal).toBe(8000);
  });

  it('treats a missing or negative count as zero, not NaN', () => {
    expect(ringSpec(NaN, 10000, 10000).percent).toBe(0);
    expect(ringSpec(-5, 10000, 10000).percent).toBe(0);
  });
});

describe('volumeTrend', () => {
  it('compares against the previous window', () => {
    const t = volumeTrend(9000, 6000, 'last week');
    expect(t.direction).toBe('up');
    expect(t.percent).toBe(50);
    expect(t.comparison).toBe('vs last week');
  });

  it('reports a decrease', () => {
    expect(volumeTrend(3000, 6000).direction).toBe('down');
  });

  it('calls small changes flat', () => {
    expect(volumeTrend(6000, 5900).direction).toBe('flat');
  });

  it('says there is nothing to compare rather than claiming flat', () => {
    const t = volumeTrend(9000, null);
    expect(t.percent).toBeNull();
    expect(t.comparison).toMatch(/no .* to compare/);
  });

  it('does not compare against a zero previous window', () => {
    expect(volumeTrend(9000, 0).percent).toBeNull();
  });
});

describe('heroInsight', () => {
  it('returns null rather than inventing a headline', () => {
    // Today is zero, there is no baseline to compare against, no gap to
    // report and no best -- genuinely nothing to say, so say nothing.
    const insight = heroInsight(
      series({ baseline: null, best: null, thin: true, has_gaps: false }),
      0,
      10000,
    );
    expect(insight).toBeNull();
  });

  it('does name a day that is meaningfully off the baseline', () => {
    const insight = heroInsight(series({ daily_average: 8000, baseline: 8000, best: null }), 5000, 10000);
    expect(insight?.headline).toMatch(/below your usual/);
  });

  it('confirms a day that is on the usual pace', () => {
    // Being on pace is a finding too: it is the reassurance the rings do not give.
    const insight = heroInsight(series({ daily_average: 8000, baseline: 8000, best: null }), 8000, 10000);
    expect(insight?.tone).toBe('on');
  });

  it('leads with a personal best when it is clearly above usual', () => {
    const insight = heroInsight(
      series({ best: { label: 'Sat', steps: 20000 }, daily_average: 8000, baseline: 8000 }),
      5000,
      10000,
    );
    expect(insight?.headline).toMatch(/Best day/);
    expect(insight?.tone).toBe('high');
  });

  it('does not call a best when the history is too thin to mean anything', () => {
    const insight = heroInsight(
      series({ best: { label: 'Sat', steps: 20000 }, daily_average: 8000, thin: true }),
      5000,
      10000,
    );
    expect(insight?.headline).not.toMatch(/Best day/);
  });

  it('announces a met goal', () => {
    const insight = heroInsight(series(), 11000, 10000);
    expect(insight?.headline).toMatch(/Goal met/);
  });

  it('says how many days a real comparison needs when thin', () => {
    const insight = heroInsight(series({ thin: true, days_recorded: 2 }), 4000, 10000);
    expect(insight?.headline).toMatch(/so far today/);
    expect(insight?.detail).toMatch(/7 days of history/);
  });

  it('compares against the baseline when one exists', () => {
    const insight = heroInsight(series({ baseline: 10000, daily_average: 10000 }), 6000, 10000);
    expect(insight?.headline).toMatch(/below your usual/);
    expect(insight?.detail).toMatch(/baseline is about 10,000/);
  });

  it('calls out gaps rather than treating them as zeros', () => {
    const insight = heroInsight(
      series({ has_gaps: true, baseline: null, best: null, thin: true }),
      0,
      10000,
    );
    expect(insight?.headline).toMatch(/missing/);
    expect(insight?.detail).toMatch(/not a zero/i);
  });

  it('has nothing to say about an empty range', () => {
    expect(heroInsight(series({ buckets: [] }), 0, 10000)).toBeNull();
    expect(heroInsight(null, 0, 10000)).toBeNull();
  });
});

describe('bucket helpers', () => {
  it('finds the peak', () => {
    expect(peakBucket([bucket({ steps: 100 }), bucket({ steps: 900 }), bucket({ steps: 400 })])).toBe(900);
  });

  it('reports no peak when everything is zero', () => {
    expect(peakBucket([bucket({ steps: 0 })])).toBe(0);
  });

  it('keeps a small bar visible next to a huge one', () => {
    expect(bucketHeightPercent(bucket({ steps: 100 }), 30000)).toBeGreaterThanOrEqual(4);
  });

  it('scales bars to the peak', () => {
    expect(bucketHeightPercent(bucket({ steps: 15000 }), 30000)).toBe(50);
  });

  it('gives a zero bar no height', () => {
    expect(bucketHeightPercent(bucket({ steps: 0 }), 30000)).toBe(0);
  });

  it('treats a bucket with a missing day as a gap', () => {
    expect(isGap(bucket({ days_missing: 2 }))).toBe(true);
    expect(isGap(bucket({ days_missing: 0 }))).toBe(false);
  });

  it('never announces a missing day as zero steps', () => {
    const label = bucketAriaLabel(bucket({ steps: 0, days_missing: 1, complete: false }));
    expect(label).toMatch(/1 day with no reading/);
    // No step count at all when a day is missing: the old code led with
    // "0 steps" and only qualified it afterwards, which reads as zero.
    expect(label).not.toMatch(/steps/);
  });

  it('withholds a partial total, since it is not a total for the window', () => {
    const label = bucketAriaLabel(bucket({ steps: 8240, days_missing: 1, days_recorded: 6, complete: false }));
    expect(label).not.toMatch(/8,240/);
    expect(label).toMatch(/6 days recorded/);
    expect(label).toMatch(/1 day with no reading/);
  });

  it('says plainly that a window with no readings at all has none', () => {
    const label = bucketAriaLabel(bucket({ steps: 0, days_missing: 7, days_recorded: 0, complete: false }));
    expect(label).toBe('Oct 1: no reading');
  });

  it('pluralises the missing days', () => {
    expect(bucketAriaLabel(bucket({ days_missing: 3 }))).toMatch(/3 days with no reading/);
  });

  it('states the steps plainly when the data is complete', () => {
    expect(bucketAriaLabel(bucket({ steps: 8000 }))).toBe('Oct 1: 8,000 steps');
  });
});
