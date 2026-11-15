// The private chat's encrypted gallery, built from the chat's own sealed
// messages: every picture ever sent (newest first, by day) and the comments
// on each. Kept free of React so it can be tested directly. Nothing here is
// stored: it is rebuilt from what was decrypted, and dropped on lock.
import type { PrivatePayload } from '../../lib/privateChatCrypto';
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
  messageId: number;
  blob: string;
  mime: string;
  name?: string;
  caption?: string;
  /** When it was sent, in seconds: the payload's `sent_at`, else the Talk message time (older pictures). */
  at: number;
  /** When the camera took it, in seconds, when known. */
  takenAt?: number;
  own: boolean;
  actorName: string;
  comments: GalleryComment[];
}

export interface GalleryDay {
  key: string;
  label: string;
  items: GalleryItem[];
}

const seconds = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : undefined;

/**
 * Every picture in the chat with its comments. A deleted picture (or comment)
 * is left out; a comment on a picture that is gone is left out too.
 */
export function buildGallery(messages: TalkMessage[], opened: Record<string, Opened>, myActorIds: ReadonlySet<string>, now = Date.now()): GalleryItem[] {
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
      items.set(payload.blob, {
        messageId: message.id,
        blob: payload.blob,
        mime: payload.mime,
        ...(payload.name ? { name: payload.name } : {}),
        ...(payload.caption ? { caption: payload.caption } : {}),
        at: seconds(payload.sent_at) ?? talkAt,
        ...(seconds(payload.taken_at) ? { takenAt: seconds(payload.taken_at) } : {}),
        own,
        actorName,
        comments: [],
      });
    } else if (payload.t === 'comment') {
      comments.push({ blob: payload.blob, messageId: message.id, text: payload.text, at: seconds(payload.sent_at) ?? talkAt, own, actorName });
    }
  }
  for (const { blob, ...comment } of comments) items.get(blob)?.comments.push(comment);
  const list = [...items.values()];
  for (const item of list) item.comments.sort((a, b) => a.at - b.at || a.messageId - b.messageId);
  return list.sort((a, b) => b.at - a.at || b.messageId - a.messageId);
}

/** The gallery by day, newest day first, each labelled like the chat's day separators. */
export function groupByDay(items: GalleryItem[], now: Date = new Date()): GalleryDay[] {
  const days: GalleryDay[] = [];
  for (const item of items) {
    const date = new Date(item.at * 1000);
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    const last = days[days.length - 1];
    if (last && last.key === key) last.items.push(item);
    else days.push({ key, label: dayLabel(item.at, now), items: [item] });
  }
  return days;
}

/** The full date and time a picture was sent, for the viewer. */
export function stampLabel(at: number): string {
  return new Date(at * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}
