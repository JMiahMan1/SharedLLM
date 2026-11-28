import { describe, expect, it } from 'vitest';
import type { TalkMessage } from './chatModel';
import type { AudioPayload } from '../../lib/privateChatCrypto';
import {
  buildGallery,
  clampAspect,
  commandOf,
  countLabel,
  filterGallery,
  galleryCommand,
  galleryMembership,
  GALLERY_RECORD_BYTES,
  inGallery,
  markRecords,
  padded,
  startCandidates,
  startRecords,
  groupByDay,
  itemBlobs,
  justifyRows,
  kenBurns,
  knownAspect,
  seededRandom,
  slideshowOrder,
  stepSlideshow,
  type GalleryItem,
  type Opened,
  type OpenedEntry,
} from './privateGalleryModel';
import type { VideoPayload } from '../../lib/privateChatCrypto';

const me = new Set(['alex']);
const day = (y: number, m: number, d: number, h = 12) => Math.floor(new Date(y, m - 1, d, h).getTime() / 1000);

function msg(id: number, actor: string, timestamp: number, extra: Partial<TalkMessage> = {}): TalkMessage {
  return { id, actor_type: 'users', actor_id: actor, actor_display_name: actor === 'alex' ? 'Alex' : 'Sam', timestamp, message: 'x', ...extra } as TalkMessage;
}

describe('the private gallery', () => {
  const messages = [
    msg(1, 'sam', day(2026, 11, 1, 9)),
    msg(2, 'alex', day(2026, 11, 1, 10)),
    msg(3, 'sam', day(2026, 11, 2, 8)),
    msg(4, 'alex', day(2026, 11, 2, 9)),
    msg(5, 'sam', day(2026, 11, 2, 9)),
    msg(6, 'alex', day(2026, 11, 3, 9), { deleted: true }),
    msg(7, 'sam', day(2026, 11, 3, 10)),
    msg(8, 'alex', day(2026, 11, 3, 11)),
  ];
  const opened: Record<string, Opened> = {
    // An older picture: no timestamp inside, so the Talk time is used.
    '1': { t: 'image', blob: 'b1', mime: 'image/jpeg' },
    '2': { t: 'text', text: 'not a picture' },
    // A newer picture with its own sent and taken times.
    '3': { t: 'image', blob: 'b3', mime: 'image/jpeg', sent_at: day(2026, 11, 2, 7), taken_at: day(2026, 11, 1, 20) },
    '4': { t: 'comment', blob: 'b3', text: 'Lovely' },
    '5': { t: 'comment', blob: 'b3', text: 'Thanks', sent_at: day(2026, 11, 2, 9) + 1 },
    '6': { t: 'image', blob: 'b6', mime: 'image/png' },
    '7': { t: 'comment', blob: 'gone', text: 'on a deleted photo' },
    '8': 'unreadable',
  };

  it('lists every picture newest first, with when it was sent, and its comments in order', () => {
    const items = buildGallery(messages, opened, me);
    expect(items.map((i) => i.blob)).toEqual(['b3', 'b1']);
    const [b3, b1] = items;
    expect(b3.at).toBe(day(2026, 11, 2, 7));
    expect(b3.takenAt).toBe(day(2026, 11, 1, 20));
    expect(b1.at).toBe(day(2026, 11, 1, 9));
    expect(b1.takenAt).toBeUndefined();
    expect(b3.comments.map((c) => [c.text, c.own])).toEqual([['Lovely', true], ['Thanks', false]]);
    expect(b1.comments).toEqual([]);
    expect(b3.own).toBe(false);
  });

  it('leaves out deleted pictures and comments on pictures that are gone', () => {
    const items = buildGallery(messages, opened, me);
    expect(items.find((i) => i.blob === 'b6')).toBeUndefined();
    expect(items.flatMap((i) => i.comments).map((c) => c.text)).not.toContain('on a deleted photo');
  });

  it('groups by day, newest day first', () => {
    const days = groupByDay(buildGallery(messages, opened, me), new Date(2026, 10, 2, 18));
    expect(days.map((d) => [d.key, d.items.length])).toEqual([['2026-11-02', 1], ['2026-11-01', 1]]);
    expect(days[0].label).toBe('Today');
    expect(days[1].label).toBe('Yesterday');
  });

  it('includes pictures added to the gallery, placed on the day they were taken, with their details and comments', () => {
    const added: OpenedEntry[] = [
      { id: 'e1', at: day(2026, 11, 3, 12), own: true, byName: 'Alex',
        payload: { t: 'added', blob: 'old', mime: 'image/jpeg', name: 'IMG_1.jpg', taken_at: day(2016, 5, 12, 14), taken_from: 'exif', added_at: day(2026, 11, 3, 12),
          source: 'between', camera: 'Acme', gps: { lat: 1, lon: 2 }, sha256: 'a'.repeat(64) } },
      // No taken time: it sits on the day it was added.
      { id: 'e2', at: day(2026, 11, 3, 13), own: false, byName: 'Sam', payload: { t: 'added', blob: 'undated', mime: 'image/png' } },
      { id: 'e3', at: day(2026, 11, 3, 14), own: false, byName: 'Sam', payload: 'unreadable' },
      // A sent picture's blob is never listed twice.
      { id: 'e4', at: day(2026, 11, 3, 15), own: true, byName: 'Alex', payload: { t: 'added', blob: 'b3', mime: 'image/jpeg' } },
    ];
    const withComment = [...messages, msg(9, 'sam', day(2026, 11, 4, 9))];
    const items = buildGallery(withComment, { ...opened, '9': { t: 'comment', blob: 'old', text: 'Ten years ago!' } }, me, added);
    expect(items.map((i) => i.blob)).toEqual(['undated', 'b3', 'b1', 'old']);
    const old = items.find((i) => i.blob === 'old')!;
    expect(old).toMatchObject({ entryId: 'e1', messageId: 0, own: true, actorName: 'Alex', takenAt: day(2016, 5, 12, 14), when: day(2016, 5, 12, 14), sha256: 'a'.repeat(64) });
    expect(old.details).toEqual({ taken_from: 'exif', source: 'between', camera: 'Acme', gps: { lat: 1, lon: 2 } });
    expect(old.comments.map((c) => [c.text, c.own])).toEqual([['Ten years ago!', false]]);
    expect(items.find((i) => i.blob === 'undated')).toMatchObject({ when: day(2026, 11, 3, 13), own: false });
    expect(items.find((i) => i.blob === 'b3')?.entryId).toBeUndefined();
    const days = groupByDay(items, new Date(2026, 10, 3, 18));
    expect(days.map((d) => d.key)).toEqual(['2026-11-03', '2026-11-02', '2026-11-01', '2016-05-12']);
    expect(days[3].label).toMatch(/2016/);
  });
});

