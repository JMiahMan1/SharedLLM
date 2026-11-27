// The private chat's encrypted gallery: the pictures and clips sent in the
// chat that were put in it (galleryMembership), the comments on each, and the
// pictures added straight to the gallery (sealed gallery entries, never chat
// messages). Newest first, by day. Kept free of React so it can be tested
// directly. Nothing here is stored: it is rebuilt from what was decrypted, and
// dropped on lock.
import { mayAnimate } from '../../lib/animatedImage';
import type { EditRecipe } from '../../lib/photoEdit/recipe';
import {
  MAX_GALLERY_MARK_BLOBS,
  type AddedPicture,
  type GalleryEdit,
  type GalleryMark,
  type GalleryStart,
  type PrivatePayload,
  type VideoPayload,
} from '../../lib/privateChatCrypto';
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
  /** The edit shown in place of the original (the newest edit record, unless it went back to the original). */
  edit?: ShownEdit;
}

/** What a gallery picture shows when it has been edited: the edited blob (the original `blob` is kept as it is). */
export interface ShownEdit {
  /** The edit record. */
  entryId: string;
  blob: string;
  mime: string;
  width?: number;
  height?: number;
  animated?: boolean;
  /** The recipe, when it is in the record; else `recipeBlob` names the encrypted blob it is in. */
  recipe?: EditRecipe;
  recipeBlob?: string;
  /** Server time of the record (seconds). */
  at: number;
  own: boolean;
  byName: string;
}

