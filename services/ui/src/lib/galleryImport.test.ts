import { describe, expect, it, vi } from 'vitest';
import JSZip from 'jszip';
import {
  captionsFrom,
  dateFromName,
  importPictures,
  isKnown,
  knownFrom,
  pictureFacts,
  pictureType,
  sha256Hex,
  sortPicked,
  zipSources,
  type ImportDeps,
  type ImportSource,
} from './galleryImport';
import type { AddedPicture } from './privateChatCrypto';
import { fakeJpeg } from '../test/fakes/fakeJpeg';

const local = (y: number, mo: number, d: number, h = 12, mi = 0, s = 0) => Math.floor(new Date(y, mo - 1, d, h, mi, s).getTime() / 1000);

function source(name: string, bytes: Uint8Array<ArrayBuffer>, extra: Partial<ImportSource> = {}): ImportSource {
  return { name, size: bytes.length, mime: 'image/jpeg', read: async () => bytes.slice(), ...extra };
}

function fakeDeps(overrides: Partial<ImportDeps> = {}) {
  const uploads: Uint8Array[] = [];
  const entries: AddedPicture[] = [];
  const deleted: string[] = [];
  const deps: ImportDeps = {
    encrypt: async (plain) => new Uint8Array([9, ...plain]),
    upload: async (sealed) => {
      uploads.push(sealed);
      return `blob${uploads.length}`;
    },
    seal: async (payload) => JSON.stringify(payload),
    addEntry: async (sealed) => {
      entries.push(JSON.parse(sealed) as AddedPicture);
    },
    deleteBlob: async (blob) => {
      deleted.push(blob);
    },
    ...overrides,
  };
  return { deps, uploads, entries, deleted };
}

describe('reading a picture without changing it', () => {
  it('finds dates in the names phones and apps write', () => {
    expect(dateFromName('IMG_20190512_143000.jpg')).toBe(local(2019, 5, 12, 14, 30, 0));
    expect(dateFromName('PXL_20210102_030405123.jpg')).toBe(local(2021, 1, 2, 3, 4, 5));
    expect(dateFromName('2018-07-04 21.15.09.jpg')).toBe(local(2018, 7, 4, 21, 15, 9));
    expect(dateFromName('Screenshot_2020-02-29-08-00-00.png')).toBe(local(2020, 2, 29, 8, 0, 0));
    expect(dateFromName('IMG-20170310-WA0001.jpg')).toBe(local(2017, 3, 10));
    expect(dateFromName('between/2016-11-20/photo_1.jpg')).toBe(local(2016, 11, 20));
    expect(dateFromName('photo.jpg')).toBeUndefined();
    expect(dateFromName('IMG_20190231_000000.jpg')).toBeUndefined();
  });

  it('takes EXIF first, then the name, then the file time, and keeps camera and GPS', () => {
    const withExif = fakeJpeg({ dateTimeOriginal: '2015:06:01 10:00:00', make: 'Acme', model: 'Acme Pocket', gps: { lat: 1.5, lon: 2.5 } });
    const facts = pictureFacts(withExif, 'IMG_20190512_143000.jpg', undefined, 1_600_000_000);
    expect(facts).toMatchObject({ takenAt: local(2015, 6, 1, 10), takenFrom: 'exif', camera: 'Acme Pocket' });
    expect(facts.gps?.lat).toBeCloseTo(1.5, 4);
    expect(pictureFacts(fakeJpeg(), 'IMG_20190512_143000.jpg', undefined, 1_600_000_000)).toMatchObject({ takenFrom: 'name' });
    expect(pictureFacts(fakeJpeg(), 'photo.jpg', 'export/2016-11-20/photo.jpg')).toMatchObject({ takenAt: local(2016, 11, 20), takenFrom: 'name' });
    expect(pictureFacts(fakeJpeg(), 'photo.jpg', undefined, 1_600_000_000)).toEqual({ takenAt: 1_600_000_000, takenFrom: 'file' });
    expect(pictureFacts(fakeJpeg({ make: 'Acme', model: 'Pocket 7' }), 'a.jpg').camera).toBe('Acme Pocket 7');
  });

  it('knows picture types by type or extension and sorts a selection', () => {
    expect(pictureType('a.JPG')).toBe('image/jpeg');
    expect(pictureType('a.bin', 'image/png')).toBe('image/png');
    expect(pictureType('a.mp4')).toBeUndefined();
    const sorted = sortPicked([
      new File([new Uint8Array([1])], 'a.jpg', { type: 'image/jpeg', lastModified: 1_500_000_000_000 }),
      new File([new Uint8Array([1])], 'backup.zip', { type: 'application/zip' }),
      new File([new Uint8Array([1])], 'clip.mp4', { type: 'video/mp4' }),
      new File([new Uint8Array([1])], 'notes.txt', { type: 'text/plain' }),
    ]);
    expect(sorted.pictures.map((p) => [p.name, p.fileTime])).toEqual([['a.jpg', 1_500_000_000]]);
    expect(sorted.zips.map((z) => z.name)).toEqual(['backup.zip']);
    expect(sorted.videos).toEqual(['clip.mp4']);
    expect(sorted.other).toEqual(['notes.txt']);
  });

  it('reads captions from a JSON file of a backup, in any nesting', () => {
    const captions = captionsFrom(JSON.stringify({ messages: [{ file: 'photos/a.jpg', text: 'Beach day' }, { text: 'no picture' }, { image: 'b.png', caption: ' Snow ' }] }));
    expect([...captions]).toEqual([['a.jpg', 'Beach day'], ['b.png', 'Snow']]);
    expect(captionsFrom('not json').size).toBe(0);
  });
});