describe('video clips in the gallery', () => {
  const clip = (poster: string, extra: Partial<VideoPayload> = {}): VideoPayload => ({
    t: 'video', mime: 'video/webm', size: 9000, duration: 42, width: 640, height: 360, poster,
    chunks: ['c1'.padEnd(32, '0'), 'c2'.padEnd(32, '0')], clip: 'AAAAAAAAAAAAAAAAAAAAAA==', chunk_bytes: 4096, ...extra,
  });

  it('sits beside the pictures by when it was sent, known by its poster, with its comments', () => {
    const messages = [msg(1, 'sam', day(2026, 11, 1, 9)), msg(2, 'alex', day(2026, 11, 2, 9)), msg(3, 'sam', day(2026, 11, 2, 10))];
    const opened: Record<string, Opened> = {
      '1': { t: 'image', blob: 'pic', mime: 'image/jpeg' },
      '2': clip('poster2', { sent_at: day(2026, 11, 2, 8), taken_at: day(2026, 11, 1, 18), name: 'beach.mp4' }),
      '3': { t: 'comment', blob: 'poster2', text: 'Play it again' },
    };
    const items = buildGallery(messages, opened, me);
    expect(items.map((i) => i.blob)).toEqual(['poster2', 'pic']);
    const [video, picture] = items;
    expect(video).toMatchObject({ messageId: 2, mime: 'image/jpeg', at: day(2026, 11, 2, 8), when: day(2026, 11, 2, 8), takenAt: day(2026, 11, 1, 18), name: 'beach.mp4', own: true });
    expect(video.video?.chunks).toHaveLength(2);
    expect(video.comments.map((c) => c.text)).toEqual(['Play it again']);
    expect(picture.video).toBeUndefined();
    // Deleting the clip deletes every piece and the poster; a picture, its one blob.
    expect(itemBlobs(video)).toEqual([...(video.video?.chunks ?? []), 'poster2']);
    expect(itemBlobs(picture)).toEqual(['pic']);
  });

  it('a deleted clip is left out', () => {
    const items = buildGallery([msg(1, 'sam', day(2026, 11, 1), { deleted: true })], { '1': clip('p') }, me);
    expect(items).toEqual([]);
  });

  it('counts photos and videos', () => {
    const items = buildGallery(
      [msg(1, 'sam', day(2026, 11, 1)), msg(2, 'sam', day(2026, 11, 2)), msg(3, 'sam', day(2026, 11, 3))],
      { '1': { t: 'image', blob: 'a', mime: 'image/jpeg' }, '2': clip('p2'), '3': { t: 'image', blob: 'b', mime: 'image/jpeg' } },
      me,
    );
    expect(countLabel(items)).toBe('2 photos, 1 video');
    expect(countLabel(items.filter((i) => !i.video))).toBe('2 photos');
    expect(countLabel(items.filter((i) => i.video))).toBe('1 video');
    expect(countLabel([])).toBe('0 photos');
    expect(countLabel(items.slice(-1))).toBe('1 photo');
  });
});

