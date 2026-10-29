/**
 * Turning the local plan and shopping list into Skylight Meals payloads.
 *
 * The Skylight Meals tab plans a recipe into a day ("sitting"), so a push needs
 * each planned dinner's calendar date, not just its weekday. The plan note
 * stores weekdays, so this maps them onto the Monday-based week the plan
 * describes. Everything here is pure; the Recipes page reads each recipe's body
 * and posts the result.
 */

import { PLAN_DAYS, type PlanDay, type WeeklyPlan } from './mealPlan';
import { parseShoppingList } from './shoppingList';

/** A local calendar day as YYYY-MM-DD (never UTC-shifted). */
export function isoDay(date: Date): string {
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

/** The Monday-based dates of the week that contains `today`, one per plan day. */
export function weekDates(today = new Date()): Record<PlanDay, string> {
  const monday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  const out = {} as Record<PlanDay, string>;
  PLAN_DAYS.forEach((day, index) => {
    const date = new Date(monday);
    date.setDate(monday.getDate() + index);
    out[day] = isoDay(date);
  });
  return out;
}

/** One planned dinner on its calendar date, ready to read a body for. */
export interface PlannedPushDay {
  day: PlanDay;
  date: string;
  title: string;
}

/** The planned dinners that name a recipe, each with the date it falls on. */
export function plannedPushDays(plan: WeeklyPlan, today = new Date()): PlannedPushDay[] {
  const dates = weekDates(today);
  const out: PlannedPushDay[] = [];
  for (const day of PLAN_DAYS) {
    const title = plan[day].title;
    if (title) out.push({ day, date: dates[day], title });
  }
  return out;
}

/** The item texts of a shopping-list note, for the Skylight grocery mirror. */
export function mirrorItems(shoppingBody: string): string[] {
  return parseShoppingList(shoppingBody).map((item) => item.text);
}
