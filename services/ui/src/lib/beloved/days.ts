/**
 * The couple's special days, all in one list with a countdown each: the
 * wedding anniversary, both birthdays, their children's birthdays, the first
 * date, their own days, and
 * the marriage's milestones (1,000 days, the 10th anniversary).
 */
import {
  dayNumber,
  nextOnce,
  nextYearly,
  ordinal,
  parseDay,
  upcomingMilestones,
  type Ymd,
} from './dates';
import type { BelovedView } from './records';

export interface SpecialDay {
  /** Stable across days, for keys and reminder ids. */
  key: string;
  name: string;
  kind: 'wedding' | 'birthday' | 'child-birthday' | 'first-date' | 'custom' | 'milestone';
  date: Ymd;
  inDays: number;
  /** The record behind a day the couple added (to edit or remove it). */
  recordId?: string;
}

export interface Person {
  username: string;
  display_name: string;
}

/** Every special day with its next date, soonest first. Past one-off days are left out. */
export function specialDays(view: BelovedView, couple: Person[], today: Ymd, milestoneCount = 4): SpecialDay[] {
  const out: SpecialDay[] = [];
  const profile = view.profile?.record;
  const wedding = parseDay(profile?.wedding);
  if (wedding) {
    const next = nextYearly(wedding, today);
    if (next.nth && next.nth > 0) {
      out.push({ key: `wedding:${next.date.y}`, name: `${ordinal(next.nth)} wedding anniversary`, kind: 'wedding', date: next.date, inDays: next.inDays });
    } else if (dayNumber(wedding) >= dayNumber(today)) {
      out.push({ key: 'wedding:day', name: 'Our wedding day', kind: 'wedding', date: wedding, inDays: dayNumber(wedding) - dayNumber(today) });
    }
    for (const m of upcomingMilestones(wedding, today, milestoneCount)) {
      // The yearly anniversary is already in the list above.
      if (m.kind === 'years' && m.value === next.nth && m.date.y === next.date.y) continue;
      out.push({ key: `milestone:${m.key}`, name: m.label, kind: 'milestone', date: m.date, inDays: m.inDays });
    }
  }
  const first = parseDay(profile?.firstDate);
  if (first) {
    const next = nextYearly(first, today);
    if (next.nth && next.nth > 0) {
      out.push({ key: `first-date:${next.date.y}`, name: `${ordinal(next.nth)} anniversary of our first date`, kind: 'first-date', date: next.date, inDays: next.inDays });
    }
  }
  for (const person of couple) {
    const birthday = parseDay(profile?.birthdays?.[person.username]);
    if (!birthday) continue;
    const next = nextYearly(birthday, today);
    out.push({ key: `birthday:${person.username}:${next.date.y}`, name: `${person.display_name}'s birthday`, kind: 'birthday', date: next.date, inDays: next.inDays });
  }
  for (const [index, child] of (profile?.children ?? []).entries()) {
    const birthday = parseDay(child.birthday);
    if (!birthday) continue;
    const next = nextYearly(birthday, today);
    // The day they are born is still to come: a due date, not an age.
    const name = next.nth && next.nth > 0 ? `${child.name}'s ${ordinal(next.nth)} birthday` : `${child.name}'s birthday`;
    out.push({ key: `child:${index}:${child.name}:${next.date.y}`, name, kind: 'child-birthday', date: next.date, inDays: next.inDays });
  }
  for (const day of view.days) {
    const when = parseDay(day.record.date);
    if (!when) continue;
    const next = day.record.yearly ? nextYearly(when, today) : nextOnce(when, today);
    if (!next) continue;
    out.push({ key: `custom:${day.id}:${next.date.y}`, name: day.record.name, kind: 'custom', date: next.date, inDays: next.inDays, recordId: day.id });
  }
  return out.sort((a, b) => a.inDays - b.inDays || a.name.localeCompare(b.name));
}