describe('importing pictures into the gallery', () => {
  it('encrypts each picture, keeps its bytes and facts in a sealed entry, and reports progress', async () => {
    const a = fakeJpeg({ dateTimeOriginal: '2014:02:14 19:00:00', make: 'Acme', gps: { lat: 3, lon: 4 } }, 1);
    const b = fakeJpeg(undefined, 2);
    const { deps, uploads, entries } = fakeDeps();
    const progress = vi.fn();
    const result = await importPictures([source('a.jpg', a), source('IMG_20160101_101010.jpg', b, { fileTime: 1_700_000_000 })], knownFrom([]), deps, {
      source: 'between',
      captions: new Map([['a.jpg', 'Valentine']]),
      onProgress: progress,
      now: () => 1_800_000_000,
    });
    expect(result).toMatchObject({ added: 2, skipped: 0, failed: 0, done: 2, total: 2, stopped: false });
    // The original bytes, untouched, went into the encryption.
    expect(Array.from(uploads[0].slice(1))).toEqual(Array.from(a));
    expect(entries[0]).toMatchObject({
      t: 'added', blob: 'blob1', mime: 'image/jpeg', name: 'a.jpg', caption: 'Valentine', taken_at: local(2014, 2, 14, 19), taken_from: 'exif',
      added_at: 1_800_000_000, sha256: await sha256Hex(a.slice()), size: a.length, source: 'between', camera: 'Acme',
    });
    expect(entries[0].gps?.lat).toBeCloseTo(3, 4);
    expect(entries[1]).toMatchObject({ taken_at: local(2016, 1, 1, 10, 10, 10), taken_from: 'name', file_time: 1_700_000_000 });
    expect(progress).toHaveBeenLastCalledWith(expect.objectContaining({ done: 2, total: 2, added: 2 }));
  });

  it('skips what is already in the gallery and duplicates within the batch, so a second run resumes', async () => {
    const a = fakeJpeg(undefined, 1);
    const b = fakeJpeg(undefined, 2);
    const known = knownFrom([{ sha256: await sha256Hex(a.slice()) }, { name: 'old.jpg', takenAt: local(2010, 1, 1) }]);
    const { deps, entries } = fakeDeps();
    const result = await importPictures(
      [source('a.jpg', a), source('b.jpg', b), source('b copy.jpg', b), source('old.jpg', fakeJpeg(undefined, 3), { fileTime: local(2010, 1, 1) })],
      known,
      deps,
      { source: 'phone' },
    );
    expect(result).toMatchObject({ added: 1, skipped: 3 });
    expect(entries.map((e) => e.name)).toEqual(['b.jpg']);
    expect(isKnown(known, await sha256Hex(b.slice()), 'x')).toBe(true);
  });

  it('a failure removes the uploaded blob, the rest carry on, and running again adds only what failed', async () => {
    const pics = [1, 2, 3].map((n) => source(`p${n}.jpg`, fakeJpeg(undefined, n)));
    let fail = true;
    const first = fakeDeps({
      addEntry: async (sealed) => {
        const entry = JSON.parse(sealed) as AddedPicture;
        if (fail && entry.name === 'p2.jpg') throw new Error('network down');
        first.entries.push(entry);
      },
    });
    const known = knownFrom([]);
    const result = await importPictures(pics, known, first.deps, { source: 'phone' });
    expect(result).toMatchObject({ added: 2, failed: 1 });
    expect(result.failures).toEqual([{ name: 'p2.jpg', reason: 'network down' }]);
    expect(first.deleted).toEqual(['blob2']);
    fail = false;
    const again = await importPictures(pics, known, first.deps, { source: 'phone' });
    expect(again).toMatchObject({ added: 1, skipped: 2 });
  });

  it('stops when asked, and stops at once when the chat locks', async () => {
    const pics = [1, 2, 3].map((n) => source(`p${n}.jpg`, fakeJpeg(undefined, n)));
    let calls = 0;
    const { deps } = fakeDeps();
    const stopped = await importPictures(pics, knownFrom([]), deps, { source: 'phone', shouldStop: () => ++calls > 1 });
    expect(stopped).toMatchObject({ added: 1, stopped: true });
    const locked = await importPictures(pics, knownFrom([]), fakeDeps({ encrypt: async () => Promise.reject(new Error('The chat is locked.')) }).deps, { source: 'phone' });
    expect(locked).toMatchObject({ added: 0, failed: 1, stopped: true, done: 1 });
  });

  it('refuses a picture over 25 MB without reading it', async () => {
    const read = vi.fn();
    const { deps } = fakeDeps();
    const result = await importPictures([{ name: 'huge.jpg', size: 26 * 1024 * 1024, mime: 'image/jpeg', read }], knownFrom([]), deps, { source: 'phone' });
    expect(result.failures[0].reason).toMatch(/25 MB/);
    expect(read).not.toHaveBeenCalled();
  });
});

