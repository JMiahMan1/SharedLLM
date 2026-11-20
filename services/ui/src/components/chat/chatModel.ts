// Pure helpers behind the chat feed: ordering, authorship, grouping and
// labels. Kept free of React so the rules the feed follows can be tested
// directly.
import { decodeEnvelope } from '../../lib/chatEnvelope';

/** A rich object a {placeholder} in a message stands for (file, mention…). */
export interface TalkParameter {
  type?: string;
  id?: string;
  name?: string;
  mimetype?: string;
  size?: string | number;
  path?: string;
  link?: string;
  'preview-available'?: string;
  'mention-id'?: string;
}

export interface TalkParent {
  id: number;
  actor_display_name?: string;
  message?: string;
}

export interface TalkMessage {
  id?: number | string;
  actor_type?: string;
  actor_id?: string;
  actor_display_name?: string;
  message?: string;
  system_message?: string;
  message_type?: string;
  timestamp?: number;
  reactions?: Record<string, number>;
  reactions_self?: string[];
  parent?: TalkParent | null;
  parameters?: Record<string, TalkParameter>;
  last_edit_time?: number | null;
  deleted?: boolean;
  /** Client-only: a message still on its way, or one that failed to send. */
  pending?: 'sending' | 'failed';
  reply_to?: number;
}

export interface TalkConversation {
  token: string;
  display_name: string;
  /** For a direct message, the other person's account id. */
  name?: string;
  description?: string;
  last_message?: string;
  last_message_actor?: string;
  last_message_actor_id?: string;
  last_message_timestamp?: number;
  last_activity?: number;
  unread_messages?: number;
  unread_mention?: boolean;
  type?: number;
  /** Everyone has read up to this message id. */
  last_common_read?: number;
  /** You have read up to this message id. */
  last_read?: number;
  /** You own or moderate it: you may delete it and manage its members. */
  can_moderate?: boolean;
  /** The parents' end-to-end encrypted chat: no preview, opened with a password. */
  private?: boolean;
  /** A private chat whose messages the gateway keeps (not Talk): counted and listed by the gateway. */
  storage?: 'talk' | 'store';
}

export interface TalkParticipant {
  attendee_id: number;
  actor_type: string;
  actor_id: string;
  display_name: string;
  is_moderator: boolean;
}

export interface TalkPerson {
  id: string;
  label: string;
}

/** Talk returns history newest-first; a feed reads oldest-first. */
export function sortMessages(messages: TalkMessage[]): TalkMessage[] {
  return [...messages].sort((a, b) => {
    const ta = a.timestamp ?? 0;
    const tb = b.timestamp ?? 0;
    if (ta !== tb) return ta - tb;
    return Number(a.id ?? 0) - Number(b.id ?? 0);
  });
}

/**
 * Whether a family game is still running, judged from the newest game line in
 * the feed: a question or memory board means yes, a final board means no.
 * Matches the lines execution's family games post.
 */
export function gameRunning(messages: TalkMessage[]): boolean {
  const ordered = sortMessages(messages);
  for (let i = ordered.length - 1; i >= 0; i--) {
    const text = ordered[i]?.message || '';
    if (text.includes('Game over') || text.includes('All pairs found')) return false;
    if (text.includes('🧠 Trivia') || text.includes('🃏 Memory: find the pairs')) return true;
    if (decodeEnvelope(text).envelope?.kind === 'game') return true;
  }
  return false;
}

/** System lines ("Sam joined the call") are shown as centred notes. */
export function isSystemMessage(message: TalkMessage): boolean {
  return Boolean(message.system_message) && message.message_type === 'system';
}

/**
 * Whether a message was sent from this account. Compared on the Nextcloud
 * actor id, not the display name: the Family page never passed a name, so
 * every message, including your own, was drawn as someone else's.
 */
export function isMine(message: TalkMessage, myActorIds: ReadonlySet<string>): boolean {
  if (message.pending) return true;
  if (isFromJarvis(message)) return false;
  const id = (message.actor_id || '').toLowerCase();
  return id !== '' && myActorIds.has(id);
}

export interface MessageRun {
  key: string;
  mine: boolean;
  system: boolean;
  author: string;
  authorId: string;
  messages: TalkMessage[];
}

