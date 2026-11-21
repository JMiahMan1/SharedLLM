import { afterEach, describe, expect, it } from 'vitest';
import { parseDay, type Ymd } from './dates';
import { buildView, latestLove, readRecord, unseenLove, type BelovedKind, type BelovedRecord, type Opened } from './records';
import { GENERIC_TITLE } from './reminders';
import {
  EVENING_IDEAS,
  MAX_PLAN_REMINDERS,
  PRIVATE_REMINDER,
  addMonths,
  countIn,
  daysSince,
  markedDays,
  markedToday,
  marksOn,
  monthGrid,
  nudgeAfter,
  openPlans,
  planPrivateReminders,
  setNudgeAfter,
  sinceLabel,
  togetherFromPlan,
} from './justus';

const ymd = (text: string): Ymd => parseDay(text) as Ymd;
let n = 0;

function opened(kind: BelovedKind, record: BelovedRecord, extra: Partial<Opened> = {}): Opened {
  n += 1;
  return { id: `${kind}${String(n).padStart(8, '0')}`, kind, at: 1, updatedAt: 1, by: 'robin', own: false, byName: 'Robin', record, ...extra };
}

const marked = (...days: string[]) => days.map((day) => opened('together', { b: 'together', day }));
const view = (...items: Opened[]) => buildView(items);

/** Words that must never be in a reminder a lock screen or a child could see. */
const REVEALING = /intima|sex|night|together|bed|romanc|love|kiss|special night/i;

describe('Just us: reading records', () => {
  it('keeps only well-shaped marked days and plans', () => {
    expect(readRecord('together', { b: 'together', day: '2026-11-02', note: 'x' })).toEqual({ b: 'together', day: '2026-11-02', note: 'x' });
    expect(readRecord('together', { b: 'together', day: '2026-02-30' })).toBeNull();
    expect(readRecord('together', { b: 'plan', day: '2026-11-02' })).toBeNull();
    expect(readRecord('plan', { b: 'plan', day: '2026-11-20', time: '19:30', idea: 'candles', doneAt: 5 })).toEqual({
      b: 'plan', day: '2026-11-20', time: '19:30', idea: 'candles', doneAt: 5,
    });
    expect(readRecord('plan', { b: 'plan', day: '2026-11-20', time: '25:00' })).toEqual({ b: 'plan', day: '2026-11-20' });
    expect(readRecord('plan', { b: 'plan', time: '19:00' })).toBeNull();
  });

  it('puts them in their own lists, which the home page does not read', () => {
    const v = view(...marked('2026-11-01'), opened('plan', { b: 'plan', day: '2026-11-20' }));
    expect(v.together).toHaveLength(1);
    expect(v.plans).toHaveLength(1);
    expect(v.notes).toEqual([]);
  });

  it('never lights the heart on the chat bar', () => {
    const rows = [
      { kind: 'together' as const, own: false, at: 100 },
      { kind: 'plan' as const, own: false, at: 200 },
    ];
    expect(unseenLove(rows, 0)).toBe(0);
    expect(latestLove(rows)).toBe(0);
  });
});

describe('Just us: days since and counts', () => {
  const today = ymd('2026-11-16');

  it('counts days since the last marked day, ignoring a future one and duplicates', () => {
    expect(daysSince(view(), today)).toBeNull();
    expect(daysSince(view(...marked('2026-11-10', '2026-11-13', '2026-11-13')), today)).toBe(3);
    expect(daysSince(view(...marked('2026-11-16')), today)).toBe(0);
    expect(daysSince(view(...marked('2026-11-10', '2026-12-01')), today)).toBe(6);
  });

  it('counts across a month and a year boundary by calendar days', () => {
    expect(daysSince(view(...marked('2025-12-31')), ymd('2026-01-01'))).toBe(1);
    expect(daysSince(view(...marked('2024-02-28')), ymd('2024-03-01'))).toBe(2);
  });

  it('labels it plainly', () => {
    expect(sinceLabel(0)).toBe('Today');
    expect(sinceLabel(1)).toBe('Yesterday');
    expect(sinceLabel(9)).toBe('9 days ago');
  });

  it('counts distinct days this month and this year', () => {
    const v = view(...marked('2026-11-01', '2026-11-01', '2026-11-12', '2026-10-30', '2025-11-05', '2026-11-30'));
    expect(countIn(v, today, 2026, 11)).toBe(2);
    expect(countIn(v, today, 2026)).toBe(3);
    expect(countIn(v, today, 2025)).toBe(1);
    expect(markedDays(v, today)).toHaveLength(4);
  });

  it('knows whether today is marked', () => {
    expect(markedToday(view(...marked('2026-11-16')), today)).toBe(true);
    expect(markedToday(view(...marked('2026-11-15')), today)).toBe(false);
  });
});

