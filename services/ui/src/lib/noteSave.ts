import { api } from '../services/api';
import type { ExecutionResponse } from '../services/api';

const STORAGE = 'nextcloud';

export interface SavedNote {
  /** Where the note now lives, e.g. "Recipes/Pancakes.md". */
  path?: string;
  /** The old copy could not be removed after a rename (the new one is saved). */
  leftOldCopy: boolean;
}

/**
 * Save a Nextcloud note (Notes and Recipes share this).
 *
 * A note's file name is its title in every list, so renaming it -- or moving
 * it to another category -- writes a new file and then removes the old one,
 * only once the new one is safely written and only when it really is a
 * different file. Editing in place writes over the same path.
 */
export async function saveNote(params: {
  title: string;
  content: string;
  category: string;
  /** The note as it was opened; omit for a new note. */
  previous?: { title: string; category: string; path: string };
  asUser?: 'admin';
}): Promise<SavedNote & { response: ExecutionResponse }> {
  const { title, content, category, previous, asUser } = params;
  const moved = Boolean(previous && (previous.title !== title || previous.category !== category));
  const response = await api.writeNote({
    title,
    content,
    category,
    path: previous && !moved ? previous.path : undefined,
    storage: STORAGE,
    as_user: asUser,
  });
  if (response.status !== 'SUCCESS') return { response, leftOldCopy: false };
  const path = ((response.detail as { path?: string } | null | undefined)?.path) ?? previous?.path;
  const strip = (p: string) => p.replace(/^\/+/, '');
  let leftOldCopy = false;
  if (moved && previous && path && strip(path) !== strip(previous.path)) {
    const old = await api.deleteNote(previous.title, STORAGE, previous.path, asUser);
    leftOldCopy = old.status !== 'SUCCESS';
  }
  return { response, path, leftOldCopy };
}