export const QUICK_REACTIONS = ['👍', '❤️', '😂', '🎉', '🙏', '😮'];

/** Consecutive messages from one sender within this window share a group. */
export const RUN_GAP_SECONDS = 5 * 60;

/**
 * Group the feed the way iMessage and WhatsApp do: one name and one avatar per
 * run of messages from the same person, a new run after a pause, a new day or
 * a system line.
 */
export function groupIntoRuns(messages: TalkMessage[], myActorIds: ReadonlySet<string>): MessageRun[] {
  const runs: MessageRun[] = [];
  for (const message of messages) {
    const system = isSystemMessage(message);
    const assistant = !system && isFromJarvis(message);
    const mine = !system && isMine(message, myActorIds);
    const authorId = assistant ? JARVIS_ID : (message.actor_id || message.actor_display_name || '').toLowerCase();
    const last = runs[runs.length - 1];
    const prev = last?.messages[last.messages.length - 1];
    const continues =
      last &&
      !system &&
      !last.system &&
      last.authorId === authorId &&
      last.mine === mine &&
      sameDay(prev?.timestamp, message.timestamp) &&
      (message.timestamp ?? 0) - (prev?.timestamp ?? 0) <= RUN_GAP_SECONDS;
    if (continues) {
      last.messages.push(message);
    } else {
      runs.push({
        key: String(message.id ?? `${message.timestamp}-${runs.length}`),
        mine,
        system,
        author: assistant ? 'Jarvis' : message.actor_display_name || 'Someone',
        authorId,
        messages: [message],
      });
    }
  }
  return runs;
}

export function sameDay(a?: number, b?: number): boolean {
  if (!a || !b) return true;
  return new Date(a * 1000).toDateString() === new Date(b * 1000).toDateString();
}

/** "Today", "Yesterday", a weekday this week, else a date. */
export function dayLabel(timestamp: number, now: Date = new Date()): string {
  const date = new Date(timestamp * 1000);
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(date)) / 86_400_000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days > 1 && days < 7) return date.toLocaleDateString([], { weekday: 'long' });
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString([], { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) });
}

export function timeLabel(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/** Conversation-list time: a clock time today, else a short day or date. */
export function listTimeLabel(timestamp: number | undefined, now: Date = new Date()): string {
  if (!timestamp) return '';
  const label = dayLabel(timestamp, now);
  if (label === 'Today') return timeLabel(timestamp);
  if (label === 'Yesterday') return 'Yesterday';
  const date = new Date(timestamp * 1000);
  const days = (now.getTime() - date.getTime()) / 86_400_000;
  if (days < 7) return date.toLocaleDateString([], { weekday: 'short' });
  return date.toLocaleDateString([], { month: 'numeric', day: 'numeric' });
}

/** A message of only emoji (up to three) is drawn large with no bubble. */
export function isEmojiOnly(text: string | undefined): boolean {
  if (!text) return false;
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > 24) return false;
  const stripped = trimmed.replace(/\s|\u200d|\ufe0f/g, '');
  if (!/^(\p{Extended_Pictographic}|\p{Emoji_Modifier}|\p{Regional_Indicator})+$/u.test(stripped)) return false;
  return [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(trimmed.replace(/\s/g, ''))].length <= 3;
}

