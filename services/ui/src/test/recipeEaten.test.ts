import { describe, expect, it } from 'vitest';
import { eatenDinner } from '../lib/recipeEaten';
import type { FoodEntry } from '../types/api';

function entry(overrides: Partial<FoodEntry> = {}): FoodEntry {
  return {
    id: 1,
    user_id: 'dad',
    day: '2026-10-22',
    meal: 'dinner',
    name: 'Chicken Chili',
    brand: 'Family recipe',
    calories: 350,
    protein_g: 25,
    carbs_g: 30,
    fat_g: 12,
    fiber_g: null,
    sugar_g: null,
    sodium_mg: null,
    servings: 1,
    serving_grams: null,
    barcode: null,
    recipe_path: 'Recipes/Chicken Chili.md',
    source: 'recipe',
    external_id: null,
    created_at: '',
    density: null,
    ...overrides,
  };
}

describe('eatenDinner', () => {
  it('matches the planned recipe by its note path', () => {
    const result = eatenDinner('Recipes/Chicken Chili.md', 'Chicken Chili', [entry()]);
    expect(result.matched?.name).toBe('Chicken Chili');
    expect(result.names).toEqual(['Chicken Chili']);
  });

  it('matches a renamed note by brand and name', () => {
    const result = eatenDinner('Recipes/Chicken Chili (2).md', 'Chicken Chili', [entry()]);
    expect(result.matched?.id).toBe(1);
  });

  it('reports a different dinner as not the plan', () => {
    const result = eatenDinner('Recipes/Chicken Chili.md', 'Chicken Chili', [
      entry({ id: 2, name: 'Pizza', brand: '', recipe_path: null }),
    ]);
    expect(result.matched).toBeNull();
    expect(result.entries).toHaveLength(1);
    expect(result.names).toEqual(['Pizza']);
  });

  it('ignores non-dinner entries and dedupes names', () => {
    const result = eatenDinner('Recipes/Chicken Chili.md', 'Chicken Chili', [
      entry({ id: 1, meal: 'lunch', name: 'Salad', recipe_path: null, brand: '' }),
      entry({ id: 2, name: 'Pizza', brand: '', recipe_path: null }),
      entry({ id: 3, name: 'Pizza', brand: '', recipe_path: null }),
    ]);
    expect(result.entries).toHaveLength(2);
    expect(result.names).toEqual(['Pizza']);
  });

  it('is empty when nothing was logged', () => {
    const result = eatenDinner('Recipes/Chicken Chili.md', 'Chicken Chili', []);
    expect(result.entries).toEqual([]);
    expect(result.matched).toBeNull();
  });
});
