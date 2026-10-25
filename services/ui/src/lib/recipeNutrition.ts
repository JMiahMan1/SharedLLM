/**
 * Per-serving nutrition read out of a recipe note, for logging it to Health.
 *
 * Imported notes carry one line under `## Nutrition`, written by
 * `scripts/import_recipes.py`:
 *
 *   *Per serving, as stated by the submitter - foodcom:* 350.2 kcal · 12 g fat · … · 25 g protein
 *
 * Only values that are written there are used. A note without the section, or
 * with "Nutrition was not stated", is `missing`; one that lacks any of
 * calories/protein/carbs/fat is `incomplete` and names what is absent. Nothing
 * is estimated: the caller offers manual entry instead.
 */

import type { FoodEntry } from '../types/api';

export interface RecipeNutrition {
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  fiber_g: number | null;
  sugar_g: number | null;
  sodium_mg: number | null;
  /** Who stated the values, e.g. `foodcom`; empty when the line does not say. */
  source: string;
}

export type RecipeNutritionResult =
  | { status: 'ok'; perServing: RecipeNutrition }
  | { status: 'missing' }
  | { status: 'incomplete'; missing: string[] };

/** The portion picker: servings of the recipe as written. */
export const PORTIONS = [0.5, 1, 1.5, 2] as const;

const CORE = ['calories', 'protein_g', 'carbs_g', 'fat_g'] as const;
const CORE_NAMES: Record<(typeof CORE)[number], string> = {
  calories: 'calories',
  protein_g: 'protein',
  carbs_g: 'carbs',
  fat_g: 'fat',
};

type Field = keyof Omit<RecipeNutrition, 'source'>;

/** Which field one `350.2 kcal` / `12 g fat` part names; `null` for ones not logged. */
function fieldOf(label: string): Field | null {
  const text = label.toLowerCase().trim();
  if (/\bkcal$|\bcalories$/.test(text)) return 'calories';
  if (/sat(urated)? fat$/.test(text)) return null;
  if (/\bfat$/.test(text)) return 'fat_g';
  if (/\bcarbs$|\bcarbohydrates?$/.test(text)) return 'carbs_g';
  if (/\bprotein$/.test(text)) return 'protein_g';
  if (/\bfib(er|re)$/.test(text)) return 'fiber_g';
  if (/\bsugars?$/.test(text)) return 'sugar_g';
  if (/\bsodium$/.test(text)) return 'sodium_mg';
  return null;
}

/** The text under `## Nutrition`, up to the next heading. */
function nutritionSection(body: string): string | null {
  const lines = body.split('\n');
  const start = lines.findIndex((line) => /^##\s+nutrition\s*$/i.test(line.trim()));
  if (start < 0) return null;
  const rest: string[] = [];
  for (const line of lines.slice(start + 1)) {
    if (/^#{1,6}\s/.test(line.trim())) break;
    rest.push(line);
  }
  return rest.join('\n');
}

export function parseRecipeNutrition(body: string): RecipeNutritionResult {
  const section = nutritionSection(body);
  if (section === null) return { status: 'missing' };
  const line = section.split('\n').find((candidate) => /per serving/i.test(candidate));
  if (!line) return { status: 'missing' };

  // "*Per serving, as stated by the submitter - foodcom:* …": the tag before the colon.
  const header = line.match(/per serving[^:]*?(?:\u2014|-)\s*([a-z0-9_-]+)\s*:/i);
  const values = line.includes(':') ? line.slice(line.indexOf(':') + 1) : line;
  const found: Partial<Record<Field, number>> = {};
  for (const part of values.replace(/\*/g, '').split('·')) {
    const match = part.trim().match(/^(\d+(?:\.\d+)?)\s*(.*)$/);
    if (!match) continue;
    const field = fieldOf(match[2]);
    const value = Number(match[1]);
    if (field && Number.isFinite(value) && !(field in found)) found[field] = value;
  }

  const missing = CORE.filter((key) => found[key] === undefined).map((key) => CORE_NAMES[key]);
  if (missing.length === CORE.length) return { status: 'missing' };
  if (missing.length > 0) return { status: 'incomplete', missing };
  return {
    status: 'ok',
    perServing: {
      calories: found.calories as number,
      protein_g: found.protein_g as number,
      carbs_g: found.carbs_g as number,
      fat_g: found.fat_g as number,
      fiber_g: found.fiber_g ?? null,
      sugar_g: found.sugar_g ?? null,
      sodium_mg: found.sodium_mg ?? null,
      source: header ? header[1] : '',
    },
  };
}

/** "½", "1", "1½", "2" for the portion buttons. */
export function portionLabel(servings: number): string {
  const whole = Math.floor(servings);
  const half = servings - whole === 0.5;
  if (!half) return String(servings);
  return whole === 0 ? '½' : `${whole}½`;
}

/** What a portion adds up to, rounded for display (the entry stores per serving). */
export function portionTotals(perServing: Pick<RecipeNutrition, 'calories' | 'protein_g' | 'carbs_g' | 'fat_g'>, servings: number) {
  const round = (value: number) => Math.round(value * servings * 10) / 10;
  return {
    calories: Math.round(perServing.calories * servings),
    protein_g: round(perServing.protein_g),
    carbs_g: round(perServing.carbs_g),
    fat_g: round(perServing.fat_g),
  };
}

export const RECIPE_BRAND = 'Family recipe';

export type RecipeFoodBody = Partial<FoodEntry> & { day: string; name: string; meal: FoodEntry['meal'] };

/**
 * The food entry a recipe logs as: per-serving values with `servings` as the
 * multiplier (the store's own convention), tied back to the note by path.
 * `source` is `recipe` when the values came from the note, `manual` when the
 * person typed them in.
 */
export function recipeFoodEntry({
  title,
  path,
  perServing,
  servings,
  day,
  meal,
  typed = false,
}: {
  title: string;
  path: string;
  perServing: Omit<RecipeNutrition, 'source'>;
  servings: number;
  day: string;
  meal: FoodEntry['meal'];
  typed?: boolean;
}): RecipeFoodBody {
  return {
    day,
    meal,
    name: title,
    brand: RECIPE_BRAND,
    calories: perServing.calories,
    protein_g: perServing.protein_g,
    carbs_g: perServing.carbs_g,
    fat_g: perServing.fat_g,
    fiber_g: perServing.fiber_g,
    sugar_g: perServing.sugar_g,
    sodium_mg: perServing.sodium_mg,
    servings,
    recipe_path: path,
    source: typed ? 'manual' : 'recipe',
  };
}
