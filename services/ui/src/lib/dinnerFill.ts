import type { CookbookRecipe } from '../types/api';
import { PLAN_DAYS, type PlanDay, type WeeklyPlan } from './mealPlan';

/**
 * "Fill the gaps" for the week's dinners: pick a recipe for every empty day so
 * the week is easy to cook and not the same dinner twice over.
 *
 * - **Easy first.** Weeknights favour quick, short recipes (total time,
 *   ingredient count and the recipe's level). Weekends may take longer ones.
 * - **Variety.** No protein two nights running and no more than twice a week;
 *   no style of dish (pasta, tacos, stir fry, soup...) twice in a week. The
 *   dinners already planned count too.
 * - **Still a surprise.** The pick for a day is drawn from its best few, so
 *   pressing it again gives another good week, not the same one.
 *
 * These are weights, not filters: a small cookbook still fills every day, it
 * just repeats as little as it can.
 */

/** Monday to Thursday: the nights easy matters most. Friday and the weekend may take longer. */
const WEEKNIGHTS: ReadonlySet<PlanDay> = new Set(['Mon', 'Tue', 'Wed', 'Thu']);
/** How many of a day's best candidates the pick is drawn from. */
const DRAW_FROM = 3;
/** A recipe made this recently is pushed back. */
const RECENT_DAYS = 14;

