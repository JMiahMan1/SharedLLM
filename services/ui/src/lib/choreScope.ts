/** Whose chores the chores widget is showing. */
export type ChoreScope = 'all' | 'me';

/**
 * Whether a Skylight assignee label belongs to a given Jarvis OS login name.
 *
 * The Skylight API labels a chore with a *category* per family member, and that
 * label is free text typed by a human in another app ("Jeremiah", "Dad",
 * "jmsummers"), while this side knows login names ("jeremiah"). Matching is
 * therefore lenient in both directions, exactly as the execution service's
 * `user` filter does -- the two must agree or an admin's "my chores" view would
 * come back empty while the server had matched it.
 */
export function choreBelongsTo(assignees: string[] | undefined | null, loginName: string): boolean {
  const wanted = (loginName || '').trim().toLowerCase();
  if (!wanted) return false;
  return (assignees ?? []).some((label) => {
    const name = (label || '').trim().toLowerCase();
    if (!name) return false;
    return name === wanted || name.includes(wanted) || wanted.includes(name);
  });
}

/** Does this login name appear on any of these chores? */
export function hasChoresFor(
  chores: Array<{ assignees?: string[] }> | undefined | null,
  loginName: string,
): boolean {
  return (chores ?? []).some((chore) => choreBelongsTo(chore?.assignees, loginName));
}