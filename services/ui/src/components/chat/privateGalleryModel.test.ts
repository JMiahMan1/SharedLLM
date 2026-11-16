import { describe, expect, it } from 'vitest';
import type { TalkMessage } from './chatModel';
import { buildGallery, groupByDay, type Opened, type OpenedEntry } from './privateGalleryModel';

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
