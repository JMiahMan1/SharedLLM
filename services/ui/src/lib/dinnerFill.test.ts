import { describe, expect, it } from 'vitest';
import type { CookbookRecipe } from '../types/api';
import { emptyPlan, PLAN_DAYS } from './mealPlan';
import { easiness, fillWeek, isEasy, styleOf } from './dinnerFill';

/** A made-up cookbook recipe; only what the picker reads matters. */
function recipe(title: string, proteins: string[], minutes: number | null, extra: Partial<CookbookRecipe> = {}): CookbookRecipe {
  return {
    title, path: `Recipes/${title}.md`, tags: [], meal: 'main', proteins, total_minutes: minutes, servings: 4, calories: null,
    ingredients: minutes != null && minutes <= 40 ? 7 : 15, level: null, owner: 'family', owner_label: 'Family', can_change: true,
    source: '', image: null, staples: [], rating: null, ratings: 0, my_rating: null, made: 0, made_by_me: 0, last_made: null,
    ...extra,
  };
}

const COOKBOOK: CookbookRecipe[] = [
  recipe('Chicken Tacos', ['chicken'], 25),
  recipe('Chicken Alfredo Pasta', ['chicken'], 30),
  recipe('Lemon Chicken Sheet Pan', ['chicken'], 35),
  recipe('Chicken Stir Fry', ['chicken'], 20),
  recipe('Beef Tacos', ['beef'], 25),
  recipe('Spaghetti and Meatballs', ['beef'], 40),
  recipe('Slow Roasted Beef Brisket', ['beef'], 360),
  recipe('Pork Fried Rice', ['pork'], 25),
  recipe('Honey Garlic Pork Chops', ['pork'], 30),
  recipe('Shrimp Scampi', ['seafood'], 20),
  recipe('Black Bean Quesadillas', ['vegetarian'], 15),
  recipe('Vegetable Lasagna', ['vegetarian'], 120),
  recipe('Turkey Chili', ['turkey'], 45, { meal: 'soup' }),
  recipe('Chocolate Cake', [], 60, { meal: 'dessert' }),
];

/** A fixed sequence, so a test sees the same draw every run. */
function seeded(seed: number) {
  let s = seed;
  return () => {
    s = (s * 1103515245 + 12345) % 2147483648;
    return s / 2147483648;
  };
}

describe('filling the week', () => {
  it('reads a dish style and how easy it is', () => {
    expect(styleOf(recipe('Beef Tacos', ['beef'], 25))).toBe('mexican');
    expect(styleOf(recipe('Spaghetti and Meatballs', ['beef'], 40))).toBe('pasta');
    expect(styleOf(recipe('Turkey Chili', ['turkey'], 45, { meal: 'soup' }))).toBe('soup');
    expect(isEasy(recipe('Shrimp Scampi', ['seafood'], 20))).toBe(true);
    expect(isEasy(recipe('Slow Roasted Beef Brisket', ['beef'], 360))).toBe(false);
    expect(easiness({ total_minutes: null, ingredients: 0, level: null })).toBe(0.5);
  });

  for (const seed of Array.from({ length: 50 }, (_, i) => i * 37 + 1)) {
    it(`gives a varied, easy week (draw ${seed})`, () => {
      const { plan, filled, easy } = fillWeek(emptyPlan(), COOKBOOK, seeded(seed));
      expect(filled).toEqual([...PLAN_DAYS]);
      const picks = PLAN_DAYS.map((day) => COOKBOOK.find((r) => r.title === plan[day].title)!);
      expect(picks.every((p) => p.meal !== 'dessert')).toBe(true);
      expect(new Set(picks.map((p) => p.title)).size).toBe(7);
      // No protein two nights running, none more than twice.
      for (let i = 1; i < picks.length; i += 1) {
        expect(picks[i].proteins.some((p) => picks[i - 1].proteins.includes(p)), `${picks[i - 1].title} then ${picks[i].title}`).toBe(false);
      }
      const counts = new Map<string, number>();
      picks.flatMap((p) => p.proteins).forEach((p) => counts.set(p, (counts.get(p) ?? 0) + 1));
      expect(Math.max(...counts.values())).toBeLessThanOrEqual(2);
      // No style of dish twice (two kinds of tacos is the same dinner).
      const styles = picks.map(styleOf).filter(Boolean);
      expect(new Set(styles).size).toBe(styles.length);
      // Weeknights are the easy ones.
      for (const day of ['Mon', 'Tue', 'Wed', 'Thu'] as const) {
        expect(isEasy(picks[PLAN_DAYS.indexOf(day)]), `${day}: ${plan[day].title}`).toBe(true);
      }
      expect(easy).toBeGreaterThanOrEqual(4);
    });
  }

  it('fits around the dinners already planned', () => {
    const start = emptyPlan();
    start.Tue = { title: 'Chicken Tacos', cooked: false };
    const { plan, filled } = fillWeek(start, COOKBOOK, seeded(3));
    expect(plan.Tue.title).toBe('Chicken Tacos');
    expect(filled).not.toContain('Tue');
    const near = [plan.Mon.title, plan.Wed.title].map((t) => COOKBOOK.find((r) => r.title === t)!);
    expect(near.some((r) => r.proteins.includes('chicken'))).toBe(false);
    expect(near.some((r) => styleOf(r) === 'mexican')).toBe(false);
    expect(Object.values(plan).filter((d) => d.title === 'Chicken Tacos')).toHaveLength(1);
  });

  it('a re-roll can give a different week', () => {
    const weeks = new Set([1, 2, 3, 4, 5, 6].map((seed) => PLAN_DAYS.map((d) => fillWeek(emptyPlan(), COOKBOOK, seeded(seed)).plan[d].title).join('|')));
    expect(weeks.size).toBeGreaterThan(1);
  });

  it('a small cookbook still fills what it can, without repeating a recipe', () => {
    const { plan, filled } = fillWeek(emptyPlan(), COOKBOOK.slice(0, 3), seeded(1));
    expect(filled).toHaveLength(3);
    expect(new Set(PLAN_DAYS.map((d) => plan[d].title).filter(Boolean)).size).toBe(3);
  });

  it('pushes back what was made in the last two weeks', () => {
    const now = Date.parse('2026-11-13T12:00:00Z');
    const two = [recipe('Shrimp Scampi', ['seafood'], 20, { last_made: '2026-11-12T18:00:00Z' }), recipe('Pork Fried Rice', ['pork'], 20)];
    const start = { ...emptyPlan(), Tue: { title: 'x', cooked: false }, Wed: { title: 'y', cooked: false }, Thu: { title: 'z', cooked: false },
      Fri: { title: 'q', cooked: false }, Sat: { title: 'r', cooked: false }, Sun: { title: 's', cooked: false } };
    expect(fillWeek(start, two, () => 0.99, now).plan.Mon.title).toBe('Pork Fried Rice');
  });
});
