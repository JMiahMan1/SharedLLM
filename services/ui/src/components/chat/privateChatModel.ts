// The private chat's shapes and the small rules its screens follow, kept free
// of React so they can be tested directly.
import { isMine, type TalkMessage } from './chatModel';
import type { PrivatePayload } from '../../lib/privateChatCrypto';

export interface PrivateChatInfo {
  token: string;
  members: string[];
  partner: string;
  partner_name: string;
  created_by: string;
  created_at: number;
  /** 1: the key is the password's (the first chats); 2: the key is wrapped and the password can change. */
  version?: 1 | 2;
  kdf: { name: 'PBKDF2-SHA256'; salt: string; iterations: number };
  wrapped_key?: { iv: string; ct: string };
  verifier: { iv: string; ct: string };
  /** The chat is one father and one mother, so it has Beloved, the couple's page (the server checks again). */
  beloved?: boolean;
}

export interface PrivateChatStatus {
  /** A parent (father or mother) with a chat account. */
  eligible: boolean;
  reason?: string | null;
  chats: PrivateChatInfo[];
  /** The other parents a chat could be started with. */
  partners: Array<{ username: string; display_name: string }>;
  min_iterations: number;
}

/**
 * Whether the signed-in person may use (or even hear of) the private chat: an
 * adult father or mother. Everyone else, and anyone not known yet, gets no
 * private-chat request and no private-chat UI; the server enforces the same
 * rule (and answers anyone else with an empty status).
 */
export function mayUsePrivateChat(me?: { family_role?: string | null; is_minor?: boolean | null; is_system_default?: boolean | null } | null): boolean {
  return Boolean(me && (me.family_role === 'father' || me.family_role === 'mother') && me.is_minor !== true && !me.is_system_default);
}

/** The shortest password the create form accepts. */
export const MIN_PASSWORD_LENGTH = 8;

