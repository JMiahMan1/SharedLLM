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
