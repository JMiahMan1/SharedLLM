// Edits of gallery pictures: sealed gallery entries that say "this original
// is now shown as that edited picture, made with this recipe" or "back to the
// original". The original blob is never changed or deleted by an edit. Kept
// free of React so it can be tested directly; both phones work out the same
// current version from the same records.
import { compactRecipe, type EditRecipe } from '../../lib/photoEdit/recipe';
import type { GalleryEdit } from '../../lib/privateChatCrypto';
import { bareRecordBytes, GALLERY_RECORD_BYTES, padded, type GalleryItem, type OpenedEntry, type ShownEdit } from './privateGalleryModel';

/** An edit record as read from the gallery's entries. */
export interface EditEntry {
  id: string;
  /** Server time (seconds). */
  at: number;
  own: boolean;
  byName: string;
  record: GalleryEdit;
}

/** Every edit record, oldest first (server time, then id): the order both phones read them in. */
export function editEntries(entries: readonly OpenedEntry[]): EditEntry[] {
  const out: EditEntry[] = [];
  for (const e of entries) {
    if (e.payload === 'unreadable' || e.payload.t !== 'gallery_edit') continue;
    out.push({ id: e.id, at: Number.isFinite(e.at) ? e.at : 0, own: e.own, byName: e.byName, record: e.payload });
  }
  return out.sort((a, b) => a.at - b.at || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/**
 * What each edited original shows now, by original blob: the newest record
 * for it wins (ties by id). An original whose newest record is a revert is
 * not in the map: it shows itself.
 */
export function currentEdits(entries: readonly OpenedEntry[]): Map<string, ShownEdit> {
  const newest = new Map<string, EditEntry>();
  for (const e of editEntries(entries)) newest.set(e.record.of, e);
  const out = new Map<string, ShownEdit>();
  for (const [of, e] of newest) {
    const r = e.record;
    if (r.revert || !r.blob || !r.mime) continue;
    out.set(of, {
      entryId: e.id,
      blob: r.blob,
      mime: r.mime,
      ...(r.width && r.height ? { width: r.width, height: r.height } : {}),
      ...(r.animated ? { animated: true } : {}),
      ...(r.recipe ? { recipe: r.recipe } : r.recipe_blob ? { recipeBlob: r.recipe_blob } : {}),
      at: e.at,
      own: e.own,
      byName: e.byName,
    });
  }
  return out;
}

/** The gallery's items, each with the edit it shows (if any). Videos are never edited. */
export function withEdits(items: GalleryItem[], edits: ReadonlyMap<string, ShownEdit>): GalleryItem[] {
  if (!edits.size) return items;
  return items.map((item) => {
    const edit = item.video ? undefined : edits.get(item.blob);
    return edit ? { ...item, edit } : item;
  });
}

/** The edited picture, as the record names it. */
export interface EditedPicture {
  blob: string;
  mime: string;
  width?: number;
  height?: number;
  animated?: boolean;
}

/**
 * The padded record for an edit of `of`, with its recipe inside when it fits
 * the one record size. Null when it does not fit (a long drawing, many
 * words): then the recipe goes in its own encrypted blob and the record names
 * it (`recipeBlob`).
 */
export function editRecord(
  of: string,
  picture: EditedPicture,
  recipe: { recipe: EditRecipe } | { recipeBlob: string },
  editedAt = Math.floor(Date.now() / 1000),
): (GalleryEdit & { p: string }) | null {
  const record: GalleryEdit = {
    t: 'gallery_edit',
    of,
    blob: picture.blob,
    mime: picture.mime,
    ...(picture.width && picture.height ? { width: Math.round(picture.width), height: Math.round(picture.height) } : {}),
    ...(picture.animated ? { animated: true } : {}),
    ...('recipe' in recipe ? { recipe: compactRecipe(recipe.recipe) } : { recipe_blob: recipe.recipeBlob }),
    edited_at: editedAt,
  };
  return bareRecordBytes(record) <= GALLERY_RECORD_BYTES ? padded(record) : null;
}

/** The padded record that puts the original back. */
export function revertRecord(of: string, editedAt = Math.floor(Date.now() / 1000)): GalleryEdit & { p: string } {
  return padded({ t: 'gallery_edit', of, revert: true, edited_at: editedAt });
}

/** The encrypted blobs an edit record brought (its picture, and its recipe's blob). */
export function editBlobs(record: GalleryEdit): string[] {
  return [record.blob, record.recipe_blob].filter((b): b is string => typeof b === 'string');
}

/**
 * What to tidy away after this phone wrote a newer record for `of`: this
 * phone's own older records for it (the server lets only the writer delete an
 * entry) and the blobs they brought. Nothing that is still shown is listed.
 */
export function supersededOwn(before: readonly OpenedEntry[], of: string): Array<{ entryId: string; blobs: string[] }> {
  return editEntries(before)
    .filter((e) => e.own && e.record.of === of)
    .map((e) => ({ entryId: e.id, blobs: editBlobs(e.record) }));
}

/**
 * Everything to remove when originals are deleted: every edit record that
 * names one of them, with its blobs. Any phone may delete a blob, but only the
 * writer may delete an entry: `own` says which entries this phone can remove
 * (the other phone's records are left pointing at nothing, and are ignored).
 */
export function editsOf(entries: readonly OpenedEntry[], originals: readonly string[]): Array<{ entryId: string; own: boolean; blobs: string[] }> {
  const set = new Set(originals);
  return editEntries(entries)
    .filter((e) => set.has(e.record.of))
    .map((e) => ({ entryId: e.id, own: e.own, blobs: editBlobs(e.record) }));
}
