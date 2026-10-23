/**
 * The family's week plan, stored as a note under `Recipes/Meal plan.md`.
 *
 * The note carries a `#meal-plan` tag and one checklist line per day
 * (`- [ ] Mon · Chicken Chili`); ticking a day marks it cooked. Only these
 * seven lines are read back and rewritten, so the note stays small and every
 * other note in the shelf stays a recipe.
 */

export const PLAN_TITLE = 'Meal plan';
export const PLAN_PATH = 'Recipes/Meal plan.md';
export const PLAN_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;

export type PlanDay = (typeof PLAN_DAYS)[number];

export interface DayPlan {
  title: string | null;
  cooked: boolean;
}

export type WeeklyPlan = Record<PlanDay, DayPlan>;

export function emptyPlan(): WeeklyPlan {
  return Object.fromEntries(PLAN_DAYS.map((day) => [day, { title: null, cooked: false }])) as WeeklyPlan;
}

const DAY_LINE = /^-\s*\[([ xX])\]\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:\s*·\s*(.+?))?\s*$/i;

/** Read the seven day lines out of a stored note; anything else is ignored. */
export function parseMealPlan(body: string): WeeklyPlan {
  const plan = emptyPlan();
  for (const raw of body.split('\n')) {
    const match = raw.match(DAY_LINE);
    if (!match) continue;
    const day = PLAN_DAYS.find((candidate) => candidate.toLowerCase() === match[2].toLowerCase());
    if (!day) continue;
    plan[day] = { cooked: match[1].toLowerCase() === 'x', title: match[3]?.trim() || null };
  }
  return plan;
}

/** The note body the plan view writes (the notes header is added on save). */
export function buildMealPlan(plan: WeeklyPlan): string {
  const lines = ['#meal-plan', ''];
  for (const day of PLAN_DAYS) {
    const entry = plan[day];
    lines.push(`- [${entry.cooked ? 'x' : ' '}] ${day}${entry.title ? ` · ${entry.title}` : ''}`);
  }
  return lines.join('\n');
}

/** The `#level-N` tag an imported recipe carries, when the body has one. */
export function planLevel(body: string): string {
  const match = body.match(/#level-([123])\b/);
  return match ? match[1] : '';
}

/** Take one random title out of the pool (the week's surprise fill). */
export function pickSurprise(pool: string[]): string | undefined {
  if (pool.length === 0) return undefined;
  return pool.splice(Math.floor(Math.random() * pool.length), 1)[0];
}

/** The chat message a shared week plan becomes: one line per planned day. */
export function planShareText(plan: WeeklyPlan, note?: string): string {
  const lines: string[] = [];
  for (const day of PLAN_DAYS) {
    const title = plan[day].title;
    if (!title) continue;
    lines.push(`- ${day} · ${plan[day].cooked ? `${title} (cooked)` : title}`);
  }
  const body =
    lines.length === 0
      ? '🍽 This week has no dinners planned yet.'
      : `🍽 This week's dinners:\n${lines.join('\n')}`;
  const intro = (note ?? '').trim();
  return intro ? `${intro}\n\n${body}` : body;
}