const item = (blob: string, extra: Partial<GalleryItem> = {}): GalleryItem => ({
  messageId: 1, blob, mime: 'image/jpeg', at: 1, when: 1, own: true, actorName: 'Alex', comments: [], ...extra,
});

describe('the gallery grid', () => {
  it('filters photos and videos', () => {
    const video = { chunks: ['c'], poster: 'p', duration: 2 } as unknown as GalleryItem['video'];
    const list = [item('a'), item('p', { video }), item('b')];
    expect(filterGallery(list, 'all')).toHaveLength(3);
    expect(filterGallery(list, 'photos').map((i) => i.blob)).toEqual(['a', 'b']);
    expect(filterGallery(list, 'videos').map((i) => i.blob)).toEqual(['p']);
  });

  it('knows a shape before decrypting only when the details carry one, and keeps extremes sane', () => {
    expect(knownAspect(item('a'))).toBeUndefined();
    expect(knownAspect(item('a', { details: { width: 4000, height: 3000 } }))).toBeCloseTo(4 / 3);
    expect(clampAspect(10)).toBe(2.4);
    expect(clampAspect(0.1)).toBe(0.6);
    expect(clampAspect(Number.NaN)).toBe(1);
  });

  it('lays rows out to fill the width exactly, leaving the last row at the target height', () => {
    const aspects = [1.5, 1, 0.75, 1.5, 1, 1];
    const rows = justifyRows(aspects, 600, 150, 4);
    expect(rows.flatMap((r) => r.indexes)).toEqual([0, 1, 2, 3, 4, 5]);
    for (const row of rows.slice(0, -1)) {
      const used = row.indexes.reduce((sum, i) => sum + aspects[i] * row.height, 0) + 4 * (row.indexes.length - 1);
      expect(used).toBeGreaterThan(598);
      expect(used).toBeLessThanOrEqual(600.5);
      expect(row.height).toBeLessThanOrEqual(150);
    }
    // One wide picture alone is not blown up past the target.
    expect(justifyRows([1], 1000, 150, 4)).toEqual([{ indexes: [0], height: 150 }]);
    expect(justifyRows([1, 1], 0, 150, 4)).toEqual([]);
  });
});

describe('the slideshow', () => {
  it('plays in gallery order from the chosen picture, wrapping round', () => {
    expect(slideshowOrder(5, 2, false)).toEqual([2, 3, 4, 0, 1]);
    expect(slideshowOrder(0, 0, false)).toEqual([]);
    expect(slideshowOrder(3, 9, false)).toEqual([2, 0, 1]);
  });

  it('shuffles the rest but keeps the chosen picture first, the same way for the same seed', () => {
    const a = slideshowOrder(20, 7, true, seededRandom(42));
    const b = slideshowOrder(20, 7, true, seededRandom(42));
    expect(a).toEqual(b);
    expect(a[0]).toBe(7);
    expect([...a].sort((x, y) => x - y)).toEqual([...Array(20).keys()]);
    expect(a).not.toEqual(slideshowOrder(20, 7, false));
  });

  it('steps forward and back, looping or stopping at the ends', () => {
    expect(stepSlideshow(1, 3, 1, false)).toBe(2);
    expect(stepSlideshow(2, 3, 1, false)).toBeNull();
    expect(stepSlideshow(2, 3, 1, true)).toBe(0);
    expect(stepSlideshow(0, 3, -1, true)).toBe(2);
    expect(stepSlideshow(0, 3, -1, false)).toBeNull();
    expect(stepSlideshow(0, 0, 1, true)).toBeNull();
  });

  it('gives each picture its own steady Ken Burns move', () => {
    expect(kenBurns('abc')).toEqual(kenBurns('abc'));
    const move = kenBurns('abc');
    expect(Math.abs(move.fromX)).toBeLessThanOrEqual(4);
    expect(move.fromScale).not.toBe(move.toScale);
  });
});

