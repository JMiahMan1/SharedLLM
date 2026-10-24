/**
 * Pure presentation helpers for the Health hub.
 *
 * The server owns the scores and the nutrition maths. This module only turns
 * those numbers into labels and layout, and keeps the "is this real?" rules in
 * one place: an absent reading is ``null`` all the way to the screen, never a
 * zero dressed up as data.
 */

import type {
  FoodEntry,
  HealthScore,
  NutritionDay,
  NutritionTarget,
} from '../types/api';

export const MEAL_ORDER = ['breakfast', 'lunch', 'dinner', 'snack'] as const;
export type Meal = (typeof MEAL_ORDER)[number];

export const MEAL_LABELS: Record<Meal, string> = {
  breakfast: 'Breakfast',
  lunch: 'Lunch',
  dinner: 'Dinner',
  snack: 'Snacks',
};

/** The local calendar day, not the UTC day. */
/** The 1–5 check-in answers in words (index 0 unused), for energy and mood. */
export const CHECKIN_WORDS = ['', 'Rough', 'Low', 'Okay', 'Good', 'Great'];

export function localDay(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function shiftDay(day: string, delta: number): string {
  const parsed = new Date(`${day}T12:00:00`);
  parsed.setDate(parsed.getDate() + delta);
  return localDay(parsed);
}

export function formatDayLabel(day: string): string {
  const parsed = new Date(`${day}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return day;
  return parsed.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

/** `7h 30m`, `45m`, or an em dash when there is no reading. */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return '—';
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (!hours) return `${rest}m`;
  if (!rest) return `${hours}h`;
  return `${hours}h ${rest}m`;
}

/** `10:30 PM` from an ISO instant or a bare `HH:MM`. */
export function formatClockTime(value: string | null | undefined): string {
  if (!value) return '—';
  const bare = /^\d{1,2}:\d{2}(:\d{2})?$/.test(value);
  if (bare) {
    const parsed = new Date(`2000-01-01T${value}`);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  // ISO instant: format in the wall-clock time of the offset embedded in the
  // string, not the runtime's local zone (so CI in UTC matches the user's day).
  const m = value.match(/T(\d{1,2}):(\d{2})/);
  if (m) {
    const h = parseInt(m[1], 10);
    const min = m[2];
    const ampm = h >= 12 ? 'PM' : 'AM';
    const h12 = h % 12 || 12;
    return `${h12}:${min} ${ampm}`;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

/** `10:30 PM – 6:30 AM`, or null when either end is unknown. */
export function sleepWindowLabel(
  bedtime: string | null | undefined,
  wake: string | null | undefined,
): string | null {
  if (!bedtime || !wake) return null;
  return `${formatClockTime(bedtime)} – ${formatClockTime(wake)}`;
}

export interface ScoreTone {
  text: string;
  bg: string;
  border: string;
  bar: string;
}

/**
 * Colour is meaningful, not decorative: it changes with the score band and is
 * always paired with a word, so a colour-blind reader loses nothing.
 */
export function scoreTone(score: number | null | undefined): ScoreTone {
  if (score === null || score === undefined) {
    return {
      text: 'text-slate-400',
      bg: 'bg-slate-500/10',
      border: 'border-slate-500/20',
      bar: 'bg-slate-500',
    };
  }
  if (score >= 80) {
    return {
      text: 'text-emerald-300',
      bg: 'bg-emerald-500/10',
      border: 'border-emerald-500/25',
      bar: 'bg-emerald-500',
    };
  }
  if (score >= 60) {
    return {
      text: 'text-sky-300',
      bg: 'bg-sky-500/10',
      border: 'border-sky-500/25',
      bar: 'bg-sky-500',
    };
  }
  if (score >= 40) {
    return {
      text: 'text-amber-300',
      bg: 'bg-amber-500/10',
      border: 'border-amber-500/25',
      bar: 'bg-amber-500',
    };
  }
  return {
    text: 'text-purple-300',
    bg: 'bg-purple-500/10',
    border: 'border-purple-500/25',
    bar: 'bg-purple-500',
  };
}

/** A one-line caption that tells the reader how much evidence is behind a score. */
export function confidenceLabel(score: HealthScore | null | undefined): string | null {
  if (!score || score.confidence === 'high') return null;
  if (score.confidence === 'medium') return 'Based on part of your usual signals.';
  return 'Early estimate — a few more days of data will sharpen it.';
}

export interface NutritionProgress {
  /** The midpoint of the range, or null when no range exists. */
  target: number | null;
  min: number | null;
  max: number | null;
  percent: number | null;
  remaining: number | null;
  over: boolean;
  /** False when the profile cannot produce an honest range. */
  available: boolean;
}

/** Eaten vs the target range, with "no range" kept distinct from "zero eaten". */
export function nutritionProgress(
  eaten: number,
  target: Pick<NutritionTarget, 'min' | 'max'> | null | undefined,
): NutritionProgress {
  const min = target?.min ?? null;
  const max = target?.max ?? null;
  const midpoint = min !== null && max !== null ? (min + max) / 2 : null;
  if (midpoint === null || midpoint <= 0) {
    return {
      target: null,
      min,
      max,
      percent: null,
      remaining: null,
      over: false,
      available: false,
    };
  }
  const clamped = Math.max(0, eaten);
  return {
    target: Math.round(midpoint),
    min,
    max,
    percent: Math.min(100, Math.round((clamped / midpoint) * 100)),
    remaining: Math.max(0, Math.round(midpoint - clamped)),
    over: max !== null && clamped > max,
    available: true,
  };
}

/** Per-serving macros multiplied by the logged portion. */
export function foodTotals(entry: FoodEntry): {
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
} {
  const factor = Math.max(0, entry.servings || 0);
  return {
    calories: entry.calories * factor,
    protein_g: entry.protein_g * factor,
    carbs_g: entry.carbs_g * factor,
    fat_g: entry.fat_g * factor,
  };
}

export function sumFoodEntries(entries: FoodEntry[]): {
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
} {
  return entries.reduce(
    (total, entry) => {
      const values = foodTotals(entry);
      total.calories += values.calories;
      total.protein_g += values.protein_g;
      total.carbs_g += values.carbs_g;
      total.fat_g += values.fat_g;
      return total;
    },
    { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0 },
  );
}

/** Percentage of a macro target, capped so an over-day does not overflow a bar. */
export function macroPercent(consumed: number, target: number | null | undefined): number | null {
  if (!target || target <= 0) return null;
  return Math.min(100, Math.round((Math.max(0, consumed) / target) * 100));
}

/** `1,850–2,050 kcal`, `Guidance: 2,100–2,500 kcal`, or the missing fields. */
export function targetLabel(target: NutritionTarget | null | undefined): string {
  if (!target || target.kind === 'unavailable' || target.min === null || target.max === null) {
    return 'Set up your profile for a range';
  }
  const prefix = target.kind === 'teen_guidance' ? 'Guidance' : 'Range';
  return `${prefix}: ${target.min.toLocaleString()}–${target.max.toLocaleString()} kcal`;
}

/** A compact, honest description of a day's calorie state. */
export function calorieSummary(nutrition: NutritionDay): string {
  if (!nutrition.logged) return 'Nothing logged yet';
  const totals = Math.round(nutrition.totals.calories).toLocaleString();
  const progress = nutritionProgress(nutrition.totals.calories, nutrition.target);
  if (!progress.available) return `${totals} kcal logged`;
  if (progress.over && progress.max !== null) {
    return `${totals} kcal — above the ${progress.max.toLocaleString()} guide`;
  }
  return `${totals} kcal — ${progress.remaining?.toLocaleString()} left of the guide`;
}

/** Age from a birth year, or null when it is not known. */
export function ageFromBirthYear(birthYear: number | null | undefined, now = new Date()): number | null {
  if (!birthYear || !Number.isFinite(birthYear)) return null;
  const age = now.getFullYear() - birthYear;
  return age >= 0 && age <= 130 ? age : null;
}
