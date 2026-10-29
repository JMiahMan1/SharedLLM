import { describe, expect, it } from 'vitest';

import { emptyPlan } from '../lib/mealPlan';
import { isoDay, mirrorItems, plannedPushDays, weekDates } from '../lib/skylightMeals';

describe('skylightMeals', () => {
  it('maps the plan week onto Monday-based calendar dates', () => {
    // 2026-10-28 is a Wednesday; the week runs 10-26 (Mon) .. 11-01 (Sun).
    const dates = weekDates(new Date(2026, 9, 28));
    expect(dates.Mon).toBe('2026-10-26');
    expect(dates.Wed).toBe('2026-10-28');
    expect(dates.Sun).toBe('2026-11-01');
  });

  it('keeps a local day from shifting across the UTC boundary', () => {
    // Late evening local time must not roll over to tomorrow's date.
    expect(isoDay(new Date(2026, 9, 28, 23, 30))).toBe('2026-10-28');
  });

  it('lists only the planned dinners, each with its date', () => {
    const plan = emptyPlan();
    plan.Mon = { title: 'Chicken Chili', cooked: false };
    plan.Thu = { title: 'Pancakes', cooked: true };

    const days = plannedPushDays(plan, new Date(2026, 9, 28));
    expect(days).toEqual([
      { day: 'Mon', date: '2026-10-26', title: 'Chicken Chili' },
      { day: 'Thu', date: '2026-10-29', title: 'Pancakes' },
    ]);
  });

  it('returns no days when nothing is planned', () => {
    expect(plannedPushDays(emptyPlan(), new Date(2026, 9, 28))).toEqual([]);
  });

  it('reads the shopping list into mirror item texts', () => {
    const body = '#shopping-list\n\n- [ ] 2 cups flour\n- [x] salt\n\nMade from the week\'s plan.';
    expect(mirrorItems(body)).toEqual(['2 cups flour', 'salt']);
  });
});
