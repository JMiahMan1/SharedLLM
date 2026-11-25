import { beforeEach, describe, expect, it, vi } from 'vitest';

// The gateway, in memory: blobs, gallery entries (with the server's time and the writer), and who may delete what.
const server = vi.hoisted(() => ({
  blobs: new Map<string, Uint8Array>(),
  entries: [] as Array<{ id: string; at: number; by: string; sealed: string }>,
  me: 'alex',
  clock: 1_000,
  next: 0,
}));
vi.mock('../../services/api', () => ({
  api: {
    uploadPrivateBlob: vi.fn(async (_token: string, bytes: Uint8Array) => {
      const id = (server.next++).toString(16).padStart(32, 'b');
      server.blobs.set(id, bytes.slice());
      return { id };
    }),
    getPrivateBlob: vi.fn(async (_token: string, id: string) => {
      const bytes = server.blobs.get(id);
      if (!bytes) throw new Error('No such picture.');
      return bytes.slice();
    }),
    deletePrivateBlob: vi.fn(async (_token: string, id: string) => {
      server.blobs.delete(id);
    }),
    addPrivateGalleryEntry: vi.fn(async (_token: string, sealed: string) => {
      const entry = { id: (server.next++).toString(16).padStart(32, 'e'), at: (server.clock += 10), by: server.me, sealed };
      server.entries.push(entry);
      return { id: entry.id, at: entry.at };
    }),
    deletePrivateGalleryEntry: vi.fn(async (_token: string, id: string) => {
      const entry = server.entries.find((e) => e.id === id);
      if (!entry) throw new Error('No such picture.');
      if (entry.by !== server.me) throw new Error('Only the parent who added it can remove it.');
      server.entries = server.entries.filter((e) => e.id !== id);
    }),
  },
}));

import { emptyRecipe, type EditRecipe } from '../../lib/photoEdit/recipe';
import { importDataKey, PrivateVault, randomBytes, type GalleryEdit } from '../../lib/privateChatCrypto';
import { currentEdits, editRecord, editsOf, revertRecord, withEdits } from './privateGalleryEdits';
import { GALLERY_RECORD_BYTES, markRecords, padded, shownPicture, type GalleryItem, type OpenedEntry } from './privateGalleryModel';
import { deleteGalleryEdits, readEditRecipe, revertGalleryEdit, saveGalleryEdit } from './useGalleryEdits';

const ORIGINAL = 'a'.repeat(32);
const OTHER = 'c'.repeat(32);
const blob = (n: number) => n.toString(16).padStart(32, 'd');

function recipe(changes: Partial<EditRecipe> = {}): EditRecipe {
  return { ...emptyRecipe(), filter: 'warm', ...changes };
}

function entry(id: string, at: number, payload: OpenedEntry['payload'], own = true): OpenedEntry {
  return { id, at, own, byName: own ? 'Alex' : 'Sam', payload };
}

const edit = (of: string, edited: string, extra: Partial<GalleryEdit> = {}): GalleryEdit => ({ t: 'gallery_edit', of, blob: edited, mime: 'image/jpeg', recipe: recipe(), ...extra });

function item(b: string, extra: Partial<GalleryItem> = {}): GalleryItem {
  return { messageId: 1, blob: b, mime: 'image/png', at: 1, when: 1, own: true, actorName: 'Alex', comments: [], ...extra };
}

async function vault(): Promise<PrivateVault> {
  const v = new PrivateVault();
  v.adopt(await importDataKey(randomBytes(32)));
  return v;
}

/** What a phone reads back from the server: every entry opened, with its time and whether it is its own. */
async function readBack(v: PrivateVault, me = server.me): Promise<OpenedEntry[]> {
  return Promise.all(
    server.entries.map(async (e) => ({ id: e.id, at: e.at, own: e.by === me, byName: e.by, payload: (await v.open(e.sealed)) ?? ('unreadable' as const) })),
  );
}

