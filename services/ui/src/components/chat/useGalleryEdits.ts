// Writing gallery edits: the edited picture as a new encrypted blob, and a
// sealed, padded edit record naming the original and the recipe. The
// original blob is never touched. See privateGalleryEdits.ts for the reading
// side (newest record wins, on both phones).
import { api } from '../../services/api';
import { parseRecipe, type EditRecipe } from '../../lib/photoEdit/recipe';
import type { PrivateVault } from '../../lib/privateChatCrypto';
import { editRecord, editsOf, revertRecord, supersededOwn, type EditedPicture } from './privateGalleryEdits';
import type { OpenedEntry, ShownEdit } from './privateGalleryModel';

/** Encrypt and upload bytes; the plaintext copy is zeroed once encrypted. */
async function uploadSealed(token: string, vault: PrivateVault, plain: Uint8Array<ArrayBuffer>): Promise<string> {
  let sealed: Uint8Array<ArrayBuffer>;
  try {
    sealed = await vault.encrypt(plain);
  } finally {
    plain.fill(0);
  }
  return (await api.uploadPrivateBlob(token, sealed)).id;
}

/**
 * After this phone wrote a newer record for `of`: remove its own older
 * records for that picture and the edited blobs they brought. Best effort: a
 * leftover is never shown (the newest record wins) and is tidied next time.
 */
async function tidy(token: string, before: readonly OpenedEntry[], of: string): Promise<void> {
  for (const old of supersededOwn(before, of)) {
    try {
      await api.deletePrivateGalleryEntry(token, old.entryId);
      for (const blob of old.blobs) await api.deletePrivateBlob(token, blob).catch(() => {});
    } catch {
      // Kept for now; it is older than the record just written, so it is not shown.
    }
  }
}

/**
 * Save an edit of the gallery picture `of`: the edited picture is encrypted
 * and uploaded as a new blob, then a sealed edit record names the original,
 * the new blob and the recipe (in the record when it fits, else in its own
 * encrypted blob). If anything fails, what was uploaded is deleted again.
 */
export async function saveGalleryEdit(
  token: string,
  vault: PrivateVault,
  of: string,
  edited: { blob: Blob; mime: string; width: number; height: number; animated: boolean; recipe: EditRecipe },
  before: readonly OpenedEntry[],
): Promise<void> {
  const uploaded: string[] = [];
  try {
    const blob = await uploadSealed(token, vault, new Uint8Array(await edited.blob.arrayBuffer()));
    uploaded.push(blob);
    const picture: EditedPicture = { blob, mime: edited.mime, width: edited.width, height: edited.height, animated: edited.animated };
    let record = editRecord(of, picture, { recipe: edited.recipe });
    if (!record) {
      const recipeBlob = await uploadSealed(token, vault, new TextEncoder().encode(JSON.stringify(edited.recipe)) as Uint8Array<ArrayBuffer>);
      uploaded.push(recipeBlob);
      record = editRecord(of, picture, { recipeBlob });
    }
    if (!record) throw new Error('Could not save the edit.');
    await api.addPrivateGalleryEntry(token, await vault.seal(record));
  } catch (error) {
    for (const id of uploaded) void api.deletePrivateBlob(token, id).catch(() => {});
    throw error;
  }
  await tidy(token, before, of);
}

/** Show the original again: a sealed revert record (the edits' blobs this phone wrote are tidied away). */
export async function revertGalleryEdit(token: string, vault: PrivateVault, of: string, before: readonly OpenedEntry[]): Promise<void> {
  await api.addPrivateGalleryEntry(token, await vault.seal(revertRecord(of)));
  await tidy(token, before, of);
}

/**
 * Deleting pictures deletes their edits too: every edited blob (and recipe
 * blob) made from them, and this phone's edit records for them. The other
 * phone's records cannot be deleted from here (only their writer can); with
 * their original and their blobs gone they are ignored.
 */
export async function deleteGalleryEdits(token: string, entries: readonly OpenedEntry[], originals: readonly string[]): Promise<void> {
  for (const edit of editsOf(entries, originals)) {
    for (const blob of edit.blobs) await api.deletePrivateBlob(token, blob).catch(() => {});
    if (edit.own) await api.deletePrivateGalleryEntry(token, edit.entryId).catch(() => {});
  }
}

/** The recipe of the current edit, to start "Edit again" from the original with it. Null when there is none to read. */
export async function readEditRecipe(token: string, vault: PrivateVault, edit: ShownEdit | undefined): Promise<EditRecipe | null> {
  if (!edit) return null;
  if (edit.recipe) return edit.recipe;
  if (!edit.recipeBlob) return null;
  try {
    const plain = await vault.decrypt(await api.getPrivateBlob(token, edit.recipeBlob));
    try {
      return parseRecipe(new TextDecoder().decode(plain));
    } finally {
      plain.fill(0);
    }
  } catch {
    return null;
  }
}

/** A gallery picture's original, decrypted into a Blob for the editor. */
export async function originalPicture(token: string, vault: PrivateVault, blob: string, mime: string): Promise<Blob> {
  const plain = await vault.decrypt(await api.getPrivateBlob(token, blob));
  try {
    return new Blob([plain], { type: mime });
  } finally {
    plain.fill(0);
  }
}
