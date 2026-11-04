import { api } from '../services/api';
import type { ExecutionResponse } from '../services/api';

const STORAGE = 'nextcloud';

export interface SavedNote {
  /** Where the note now lives, e.g. "Recipes/Pancakes.md". */
  path?: string;
  /** The old copy could not be removed after a rename (the new one is saved). */
  leftOldCopy: boolean;
  /** The saved file's version, for the next save's `ifMatch`. */
  etag?: string;
  /** Refused: the note changed elsewhere since `ifMatch` was read. Nothing was written. */
  conflict: boolean;
  /** The title it was saved under: a new name already taken gets a number ("Groceries 2"). */
  title?: string;
}

/**
 * Save a Nextcloud note (Notes and Recipes share this).
 *
 * A note's file name is its title in every list, so renaming it -- or moving
 * it to another category -- writes a new file and then removes the old one,
 * only once the new one is safely written and only when it really is a
 * different file. Editing in place writes over the same path, and when
 * `ifMatch` is given only if the file is still the version that was opened.
 */
export async function saveNote(params: {
  title: string;
  content: string;
  category: string;
  /** The note as it was opened; omit for a new note. */
  previous?: { title: string; category: string; path: string };
  asUser?: 'admin';
  /** The version the note was opened at (an in-place save is refused if it changed). */
  ifMatch?: string;
  /** The note came from Nextcloud Notes without a Jarvis header: keep it that way. */
  plain?: boolean;
}): Promise<SavedNote & { response: ExecutionResponse }> {
  const { title, content, category, previous, asUser, ifMatch, plain } = params;
  const moved = Boolean(previous && (previous.title !== title || previous.category !== category));
  const inPlace = Boolean(previous && !moved);
  // A new note, or a rename, takes a free name: "Groceries" when there is
  // already a Groceries note becomes "Groceries 2" instead of replacing it.
  const write = (name: string) =>
    api.writeNote({
      title: name,
      content,
      category,
      path: inPlace ? previous!.path : undefined,
      storage: STORAGE,
      as_user: asUser,
      if_match: inPlace && ifMatch ? ifMatch : undefined,
      ...(inPlace ? {} : { if_none_match: true }),
      ...(plain ? { plain: true } : {}),
    });
  const taken = (r: ExecutionResponse) =>
    r.status !== 'SUCCESS' && Boolean((r.detail as { exists?: boolean } | undefined)?.exists);
  let usedTitle = title;
  let response = await write(usedTitle);
  for (let n = 2; !inPlace && taken(response) && n <= 20; n += 1) {
    usedTitle = `${title} ${n}`;
    response = await write(usedTitle);
  }
  const detail = (response.detail ?? {}) as { path?: string; etag?: string | null; conflict?: boolean };
  // Only an in-place save can be a conflict (changed elsewhere); a taken name is not one.
  if (response.status !== 'SUCCESS') return { response, leftOldCopy: false, conflict: inPlace && Boolean(detail.conflict) };
  const path = detail.path ?? previous?.path;
  const strip = (p: string) => p.replace(/^\/+/, '');
  let leftOldCopy = false;
  if (moved && previous && path && strip(path) !== strip(previous.path)) {
    // The new copy links to the same attachments: they follow it rather
    // than being deleted with the old one.
    const old = await api.deleteNote(previous.title, STORAGE, previous.path, asUser, strip(path));
    leftOldCopy = old.status !== 'SUCCESS';
  }
  return { response, path, leftOldCopy, etag: detail.etag ?? undefined, conflict: false, title: usedTitle };
}
