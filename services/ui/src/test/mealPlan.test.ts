import { describe, expect, it } from 'vitest';

import { PLAN_DAYS, buildMealPlan, emptyPlan, parseMealPlan, planLevel, planShareText } from '../lib/mealPlan';

const STORED = [
  '# Meal plan',
  'Category: Recipes',
  'Tags: #meal-plan',
  '',
  '- [ ] Mon · Chicken Chili',
  '- [x] Tue · Pancakes',
  '- [ ] Wed',
  '',
  'Some stray line',
].join('\n');

describe('mealPlan', () => {
  it('parses day lines and ignores everything else', () => {
    const plan = parseMealPlan(STORED);
    expect(plan.Mon).toEqual({ title: 'Chicken Chili', cooked: false });
    expect(plan.Tue).toEqual({ title: 'Pancakes', cooked: true });
    expect(plan.Wed).toEqual({ title: null, cooked: false });
    for (const day of PLAN_DAYS.slice(3)) {
      expect(plan[day]).toEqual({ title: null, cooked: false });
    }
  });

  it('round-trips through buildMealPlan', () => {
    const plan = parseMealPlan(STORED);
    const body = buildMealPlan(plan);
    expect(body.startsWith('#meal-plan')).toBe(true);
    expect(body).toContain('- [ ] Mon · Chicken Chili');
    expect(body).toContain('- [x] Tue · Pancakes');
    expect(parseMealPlan(body)).toEqual(plan);
  });

  it('reads the level tag from a stored recipe', () => {
    const body = '# Pancakes\nCategory: Recipes\nTags: #level-2 #30-minutes\n\nflour';
    expect(planLevel(body)).toBe('2');
    expect(planLevel('no tags here')).toBe('');
  });

  it('starts from an empty plan', () => {
    const plan = emptyPlan();
    for (const day of PLAN_DAYS) {
      expect(plan[day]).toEqual({ title: null, cooked: false });
    }
  });

  it('writes a shareable week of dinners with an optional note', () => {
    const plan = emptyPlan();
    plan.Mon = { title: 'Chicken Chili', cooked: false };
    plan.Tue = { title: 'Pancakes', cooked: true };

    const text = planShareText(plan, 'dinner vote?');
    expect(text.startsWith('dinner vote?\n\n')).toBe(true);
    expect(text).toContain("🍽 This week's dinners:");
    expect(text).toContain('- Mon · Chicken Chili');
    expect(text).toContain('- Tue · Pancakes (cooked)');

    expect(planShareText(emptyPlan())).toBe('🍽 This week has no dinners planned yet.');
  });
});
