/**
 * Range, zone and insight logic for the Health page.
 *
 * The patterns here each come from a specific app, and the reason they are
 * worth copying is that they answer a question the raw numbers do not:
 *
 *  - Whoop's status zones: a single number is never motivating, but "you are in
 *    the red zone" is. The bands are relative to the user's *own* baseline
 *    rather than a population average, because a 6,000-step day is unremarkable
 *    for one person and a personal best for another.
 *  - Oura's "one big thing": a dashboard of numbers makes the reader do the
 *    summarising. One sentence that names the most notable fact is more useful
 *    than six charts.
 *  - Fitbit's rings: progress against a goal, with the remainder legible.
 *  - Strava's weekly volume: trend against the user's own recent average.
 *
 * Everything is derived from the server's pre-aggregated payload, so no surface
 * re-folds daily buckets. That payload distinguishes "no reading" from "zero
 * steps" (days_missing), and that distinction is preserved everywhere here --
 * a missing day is never drawn or averaged as a zero.
 */

import type { StepRange, StepRangeBucket, StepRangeResponse } from '../types/api';

export const STEP_RANGES: { id: StepRange; label: string; short: string }[] = [
  { id: 'D', label: 'Day', short: 'D' },
  { id: 'W', label: 'Week', short: 'W' },
  { id: 'M', label: 'Month', short: 'M' },
  { id: '3M', label: '3 Months', short: '3M' },
  { id: 'Y', label: 'Year', short: 'Y' },
];

/** How many ranges to show on the phone before the strip gets unreadable. */
export const MOBILE_RANGE_IDS: StepRange[] = ['D', 'W', 'M'];

export interface RangeOption {
  id: StepRange;
  label: string;
  /** Compact form for the button face; the full label stays for a11y. */
  short: string;
}

/**
 * The ranges to offer, fewer on a narrow screen.
 *
 * Every range stays reachable -- dropping "3M" and "Y" from the phone outright
 * would hide data rather than defer it, so the overflow keeps them one tap away.
 */
export function rangeOptions(narrow: boolean): RangeOption[] {
  const all = STEP_RANGES.map((r) => ({ id: r.id, label: r.label, short: r.short }));
  if (!narrow) return all;
  const primary = STEP_RANGES.filter((r) => MOBILE_RANGE_IDS.includes(r.id));
  const rest = STEP_RANGES.filter((r) => !MOBILE_RANGE_IDS.includes(r.id));
  return [...primary, ...rest].map((r) => ({ id: r.id, label: r.label, short: r.short }));
}

// --- Whoop-style status zones -------------------------------------------

export type StepZone = 'under' | 'low' | 'on' | 'high';

/**
 * Where today sits relative to the user's own baseline.
 *
 * Bands are anchored on the baseline rather than the goal: someone whose
 * normal is 14,000 should not read 9,000 as a failure, and someone whose
 * normal is 4,000 should not have their ordinary day called "high".
 *
 * `baseline: null` (too little history) yields 'on' rather than a guess, and
 * the page reports thinness separately.
 */
export function stepZone(today: number, baseline: number | null): StepZone {
  if (!baseline || baseline <= 0) return 'on';
  const ratio = today / baseline;
  if (ratio < 0.5) return 'under';
  if (ratio < 0.85) return 'low';
  if (ratio < 1.2) return 'on';
  return 'high';
}

/** Tailwind classes per zone, used for ring and accent colour. */
export const ZONE_STYLES: Record<StepZone, { text: string; ring: string; label: string }> = {
  under: { text: 'text-sky-300', ring: 'stroke-sky-400', label: 'Well under usual' },
  low: { text: 'text-purple-300', ring: 'stroke-purple-400', label: 'Below usual' },
  on: { text: 'text-emerald-300', ring: 'stroke-emerald-400', label: 'On your usual pace' },
  high: { text: 'text-amber-300', ring: 'stroke-amber-400', label: 'Above usual' },
};

// --- Rings ---------------------------------------------------------------

export interface RingSpec {
  /** 0-100, rounded and clamped. */
  percent: number;
  reached: boolean;
  remaining: number;
  /** The goal the ring is drawn against. */
  goal: number;
  actual: number;
}

/**
 * One ring, sized to its share of a set.
 *
 * A ring is only meaningful against a goal, so when the server sends no goal
 * the ring falls back to the default rather than dividing by zero -- and the
 * page is told the goal is an assumption via `goalIsDefault`.
 */
export function ringSpec(
  actual: number,
  goal: number | null | undefined,
  defaultGoal: number,
): RingSpec {
  const effective = goal && goal > 0 ? goal : defaultGoal;
  const value = Number.isFinite(actual) && actual > 0 ? actual : 0;
  const percent = Math.min(100, Math.max(0, Math.round((value / effective) * 100)));
  return {
    percent,
    reached: value >= effective,
    remaining: Math.max(0, effective - value),
    goal: effective,
    actual: value,
  };
}

// --- Strava-style volume trend ------------------------------------------

export interface VolumeTrend {
  /** Percent change against the preceding window, or null when unknown. */
  percent: number | null;
  direction: 'up' | 'down' | 'flat';
  /** A short, honest phrase: "vs last week". */
  comparison: string;
}

/**
 * Compare the current window against the one before it.
 *
 * `previous` is passed in rather than fetched: the caller already has the
 * other range cached, and a second request for a number the UI can carry is
 * waste. A null previous means "no comparison", never "flat" -- drawing
 * "unchanged" against missing data would be a claim the data does not support.
 */