describe('a zip export (a Between backup)', () => {
  it('lists the pictures and captions inside, leaves out videos and junk, and reads each picture as it was', async () => {
    const zip = new JSZip();
    const a = fakeJpeg({ dateTimeOriginal: '2013:08:09 07:06:05' }, 1);
    const b = fakeJpeg(undefined, 2);
    zip.file('Between/photos/a.jpg', a, { compression: 'DEFLATE' });
    zip.file('Between/photos/2012-12-25/b.jpg', b, { compression: 'STORE' });
    zip.file('Between/videos/clip.mp4', new Uint8Array([0, 1, 2]));
    zip.file('__MACOSX/Between/photos/._a.jpg', new Uint8Array([0]));
    zip.file('Between/chat.json', JSON.stringify([{ photo: 'a.jpg', message: 'First trip' }]));
    const blob = new Blob([await zip.generateAsync({ type: 'arraybuffer' })], { type: 'application/zip' });
    const inside = await zipSources(blob);
    expect(inside.pictures.map((p) => p.path).sort()).toEqual(['Between/photos/2012-12-25/b.jpg', 'Between/photos/a.jpg']);
    expect(inside.videos).toEqual(['Between/videos/clip.mp4']);
    expect(inside.captions.get('a.jpg')).toBe('First trip');
    const readA = await inside.pictures.find((p) => p.name === 'a.jpg')!.read();
    expect(Array.from(readA)).toEqual(Array.from(a));

    const { deps, entries } = fakeDeps();
    const result = await importPictures(inside.pictures, knownFrom([]), deps, { source: 'between', captions: inside.captions });
    expect(result.added).toBe(2);
    const byName = Object.fromEntries(entries.map((e) => [e.name, e]));
    expect(byName['a.jpg']).toMatchObject({ caption: 'First trip', taken_from: 'exif', path: 'Between/photos/a.jpg' });
    expect(byName['b.jpg']).toMatchObject({ taken_at: local(2012, 12, 25), taken_from: 'name' });
  });

  it('says plainly when a file is not a zip', async () => {
    await expect(zipSources(new Blob([new Uint8Array(100)]))).rejects.toThrow(/not a zip/);
  });
});
