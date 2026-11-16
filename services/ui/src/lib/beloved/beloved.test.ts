import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  dayNumber,
  durationLabel,
  elapsed,
  fromDayNumber,
  isDayMilestone,
  localToday,
  nextOnce,
  nextYearly,
  ordinal,
  parseDay,
  upcomingMilestones,
  type Ymd,
} from './dates';
import { DATE_IDEAS, DEVOTIONS, QUESTIONS, dailyContent, fetchRef } from './content';
import { specialDays } from './days';
import { buildView, markPrayed, prayedCount, readRecord, type Opened, type BelovedRecord, type BelovedKind } from './records';
import { GENERIC_SOON, GENERIC_TITLE, GENERIC_TODAY, MAX_REMINDERS, planReminders, reminderId } from './reminders';
import { canSeeBeloved } from './access';
import { PrivateVault, readSealed, sealRecord, openRecord, randomBytes } from '../privateChatCrypto';

const ymd = (text: string): Ymd => parseDay(text) as Ymd;

function opened(kind: BelovedKind, record: BelovedRecord, extra: Partial<Opened> = {}): Opened {
  return { id: `${kind}${Math.random().toString(16).slice(2, 10)}`, kind, at: 1, updatedAt: 1, by: 'robin', own: false, byName: 'Robin', record, ...extra };
}

describe('calendar days', () => {
  it('reads only real calendar days', () => {
    expect(parseDay('2024-02-29')).toEqual({ y: 2024, m: 2, d: 29 });
    expect(parseDay('2023-02-29')).toBeNull();
    expect(parseDay('2023-13-01')).toBeNull();
    expect(parseDay('2023-1-01')).toBeNull();
    expect(parseDay(20230101)).toBeNull();
  });

  it('counts days across leap years exactly', () => {
    // 2020 and 2024 are leap years: 365 * 5 + 2.
    expect(elapsed(ymd('2019-06-01'), ymd('2024-06-01'))?.days).toBe(1827);
    expect(dayNumber(ymd('1970-01-01'))).toBe(0);
    expect(fromDayNumber(dayNumber(ymd('2000-02-29')))).toEqual(ymd('2000-02-29'));
    // 1900 was not a leap year, 2000 was.
    expect(dayNumber(ymd('1900-03-01')) - dayNumber(ymd('1900-02-28'))).toBe(1);
    expect(dayNumber(ymd('2000-03-01')) - dayNumber(ymd('2000-02-28'))).toBe(2);
  });

  it('reads years, months and days as a calendar does', () => {
    expect(elapsed(ymd('2015-08-20'), ymd('2025-11-15'))).toEqual({ days: 3740, years: 10, months: 2, remainderDays: 26 });
    expect(elapsed(ymd('2024-01-31'), ymd('2024-03-01'))).toMatchObject({ years: 0, months: 1, remainderDays: 1 });
    expect(elapsed(ymd('2025-11-16'), ymd('2025-11-15'))).toBeNull();
    expect(durationLabel({ days: 3740, years: 10, months: 2, remainderDays: 26 })).toBe('10 years, 2 months');
    expect(durationLabel({ days: 3, years: 0, months: 0, remainderDays: 3 })).toBe('3 days');
  });

  it('keeps a 29 February day on 28 February in other years', () => {
    expect(nextYearly(ymd('2016-02-29'), ymd('2025-01-10'))).toEqual({ date: ymd('2025-02-28'), inDays: 49, nth: 9 });
    expect(nextYearly(ymd('2016-02-29'), ymd('2027-03-01')).date).toEqual(ymd('2028-02-29'));
  });

  it('counts today as the day itself, and one-off days only until they pass', () => {
    expect(nextYearly(ymd('2010-05-05'), ymd('2026-05-05'))).toMatchObject({ inDays: 0, nth: 16 });
    expect(nextOnce(ymd('2026-05-05'), ymd('2026-05-06'))).toBeNull();
    expect(nextOnce(ymd('2026-05-07'), ymd('2026-05-06'))?.inDays).toBe(1);
  });

  it('takes the local calendar day, not the UTC one, in every time zone', () => {
    // 23:30 on 31 December in the phone's own zone is still 31 December there.
    const late = new Date(2025, 11, 31, 23, 30);
    expect(localToday(late)).toEqual({ y: 2025, m: 12, d: 31 });
    const early = new Date(2026, 0, 1, 0, 15);
    expect(localToday(early)).toEqual({ y: 2026, m: 1, d: 1 });
  });

  it('is not a day off across a daylight saving change', () => {
    // Counting milliseconds would make one of these 0.958 or 1.042 days.
    expect(dayNumber(ymd('2026-03-09')) - dayNumber(ymd('2026-03-08'))).toBe(1);
    expect(dayNumber(ymd('2026-11-02')) - dayNumber(ymd('2026-11-01'))).toBe(1);
  });

  it('writes ordinals', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111].map(ordinal)).toEqual(
      ['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '101st', '111th'],
    );
  });
});

