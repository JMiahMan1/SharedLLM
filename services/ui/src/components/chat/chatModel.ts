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

/** System lines ("Michele joined the call") are shown as centred notes. */
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
  if (isAssistant(message)) return false;
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
    const assistant = !system && isAssistant(message);
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

/** "You: …" or "Michele: …" in a group; just the text in a one-to-one. */
export function previewLine(conversation: TalkConversation, myActorIds: ReadonlySet<string>): string {
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
  const palette = ['#863BFF', '#0EA5E9', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6', '#14B8A6', '#F43F5E'];
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

export const JARVIS_ID = 'jarvis';

/** A Jarvis answer, marked by its envelope (it is posted with the asker's login). */
export function isAssistant(message: TalkMessage): boolean {
  return decodeEnvelope(message.message).envelope?.kind === 'assistant';
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
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Talk attachments are capped here and in the execution service alike. */
export const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;

/** A message's readable text, with any Jarvis envelope removed. */
export function plainText(raw: string | null | undefined): string {
  return decodeEnvelope(raw).text.trim();
}
