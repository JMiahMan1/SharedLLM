// Pure helpers behind the chat feed: ordering, authorship, grouping and
// labels. Kept free of React so the rules the feed follows can be tested
// directly.

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
    const mine = !system && isMine(message, myActorIds);
    const authorId = (message.actor_id || message.actor_display_name || '').toLowerCase();
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
        author: message.actor_display_name || 'Someone',
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
  const text = (conversation.last_message || '').replace(/```jarvis-envelope[\s\S]*?```/g, '').trim();
  if (!text) return 'No messages yet';
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