describe('milestones', () => {
  it('celebrates the hundreds, then the five hundreds, then the thousands', () => {
    expect([100, 900, 1000].every(isDayMilestone)).toBe(true);
    expect([150, 1100, 0, -100].some(isDayMilestone)).toBe(false);
    expect([1500, 5000, 10_000, 11_000, 11_111].every(isDayMilestone)).toBe(true);
    expect(isDayMilestone(10_500)).toBe(false);
  });

  it('lists the next ones soonest first, day counts and anniversaries together', () => {
    const list = upcomingMilestones(ymd('2023-01-01'), ymd('2025-09-20'), 4);
    expect(list.map((m) => m.label)).toEqual(['1,000 days married', '3rd anniversary', '4th anniversary', '1,500 days married']);
    expect(list[0]).toMatchObject({ key: 'days:1000', date: ymd('2025-09-27'), inDays: 7 });
  });

  it('includes a milestone that is today', () => {
    expect(upcomingMilestones(ymd('2023-01-01'), ymd('2025-09-27'), 1)[0]).toMatchObject({ value: 1000, inDays: 0 });
  });

  it('has none before the wedding', () => {
    expect(upcomingMilestones(ymd('2030-06-01'), ymd('2026-01-01'))).toEqual([]);
  });
});

describe('special days', () => {
  const couple = [{ username: 'parent', display_name: 'Avery' }, { username: 'robin', display_name: 'Robin' }];

  it('puts every kind in one list, soonest first', () => {
    const view = buildView([
      opened('profile', { b: 'profile', wedding: '2015-06-20', firstDate: '2013-10-02', birthdays: { parent: '1990-03-14', robin: '1991-12-01' } }),
      opened('day', { b: 'day', name: 'Moved into our first home', date: '2016-04-10', yearly: true }),
      opened('day', { b: 'day', name: 'Retreat weekend', date: '2026-02-01', yearly: false }),
      opened('day', { b: 'day', name: 'A day long past', date: '2020-02-01', yearly: false }),
    ]);
    const list = specialDays(view, couple, ymd('2026-01-15'), 2);
    expect(list.map((d) => [d.name, d.inDays])).toEqual([
      ['Retreat weekend', 17],
      ["Avery's birthday", 58],
      ['Moved into our first home', 85],
      ['4,000 days married', 138],
      ['11th wedding anniversary', 156],
      ['13th anniversary of our first date', 260],
      ["Robin's birthday", 320],
    ]);
    expect(list[0]).toMatchObject({ kind: 'custom', inDays: 17 });
    expect(list.every((d, i) => i === 0 || list[i - 1].inDays <= d.inDays)).toBe(true);
  });

  it('counts down to the wedding before it happens', () => {
    const view = buildView([opened('profile', { b: 'profile', wedding: '2026-06-01' })]);
    expect(specialDays(view, couple, ymd('2026-05-30'))).toEqual([
      expect.objectContaining({ name: 'Our wedding day', inDays: 2 }),
    ]);
  });
});