export function volumeTrend(
  currentTotal: number,
  previousTotal: number | null | undefined,
  previousLabel = 'last period',
): VolumeTrend {
  if (previousTotal === null || previousTotal === undefined || previousTotal <= 0) {
    return { percent: null, direction: 'flat', comparison: `no ${previousLabel} to compare` };
  }
  const percent = Math.round(((currentTotal - previousTotal) / previousTotal) * 100);
  const direction = percent > 2 ? 'up' : percent < -2 ? 'down' : 'flat';
  return { percent, direction, comparison: `vs ${previousLabel}` };
}

// --- Oura-style "one big thing" -----------------------------------------

export interface HeroInsight {
  /** The single sentence. */
  headline: string;
  detail: string | null;
  tone: StepZone | 'neutral';
}

/**
 * The one fact worth reading first.
 *
 * Ranked by how surprising the fact is, not by how easy it is to compute: a
 * personal best outranks a missed goal, because the user already knows the
 * goal. Returns null rather than filler when there is nothing to say -- an
 * "insight" that just restates the number is noise.
 */
export function heroInsight(
  series: StepRangeResponse | null | undefined,
  today: number,
  goal: number,
): HeroInsight | null {
  if (!series || !series.buckets.length) return null;

  const recorded = series.days_recorded || 0;
  const thin = series.thin || recorded < series.baseline_min_days;

  // A personal best, when there is enough history for "best" to mean anything.
  if (series.best && !thin && series.daily_average > 0) {
    const isRecord = series.best.steps > series.daily_average * 1.25;
    if (isRecord) {
      return {
        headline: `Best day in ${series.label.toLowerCase()}: ${series.best.steps.toLocaleString()} steps`,
        detail: `Your usual is about ${Math.round(series.daily_average).toLocaleString()} a day.`,
        tone: 'high',
      };
    }
  }

  if (today > 0 && today >= goal) {
    return {
      headline: `Goal met — ${today.toLocaleString()} steps today`,
      detail: `${series.label} total is ${series.total.toLocaleString()}.`,
      tone: 'high',
    };
  }

  if (today > 0 && thin) {
    return {
      headline: `${today.toLocaleString()} steps so far today`,
      detail: `Keep going — a fair comparison needs ${series.baseline_min_days} days of history.`,
      tone: 'neutral',
    };
  }

  if (today > 0 && series.baseline) {
    const zone = stepZone(today, series.baseline);
    const diff = Math.abs(Math.round(today - series.baseline));
    if (zone === 'low' || zone === 'under') {
      return {
        headline: `${diff.toLocaleString()} steps below your usual`,
        detail: `Your baseline is about ${Math.round(series.baseline).toLocaleString()} a day.`,
        tone: zone,
      };
    }
    if (zone === 'high') {
      return {
        headline: `${diff.toLocaleString()} steps above your usual`,
        detail: `Your baseline is about ${Math.round(series.baseline).toLocaleString()} a day.`,
        tone: zone,
      };
    }
    return {
      headline: `Right on your usual pace`,
      detail: `About ${Math.round(series.baseline).toLocaleString()} steps a day for you.`,
      tone: 'on',
    };
  }

  if (series.has_gaps) {
    return {
      headline: 'Some days are missing from this range',
      detail: 'A gap is not a zero — those days simply have no reading.',
      tone: 'neutral',
    };
  }

  return null;
}

// --- Bucket helpers ------------------------------------------------------

/** The tallest bucket, used to scale bars. Null when everything is zero. */
export function peakBucket(buckets: StepRangeBucket[]): number {
  return buckets.reduce((max, b) => Math.max(max, b.steps), 0);
}

/**
 * Bar height as a percentage of the peak, with a floor so a non-zero day is
 * still visible next to a 30,000-step day.
 */
export function bucketHeightPercent(bucket: StepRangeBucket, peak: number): number {
  if (bucket.steps <= 0 || peak <= 0) return 0;
  return Math.max(4, Math.round((bucket.steps / peak) * 100));
}

/** True when the bucket has no reading for at least one day in its window. */
export function isGap(bucket: StepRangeBucket): boolean {
  return bucket.days_missing > 0;
}

/**
 * How to describe a bar for a screen reader.
 *
 * A bar with no data must not be announced as zero steps -- that is a different
 * claim, and for a missing day it is a false one.
 *
 * A bucket spanning several days is a sum over the days that *were* recorded,
 * so a partial bucket's total is not a real total for its window either. When
 * any day is missing, the count is withheld and the gap is stated instead:
 * "3 days recorded, 1 with no reading" is true, "8,240 steps" is not.
 */
export function bucketAriaLabel(bucket: StepRangeBucket): string {
  if (isGap(bucket)) {
    const missing =
      bucket.days_missing === 1 ? '1 day with no reading' : `${bucket.days_missing} days with no reading`;
    if (bucket.days_recorded === 0) {
      return `${bucket.label}: no reading`;
    }
    const recorded =
      bucket.days_recorded === 1 ? '1 day recorded' : `${bucket.days_recorded} days recorded`;
    return `${bucket.label}: ${recorded}, ${missing}`;
  }
  return `${bucket.label}: ${bucket.steps.toLocaleString()} steps`;
}