/** Why a new password cannot be used yet, or null when it can. */
export function passwordProblem(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) return `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  if (password !== confirm) return 'The two passwords do not match.';
  return null;
}

/**
 * Whether a nudge makes sense: the latest message in the chat is mine and the
 * other parent has not read it (Talk's everyone-has-read marker is behind it).
 */
export function canNudge(messages: TalkMessage[], myActorIds: ReadonlySet<string>, lastCommonRead?: number): boolean {
  const sent = messages.filter((m) => typeof m.id === 'number' && !m.deleted && !m.system_message);
  const latest = sent[sent.length - 1];
  if (!latest || !isMine(latest, myActorIds)) return false;
  return (lastCommonRead ?? 0) < Number(latest.id);
}

/** The server's reason from an axios error (detail), else the error's own message. */
export function errorText(error: unknown, fallback: string): string {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data && typeof data === 'object' && typeof (data as { detail?: unknown }).detail === 'string') {
    return (data as { detail: string }).detail;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

/**
 * The messages the thread shows. A picture added straight to the gallery is a
 * gallery entry, never a Talk message; should one ever arrive as a message
 * (an old or a misbehaving client) it still stays out of the thread, so the
 * thread only ever shows what was said in it.
 */
export function threadMessages(messages: TalkMessage[], opened: Record<string, PrivatePayload | 'unreadable'>): TalkMessage[] {
  return messages.filter((m) => {
    if (m.deleted) return false;
    const payload = opened[String(m.id)];
    return !(payload && payload !== 'unreadable' && payload.t === 'added');
  });
}

// --- Edits and reactions (sealed overlays kept by the gateway, services/gateway/private_chat_marks.py) ---

/** The reactions a message can take, each with its own animation. */
export const REACTIONS = [
  { emoji: '❤️', kind: 'love', label: 'Love' },
  { emoji: '👍', kind: 'like', label: 'Thumbs up' },
  { emoji: '😂', kind: 'laugh', label: 'Haha' },
  { emoji: '😮', kind: 'wow', label: 'Wow' },
  { emoji: '😢', kind: 'sad', label: 'Sad' },
  { emoji: '🙏', kind: 'pray', label: 'Praying' },
] as const;

export type ReactionKind = (typeof REACTIONS)[number]['kind'];

const KIND_BY_EMOJI = new Map<string, ReactionKind>(REACTIONS.map((r) => [r.emoji, r.kind]));

/** The animation an emoji plays, or null for one this version does not know. */
export function reactionKind(emoji: string): ReactionKind | null {
  return KIND_BY_EMOJI.get(emoji) ?? null;
}

/** One reaction as the server lists it: who and when are in the clear, the emoji is sealed. */
export interface MarkReaction {
  id: string;
  by: string;
  by_name: string;
  own: boolean;
  at: number;
  /** I have seen it (played its animation), or it is mine. */
  seen: boolean;
  sealed: string;
}

/** One message's sealed overlays. */
export interface MessageMark {
  message_id: number;
  edit: { at: number; by: string; own: boolean; sealed: string } | null;
  reactions: MarkReaction[];
}

/** What a sealed edit or reaction says inside: always bound to the message it belongs to. */
export type MarkRecord = { t: 'edit'; m: number; text: string } | { t: 'reaction'; m: number; e: string };

/** The longest edited message accepted on a device (the server caps the sealed size too). */
export const MAX_EDIT_CHARS = 8000;

/**
 * An opened sealed record, checked: the right kind, the right message (a record
 * the server moved onto another message is refused) and a known emoji.
 */
export function readMarkRecord(value: unknown, messageId: number, expected: MarkRecord['t']): MarkRecord | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (record.t !== expected || record.m !== messageId) return null;
  if (expected === 'edit') {
    return typeof record.text === 'string' && record.text.trim() && record.text.length <= MAX_EDIT_CHARS
      ? { t: 'edit', m: messageId, text: record.text }
      : null;
  }
  return typeof record.e === 'string' && reactionKind(record.e) ? { t: 'reaction', m: messageId, e: record.e } : null;
}

/** A reaction once its emoji is opened. */
export interface OpenedReaction {
  id: string;
  messageId: number;
  by: string;
  byName: string;
  own: boolean;
  at: number;
  seen: boolean;
  emoji: string;
}

/** A message's overlays once opened: its latest text (when edited) and its readable reactions. */
export interface OpenedMark {
  edit?: { text: string; at: number };
  reactions: OpenedReaction[];
}

/**
 * Open every mark with `open` (the vault's openRecord). What fails to open,
 * or opens to the wrong message, is left out: never shown, never played.
 */
export async function openMarks(marks: MessageMark[], open: (sealed: string) => Promise<unknown>): Promise<Record<number, OpenedMark>> {
  const out: Record<number, OpenedMark> = {};
  await Promise.all(
    marks.map(async (mark) => {
      const id = mark.message_id;
      const opened: OpenedMark = { reactions: [] };
      if (mark.edit) {
        const record = readMarkRecord(await open(mark.edit.sealed).catch(() => null), id, 'edit');
        if (record?.t === 'edit') opened.edit = { text: record.text, at: mark.edit.at };
      }
      const reactions = await Promise.all(
        mark.reactions.map(async (r) => {
          const record = readMarkRecord(await open(r.sealed).catch(() => null), id, 'reaction');
          return record?.t === 'reaction'
            ? { id: r.id, messageId: id, by: r.by, byName: r.by_name, own: r.own, at: r.at, seen: r.seen, emoji: record.e }
            : null;
        }),
      );
      opened.reactions = reactions.filter((r): r is OpenedReaction => r !== null).sort((a, b) => a.at - b.at || a.id.localeCompare(b.id));
      if (opened.edit || opened.reactions.length) out[id] = opened;
    }),
  );
  return out;
}

/** One chip under a bubble: an emoji, how many people, who, and whether one of them is me. */
export interface ReactionChip {
  emoji: string;
  count: number;
  names: string[];
  mine: boolean;
}

/**
 * The chips under a bubble. Each person counts once per emoji (the server
 * cannot tell emoji apart, so a double tap racing itself is folded here), in
 * the order each emoji first arrived.
 */
export function reactionChips(reactions: OpenedReaction[]): ReactionChip[] {
  const chips = new Map<string, { emoji: string; people: Map<string, string>; mine: boolean }>();
  for (const r of [...reactions].sort((a, b) => a.at - b.at || a.id.localeCompare(b.id))) {
    const chip = chips.get(r.emoji) ?? { emoji: r.emoji, people: new Map<string, string>(), mine: false };
    chip.people.set(r.by, r.own ? 'You' : r.byName);
    chip.mine ||= r.own;
    chips.set(r.emoji, chip);
  }
  return [...chips.values()].map((c) => ({ emoji: c.emoji, count: c.people.size, names: [...c.people.values()], mine: c.mine }));
}

/** My reactions with this emoji on a message: tapping it again takes back every one of them. */
export function myReactionIds(reactions: OpenedReaction[] | undefined, emoji: string): string[] {
  return (reactions ?? []).filter((r) => r.own && r.emoji === emoji).map((r) => r.id);
}

/** A reaction waiting to play its animation on this device. */
export interface PendingAnimation {
  id: string;
  messageId: number;
  emoji: string;
}

/**
 * The other parent's reactions I have not seen yet, oldest first, leaving out
 * ones already played on this device (the server may not have caught up).
 * One person's several taps of one emoji on one message play once.
 */
export function pendingAnimations(marks: Record<number, OpenedMark>, played: ReadonlySet<string>): PendingAnimation[] {
  const all = Object.values(marks).flatMap((m) => m.reactions);
  const waiting = all.filter((r) => !r.own && !r.seen && !played.has(r.id)).sort((a, b) => a.at - b.at || a.id.localeCompare(b.id));
  const once = new Set<string>();
  const out: PendingAnimation[] = [];
  for (const r of waiting) {
    const key = `${r.messageId}:${r.by}:${r.emoji}`;
    if (once.has(key)) continue;
    once.add(key);
    out.push({ id: r.id, messageId: r.messageId, emoji: r.emoji });
  }
  return out;
}

/** Every unseen reaction from the other parent that shares a played one's message, person and emoji (marked seen together). */
export function sameReactionIds(marks: Record<number, OpenedMark>, played: PendingAnimation): string[] {
  const mark = marks[played.messageId];
  const first = mark?.reactions.find((r) => r.id === played.id);
  if (!mark || !first) return [played.id];
  return mark.reactions.filter((r) => !r.own && !r.seen && r.by === first.by && r.emoji === first.emoji).map((r) => r.id);
}

/** Whether this device asks for less motion: reactions then fade instead of bursting. */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true;
}