describe('daily content', () => {
  it('has two months of devotions, questions and date ideas, with unique ids', () => {
    expect(DEVOTIONS.length).toBeGreaterThanOrEqual(60);
    expect(QUESTIONS.length).toBeGreaterThanOrEqual(60);
    expect(DATE_IDEAS.length).toBeGreaterThanOrEqual(50);
    expect(new Set(DEVOTIONS.map((d) => d.id)).size).toBe(DEVOTIONS.length);
    expect(new Set(DATE_IDEAS.map((d) => d.id)).size).toBe(DATE_IDEAS.length);
    expect(new Set(QUESTIONS).size).toBe(QUESTIONS.length);
  });

  it('keeps references only, with no dashes but a plain hyphen anywhere', () => {
    const text = JSON.stringify([DEVOTIONS, QUESTIONS, DATE_IDEAS]);
    expect(text).not.toMatch(/[\u2013\u2014]/);
    for (const d of DEVOTIONS) expect(d.ref).toMatch(/^[1-3]? ?[A-Z][A-Za-z ]+ \d+:\d+(-\d+)?(, \d+)?$/);
    expect(DEVOTIONS.filter((d) => d.ref.startsWith('Song of Solomon')).length).toBeGreaterThanOrEqual(12);
  });

  it('is the same for the same day on any phone, and changes every day', () => {
    expect(dailyContent(ymd('2026-11-15'))).toEqual(dailyContent(ymd('2026-11-15')));
    expect(dailyContent(ymd('2026-11-16')).devotion.id).not.toBe(dailyContent(ymd('2026-11-15')).devotion.id);
    expect(dailyContent(ymd('2026-11-15')).day).toBe('2026-11-15');
  });

  it('does not repeat a devotion for as many days as there are devotions', () => {
    const start = dayNumber(ymd('2026-01-01'));
    const seen = new Set<string>();
    for (let i = 0; i < DEVOTIONS.length; i += 1) seen.add(dailyContent(fromDayNumber(start + i)).devotion.id);
    expect(seen.size).toBe(DEVOTIONS.length);
    const questions = new Set<string>();
    for (let i = 0; i < QUESTIONS.length; i += 1) questions.add(dailyContent(fromDayNumber(start + i)).question);
    expect(questions.size).toBe(QUESTIONS.length);
  });

  it('asks the Bible service for the Song by the name it knows', () => {
    expect(fetchRef('Song of Solomon 6:3')).toBe('Song 6:3');
    expect(fetchRef('Genesis 2:24')).toBe('Genesis 2:24');
  });
});

