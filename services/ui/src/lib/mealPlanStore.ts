/**
 * Reading and writing the week plan note (`Recipes/Meal plan.md`).
 *
 * One place for the Recipes page, the Health hub's "mark cooked" step and the
 * dashboard's health card, so they all read the same note the same way.
 */
import { api } from '../services/api';
import { saveNote } from './noteSave';
import { PLAN_PATH, PLAN_TITLE, buildMealPlan, emptyPlan, parseMealPlan, type PlanDay, type WeeklyPlan } from './mealPlan';

const STORAGE = 'nextcloud';
const RECIPES_DIR = 'Recipes';

export interface StoredPlan {
  plan: WeeklyPlan;
  /** False when the note has never been saved (an empty plan, not an error). */
  exists: boolean;
}

/** The saved plan. A note that is not there yet is an empty plan; a failed read throws. */
export async function readMealPlan(asUser?: 'admin'): Promise<StoredPlan> {
  const res = await api.readNote(PLAN_TITLE, STORAGE, PLAN_PATH, asUser);
  if (res.status === 'SUCCESS') return { plan: parseMealPlan(res.message ?? ''), exists: true };
  return { plan: emptyPlan(), exists: false };
}

/** Write the plan note; returns the save's response for the caller's message. */
export async function saveMealPlan(plan: WeeklyPlan, exists: boolean, asUser?: 'admin') {
  return saveNote({
    title: PLAN_TITLE,
    content: buildMealPlan(plan),
    category: RECIPES_DIR,
    previous: exists ? { title: PLAN_TITLE, category: RECIPES_DIR, path: PLAN_PATH } : undefined,
    asUser,
  });
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