describe('gallery commands in the chat', () => {
  it('reads a whole message asking to save or remove the picture it answers', () => {
    for (const text of [
      'save to gallery',
      'Save to gallery!',
      'Add to gallery.',
      'save this to the gallery',
      'Save it to our gallery please',
      'please add this one to the gallery',
      'put that photo in the gallery',
      'Can you save this to the gallery?',
      'keep this in the gallery, thanks',
      '  SAVE   TO   GALLERY  ',
    ]) {
      expect(galleryCommand(text), text).toBe('add');
    }
    for (const text of ['remove from gallery', 'Remove this from the gallery.', 'take it out of the gallery', 'take this off the gallery please']) {
      expect(galleryCommand(text), text).toBe('remove');
    }
  });

  it('ignores ordinary sentences that only mention the gallery', () => {
    for (const text of [
      'we should save to gallery later?',
      'did you save to gallery yet',
      'save to gallery and then call me',
      'I love our gallery',
      'gallery',
      'save',
      'don\'t save to gallery',
      'remove from gallery? no, keep it',
      'add to the gallery of shame lol',
      '',
    ]) {
      expect(galleryCommand(text), text).toBeNull();
    }
  });

  it('counts only a reply that answers a picture: the same words with nothing quoted are just words', () => {
    const blob = 'c'.repeat(32);
    expect(commandOf({ t: 'text', text: 'save to gallery', quote: { m: 4, blob } })).toEqual({ op: 'add', blob });
    expect(commandOf({ t: 'text', text: 'save to gallery' })).toBeNull();
    expect(commandOf({ t: 'text', text: 'we should save to gallery later?', quote: { m: 4, blob } })).toBeNull();
    expect(commandOf({ t: 'comment', blob, text: 'save to gallery' })).toBeNull();
    expect(commandOf('unreadable')).toBeNull();
  });
});

