// Reply reminders in the private chat (services/gateway/private_chat_reminders.py).
//
// The server decides whether a reminder is due from metadata only (who wrote
// last, read markers). Whether the other parent's last messages ask something
// can only be decided here, on the device that holds the text. The answer
// leaves the device as one bit for one message id: "the newest message expects
// a reply". Never the text, never which message asked, never how.

import type { PrivatePayload } from './privateChatCrypto';

/** How many of the partner's latest messages (since my last one) are looked at. */
export const LOOK_BACK = 3;

const MARKS = /[?？¿]/;

const WH = new Set(['who', 'whom', 'whose', 'what', 'when', 'where', 'why', 'how', 'which']);
const WH_CONTRACTED = /^(what|where|who|how|when|why)(?:'|’)?s$/;
/** Words after a question word that make it a question ("what time", "how about", "why not"). */
const WH_NEXT = new Set(['about', 'time', 'if', 'else', 'kind', 'happened', 'not', 'come', 'one', 'much', 'many', 'long', 'often', 'far', 'soon', 'late', 'early']);

const BE = new Set(['am', 'is', 'are', 'was', 'were', "isn't", "aren't", "wasn't", "weren't"]);
const AUX = new Set([
  ...BE,
  'do', 'does', 'did', 'have', 'has', 'had', 'can', 'could', 'will', 'would', 'shall', 'should', 'may', 'might',
  "don't", "doesn't", "didn't", "haven't", "hasn't", "can't", "couldn't", "won't", "wouldn't", "shouldn't",
]);
/** Who an auxiliary asks about ("do you", "can we", "is it"). "Do the dishes" is an instruction, not a question. */
const SUBJECTS = new Set(['i', 'you', 'u', 'ya', "y'all", 'we', 'they', 'he', 'she', 'it', 'that', 'this', 'there', 'anyone', 'anybody', 'someone', 'somebody', 'everyone', 'everything']);
/** After a form of "to be", a thing can be asked about too ("is the oven off"). */
const BE_SUBJECTS = new Set([...SUBJECTS, 'the', 'my', 'your', 'our', 'his', 'her', 'their', 'its']);
/** Openers skipped before looking at the first word ("hey, do you..."). */
const OPENERS = new Set(['hey', 'hi', 'so', 'ok', 'okay', 'also', 'and', 'but', 'oh', 'um', 'umm', 'well', 'btw', 'babe', 'honey', 'love', 'hon', 'sweetie', 'quick', 'question', 'random', 'just', 'wondering', 'sorry']);
/** Asking without asking. */
const PHRASES = /\b(let me know|lmk|wdyt|what do you think|get back to me|your thoughts|any thoughts|any ideas?|thoughts\s*$)/;

function words(clause: string): string[] {
  return clause
    .toLowerCase()
    .replace(/’/g, "'")
    .split(/[^a-z']+/)
    .map((w) => w.replace(/^'+|'+$/g, ''))
    .filter(Boolean);
}

function clauseAsks(clause: string): boolean {
  const list = words(clause);
  let i = 0;
  while (i < list.length && OPENERS.has(list[i])) i += 1;
  const first = list[i];
  const next = list[i + 1];
  if (!first) return false;
  if (WH_CONTRACTED.test(first) && !WH.has(first)) return true;
  if (WH.has(first)) {
    if (!next) return true; // "Why", "When"
    return AUX.has(next) || WH_NEXT.has(next);
  }
  if (!next) return false;
  if (BE.has(first)) return BE_SUBJECTS.has(next);
  if (AUX.has(first)) return SUBJECTS.has(next);
  return false;
}

/**
 * Whether a message asks something: a question mark, or a sentence shaped
 * like a question ("are you home", "what time is dinner", "let me know").
 * A plain statement ("When I got home the dog was out") or an instruction
 * ("Do the dishes") does not count.
 */
export function asksSomething(text: string | null | undefined): boolean {
  if (!text) return false;
  if (MARKS.test(text)) return true;
  const lower = text.toLowerCase();
  if (PHRASES.test(lower)) return true;
  return lower
    .split(/[.!\n;,:…]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .some(clauseAsks);
}

/** One message of the thread, reduced to what the decision needs (text stays on the device). */
export interface ReminderItem {
  id: number;
  mine: boolean;
  /** The decrypted text (or caption), null for a picture without one; undefined while still being decrypted. */
  text: string | null | undefined;
}

/**
 * What to tell the server about the thread, or null when there is nothing to
 * say yet: the newest message is mine, the thread is empty, or one of the
 * messages looked at is not decrypted yet. `expectsReply` is true when one of
 * the partner's last `LOOK_BACK` messages since my last one asks something.
 */
export function replyFlag(items: ReminderItem[], lookBack = LOOK_BACK): { messageId: number; expectsReply: boolean } | null {
  const latest = items[items.length - 1];
  if (!latest || latest.mine) return null;
  const theirs: ReminderItem[] = [];
  for (let i = items.length - 1; i >= 0 && theirs.length < lookBack; i -= 1) {
    if (items[i].mine) break;
    theirs.push(items[i]);
  }
  if (theirs.some((item) => item.text === undefined)) return null;
  return { messageId: latest.id, expectsReply: theirs.some((item) => asksSomething(item.text)) };
}

/**
 * The words a decrypted message says, for `asksSomething`: its text, a
 * picture's caption, a photo comment, or its edited text when it was edited.
 * `undefined` while it is still being decrypted; null when it has no words.
 */
export function reminderText(payload: PrivatePayload | 'unreadable' | undefined, editedText?: string): string | null | undefined {
  if (typeof editedText === 'string') return editedText;
  if (payload === undefined) return undefined;
  if (payload === 'unreadable') return null;
  if (payload.t === 'text' || payload.t === 'comment') return payload.text;
  if (payload.t === 'image') return payload.caption ?? null;
  return null;
}
