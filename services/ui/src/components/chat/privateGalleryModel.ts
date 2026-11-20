// The private chat's encrypted gallery, built from the chat's own sealed
// messages (every picture ever sent, and the comments on each) plus the
// pictures added straight to the gallery (sealed gallery entries, never chat
// messages). Newest first, by day. Kept free of React so it can be tested
// directly. Nothing here is stored: it is rebuilt from what was decrypted, and
// dropped on lock.
import type { AddedPicture, PrivatePayload, VideoPayload } from '../../lib/privateChatCrypto';
import { dayLabel, isMine, type TalkMessage } from './chatModel';

export type Opened = PrivatePayload | 'unreadable';

export interface GalleryComment {
  messageId: number;
  text: string;
  /** Seconds since the epoch. */
  at: number;
  own: boolean;
  actorName: string;
}

export interface GalleryItem {
  /** The chat message that sent it (0 for a picture added straight to the gallery). */
  messageId: number;
  /** A video clip sent in the chat: `blob` is then its poster frame (what the tile shows and comments name). */
  video?: VideoPayload;
  /** The gallery entry, for a picture added straight to the gallery. */
  entryId?: string;
  blob: string;
  mime: string;
  name?: string;
  caption?: string;
  /** When it was sent or added, in seconds: the payload's own time, else the server's. */
  at: number;
  /** When the camera took it, in seconds, when known. */
  takenAt?: number;
  /** Where it sits in the gallery: when it was sent, or for an added picture when it was taken. */
  when: number;
  own: boolean;
  actorName: string;
  comments: GalleryComment[];
  /** What the original file said, for an added picture. */
  details?: Pick<AddedPicture, 'path' | 'taken_from' | 'camera' | 'gps' | 'width' | 'height' | 'source' | 'size'>;
  sha256?: string;
}

/** A gallery entry from the server, opened. */
export interface OpenedEntry {
  id: string;
  at: number;
  own: boolean;
  byName: string;
  payload: Opened;
}

export interface GalleryDay {
  key: string;
  label: string;
  items: GalleryItem[];
}

const seconds = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : undefined;

/**
 * Every picture in the chat and every picture added to the gallery, with their
 * comments. A deleted picture (or comment) is left out; a comment on a picture
 * that is gone is left out too. The same picture twice (same blob) is kept once.
 */
export function buildGallery(
  messages: TalkMessage[],
  opened: Record<string, Opened>,
  myActorIds: ReadonlySet<string>,
  added: OpenedEntry[] = [],
  now = Date.now(),
): GalleryItem[] {
  const items = new Map<string, GalleryItem>();
  const comments: Array<GalleryComment & { blob: string }> = [];
  for (const message of messages) {
    if (message.deleted || typeof message.id !== 'number') continue;
    const payload = opened[String(message.id)];
    if (!payload || payload === 'unreadable') continue;
    const talkAt = seconds(message.timestamp) ?? Math.floor(now / 1000);
    const own = isMine(message, myActorIds);
    const actorName = String(message.actor_display_name || '');
    if (payload.t === 'image') {
      const at = seconds(payload.sent_at) ?? talkAt;
      items.set(payload.blob, {
        messageId: message.id,
        blob: payload.blob,
        mime: payload.mime,
        ...(payload.name ? { name: payload.name } : {}),
        ...(payload.caption ? { caption: payload.caption } : {}),
        at,
        ...(seconds(payload.taken_at) ? { takenAt: seconds(payload.taken_at) } : {}),
        when: at,
        own,
        actorName,
        comments: [],
        ...(payload.sha256 ? { sha256: payload.sha256 } : {}),
      });
    } else if (payload.t === 'video') {
      // A clip is placed by when it was sent, like a sent picture, and known by its poster.
      const at = seconds(payload.sent_at) ?? talkAt;
      items.set(payload.poster, {
        messageId: message.id,
        video: payload,
        blob: payload.poster,
        mime: 'image/jpeg',
        ...(payload.name ? { name: payload.name } : {}),
        at,
        ...(seconds(payload.taken_at) ? { takenAt: seconds(payload.taken_at) } : {}),
        when: at,
        own,
        actorName,
        comments: [],
      });
    } else if (payload.t === 'comment') {
      comments.push({ blob: payload.blob, messageId: message.id, text: payload.text, at: seconds(payload.sent_at) ?? talkAt, own, actorName });
    }
  }
  for (const entry of added) {
    const payload = entry.payload;
    if (payload === 'unreadable' || payload.t !== 'added' || items.has(payload.blob)) continue;
    const at = seconds(payload.added_at) ?? seconds(entry.at) ?? Math.floor(now / 1000);
    const takenAt = seconds(payload.taken_at);
    const details: NonNullable<GalleryItem['details']> = {};
    for (const key of ['path', 'taken_from', 'camera', 'gps', 'width', 'height', 'source', 'size'] as const) {
      if (payload[key] !== undefined) (details as Record<string, unknown>)[key] = payload[key];
    }
    items.set(payload.blob, {
      messageId: 0,
      entryId: entry.id,
      blob: payload.blob,
      mime: payload.mime,
      ...(payload.name ? { name: payload.name } : {}),
      ...(payload.caption ? { caption: payload.caption } : {}),
      at,
      ...(takenAt ? { takenAt } : {}),
      // Years of imported pictures land on the days they were taken.
      when: takenAt ?? at,
      own: entry.own,
      actorName: entry.byName,
      comments: [],
      ...(Object.keys(details).length ? { details } : {}),
      ...(payload.sha256 ? { sha256: payload.sha256 } : {}),
    });
  }
  for (const { blob, ...comment } of comments) items.get(blob)?.comments.push(comment);
  const list = [...items.values()];
  for (const item of list) item.comments.sort((a, b) => a.at - b.at || a.messageId - b.messageId);
  return list.sort((a, b) => b.when - a.when || b.messageId - a.messageId || (b.entryId ?? '').localeCompare(a.entryId ?? ''));
}