/** Kinds of dish, read from a recipe's title and tags. The first that matches is its style. */
const STYLES: ReadonlyArray<[string, RegExp]> = [
  ['pizza', /\b(pizza|flatbread|calzone)\b/],
  ['pasta', /\b(pasta|spaghetti|lasagn[ae]|macaroni|mac and cheese|penne|fettuccine|alfredo|ziti|noodle casserole|tortellini|ravioli|gnocchi|carbonara)\b/],
  ['mexican', /\b(taco|tacos|burrito|enchilada|quesadilla|fajita|nacho|tamale|mexican|tex-mex|salsa verde)\b/],
  ['asian', /\b(stir[- ]?fry|teriyaki|curry|fried rice|lo mein|ramen|pad thai|orange chicken|sesame|asian|thai|chinese|korean|bulgogi|katsu|pho)\b/],
  ['soup', /\b(soup|stew|chili|chowder|bisque|gumbo)\b/],
  ['sandwich', /\b(sandwich|sandwiches|burger|burgers|sliders|wrap|wraps|sub|hoagie|panini|sloppy joe|grilled cheese|pita)\b/],
  ['casserole', /\b(casserole|bake|baked ziti|pot pie|shepherd'?s pie|hotdish)\b/],
  ['salad', /\b(salad|bowl|bowls)\b/],
  ['breakfast', /\b(pancake|waffle|french toast|omelet|omelette|frittata|quiche|breakfast)\b/],
  ['grill', /\b(grill|grilled|bbq|barbecue|kebab|skewer)\b/],
];

export function styleOf(recipe: Pick<CookbookRecipe, 'title' | 'tags' | 'meal'>): string {
  const text = `${recipe.title} ${recipe.tags.join(' ')}`.toLowerCase();
  const style = STYLES.find(([, pattern]) => pattern.test(text));
  if (style) return style[0];
  return recipe.meal === 'soup' ? 'soup' : '';
}

/** 0 (a project) to 1 (dinner in half an hour, few ingredients). Unknowns count as middling. */
export function easiness(recipe: Pick<CookbookRecipe, 'total_minutes' | 'ingredients' | 'level'>): number {
  const minutes = recipe.total_minutes;
  const time = minutes == null ? 0.5 : minutes <= 30 ? 1 : minutes <= 45 ? 0.8 : minutes <= 60 ? 0.5 : minutes <= 90 ? 0.2 : 0;
  const count = recipe.ingredients;
  const items = !count ? 0.5 : count <= 8 ? 1 : count <= 12 ? 0.7 : count <= 16 ? 0.4 : 0.1;
  const level = recipe.level == null ? 0.5 : recipe.level <= 1 ? 1 : recipe.level === 2 ? 0.5 : 0;
  return time * 0.5 + items * 0.3 + level * 0.2;
}

/** A dinner a weeknight can count on: about 45 minutes or less, and not a long list. */
export function isEasy(recipe: Pick<CookbookRecipe, 'total_minutes' | 'ingredients' | 'level'>): boolean {
  return easiness(recipe) >= 0.7;
}

function daysSince(iso: string | null, now: number): number | null {
  if (!iso) return null;
  const when = Date.parse(iso);
  return Number.isFinite(when) ? (now - when) / 86_400_000 : null;
}

interface Placed {
  proteins: string[];
  style: string;
}

function score(
  recipe: CookbookRecipe,
  day: PlanDay,
  week: Partial<Record<PlanDay, Placed>>,
  now: number,
): number {
  const index = PLAN_DAYS.indexOf(day);
  const neighbours = [PLAN_DAYS[index - 1], PLAN_DAYS[index + 1]].filter(Boolean).map((d) => week[d]).filter(Boolean) as Placed[];
  const placed = Object.values(week).filter(Boolean) as Placed[];
  const style = styleOf(recipe);
  // On a weeknight an easy dinner beats a merely new one: being easy outweighs repeating a protein once.
  let total = WEEKNIGHTS.has(day) ? easiness(recipe) * 3 + (isEasy(recipe) ? 3 : 0) : easiness(recipe);

  for (const protein of recipe.proteins) {
    if (neighbours.some((n) => n.proteins.includes(protein))) total -= 4;
    const times = placed.filter((p) => p.proteins.includes(protein)).length;
    if (times >= 2) total -= 6;
    else if (times === 1) total -= 1.5;
  }
  if (style) {
    const times = placed.filter((p) => p.style === style).length;
    total -= times * 3;
    if (neighbours.some((n) => n.style === style)) total -= 2;
  }
  // Family favourites a little ahead; something just made a little back.
  if (recipe.rating != null) total += (recipe.rating - 3) * 0.3;
  const since = daysSince(recipe.last_made, now);
  if (since != null && since < RECENT_DAYS) total -= 1.5 * (1 - since / RECENT_DAYS);
  return total;
}

export interface FillResult {
  plan: WeeklyPlan;
  /** The days given a dinner. */
  filled: PlanDay[];
  /** Of those, how many are easy. */
  easy: number;
}

/**
 * Fill every empty day of ``plan`` from ``recipes``. Dinners come from the
 * mains and soups; with none of those left, from anything not yet planned.
 */
export function fillWeek(
  plan: WeeklyPlan,
  recipes: CookbookRecipe[],
  random: () => number = Math.random,
  now: number = Date.now(),
): FillResult {
  const byTitle = new Map(recipes.map((r) => [r.title.toLowerCase(), r]));
  const planned = new Set(PLAN_DAYS.map((d) => plan[d].title?.toLowerCase()).filter(Boolean) as string[]);
  const dinners = recipes.filter((r) => (r.meal === 'main' || r.meal === 'soup') && !planned.has(r.title.toLowerCase()));
  let pool = dinners.length ? dinners : recipes.filter((r) => !planned.has(r.title.toLowerCase()));

  const week: Partial<Record<PlanDay, Placed>> = {};
  for (const day of PLAN_DAYS) {
    const known = plan[day].title ? byTitle.get(plan[day].title!.toLowerCase()) : undefined;
    if (known) week[day] = { proteins: known.proteins, style: styleOf(known) };
  }

  const next: WeeklyPlan = { ...plan };
  const filled: PlanDay[] = [];
  let easy = 0;
  // Weeknights first: they need the easy dinners, so they choose before the weekend takes them.
  const order = [...PLAN_DAYS].sort((a, b) => Number(WEEKNIGHTS.has(b)) - Number(WEEKNIGHTS.has(a)));
  for (const day of order) {
    if (next[day].title || pool.length === 0) continue;
    const ranked = pool
      .map((recipe) => ({ recipe, value: score(recipe, day, week, now) }))
      .sort((a, b) => b.value - a.value);
    const best = ranked[0].value;
    // Draw among the top few that are nearly as good, so a re-roll differs without getting worse.
    const near = ranked.slice(0, DRAW_FROM).filter((c) => c.value >= best - 1);
    const pick = near[Math.min(near.length - 1, Math.floor(random() * near.length))].recipe;
    pool = pool.filter((r) => r !== pick);
    next[day] = { title: pick.title, cooked: false };
    week[day] = { proteins: pick.proteins, style: styleOf(pick) };
    filled.push(day);
    if (isEasy(pick)) easy += 1;
  }
  filled.sort((a, b) => PLAN_DAYS.indexOf(a) - PLAN_DAYS.indexOf(b));
  return { plan: next, filled, easy };
}