describe('gallery membership', () => {
  const START = day(2026, 11, 20, 12);
  const still = 'a'.repeat(32);
  const gif = 'b'.repeat(32);
  const png = 'c'.repeat(32);
  const clip = 'd'.repeat(32);
  const newStill = 'e'.repeat(32);
  const newGif = 'f'.repeat(32);
  const video = (poster: string): VideoPayload => ({ t: 'video', mime: 'video/mp4', size: 10, duration: 2, poster, chunks: ['1'.repeat(32)], clip: 'AAAAAAAAAAAAAAAAAAAAAA==', chunk_bytes: 10 });
  const messages = [
    msg(1, 'sam', START - 300),
    msg(2, 'alex', START - 200),
    msg(3, 'sam', START - 100),
    msg(4, 'alex', START - 50),
    msg(5, 'sam', START + 100),
    msg(6, 'alex', START + 200),
  ];
  const opened: Record<string, Opened> = {
    '1': { t: 'image', blob: still, mime: 'image/jpeg' },
    '2': { t: 'image', blob: gif, mime: 'image/gif' },
    '3': { t: 'image', blob: png, mime: 'image/png' },
    '4': video(clip),
    '5': { t: 'image', blob: newStill, mime: 'image/jpeg' },
    '6': { t: 'image', blob: newGif, mime: 'image/gif' },
  };
  const entry = (id: string, at: number, payload: Opened): OpenedEntry => ({ id, at, own: true, byName: 'Alex', payload });
  const start = (at: number, kept: string[], id = 's'.repeat(32)) => entry(id, at, { t: 'gallery_start', kept });

  it('before the start: stills and clips stay, animated pictures and anything sent later leave', () => {
    const m = galleryMembership(messages, opened, [start(START, [png])]);
    expect(m.started).toBe(true);
    expect(m.since).toBe(START);
    // The PNG was read and found still, so it stays; the GIF was not kept; nothing sent after the start is in.
    expect([...m.members].sort()).toEqual([still, png, clip].sort());
  });

  it('nothing sent after the start is in until added, by the button or by a "save to gallery" reply', () => {
    const replies = [...messages, msg(7, 'sam', START + 300), msg(8, 'alex', START + 400)];
    const said: Record<string, Opened> = {
      ...opened,
      '7': { t: 'text', text: 'Save to gallery!', quote: { m: 6, blob: newGif } },
      // The same words with no picture answered do nothing.
      '8': { t: 'text', text: 'save to gallery' },
    };
    const m = galleryMembership(replies, said, [start(START, []), entry('1'.repeat(32), START + 250, { t: 'gallery', op: 'add', blobs: [newStill] })]);
    expect(m.members.has(newStill)).toBe(true);
    expect(m.members.has(newGif)).toBe(true);
  });

  it('adds and removes apply oldest first; a removed picture stays in the chat but leaves the gallery', () => {
    const entries = [
      start(START, []),
      entry('2'.repeat(32), START + 500, { t: 'gallery', op: 'remove', blobs: [still] }),
      entry('3'.repeat(32), START + 400, { t: 'gallery', op: 'add', blobs: [still, newStill] }),
    ];
    const m = galleryMembership(messages, opened, entries);
    expect(m.members.has(still)).toBe(false);
    expect(m.members.has(newStill)).toBe(true);
    // A reply after that puts it back.
    const more = [...messages, msg(9, 'sam', START + 600)];
    const back = galleryMembership(more, { ...opened, '9': { t: 'text', text: 'add this to the gallery', quote: { m: 1, blob: still } } }, entries);
    expect(back.members.has(still)).toBe(true);
  });

  it('two phones racing to write the start agree: the earliest counts and the kept lists merge', () => {
    const a = galleryMembership(messages, opened, [start(START + 150, [png], '7'.repeat(32)), start(START, [], '8'.repeat(32))]);
    const b = galleryMembership(messages, opened, [start(START, [], '8'.repeat(32)), start(START + 150, [png], '7'.repeat(32))]);
    expect([...a.members].sort()).toEqual([...b.members].sort());
    expect(a.since).toBe(START);
    // The still sent between the two starts is after the earliest: not in.
    expect(a.members.has(newStill)).toBe(false);
    expect(a.members.has(png)).toBe(true);
  });

  it('with no start written yet, it shows what the start would keep if written now', () => {
    const m = galleryMembership(messages, opened, []);
    expect(m.started).toBe(false);
    expect([...m.members].sort()).toEqual([still, clip, newStill].sort());
  });

  it('a deleted picture is out whatever was recorded, and a record naming something not in the chat does nothing', () => {
    const gone = messages.map((m) => (m.id === 1 ? { ...m, deleted: true } : m));
    const m = galleryMembership(gone, opened, [start(START, []), entry('4'.repeat(32), START + 10, { t: 'gallery', op: 'add', blobs: [still, '9'.repeat(32)] })]);
    expect(m.members.has(still)).toBe(false);
    expect(m.members.has('9'.repeat(32))).toBe(false);
  });

  it('the gallery is its added pictures plus the chat pictures put in it', () => {
    const items = buildGallery(messages, opened, me, [entry('5'.repeat(32), START, { t: 'added', blob: '6'.repeat(32), mime: 'image/jpeg' })]);
    const shown = inGallery(items, new Set([still]));
    expect(shown.map((i) => i.blob).sort()).toEqual([still, '6'.repeat(32)].sort());
  });

  it('the start reads only the pictures that might move', () => {
    expect(startCandidates(messages, opened).map((c) => c.blob).sort()).toEqual([gif, png, newGif].sort());
  });
});

