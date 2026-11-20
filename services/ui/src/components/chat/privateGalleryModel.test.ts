import { describe, expect, it } from 'vitest';
import type { TalkMessage } from './chatModel';
import {
  buildGallery,
  clampAspect,
  countLabel,
  filterGallery,
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