describe('a gallery edit record', () => {
  it('seals and opens back as the same edit, the recipe checked like any stored recipe', async () => {
    const v = await vault();
    const record = editRecord(ORIGINAL, { blob: blob(1), mime: 'image/gif', width: 320, height: 240, animated: true }, { recipe: recipe({ crop: { x: 0.123456, y: 0, w: 0.5, h: 0.5 } }) }, 1700);
    expect(record).not.toBeNull();
    const opened = await v.open(await v.seal(record!));
    expect(opened).toMatchObject({ t: 'gallery_edit', of: ORIGINAL, blob: blob(1), mime: 'image/gif', width: 320, height: 240, animated: true, edited_at: 1700 });
    if (opened?.t !== 'gallery_edit') throw new Error('not an edit');
    // Rounded to four decimals when stored: far below a pixel.
    expect(opened.recipe?.crop).toEqual({ x: 0.1235, y: 0, w: 0.5, h: 0.5 });
    expect(opened.recipe?.filter).toBe('warm');
  });

  it('is refused when it is not the right shape, and a bad recipe is dropped rather than trusted', async () => {
    const v = await vault();
    const seal = (raw: object) => v.seal(raw as GalleryEdit);
    expect(await v.open(await seal({ t: 'gallery_edit', of: 'nope', blob: blob(1), mime: 'image/jpeg' }))).toBeNull();
    expect(await v.open(await seal({ t: 'gallery_edit', of: ORIGINAL, blob: blob(1), mime: 'text/html' }))).toBeNull();
    // An edit cannot claim the original as its own picture (deleting the edit would delete the original).
    expect(await v.open(await seal({ t: 'gallery_edit', of: ORIGINAL, blob: ORIGINAL, mime: 'image/jpeg' }))).toBeNull();
    const odd = await v.open(await seal({ t: 'gallery_edit', of: ORIGINAL, blob: blob(1), mime: 'image/jpeg', recipe: { v: 99 } }));
    expect(odd).toEqual({ t: 'gallery_edit', of: ORIGINAL, blob: blob(1), mime: 'image/jpeg' });
    expect(await v.open(await seal({ t: 'gallery_edit', of: ORIGINAL, revert: true, blob: blob(1) }))).toEqual({ t: 'gallery_edit', of: ORIGINAL, revert: true });
  });

  it('is padded to the size of every other gallery record, words and emoji included, so the server cannot tell them apart', async () => {
    const v = await vault();
    const words = recipe({
      overlays: [{ id: 't1', kind: 'text', text: 'Grandma’s birthday \u{1F382}\u{1F389} café', font: 'hand', color: '#ffffff', pill: true, x: 0.5, y: 0.5, scale: 1, rotation: 0 }],
    });
    const records = [
      editRecord(ORIGINAL, { blob: blob(1), mime: 'image/jpeg' }, { recipe: words })!,
      editRecord(ORIGINAL, { blob: blob(2), mime: 'image/jpeg' }, { recipeBlob: blob(3) })!,
      revertRecord(ORIGINAL),
      ...markRecords('add', [ORIGINAL]),
      padded({ t: 'gallery_start', kept: [] }),
    ];
    const bytes = records.map((r) => new TextEncoder().encode(JSON.stringify(r)).length);
    expect(new Set(bytes)).toEqual(new Set([GALLERY_RECORD_BYTES]));
    const sealed = await Promise.all(records.map((r) => v.seal(r)));
    expect(new Set(sealed.map((s) => s.length)).size).toBe(1);
    for (const s of sealed) expect(s).not.toContain(ORIGINAL);
  });

  it('does not fit a recipe too big for one record: that recipe goes in its own blob', () => {
    const points: Array<[number, number]> = Array.from({ length: 2000 }, (_, i) => [i / 2000, (i % 7) / 7]);
    const big = recipe({ overlays: [{ id: 'd', kind: 'drawing', strokes: [{ color: '#ff0000', width: 0.01, points }] }] });
    expect(editRecord(ORIGINAL, { blob: blob(1), mime: 'image/jpeg' }, { recipe: big })).toBeNull();
    expect(editRecord(ORIGINAL, { blob: blob(1), mime: 'image/jpeg' }, { recipeBlob: blob(2) })).not.toBeNull();
  });
});