describe('gallery records on the server', () => {
  it('are all padded to one size, whatever they say and however many pictures they name', () => {
    const one = padded({ t: 'gallery', op: 'add', blobs: ['a'.repeat(32)] });
    const many = padded({ t: 'gallery', op: 'remove', blobs: Array.from({ length: 180 }, (_, i) => i.toString(16).padStart(32, '0')) });
    const begin = padded({ t: 'gallery_start', kept: [] });
    for (const record of [one, many, begin]) expect(JSON.stringify(record).length).toBe(GALLERY_RECORD_BYTES);
  });

  it('split long lists, drop repeats, and the start always writes at least one record', () => {
    const blobs = Array.from({ length: 400 }, (_, i) => i.toString(16).padStart(32, '0'));
    const records = markRecords('add', [...blobs, blobs[0]]);
    expect(records.map((r) => r.blobs.length)).toEqual([180, 180, 40]);
    expect(startRecords([])).toHaveLength(1);
    expect(startRecords([])[0].kept).toEqual([]);
    expect(markRecords('add', [])).toEqual([]);
  });
});

describe('a voice note in the gallery', () => {
  const BLOB = 'c'.repeat(32);
  const note = (extra: Partial<AudioPayload> = {}): AudioPayload => ({
    t: 'audio', blob: BLOB, mime: 'audio/webm', duration: 14, caption: 'listen to this', name: 'voice-note.webm', ...extra,
  });

  it('sits beside the pictures, known by the recording itself', () => {
    const items = buildGallery(
      [msg(1, 'sam', day(2026, 11, 1, 9)), msg(2, 'alex', day(2026, 11, 1, 10))],
      { '1': { t: 'image', blob: 'pic', mime: 'image/jpeg' }, '2': note() },
      me,
    );
    // Newest first, like the pictures: the note came a minute after the photo.
    expect(items.map((i) => i.blob)).toEqual([BLOB, 'pic']);
    const recording = items[0];
    expect(recording).toMatchObject({ messageId: 2, mime: 'audio/webm', caption: 'listen to this', name: 'voice-note.webm', own: true, at: day(2026, 11, 1, 10) });
    expect(recording.audio?.duration).toBe(14);
    expect(recording.video).toBeUndefined();
    // Nothing to show means nothing to decrypt: the tile draws an icon instead of an image.
    expect(itemBlobs(recording)).toEqual([BLOB]);
    expect(inGallery(items, new Set([BLOB]))).toEqual([recording]);
    expect(inGallery(items, new Set(['somebody-elses']))).toEqual([]);
  });

  it('is counted as a voice note, not as a photo', () => {
    const items = buildGallery(
      [msg(1, 'sam', day(2026, 11, 1)), msg(2, 'sam', day(2026, 11, 2)), msg(3, 'sam', day(2026, 11, 3)), msg(4, 'sam', day(2026, 11, 4))],
      {
        '1': { t: 'image', blob: 'a', mime: 'image/jpeg' },
        '2': note(),
        '3': note({ blob: 'd'.repeat(32), caption: undefined }),
        '4': { t: 'video', mime: 'video/mp4', size: 10, duration: 2, poster: 'p', chunks: ['1'.repeat(32)], clip: 'AAAAAAAAAAAAAAAAAAAAAA==', chunk_bytes: 10 },
      },
      me,
    );
    expect(countLabel(items)).toBe('1 photo, 1 video, 2 voice notes');
    expect(countLabel(items.filter((i) => i.audio))).toBe('2 voice notes');
    expect(countLabel(items.filter((i) => i.audio).slice(0, 1))).toBe('1 voice note');
  });

  it('has its own filter, and is never one of the photos', () => {
    const items = buildGallery(
      [msg(1, 'sam', day(2026, 11, 1)), msg(2, 'sam', day(2026, 11, 2)), msg(3, 'sam', day(2026, 11, 3))],
      { '1': { t: 'image', blob: 'a', mime: 'image/jpeg' }, '2': note(), '3': note({ blob: 'd'.repeat(32) }) },
      me,
    );
    expect(filterGallery(items, 'audio').map((i) => i.blob)).toEqual(['d'.repeat(32), BLOB]);
    expect(filterGallery(items, 'photos').map((i) => i.blob)).toEqual(['a']);
    expect(filterGallery(items, 'all')).toHaveLength(3);
  });

  it('carries the comments left on it', () => {
    const items = buildGallery(
      [msg(1, 'sam', day(2026, 11, 1)), msg(2, 'alex', day(2026, 11, 1, 11), { message: 'On a voice note: that was lovely' } as never)],
      { '1': note(), '2': { t: 'comment', text: 'that was lovely', blob: BLOB } },
      me,
    );
    expect(items[0].comments.map((c) => c.text)).toEqual(['that was lovely']);
  });
});
