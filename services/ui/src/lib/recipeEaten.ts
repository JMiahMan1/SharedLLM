/**
 * What was actually eaten against what was planned (Phase 10).
 *
 * Health -> Today already shows tonight's planned dinner; this compares it with
 * the day's logged dinner entries so a parent can see the difference: the plan
 * logged as itself, something else logged instead, or nothing logged yet.
 *
 * A recipe logged through the app carries the note's path (`recipe_path`) and
 * the "Family recipe" brand, which is how a logged entry is tied back to the
 * plan. Nothing here reads nutrition - the card decides what numbers to hide.
 */

import type { FoodEntry } from '../types/api';
import { RECIPE_BRAND } from './recipeNutrition';

export interface EatenDinner {
  /** The dinner entries logged for the day, in order. */
  entries: FoodEntry[];
  /** The logged entry that is tonight's planned recipe, when one is. */
  matched: FoodEntry | null;
  /** The distinct dinner names logged, for "something else" lines. */
  names: string[];
}

/**
 * Compare tonight's planned dinner with the day's logged dinner.
 *
 * `plannedPath` is the plan note's path for the recipe (how a logged recipe
 * points back at it); a name + brand match is the fallback for a note that was
 * renamed after logging. Only dinner entries count: a snack is not the meal.
 */
export function eatenDinner(
  plannedPath: string,
  plannedTitle: string,
  entries: FoodEntry[],
): EatenDinner {
  const dinner = entries.filter((entry) => entry.meal === 'dinner');
  const matched =
    dinner.find(
      (entry) =>
        (Boolean(plannedPath) && entry.recipe_path === plannedPath) ||
        (entry.brand === RECIPE_BRAND && entry.name === plannedTitle),
    ) ?? null;
  const names = Array.from(new Set(dinner.map((entry) => entry.name)));
  return { entries: dinner, matched, names };
}
