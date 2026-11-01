/**
 * Reading and writing the shopping list note (`Recipes/Shopping list.md`).
 *
 * Saves once went to `Recipes/Shopping-list.md` (the file name the server
 * makes from the title) while every read asked for `Shopping list.md`, so
 * each new list started from nothing. A list left there is read until the
 * next save moves it.
 */
import { api } from '../services/api';
import { saveNote } from './noteSave';
import { LEGACY_SHOPPING_PATH, SHOPPING_PATH, SHOPPING_TITLE } from './shoppingList';

const STORAGE = 'nextcloud';
const RECIPES_DIR = 'Recipes';

export interface StoredList {
  /** The note's text, or null when there is no list yet. */
  text: string | null;
  /** False when the list is not at `SHOPPING_PATH` yet. */
  exists: boolean;
}

export async function readShoppingList(asUser?: 'admin'): Promise<StoredList> {
  const res = await api.readNote(SHOPPING_TITLE, STORAGE, SHOPPING_PATH, asUser);
  if (res.status === 'SUCCESS') return { text: res.message ?? '', exists: true };
  const old = await api.readNote(SHOPPING_TITLE, STORAGE, LEGACY_SHOPPING_PATH, asUser);
  if (old.status === 'SUCCESS') return { text: old.message ?? '', exists: false };
  return { text: null, exists: false };
}

/** Always to `SHOPPING_PATH` itself, never a file name made from the title. */
export async function saveShoppingList(content: string, exists: boolean, asUser?: 'admin') {
  const saved = await saveNote({
    title: SHOPPING_TITLE,
    content,
    category: RECIPES_DIR,
    previous: { title: SHOPPING_TITLE, category: RECIPES_DIR, path: SHOPPING_PATH },
    asUser,
  });
  if (!exists && saved.response.status === 'SUCCESS') {
    await api.deleteNote(SHOPPING_TITLE, STORAGE, LEGACY_SHOPPING_PATH, asUser).catch(() => undefined);
  }
  return saved;
}