describe('which version a gallery picture shows', () => {
  it('is the newest edit record by server time, the same on both phones whoever wrote it', () => {
    const entries = [
      entry('e2', 20, edit(ORIGINAL, blob(2)), false),
      entry('e1', 10, edit(ORIGINAL, blob(1))),
      entry('e9', 5, edit(OTHER, blob(9))),
      entry('m', 30, { t: 'gallery', op: 'add', blobs: [ORIGINAL] }),
    ];
    const mine = currentEdits(entries);
    const theirs = currentEdits(entries.map((e) => ({ ...e, own: !e.own })));
    expect(mine.get(ORIGINAL)?.blob).toBe(blob(2));
    expect(theirs.get(ORIGINAL)?.blob).toBe(blob(2));
    expect(mine.get(ORIGINAL)).toMatchObject({ entryId: 'e2', own: false, byName: 'Sam' });
    expect(mine.get(OTHER)?.blob).toBe(blob(9));
  });

  it('breaks a tie in server time by record id, so both phones still agree', () => {
    const a = [entry('e1', 10, edit(ORIGINAL, blob(1))), entry('e2', 10, edit(ORIGINAL, blob(2)))];
    expect(currentEdits(a).get(ORIGINAL)?.blob).toBe(blob(2));
    expect(currentEdits([...a].reverse()).get(ORIGINAL)?.blob).toBe(blob(2));
  });

  it('is the original again after a revert, and the newest edit after an edit made since', () => {
    const reverted = [entry('e1', 10, edit(ORIGINAL, blob(1))), entry('e2', 20, { t: 'gallery_edit', of: ORIGINAL, revert: true })];
    expect(currentEdits(reverted).has(ORIGINAL)).toBe(false);
    const again = [...reverted, entry('e3', 30, edit(ORIGINAL, blob(3)))];
    expect(currentEdits(again).get(ORIGINAL)?.blob).toBe(blob(3));
  });

  it('is shown in the gallery in place of the original, which keeps its own blob (comments and membership name it)', () => {
    const items = withEdits([item(ORIGINAL), item(OTHER)], currentEdits([entry('e1', 10, edit(ORIGINAL, blob(1), { width: 100, height: 50 }))]));
    expect(items[0].blob).toBe(ORIGINAL);
    expect(shownPicture(items[0])).toEqual({ blob: blob(1), mime: 'image/jpeg' });
    expect(shownPicture(items[1])).toEqual({ blob: OTHER, mime: 'image/png' });
    // A clip is never edited.
    const clip = item(ORIGINAL, { video: { t: 'video', mime: 'video/mp4', size: 1, duration: 1, poster: ORIGINAL, chunks: [blob(5)], clip: 'x', chunk_bytes: 1 } });
    expect(withEdits([clip], currentEdits([entry('e1', 10, edit(ORIGINAL, blob(1)))]))[0].edit).toBeUndefined();
  });
});