describe('records', () => {
  it('opens only well-shaped records of the expected kind', () => {
    expect(readRecord('note', { b: 'note', text: 'Hi' })).toEqual({ b: 'note', text: 'Hi' });
    expect(readRecord('note', { b: 'day', name: 'x', date: '2020-01-01' })).toBeNull();
    expect(readRecord('day', { b: 'day', name: 'x', date: 'soon' })).toBeNull();
    expect(readRecord('profile', { b: 'profile', wedding: '2020-02-30', cover: { blob: '../x', mime: 'image/png' }, birthdays: { 'a/b': '2000-01-01' } })).toEqual({ b: 'profile' });
    expect(readRecord('prayer', { b: 'prayer', text: 'Work', prayed: { robin: [5, 'x', -1] } })).toEqual({ b: 'prayer', text: 'Work', prayed: { robin: [5] } });
  });

  it('keeps the newest profile and counts prayers', () => {
    const older = opened('profile', { b: 'profile', wedding: '2010-01-01' }, { updatedAt: 5 });
    const newer = opened('profile', { b: 'profile', wedding: '2011-01-01' }, { updatedAt: 9 });
    expect(buildView([newer, older]).profile?.record).toEqual({ b: 'profile', wedding: '2011-01-01' });
    const prayer = markPrayed(markPrayed({ b: 'prayer', text: 'Health' }, 'parent', 10), 'robin', 11);
    expect(prayedCount(prayer)).toBe(2);
  });

  it('round trips through the chat key and leaves nothing readable in the envelope', async () => {
    const key = await crypto.subtle.importKey('raw', randomBytes(32), { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
    const other = await crypto.subtle.importKey('raw', randomBytes(32), { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
    const record = { b: 'day', name: 'Our engagement', date: '2014-12-24', yearly: true };
    const sealed = await sealRecord(key, record);
    expect(readSealed(sealed)).not.toBeNull();
    expect(sealed).not.toContain('engagement');
    expect(sealed).not.toContain('2014');
    expect(readRecord('day', await openRecord(key, sealed))).toEqual(record);
    expect(await openRecord(other, sealed)).toBeNull();
    expect(await openRecord(key, sealed.replace(/"ct":"(.)/, '"ct":"A'))).toBeNull();

    const vault = new PrivateVault();
    vault.adopt(key);
    expect(await vault.openRecord(await vault.sealRecord({ b: 'heart' }))).toEqual({ b: 'heart' });
    vault.lock();
    await expect(vault.sealRecord({ b: 'heart' })).rejects.toThrow('locked');
  });
});

describe('reminders', () => {
  afterEach(() => vi.useRealTimers());
  const days = [
    { key: 'wedding:2026', name: '11th wedding anniversary', kind: 'wedding' as const, date: ymd('2026-06-20'), inDays: 5 },
    { key: 'birthday:robin:2026', name: "Robin's birthday", kind: 'birthday' as const, date: ymd('2026-06-16'), inDays: 1 },
    { key: 'custom:far', name: 'Far away', kind: 'custom' as const, date: ymd('2027-06-16'), inDays: 366 },
  ];
  const now = new Date(2026, 5, 15, 20, 0).getTime();

  it('says nothing about the day unless this phone allows it', () => {
    const plan = planReminders(days, now, false);
    const text = JSON.stringify(plan);
    expect(text).not.toMatch(/anniversary|Robin|birthday|Far away|wedding/i);
    expect(new Set(plan.map((r) => r.title))).toEqual(new Set([GENERIC_TITLE]));
    expect(plan.map((r) => r.body)).toEqual([GENERIC_TODAY, GENERIC_SOON, GENERIC_TODAY]);
  });

  it('schedules the evening before and the morning of, never in the past or beyond the horizon', () => {
    const plan = planReminders(days, now, true);
    // The evening before Robin's birthday (19:00 on the 15th) has passed at 20:00.
    expect(plan.map((r) => [new Date(r.at).getDate(), new Date(r.at).getHours(), r.body])).toEqual([
      [16, 8, "Today: Robin's birthday"],
      [19, 19, 'Tomorrow: 11th wedding anniversary'],
      [20, 8, 'Today: 11th wedding anniversary'],
    ]);
  });

  it('keeps stable ids so a re-sync replaces rather than duplicates, and caps the count', () => {
    expect(planReminders(days, now, false).map((r) => r.id)).toEqual(planReminders(days, now, true).map((r) => r.id));
    expect(reminderId('a')).not.toBe(reminderId('b'));
    expect(reminderId('a')).toBeGreaterThan(0);
    expect(reminderId('a')).toBeLessThan(2 ** 31);
    const many = Array.from({ length: 100 }, (_, i) => ({ ...days[0], key: `k${i}`, date: ymd('2026-07-01'), inDays: 16 }));
    expect(planReminders(many, now, false)).toHaveLength(MAX_REMINDERS);
  });
});

describe('who sees Beloved', () => {
  const chat = { members: ['parent', 'robin'], beloved: true };

  it('shows it to the father or mother of this chat only', () => {
    expect(canSeeBeloved({ username: 'parent', family_role: 'father', is_minor: false }, chat)).toBe(true);
    expect(canSeeBeloved({ username: 'robin', family_role: 'mother' }, chat)).toBe(true);
  });

  it.each([
    ['an admin who is not one of the two', { username: 'admin', family_role: 'other' }],
    ['a father who is not in this chat', { username: 'uncle', family_role: 'father' }],
    ['a teen', { username: 'parent', family_role: 'teen', is_minor: true }],
    ['a child', { username: 'parent', family_role: 'child', is_minor: true }],
    ['a minor marked as a parent', { username: 'parent', family_role: 'father', is_minor: true }],
    ['the household account', { username: 'parent', family_role: 'father', is_system_default: true }],
    ['an adult child', { username: 'parent', family_role: 'adult_child' }],
    ['nobody', null],
  ])('hides it from %s', (_, viewer) => {
    expect(canSeeBeloved(viewer, chat)).toBe(false);
  });

  it('hides it in a chat the server says is not a couple', () => {
    expect(canSeeBeloved({ username: 'parent', family_role: 'father' }, { members: ['parent', 'robin'] })).toBe(false);
    expect(canSeeBeloved({ username: 'parent', family_role: 'father' }, { ...chat, beloved: false })).toBe(false);
  });
});
