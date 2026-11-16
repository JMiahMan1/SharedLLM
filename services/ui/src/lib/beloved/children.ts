/**
 * The children's birthdays in Beloved's details: the rows being edited, the
 * family's children not listed yet (to add with one tap), and what is kept.
 *
 * The list itself is sealed in the couple's profile like every other date.
 * Jarvis's own record of a child (name and the birth date the parents set for
 * app ratings) only fills a row in; changing a row here changes nothing else.
 */
import { parseDay } from './dates';
import { MAX_CHILDREN, type ChildBirthday, type FamilyChild } from './records';

/** A row in the editor: either field may still be empty. */
export interface ChildRow {
  name: string;
  birthday: string;
  username?: string;
}

function sameName(a: string, b: string): boolean {
  return a.trim().toLocaleLowerCase() === b.trim().toLocaleLowerCase();
}

/** The family's children who are not in the list yet, by account or by name. */
export function unlistedChildren(family: readonly FamilyChild[], rows: readonly ChildRow[]): FamilyChild[] {
  return family.filter(
    (child) => !rows.some((row) => row.username === child.username || sameName(row.name, child.display_name)),
  );
}

/** A row for one of the family's children, with the birth date Jarvis has (or blank). */
export function rowFor(child: FamilyChild): ChildRow {
  return { name: child.display_name, birthday: parseDay(child.birth_date) ? (child.birth_date as string) : '', username: child.username };
}

/** A row with a name and no date, or a date and no name: not kept, so the editor says so. */
export function isIncomplete(row: ChildRow): boolean {
  const named = row.name.trim() !== '';
  const dated = parseDay(row.birthday) !== null;
  return named !== dated;
}

/** What is saved: complete rows only, in the couple's order, at most MAX_CHILDREN. */
export function keptChildren(rows: readonly ChildRow[]): ChildBirthday[] {
  return rows
    .filter((row) => row.name.trim() && parseDay(row.birthday))
    .slice(0, MAX_CHILDREN)
    .map((row) => {
      const kept: ChildBirthday = { name: row.name.trim().slice(0, 60), birthday: row.birthday };
      if (row.username) kept.username = row.username;
      return kept;
    });
}