/** "You: …" or "Sam: …" in a group; just the text in a one-to-one. */
export function previewLine(conversation: TalkConversation, myActorIds: ReadonlySet<string>): string {
  // Never a preview of a private chat, whatever the server sent.
  if (conversation.private) return 'Encrypted message';
  const raw = conversation.last_message || '';
  const fromJarvis = decodeEnvelope(raw).envelope?.kind === 'assistant';
  const text = plainText(raw)
    .replace(/\{file\}/g, '📎 Attachment')
    .replace(/\{mention-[a-z0-9-]+\}/gi, '@…')
    .replace(/\{[a-z0-9-]+\}/gi, '')
    .trim();
  if (!text) return 'No messages yet';
  if (fromJarvis) return `Jarvis: ${text}`;
  const actorId = (conversation.last_message_actor_id || '').toLowerCase();
  if (actorId && myActorIds.has(actorId)) return `You: ${text}`;
  const isGroup = conversation.type !== undefined && conversation.type !== 1;
  if (isGroup && conversation.last_message_actor) {
    return `${conversation.last_message_actor.split(/\s+/)[0]}: ${text}`;
  }
  return text;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Stable per-person tint so a family member is recognisable at a glance. */
export function avatarTint(name: string): string {
  // Dark enough that white initials stay readable (at least 4.5:1) on every tint.
  const palette = ['#7C3AED', '#0369A1', '#047857', '#B45309', '#BE185D', '#6D28D9', '#0F766E', '#BE123C'];
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return palette[hash % palette.length];
}

/**
 * Reaction counts from any shape Talk or the fixtures produce: the per-message
 * `{emoji: count}`, the reactions endpoint's `{emoji: [actors]}`, or a list of
 * `{reaction}` rows. The panel used to expect only the list, which the live
 * endpoint never returns, so a reaction never showed after it was sent.
 */
export function reactionCounts(raw: unknown): Record<string, number> {
  const counts: Record<string, number> = {};
  if (Array.isArray(raw)) {
    for (const row of raw) {
      const key = (row as { reaction?: string })?.reaction || '👍';
      counts[key] = (counts[key] || 0) + 1;
    }
  } else if (raw && typeof raw === 'object') {
    for (const [emoji, value] of Object.entries(raw as Record<string, unknown>)) {
      const n = Array.isArray(value) ? value.length : Number(value) || 0;
      if (n > 0) counts[emoji] = n;
    }
  }
  return counts;
}

/**
 * Talk posts each reaction (and each removal) as a system message whose parent
 * is the message reacted to. They are updates, not lines to draw: shown, each
 * reaction became a lone emoji in the middle of the feed instead of a chip on
 * the message. Execution leaves them out; this keeps a stale server honest.
 */
const HIDDEN_UPDATES = new Set(['reaction', 'reaction_deleted', 'reaction_revoked']);

export function isHiddenUpdate(message: TalkMessage): boolean {
  return message.message_type === 'system' && HIDDEN_UPDATES.has(message.system_message || '');
}

/** The newest message id a page or long poll has seen, hidden updates included. */
export function newestMessageId(response: { detail?: unknown } | undefined): number {
  const detail = response?.detail as { last_id?: unknown } | undefined;
  const ids = detailList<{ id?: unknown }>(response, 'messages').map((m) => Number(m.id) || 0);
  return Math.max(0, Number(detail?.last_id) || 0, ...ids);
}

/** What a message's reactions are now, as the feed draws them. */
export interface ReactionState {
  reactions: Record<string, number>;
  reactions_self: string[];
}

/**
 * My reaction toggled on a message, before the server answers: tapping an
 * emoji I have not used adds me (one more, beside everyone else's), tapping one
 * I have takes me off. Nobody else's reaction is touched.
 */
export function toggleReaction(message: Pick<TalkMessage, 'reactions' | 'reactions_self'>, emoji: string): ReactionState & { adding: boolean } {
  const counts = reactionCounts(message.reactions);
  const mine = new Set(message.reactions_self ?? []);
  const adding = !mine.has(emoji);
  if (adding) {
    mine.add(emoji);
    counts[emoji] = (counts[emoji] || 0) + 1;
  } else {
    mine.delete(emoji);
    const left = (counts[emoji] || 1) - 1;
    if (left > 0) counts[emoji] = left;
    else delete counts[emoji];
  }
  return { reactions: counts, reactions_self: [...mine], adding };
}

/** One person's reaction, as the reactions endpoint lists them. */
export interface Reactor {
  actor_id?: string;
  actor_type?: string;
  display_name?: string;
}

/** "You, Sam and Alex" for a chip's label: me first, then everyone else by name. */
export function reactorNames(people: Reactor[] | undefined, myActorIds: ReadonlySet<string>): string[] {
  const names: string[] = [];
  let me = false;
  for (const person of people ?? []) {
    if (person.actor_type === 'users' && myActorIds.has(String(person.actor_id || '').toLowerCase())) me = true;
    else names.push(person.display_name || person.actor_id || 'Someone');
  }
  return me ? ['You', ...names] : names;
}

export const JARVIS_ID = 'jarvis';

/** A Jarvis answer, marked by its envelope (it is posted with the asker's login). */
export function isAssistant(message: TalkMessage): boolean {
  return decodeEnvelope(message.message).envelope?.kind === 'assistant';
}

/**
 * Whether Jarvis posted this, whoever's account carried it: its own bot
 * account, an answer (`assistant` envelope) or a game post (`from: jarvis`).
 */
export function isFromJarvis(message: TalkMessage): boolean {
  if ((message.actor_id || '').toLowerCase() === JARVIS_ID) return true;
  const envelope = decodeEnvelope(message.message).envelope;
  return envelope?.kind === 'assistant' || envelope?.from === 'jarvis';
}

/** Whether a message asks Jarvis something (the bot answers messages that start with @Jarvis). */
export function asksJarvis(text: string): boolean {
  return text.trimStart().toLowerCase().startsWith('@jarvis');
}

export type RichPart =
  | { kind: 'text'; text: string }
  | { kind: 'link'; text: string; href: string }
  | { kind: 'mention'; id?: string; label: string }
  | { kind: 'file'; file: TalkParameter };

const URL_RE = /\bhttps?:\/\/[^\s<>"')\]]+/gi;

function linkify(text: string, out: RichPart[]): void {
  let last = 0;
  for (const match of text.matchAll(URL_RE)) {
    const start = match.index ?? 0;
    // Trailing punctuation belongs to the sentence, not the link.
    const href = match[0].replace(/[.,!?;:]+$/, '');
    if (start > last) out.push({ kind: 'text', text: text.slice(last, start) });
    out.push({ kind: 'link', text: href, href });
    last = start + href.length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
}

/** Plain text with its web addresses as link parts (an encrypted private message has no placeholders). */
export function splitLinks(text: string): RichPart[] {
  const out: RichPart[] = [];
  linkify(text, out);
  return out;
}

/**
 * Split a Talk message into drawable parts. Talk sends "{file}" for a shared
 * file and "{mention-user1}" for a mention, with the objects in
 * messageParameters; an unknown placeholder becomes its object's name.
 */
export function splitRichText(text: string, parameters: Record<string, TalkParameter>): RichPart[] {
  const out: RichPart[] = [];
  let last = 0;
  for (const match of text.matchAll(/\{([a-z0-9-]+)\}/gi)) {
    const param = parameters[match[1]];
    if (!param) continue;
    const start = match.index ?? 0;
    if (start > last) linkify(text.slice(last, start), out);
    if (param.type === 'file') out.push({ kind: 'file', file: param });
    else if (match[1].startsWith('mention-') || param.type === 'user' || param.type === 'guest' || param.type === 'call')
      out.push({ kind: 'mention', id: param.id, label: param.name || param.id || '' });
    else out.push({ kind: 'text', text: param.name || '' });
    last = start + match[0].length;
  }
  if (last < text.length) linkify(text.slice(last), out);
  // A caption-less file share is just "{file}"; nothing else to show.
  return out.filter((part) => part.kind !== 'text' || part.text !== '');
}

export type ReceiptState = 'sending' | 'failed' | 'sent' | 'read';

/** iMessage-style status for your latest message: Sent, then Read. */
export function receiptFor(message: TalkMessage, lastCommonRead: number | undefined): ReceiptState {
  if (message.pending === 'sending') return 'sending';
  if (message.pending === 'failed') return 'failed';
  if (typeof message.id === 'number' && lastCommonRead !== undefined && message.id <= lastCommonRead) return 'read';
  return 'sent';
}

export function formatBytes(size: string | number | undefined): string {
  const n = Number(size);
  if (!Number.isFinite(n) || n <= 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

/** Talk attachments are capped here and in the execution service alike. */
export const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;

/** A message's readable text, with any Jarvis envelope removed. */
export function plainText(raw: string | null | undefined): string {
  return decodeEnvelope(raw).text.trim();
}

/** A list out of an execution response's ``detail`` (empty when absent). */
export function detailList<T>(response: { detail?: unknown } | undefined, key: string): T[] {
  const detail = response?.detail as Record<string, unknown> | undefined;
  const value = detail?.[key];
  return Array.isArray(value) ? (value as T[]) : [];
}