describe('Just us: the month on screen', () => {
  it('moves between months across years', () => {
    expect(addMonths({ y: 2026, m: 12 }, 1)).toEqual({ y: 2027, m: 1 });
    expect(addMonths({ y: 2026, m: 1 }, -1)).toEqual({ y: 2025, m: 12 });
    expect(addMonths({ y: 2026, m: 5 }, -17)).toEqual({ y: 2024, m: 12 });
  });

  it('lays a month out Sunday first with marks and open plans', () => {
    const v = view(...marked('2026-11-03'), opened('plan', { b: 'plan', day: '2026-11-20' }), opened('plan', { b: 'plan', day: '2026-11-21', doneAt: 9 }));
    const weeks = monthGrid(v, 2026, 11, ymd('2026-11-16'));
    // 1 November 2026 is a Sunday; 30 days fill five weeks.
    expect(weeks).toHaveLength(5);
    expect(weeks.every((w) => w.length === 7)).toBe(true);
    expect(weeks[0][0]?.key).toBe('2026-11-01');
    const cells = weeks.flat().filter((c) => c !== null);
    expect(cells).toHaveLength(30);
    expect(cells.find((c) => c.key === '2026-11-03')?.marked).toBe(true);
    expect(cells.find((c) => c.key === '2026-11-20')?.planned).toBe(true);
    expect(cells.find((c) => c.key === '2026-11-21')?.planned).toBe(false);
    expect(cells.filter((c) => c.today).map((c) => c.key)).toEqual(['2026-11-16']);
    // February 2027 starts on a Monday.
    expect(monthGrid(view(), 2027, 2, ymd('2026-11-16'))[0][0]).toBeNull();
  });

  it('counts every mark on a day, and a day with several still counts once', () => {
    const v = view(
      opened('together', { b: 'together', day: '2026-11-03' }, { at: 300 }),
      opened('together', { b: 'together', day: '2026-11-03', note: 'first' }, { at: 100, own: true }),
      opened('together', { b: 'together', day: '2026-11-03' }, { at: 200 }),
      ...marked('2026-11-05'),
    );
    const cells = monthGrid(v, 2026, 11, ymd('2026-11-16')).flat().filter((c) => c !== null);
    expect(cells.find((c) => c.key === '2026-11-03')).toMatchObject({ marked: true, marks: 3 });
    expect(cells.find((c) => c.key === '2026-11-05')).toMatchObject({ marked: true, marks: 1 });
    expect(cells.find((c) => c.key === '2026-11-04')).toMatchObject({ marked: false, marks: 0 });
    // Distinct days for the counts and "days since".
    expect(countIn(v, ymd('2026-11-16'), 2026, 11)).toBe(2);
  });

  it('lists the marks of one day oldest first, each its own record', () => {
    const v = view(
      opened('together', { b: 'together', day: '2026-11-03' }, { at: 300 }),
      opened('together', { b: 'together', day: '2026-11-03', note: 'first' }, { at: 100 }),
      ...marked('2026-11-04'),
    );
    const marks = marksOn(v, '2026-11-03');
    expect(marks.map((m) => m.at)).toEqual([100, 300]);
    expect(marks[0].record.note).toBe('first');
    expect(new Set(marks.map((m) => m.id)).size).toBe(2);
    expect(marksOn(v, '2026-11-09')).toEqual([]);
  });
});