describe('editing, reverting and deleting through the gateway', () => {
  beforeEach(() => {
    server.blobs.clear();
    server.entries = [];
    server.me = 'alex';
    server.clock = 1_000;
    server.next = 0;
    server.blobs.set(ORIGINAL, new Uint8Array([1, 2, 3]));
  });

  const picture = (r: EditRecipe, bytes = [9, 9, 9]) => ({ blob: new Blob([new Uint8Array(bytes)], { type: 'image/jpeg' }), mime: 'image/jpeg', width: 10, height: 10, animated: false, recipe: r });

  it('stores the edit as a new encrypted blob and keeps the original untouched', async () => {
    const v = await vault();
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe()), await readBack(v));
    expect(server.blobs.get(ORIGINAL)).toEqual(new Uint8Array([1, 2, 3]));
    const shown = currentEdits(await readBack(v)).get(ORIGINAL)!;
    expect(shown.blob).not.toBe(ORIGINAL);
    // Ciphertext on the server; it decrypts to the edited picture.
    expect(server.blobs.get(shown.blob)).not.toEqual(new Uint8Array([9, 9, 9]));
    expect([...(await v.decrypt(server.blobs.get(shown.blob)!.slice() as Uint8Array<ArrayBuffer>))]).toEqual([9, 9, 9]);
  });

  it('edits again from the original: every record names the original, never the edit before it, and the old edit is tidied away', async () => {
    const v = await vault();
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe({ filter: 'noir' })), await readBack(v));
    const first = currentEdits(await readBack(v)).get(ORIGINAL)!;
    // "Edit again" opens the original with the recipe it was made with.
    expect((await readEditRecipe('t', v, first))?.filter).toBe('noir');
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe({ filter: 'pop' }), [7]), await readBack(v));
    const entries = await readBack(v);
    const second = currentEdits(entries).get(ORIGINAL)!;
    expect(second.recipe?.filter).toBe('pop');
    expect(entries.filter((e) => e.payload !== 'unreadable' && e.payload.t === 'gallery_edit').map((e) => e.payload)).toEqual([
      expect.objectContaining({ of: ORIGINAL, blob: second.blob }),
    ]);
    expect(server.blobs.has(first.blob)).toBe(false);
    expect(server.blobs.has(ORIGINAL)).toBe(true);
  });

  it('keeps a recipe too big for the record in its own encrypted blob, and reads it back for Edit again', async () => {
    const v = await vault();
    const points: Array<[number, number]> = Array.from({ length: 3000 }, (_, i) => [i / 3000, 0.5]);
    const big = recipe({ overlays: [{ id: 'd', kind: 'drawing', strokes: [{ color: '#ff0000', width: 0.01, points }] }] });
    await saveGalleryEdit('t', v, ORIGINAL, picture(big), await readBack(v));
    const shown = currentEdits(await readBack(v)).get(ORIGINAL)!;
    expect(shown.recipe).toBeUndefined();
    expect(shown.recipeBlob).toBeTruthy();
    const back = await readEditRecipe('t', v, shown);
    expect(back?.overlays[0]).toMatchObject({ kind: 'drawing' });
    expect(back?.overlays[0].kind === 'drawing' && back.overlays[0].strokes[0].points).toHaveLength(3000);
  });

  it('reverts to the original, and an edit after that shows again', async () => {
    const v = await vault();
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe()), await readBack(v));
    const edited = currentEdits(await readBack(v)).get(ORIGINAL)!;
    await revertGalleryEdit('t', v, ORIGINAL, await readBack(v));
    expect(currentEdits(await readBack(v)).has(ORIGINAL)).toBe(false);
    expect(server.blobs.has(edited.blob)).toBe(false);
    expect(server.blobs.has(ORIGINAL)).toBe(true);
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe({ filter: 'ocean' })), await readBack(v));
    expect(currentEdits(await readBack(v)).get(ORIGINAL)?.recipe?.filter).toBe('ocean');
  });

  it('the other parent edits after me: theirs shows on both phones, and my older edit is left alone until I edit again', async () => {
    const v = await vault();
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe({ filter: 'warm' })), await readBack(v));
    server.me = 'sam';
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe({ filter: 'cool' })), await readBack(v, 'sam'));
    expect(currentEdits(await readBack(v, 'alex')).get(ORIGINAL)?.recipe?.filter).toBe('cool');
    expect(currentEdits(await readBack(v, 'sam')).get(ORIGINAL)?.recipe?.filter).toBe('cool');
    expect(server.entries.filter((e) => e.by === 'alex')).toHaveLength(1);
  });

  it('deleting the picture removes its edits: every edited blob, and the records this phone wrote', async () => {
    const v = await vault();
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe()), await readBack(v));
    server.me = 'sam';
    await saveGalleryEdit('t', v, ORIGINAL, picture(recipe({ filter: 'pop' })), await readBack(v, 'sam'));
    server.blobs.set(OTHER, new Uint8Array([4]));
    await saveGalleryEdit('t', v, OTHER, picture(recipe()), await readBack(v, 'sam'));
    const before = await readBack(v, 'sam');
    const editedBlobs = [...currentEdits(before).values()].map((e) => e.blob);
    expect(editsOf(before, [ORIGINAL])).toHaveLength(2);

    await deleteGalleryEdits('t', before, [ORIGINAL]);
    const after = await readBack(v, 'sam');
    // Sam's own record for it is gone; Alex's cannot be deleted by Sam, but its picture is, and it names a deleted original.
    expect(editsOf(after, [ORIGINAL]).map((e) => e.own)).toEqual([false]);
    for (const e of editsOf(before, [ORIGINAL])) for (const b of e.blobs) expect(server.blobs.has(b)).toBe(false);
    // The other picture's edit is untouched.
    expect(currentEdits(after).get(OTHER)?.blob).toBe(editedBlobs[1]);
    expect(server.blobs.has(editedBlobs[1])).toBe(true);
  });
});