/** The picture a gallery item shows: the current edit, or the original. */
export function shownPicture(item: Pick<GalleryItem, 'blob' | 'mime' | 'edit'>): { blob: string; mime: string } {
  return item.edit ? { blob: item.edit.blob, mime: item.edit.mime } : { blob: item.blob, mime: item.mime };
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
 * comments (the gallery itself is `inGallery` of this: what was put in it). A deleted picture (or comment) is left out; a comment on a picture
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
  if (item.edit) {
    const ew = Number(item.edit.width);
    const eh = Number(item.edit.height);
    return ew > 0 && eh > 0 ? clampAspect(ew / eh) : undefined;
  }
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

// --- Membership: what of the chat is in the gallery ------------------------------

/**
 * The gallery's own pictures: everything added straight to it, and of the
 * pictures and clips sent in the chat only the ones put in it.
 */
export function inGallery(items: GalleryItem[], members: ReadonlySet<string>): GalleryItem[] {
  return items.filter((item) => item.entryId || members.has(item.blob));
}

/** Who may take a picture out of the gallery without deleting it: one sent in the chat (an added one is deleted instead). */
export function removable(item: GalleryItem): boolean {
  return !item.entryId;
}

const OBJECT = String.raw`(?:\s+(?:this|that|it|these|them|those)(?:\s+(?:one|ones|picture|pictures|photo|photos|pic|pics|video|videos|clip|clips|gif|gifs))?)?`;
const PLACE = String.raw`\s+(?:the\s+|our\s+|my\s+)?gallery`;
const ASK = String.raw`^(?:(?:please|pls|can\s+you|could\s+you|jarvis,?)\s+)?`;
const THANKS = String.raw`(?:\s*,?\s*(?:please|pls|thanks|thank\s+you))?$`;
const ADD_COMMAND = new RegExp(`${ASK}(?:save|add|put|move|keep)${OBJECT}\\s+(?:to|in|into|on)${PLACE}${THANKS}`);
const REMOVE_COMMAND = new RegExp(`${ASK}(?:remove|take)${OBJECT}\\s+(?:out\\s+of|from|off(?:\\s+of)?)${PLACE}${THANKS}`);

/**
 * Whether a message is, as a whole, a request to put the picture it answers
 * in the gallery ("save to gallery", "Add this to the gallery!") or take it
 * out ("remove from gallery"). Only the whole message counts: a sentence that
 * merely mentions the gallery ("we should save to gallery later?") is talk.
 */
export function galleryCommand(text: string): 'add' | 'remove' | null {
  const said = text
    .toLowerCase()
    .replace(/[\u2019']/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[\s.!?\u2026]+$/, '');
  if (!said || said.length > 80) return null;
  if (ADD_COMMAND.test(said)) return 'add';
  if (REMOVE_COMMAND.test(said)) return 'remove';
  return null;
}

/** A sent message that is a gallery command answering a picture: what it does and to which picture (by blob). */
export function commandOf(payload: Opened | undefined): { op: 'add' | 'remove'; blob: string } | null {
  if (!payload || payload === 'unreadable' || payload.t !== 'text' || !payload.quote) return null;
  const op = galleryCommand(payload.text);
  return op ? { op, blob: payload.quote.blob } : null;
}

/** A picture or clip sent in the chat, as membership sees it. */
interface Sent {
  blob: string;
  /** When the server got it (seconds); undefined for one not stored yet. */
  at?: number;
  /** A clip, or a picture that can never animate (JPEG, HEIC). */
  still: boolean;
}

function sentPictures(messages: TalkMessage[], opened: Record<string, Opened>): Map<string, Sent> {
  const sent = new Map<string, Sent>();
  for (const message of messages) {
    if (message.deleted || typeof message.id !== 'number') continue;
    const payload = opened[String(message.id)];
    if (!payload || payload === 'unreadable') continue;
    if (payload.t === 'image') sent.set(payload.blob, { blob: payload.blob, at: seconds(message.timestamp), still: !mayAnimate(payload.mime) });
    else if (payload.t === 'video') sent.set(payload.poster, { blob: payload.poster, at: seconds(message.timestamp), still: true });
  }
  return sent;
}

export interface GalleryMembership {
  /** Sent pictures (by blob) and clips (by poster) that are in the gallery. */
  members: Set<string>;
  /** The opt-in start has been written (until it is, sent stills count as if it were written now). */
  started: boolean;
  /** When it started (the server's time of the earliest start record), in seconds. */
  since?: number;
}

/**
 * Which pictures and clips sent in the chat are in the gallery. Both phones
 * work this out from the same sealed records, so they always agree:
 *
 * 1. Before the opt-in start (the server's time of the earliest
 *    `gallery_start` entry), a sent clip or still picture counts as added: a
 *    JPEG or HEIC always, a GIF, WebP or PNG only if a start record lists it
 *    as read and found still. Animated pictures from before start out.
 * 2. Then every add and remove, oldest first by the server's time: sealed
 *    `gallery` entries (Add to gallery, Remove from gallery) and messages that
 *    answer a picture with "save to gallery" / "remove from gallery".
 *
 * A picture deleted from the chat is gone whatever was recorded about it.
 */
export function galleryMembership(messages: TalkMessage[], opened: Record<string, Opened>, entries: OpenedEntry[]): GalleryMembership {
  const sent = sentPictures(messages, opened);
  const starts = entries.filter((e) => e.payload !== 'unreadable' && e.payload.t === 'gallery_start');
  const started = starts.length > 0;
  const since = started ? Math.min(...starts.map((e) => seconds(e.at) ?? 0)) : undefined;
  const kept = new Set(starts.flatMap((e) => (e.payload !== 'unreadable' && e.payload.t === 'gallery_start' ? e.payload.kept : [])));
  const members = new Set<string>();
  for (const picture of sent.values()) {
    // Not started yet: as if it started now (what the first phone to open the gallery will write).
    const before = since === undefined ? true : picture.at !== undefined && picture.at <= since;
    if (before && (picture.still || kept.has(picture.blob))) members.add(picture.blob);
  }
  const ops: Array<{ at: number; order: string; op: 'add' | 'remove'; blobs: string[] }> = [];
  for (const entry of entries) {
    if (entry.payload === 'unreadable' || entry.payload.t !== 'gallery') continue;
    ops.push({ at: seconds(entry.at) ?? 0, order: `e:${entry.id}`, op: entry.payload.op, blobs: entry.payload.blobs });
  }
  for (const message of messages) {
    if (message.deleted || typeof message.id !== 'number') continue;
    const command = commandOf(opened[String(message.id)]);
    if (command) ops.push({ at: seconds(message.timestamp) ?? Number.MAX_SAFE_INTEGER, order: `m:${String(message.id).padStart(16, '0')}`, op: command.op, blobs: [command.blob] });
  }
  ops.sort((a, b) => a.at - b.at || (a.order < b.order ? -1 : a.order > b.order ? 1 : 0));
  for (const { op, blobs } of ops) {
    for (const blob of blobs) {
      if (!sent.has(blob)) continue;
      if (op === 'add') members.add(blob);
      else members.delete(blob);
    }
  }
  return { members, started, ...(since !== undefined ? { since } : {}) };
}

/** The sent pictures the opt-in start has to read (ones that might animate), by blob with their type. */
export function startCandidates(messages: TalkMessage[], opened: Record<string, Opened>): Array<{ blob: string; mime: string }> {
  const out = new Map<string, string>();
  for (const message of messages) {
    if (message.deleted || typeof message.id !== 'number') continue;
    const payload = opened[String(message.id)];
    if (payload && payload !== 'unreadable' && payload.t === 'image' && mayAnimate(payload.mime)) out.set(payload.blob, payload.mime);
  }
  return [...out].map(([blob, mime]) => ({ blob, mime }));
}

/** Every gallery record is padded to this many bytes before sealing, so the server sees one size for all of them. */
export const GALLERY_RECORD_BYTES = 8192;

/** UTF-8 bytes of a record's JSON with an empty pad: what sealing encrypts, before padding. */
export function bareRecordBytes(record: object): number {
  return new TextEncoder().encode(JSON.stringify({ ...record, p: '' })).length;
}

/**
 * A record padded (with `p`) to GALLERY_RECORD_BYTES bytes of JSON. Counted
 * in UTF-8 bytes, not characters, so a record with words in it (an edit's
 * text overlay) seals to the same size as every other.
 */
export function padded<T extends GalleryMark | GalleryStart | GalleryEdit>(record: T): T & { p: string } {
  return { ...record, p: ' '.repeat(Math.max(0, GALLERY_RECORD_BYTES - bareRecordBytes(record))) };
}

function chunks(blobs: string[]): string[][] {
  const unique = [...new Set(blobs)];
  const out: string[][] = [];
  for (let i = 0; i < unique.length; i += MAX_GALLERY_MARK_BLOBS) out.push(unique.slice(i, i + MAX_GALLERY_MARK_BLOBS));
  return out;
}

/** The sealed-to-be records that add or remove these pictures (as few as fit). */
export function markRecords(op: 'add' | 'remove', blobs: string[]): Array<GalleryMark & { p: string }> {
  return chunks(blobs).map((part) => padded({ t: 'gallery', op, blobs: part }));
}

/** The opt-in start's records: at least one, the still pictures it kept split over as many as needed. */
export function startRecords(kept: string[]): Array<GalleryStart & { p: string }> {
  const parts = chunks(kept);
  return (parts.length ? parts : [[]]).map((part) => padded({ t: 'gallery_start', kept: part }));
}
