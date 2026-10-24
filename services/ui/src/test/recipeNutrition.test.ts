import { describe, expect, it } from 'vitest';
import {
  parseRecipeNutrition,
  portionLabel,
  portionTotals,
  recipeFoodEntry,
  RECIPE_BRAND,
} from '../lib/recipeNutrition';
import { planDayOf } from '../lib/mealPlan';

// The exact shape scripts/import_recipes.py renders.
const IMPORTED = [
  'Tags: #level-2',
  '',
  '## Ingredients',
  '- [ ] 2 lb beef',
  '',
  '## Nutrition',
  '*Per serving, as stated by the submitter — foodcom:* 350.2 kcal · 12 g fat · 3.1 g sat fat · 45 mg cholesterol · 600 mg sodium · 30 g carbs · 2 g fiber · 5 g sugar · 25 g protein',
  '',
].join('\n');

describe('parseRecipeNutrition', () => {
  it('reads the imported per-serving line', () => {
    expect(parseRecipeNutrition(IMPORTED)).toEqual({
      status: 'ok',
      perServing: {
        calories: 350.2,
        protein_g: 25,
        carbs_g: 30,
        fat_g: 12,
        fiber_g: 2,
        sugar_g: 5,
        sodium_mg: 600,
        source: 'foodcom',
      },
    });
  });

  it('does not take saturated fat for fat', () => {
    const body = '## Nutrition\n*Per serving — foodcom:* 3 g sat fat · 200 kcal · 9 g fat · 10 g carbs · 4 g protein';
    const result = parseRecipeNutrition(body);
    expect(result.status === 'ok' && result.perServing.fat_g).toBe(9);
  });

  it('tolerates units repeated from page data and leaves unstated extras null', () => {
    const body = '## Nutrition\n*Per serving, as stated by the submitter — foodcom:* 410 kcal · 15.5 g g fat · 40 g g carbs · 22 g g protein';
    const result = parseRecipeNutrition(body);
    expect(result).toMatchObject({ status: 'ok', perServing: { calories: 410, fat_g: 15.5, carbs_g: 40, protein_g: 22 } });
    expect(result.status === 'ok' && result.perServing.fiber_g).toBeNull();
  });

  it('is missing when there is no section or the submitter stated none', () => {
    expect(parseRecipeNutrition('## Ingredients\n- [ ] eggs')).toEqual({ status: 'missing' });
    expect(parseRecipeNutrition('## Nutrition\n*Nutrition was not stated by the submitter.*')).toEqual({ status: 'missing' });
  });

  it('names what is absent instead of filling a zero', () => {
    expect(parseRecipeNutrition('## Nutrition\n*Per serving — foodcom:* 300 kcal · 10 g fat')).toEqual({
      status: 'incomplete',
      missing: ['protein', 'carbs'],
    });
  });

  it('stops at the next heading', () => {
    const body = '## Nutrition\n\n## Notes\n*Per serving — foodcom:* 300 kcal · 10 g fat · 5 g carbs · 3 g protein';
    expect(parseRecipeNutrition(body)).toEqual({ status: 'missing' });
  });
});

describe('portions', () => {
  it('labels halves', () => {
    expect([0.5, 1, 1.5, 2].map(portionLabel)).toEqual(['½', '1', '1½', '2']);
  });

  it('scales a portion for display', () => {
    expect(portionTotals({ calories: 350.2, protein_g: 25, carbs_g: 30, fat_g: 12 }, 1.5)).toEqual({
      calories: 525,
      protein_g: 37.5,
      carbs_g: 45,
      fat_g: 18,
    });
  });
});

describe('recipeFoodEntry', () => {
  it('stores per serving with the portion as the multiplier and links the note', () => {
    const parsed = parseRecipeNutrition(IMPORTED);
    if (parsed.status !== 'ok') throw new Error('expected nutrition');
    const entry = recipeFoodEntry({
      title: 'Chicken Chili',
      path: 'Recipes/Chicken Chili.md',
      perServing: parsed.perServing,
      servings: 1.5,
      day: '2026-10-21',
      meal: 'dinner',
    });
    expect(entry).toMatchObject({
      day: '2026-10-21',
      meal: 'dinner',
      name: 'Chicken Chili',
      brand: RECIPE_BRAND,
      calories: 350.2,
      protein_g: 25,
      servings: 1.5,
      recipe_path: 'Recipes/Chicken Chili.md',
      source: 'recipe',
    });
  });

  it('marks typed values as manual', () => {
    const entry = recipeFoodEntry({
      title: 'Soup',
      path: 'Recipes/Soup.md',
      perServing: { calories: 200, protein_g: 5, carbs_g: 20, fat_g: 8, fiber_g: null, sugar_g: null, sodium_mg: null },
      servings: 1,
      day: '2026-10-21',
      meal: 'lunch',
      typed: true,
    });
    expect(entry.source).toBe('manual');
    expect(entry.recipe_path).toBe('Recipes/Soup.md');
  });
});

describe('planDayOf', () => {
  it('maps local weekdays onto the Monday-first plan', () => {
    expect(planDayOf(new Date(2026, 9, 19))).toBe('Mon');
    expect(planDayOf(new Date(2026, 9, 25))).toBe('Sun');
  });
});
