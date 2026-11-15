import { describe, expect, it } from 'vitest';
import type { TalkMessage } from './chatModel';
import { buildGallery, groupByDay, type Opened } from './privateGalleryModel';

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
});