/** The encrypted blobs a gallery item is made of (what deleting it deletes): a picture's one, or a clip's pieces and poster. */
export function itemBlobs(item: GalleryItem): string[] {
  return item.video ? [...item.video.chunks, item.video.poster] : [item.blob];
}

/** "3 photos", "1 video", "3 photos, 2 videos". */
export function countLabel(items: GalleryItem[]): string {
  const videos = items.filter((item) => item.video).length;
  const photos = items.length - videos;
  const word = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`;
  if (!videos) return word(photos, 'photo');
  if (!photos) return word(videos, 'video');
  return `${word(photos, 'photo')}, ${word(videos, 'video')}`;
}

/** The gallery by day, newest day first, each labelled like the chat's day separators. */
export function groupByDay(items: GalleryItem[], now: Date = new Date()): GalleryDay[] {
  const days: GalleryDay[] = [];
  for (const item of items) {
    const date = new Date(item.when * 1000);
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    const last = days[days.length - 1];
    if (last && last.key === key) last.items.push(item);
    else days.push({ key, label: dayLabel(item.when, now), items: [item] });
  }
  return days;
}

/** The full date and time a picture was sent, for the viewer. */
export function stampLabel(at: number): string {
  return new Date(at * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

/** What the gallery is showing: everything, only photos, or only videos. */
export type GalleryFilter = 'all' | 'photos' | 'videos';

export function filterGallery(items: GalleryItem[], filter: GalleryFilter): GalleryItem[] {
  if (filter === 'photos') return items.filter((item) => !item.video);
  if (filter === 'videos') return items.filter((item) => item.video);
  return items;
}

/** Panoramas and tall screenshots are cropped rather than allowed to make a row a sliver. */
export function clampAspect(aspect: number): number {
  if (!Number.isFinite(aspect) || aspect <= 0) return 1;
  return Math.min(2.4, Math.max(0.6, aspect));
}

/** Width over height of a picture when it is known before decrypting (an added picture's details). */
export function knownAspect(item: GalleryItem): number | undefined {
  const w = Number(item.details?.width);
  const h = Number(item.details?.height);
  return w > 0 && h > 0 ? clampAspect(w / h) : undefined;
}

export interface JustifiedRow {
  /** Indexes into the list that was laid out. */
  indexes: number[];
  /** Height in px; every tile in the row is this tall and `aspect * height` wide. */
  height: number;
}

/**
 * Lay pictures out in rows of equal height that fill the width exactly (the
 * Google Photos layout): add pictures to a row until, scaled to fill the
 * width, it would be no taller than the target. The last row keeps the target
 * height rather than blowing a lone picture up to the full width.
 */
export function justifyRows(aspects: number[], width: number, targetHeight: number, gap: number): JustifiedRow[] {
  const rows: JustifiedRow[] = [];
  if (width <= 0 || targetHeight <= 0) return rows;
  let start = 0;
  while (start < aspects.length) {
    let sum = 0;
    let end = start;
    let height = targetHeight;
    while (end < aspects.length) {
      sum += clampAspect(aspects[end]);
      end += 1;
      const fill = (width - gap * (end - start - 1)) / sum;
      if (fill <= targetHeight) {
        height = fill;
        break;
      }
      if (end === aspects.length) height = Math.min(targetHeight, fill);
    }
    const indexes: number[] = [];
    for (let i = start; i < end; i += 1) indexes.push(i);
    rows.push({ indexes, height: Math.max(1, Math.floor(height * 100) / 100) });
    start = end;
  }
  return rows;
}

/** A small seeded random generator (mulberry32), so a shuffle can be tested and repeated. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * The order a slideshow plays `count` items in, starting with `start`: in
 * gallery order from there (wrapping round), or shuffled with the starting
 * picture still first.
 */
export function slideshowOrder(count: number, start: number, shuffle: boolean, random: () => number = Math.random): number[] {
  if (count <= 0) return [];
  const first = Math.min(Math.max(0, start), count - 1);
  const rest: number[] = [];
  for (let i = 1; i < count; i += 1) rest.push((first + i) % count);
  if (shuffle) {
    for (let i = rest.length - 1; i > 0; i -= 1) {
      const j = Math.floor(random() * (i + 1));
      [rest[i], rest[j]] = [rest[j], rest[i]];
    }
  }
  return [first, ...rest];
}

/** The next position in a slideshow, or null past either end when it does not loop. */
export function stepSlideshow(position: number, length: number, delta: number, loop: boolean): number | null {
  if (length <= 0) return null;
  const next = position + delta;
  if (next >= 0 && next < length) return next;
  if (!loop) return null;
  return ((next % length) + length) % length;
}

/** Seconds each photo stays up in a slideshow. */
export const SLIDE_INTERVALS = [3, 5, 8, 12] as const;

/** How the slideshow moves from one picture to the next. */
export type SlideTransition = 'fade' | 'kenburns';

/**
 * A Ken Burns move for one slide: a slow zoom from one corner towards another,
 * different for each picture but the same every time it is shown.
 */
export function kenBurns(key: string): { fromX: number; fromY: number; toX: number; toY: number; fromScale: number; toScale: number } {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  const random = seededRandom(hash);
  const pick = () => Math.round((random() * 2 - 1) * 4 * 10) / 10;
  const zoomIn = random() < 0.5;
  return { fromX: pick(), fromY: pick(), toX: pick(), toY: pick(), fromScale: zoomIn ? 1.04 : 1.16, toScale: zoomIn ? 1.16 : 1.04 };
}
