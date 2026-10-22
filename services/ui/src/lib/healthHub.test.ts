import { describe, expect, it } from 'vitest';
import {
  calorieSummary,
  foodTotals,
  formatClockTime,
  formatMinutes,
  localDay,
  macroPercent,
  nutritionProgress,
  scoreTone,
  shiftDay,
  sleepWindowLabel,
  sumFoodEntries,
  targetLabel,
} from './healthHub';
import type { FoodEntry, NutritionDay, NutritionTarget } from '../types/api';

const food = (overrides: Partial<FoodEntry> = {}): FoodEntry => ({
  id: 1,
  user_id: 'jeremiah',
  day: '2026-10-22',
  meal: 'breakfast',
  name: 'Oats',
  brand: '',
  calories: 150,
  protein_g: 5,
  carbs_g: 27,
  fat_g: 3,
  fiber_g: 4,
  sugar_g: 1,
  sodium_mg: 10,
  servings: 1,
  serving_grams: 40,
  barcode: null,
  recipe_path: null,
  source: 'manual',
  external_id: null,
  created_at: '2026-10-22T07:00:00Z',
  density: null,
  ...overrides,
});

const target = (overrides: Partial<NutritionTarget> = {}): NutritionTarget => ({
  kind: 'adult_range',
  min: 1800,
  max: 2000,
  bmr: 1700,
  tdee: 1900,
  goal: 'maintain',
  note: '',
  missing: [],
  deficit_allowed: false,
  protein_g: 120,
  carbs_g: 210,
  fat_g: 63,
  ...overrides,
});

const day = (overrides: Partial<NutritionDay> = {}): NutritionDay => ({
  user_id: 'jeremiah',
  day: '2026-10-22',
  entries_count: 2,
  logged: true,
  totals: { calories: 1500, protein_g: 60, carbs_g: 180, fat_g: 50, fiber_g: 20 },
  meals: {},
  target: target(),
  ...overrides,
});

describe('local days', () => {
  it('uses the local calendar day, not UTC', () => {
    const lateEvening = new Date(2026, 9, 22, 23, 30);
    expect(localDay(lateEvening)).toBe('2026-10-22');
  });

  it('shifts across month boundaries', () => {
    expect(shiftDay('2026-10-01', -1)).toBe('2026-09-30');
  });
});

describe('formatting', () => {
  it('formats durations without a fake zero hour', () => {
    expect(formatMinutes(450)).toBe('7h 30m');
    expect(formatMinutes(60)).toBe('1h');
    expect(formatMinutes(45)).toBe('45m');
    expect(formatMinutes(null)).toBe('-');
  });

  it('formats a sleep window', () => {
    expect(sleepWindowLabel('2026-10-21T22:30:00-07:00', '2026-10-22T06:30:00-07:00')).toMatch(
      /10:30 PM . 6:30 AM/,
    );
    expect(sleepWindowLabel(null, '2026-10-22T06:30:00-07:00')).toBeNull();
  });

  it('formats a bare clock time', () => {
    expect(formatClockTime('07:05')).toMatch(/7:05/);
  });
});

describe('score tone', () => {
  it('pairs every band with a label colour', () => {
    expect(scoreTone(90).text).toContain('emerald');
    expect(scoreTone(65).text).toContain('sky');
    expect(scoreTone(45).text).toContain('amber');
    expect(scoreTone(10).text).toContain('purple');
  });

  it('does not colour an absent score as success or failure', () => {
    expect(scoreTone(null).text).toContain('slate');
  });
});

describe('nutrition progress', () => {
  it('computes remaining against the midpoint', () => {
    const progress = nutritionProgress(1500, target());
    expect(progress.available).toBe(true);
    expect(progress.percent).toBe(79);
    expect(progress.remaining).toBe(400);
    expect(progress.over).toBe(false);
  });

  it('marks a day over the top of the range without shaming it', () => {
    const progress = nutritionProgress(2300, target());
    expect(progress.over).toBe(true);
    expect(progress.percent).toBe(100);
  });

  it('keeps an unavailable range distinct from zero', () => {
    const progress = nutritionProgress(0, target({ kind: 'unavailable', min: null, max: null }));
    expect(progress.available).toBe(false);
    expect(progress.percent).toBeNull();
  });

  it('gives guidance wording for minors', () => {
    expect(targetLabel(target({ kind: 'teen_guidance', min: 2200, max: 2500 }))).toContain(
      'Guidance',
    );
  });

  it('names the missing profile fields instead of a number', () => {
    expect(targetLabel(target({ kind: 'unavailable', min: null, max: null }))).toBe(
      'Set up your profile for a range',
    );
  });
});

describe('food totals', () => {
  it('multiplies per-serving values by the portion', () => {
    const totals = foodTotals(food({ servings: 2 }));
    expect(totals.calories).toBe(300);
    expect(totals.protein_g).toBe(10);
  });

  it('sums a meal', () => {
    const totals = sumFoodEntries([food({ servings: 1 }), food({ id: 2, servings: 0.5 })]);
    expect(totals.calories).toBe(225);
  });

  it('never divides by a missing macro target', () => {
    expect(macroPercent(50, null)).toBeNull();
    expect(macroPercent(50, 100)).toBe(50);
  });

  it('describes a logged day against the range', () => {
    expect(calorieSummary(day())).toContain('1,500 kcal');
    expect(calorieSummary(day({ logged: false }))).toBe('Nothing logged yet');
  });
});