describe('Just us: plans', () => {
  it('lists open plans soonest first and turns a finished one into a marked day', () => {
    const v = view(
      opened('plan', { b: 'plan', day: '2026-12-01' }),
      opened('plan', { b: 'plan', day: '2026-11-20', time: '20:00' }),
      opened('plan', { b: 'plan', day: '2026-11-20', time: '18:00' }),
      opened('plan', { b: 'plan', day: '2026-11-10', doneAt: 3 }),
    );
    expect(openPlans(v).map((p) => `${p.record.day} ${p.record.time ?? ''}`)).toEqual(['2026-11-20 18:00', '2026-11-20 20:00', '2026-12-01 ']);
    expect(togetherFromPlan({ b: 'plan', day: '2026-11-20', note: 'secret' })).toEqual({ b: 'together', day: '2026-11-20' });
  });

  it('offers only gentle ideas', () => {
    expect(EVENING_IDEAS.length).toBeGreaterThanOrEqual(5);
    for (const idea of EVENING_IDEAS) expect(idea.title).not.toMatch(/sex|intima|naked|lingerie/i);
  });
});

describe('Just us: reminders', () => {
  const today = ymd('2026-11-16');
  const now = new Date(2026, 10, 16, 12, 0).getTime();

  it('reminds both phones on the morning of a plan, or an hour before an early one', () => {
    const v = view(
      opened('plan', { b: 'plan', day: '2026-11-20' }),
      opened('plan', { b: 'plan', day: '2026-11-21', time: '07:30' }),
      opened('plan', { b: 'plan', day: '2026-11-22', time: '19:00' }),
      opened('plan', { b: 'plan', day: '2026-11-16' }), // this morning is past
      opened('plan', { b: 'plan', day: '2026-11-23', doneAt: 1 }),
    );
    const out = planPrivateReminders(v, now, today, 0);
    expect(out.map((r) => r.at)).toEqual([
      new Date(2026, 10, 20, 8, 0).getTime(),
      new Date(2026, 10, 21, 6, 30).getTime(),
      new Date(2026, 10, 22, 8, 0).getTime(),
    ]);
  });

  it('reminds this phone N days after the last marked day, and moves when a day is marked', () => {
    const before = planPrivateReminders(view(...marked('2026-11-13')), now, today, 5);
    expect(before.map((r) => r.at)).toEqual([new Date(2026, 10, 18, 19, 0).getTime()]);
    const after = planPrivateReminders(view(...marked('2026-11-13', '2026-11-16')), now, today, 5);
    expect(after.map((r) => r.at)).toEqual([new Date(2026, 10, 21, 19, 0).getTime()]);
    // Same id, so the phone replaces the old one.
    expect(after[0].id).toBe(before[0].id);
  });

  it('is off by default, and never nags once the day has passed or nothing is marked', () => {
    expect(planPrivateReminders(view(...marked('2026-11-13')), now, today, 0)).toEqual([]);
    expect(planPrivateReminders(view(...marked('2026-11-01')), now, today, 7)).toEqual([]);
    expect(planPrivateReminders(view(), now, today, 7)).toEqual([]);
  });

  it('caps the plan reminders', () => {
    const many = Array.from({ length: 20 }, (_, i) => opened('plan', { b: 'plan', day: `2026-12-${String(i + 1).padStart(2, '0')}` }));
    expect(planPrivateReminders(view(...many), now, today, 0)).toHaveLength(MAX_PLAN_REMINDERS);
  });

  it('says nothing revealing, ever', () => {
    const v = view(...marked('2026-11-15'), opened('plan', { b: 'plan', day: '2026-11-20', idea: 'hotel', note: 'Our night together' }));
    const out = planPrivateReminders(v, now, today, 3);
    expect(out).toHaveLength(2);
    for (const r of out) {
      expect(r.title).toBe(GENERIC_TITLE);
      expect(r.body).toBe(PRIVATE_REMINDER);
      expect(`${r.title} ${r.body}`).not.toMatch(REVEALING);
      expect(r.id).toBeGreaterThan(0);
    }
  });
});

describe('Just us: this phone\'s reminder choice', () => {
  afterEach(() => localStorage.clear());

  it('is off by default and keeps only the offered choices', () => {
    expect(nudgeAfter()).toBe(0);
    setNudgeAfter(7);
    expect(nudgeAfter()).toBe(7);
    setNudgeAfter(0);
    expect(nudgeAfter()).toBe(0);
    localStorage.setItem('jarvis.beloved.after', '2');
    expect(nudgeAfter()).toBe(0);
    // The key itself says nothing.
    setNudgeAfter(3);
    expect(Object.keys(localStorage).join(' ')).not.toMatch(REVEALING);
  });
});
