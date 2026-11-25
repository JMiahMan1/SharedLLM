import { describe, expect, it } from 'vitest';
import { chaptersThisWeek, localDayKey, pickCurrentPlan } from './progressNumbers';
import type { BiblePlanEnrollment } from '../../types/api';

function plan(overrides: Partial<BiblePlanEnrollment>): BiblePlanEnrollment {
  return {
    plan_id: 'plan-a', name: 'Plan A', started_on: '2026-01-01', finished_on: null, days: 30, completed: 0,
    completed_days: [], percent: 0, calendar_day: 1, current_day: 1, next_day: 1, behind: 0, finished: false, today: null,
    ...overrides,
  };
}

describe('localDayKey', () => {
  it('names the local calendar day, zero padded', () => {
    expect(localDayKey(new Date(2026, 2, 5, 23, 59))).toBe('2026-03-05');
  });
});

describe('chaptersThisWeek', () => {
  const now = new Date(2026, 9, 13, 21, 0);

  it('sums the last seven local days by date, today included', () => {
    const perDay = {
      '2026-10-06': 50, // eight days back: outside the week
      '2026-10-07': 2,
      '2026-10-09': 3,
      '2026-10-13': 1,
    };
    expect(chaptersThisWeek(perDay, now)).toBe(6);
  });

  it('does not depend on the order of the keys', () => {
    const perDay = { '2026-10-13': 1, '2026-09-01': 40, '2026-10-07': 2, '2026-10-20': 9 };
    expect(chaptersThisWeek(perDay, now)).toBe(3);
  });

  it('crosses a month boundary', () => {
    const early = new Date(2026, 10, 2, 8, 0);
    expect(chaptersThisWeek({ '2026-10-27': 1, '2026-10-26': 5, '2026-11-02': 2 }, early)).toBe(3);
  });

  it('is zero for no window', () => {
    expect(chaptersThisWeek(undefined, now)).toBe(0);
    expect(chaptersThisWeek({}, now)).toBe(0);
  });
});

describe('pickCurrentPlan', () => {
  it('is null with no plans', () => {
    expect(pickCurrentPlan(undefined)).toBeNull();
    expect(pickCurrentPlan([])).toBeNull();
  });

  it('prefers the plan the reader last opened', () => {
    const a = plan({ plan_id: 'a', started_on: '2026-05-01' });
    const b = plan({ plan_id: 'b', started_on: '2026-01-01' });
    expect(pickCurrentPlan([a, b], 'b')?.plan_id).toBe('b');
  });

  it('otherwise takes the plan started last', () => {
    const a = plan({ plan_id: 'a', started_on: '2026-01-01' });
    const b = plan({ plan_id: 'b', started_on: '2026-05-01' });
    expect(pickCurrentPlan([a, b], null)?.plan_id).toBe('b');
  });

  it('puts a plan under way ahead of a finished one, even the last opened', () => {
    const done = plan({ plan_id: 'done', finished: true, started_on: '2026-06-01' });
    const going = plan({ plan_id: 'going', started_on: '2026-01-01' });
    expect(pickCurrentPlan([done, going], 'done')?.plan_id).toBe('going');
  });

  it('falls back to a finished plan when that is all there is', () => {
    const done = plan({ plan_id: 'done', finished: true });
    expect(pickCurrentPlan([done])?.plan_id).toBe('done');
  });
});
