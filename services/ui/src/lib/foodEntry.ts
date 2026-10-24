/**
 * A food-database result turned into a food log entry at a chosen portion.
 *
 * Search results carry nutrition per 100 g; an entry stores it per serving
 * (the portion in grams) with `servings` as the multiplier. Used by the
 * Nutrition search/barcode add and by voice logging, so both scale alike.
 */
import type { FoodEntry, FoodSearchItem } from '../types/api';

export function foodEntryFromSearch(
  item: FoodSearchItem,
  { grams, servings, day, meal, source }: { grams: number; servings: number; day: string; meal: FoodEntry['meal']; source?: string },
): Partial<FoodEntry> & { day: string; name: string; meal: FoodEntry['meal'] } {
  const per100 = item.per_100g;
  if (per100.calories === null) throw new Error(`${item.name} has no calorie data`);
  if (!Number.isFinite(grams) || grams <= 0) throw new Error('Enter a portion in grams');
  const factor = grams / 100;
  const fromSeed = item.source === 'usda_fdc';
  return {
    day,
    meal,
    name: item.name,
    brand: item.brand,
    calories: (per100.calories ?? 0) * factor,
    protein_g: (per100.protein_g ?? 0) * factor,
    carbs_g: (per100.carbs_g ?? 0) * factor,
    fat_g: (per100.fat_g ?? 0) * factor,
    fiber_g: per100.fiber_g !== null ? per100.fiber_g * factor : null,
    sugar_g: per100.sugar_g !== null ? per100.sugar_g * factor : null,
    sodium_mg: per100.sodium_mg !== null ? per100.sodium_mg * factor : null,
    servings,
    serving_grams: grams,
    barcode: fromSeed ? null : item.code,
    source: source ?? (fromSeed ? 'usda_fdc' : 'openfoodfacts'),
  };
}

/** The portion a result suggests: its first household measure, else its serving weight. */
export function suggestedGrams(item: FoodSearchItem): number | null {
  return item.portions?.[0]?.grams ?? item.serving_grams ?? null;
}
