/**
 * Reading and writing the week plan note (`Recipes/Meal plan.md`).
 *
 * One place for the Recipes page, the Health hub's "mark cooked" step and the
 * dashboard's health card, so they all read the same note the same way.
 */
import { api } from '../services/api';
import { saveNote } from './noteSave';
import { LEGACY_PLAN_PATH, PLAN_PATH, PLAN_TITLE, buildMealPlan, emptyPlan, parseMealPlan, type PlanDay, type WeeklyPlan } from './mealPlan';

const STORAGE = 'nextcloud';
const RECIPES_DIR = 'Recipes';

export interface StoredPlan {
  plan: WeeklyPlan;
  /** False when the note has never been saved (an empty plan, not an error). */
  exists: boolean;
}

/**
 * The saved plan. A note that is not there yet is an empty plan; a failed read throws.
 *
 * Saves once went to `Recipes/Meal-plan.md` (the file name the server makes
 * from the title) while every read asked for `Recipes/Meal plan.md`, so such
 * a plan is read from there until the next save moves it.
 */
export async function readMealPlan(asUser?: 'admin'): Promise<StoredPlan> {
  const res = await api.readNote(PLAN_TITLE, STORAGE, PLAN_PATH, asUser);
  if (res.status === 'SUCCESS') return { plan: parseMealPlan(res.message ?? ''), exists: true };
  const old = await api.readNote(PLAN_TITLE, STORAGE, LEGACY_PLAN_PATH, asUser);
  if (old.status === 'SUCCESS') return { plan: parseMealPlan(old.message ?? ''), exists: false };
  return { plan: emptyPlan(), exists: false };
}

/**
 * Write the plan note; returns the save's response for the caller's message.
 *
 * Always to `PLAN_PATH` itself: without a path the server names the file
 * from the title (`Meal-plan.md`), which no read ever looks for.
 */
export async function saveMealPlan(plan: WeeklyPlan, exists: boolean, asUser?: 'admin') {
  const saved = await saveNote({
    title: PLAN_TITLE,
    content: buildMealPlan(plan),
    category: RECIPES_DIR,
    previous: { title: PLAN_TITLE, category: RECIPES_DIR, path: PLAN_PATH },
    asUser,
  });
  if (!exists && saved.response.status === 'SUCCESS') {
    await api.deleteNote(PLAN_TITLE, STORAGE, LEGACY_PLAN_PATH, asUser).catch(() => undefined);
  }
  return saved;
}

/**
 * Tick `day` cooked in the saved plan when that day's dinner is `title`.
 * Re-reads the note first so an unsaved edit elsewhere is never written; returns
 * false (and writes nothing) when the plan has a different dinner that day.
 */
export async function markPlanCooked(day: PlanDay, title: string, asUser?: 'admin'): Promise<boolean> {
  const { plan, exists } = await readMealPlan(asUser);
  if (!exists || plan[day].title !== title) return false;
  if (plan[day].cooked) return true;
  const saved = await saveMealPlan({ ...plan, [day]: { ...plan[day], cooked: true } }, exists, asUser);
  if (saved.response.status !== 'SUCCESS') throw new Error(saved.response.message || 'Could not update the week plan');
  return true;
}
