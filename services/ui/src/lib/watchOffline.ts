import OfflineMode from '../plugins/offlineMode';
import { askWatch, isWatchConnected, notifyWatch } from './watchBle';

/**
 * A question sent through the Jarvis Watch (written to its RX) that the watch
 * cannot answer because it has no way to Jarvis (no Wi-Fi, or Jarvis
 * unreachable) joins this phone's offline-mode queue (kind "ask"), so a family
 * phone that is online, or this phone once it is, takes it to Jarvis. When the
 * answer arrives it goes back to the watch.
 *
 * The watch firmware has no way for the phone to write its TX (that is the
 * watch's own notify), so the answer reaches the watch screen through the
 * control channel's `notify Jarvis|<answer>`, the same as any phone notice.
 * The firmware is unchanged.
 */

/** Queued watch questions waiting for their answers, by request id (per phone, in localStorage). */
const PENDING_KEY = 'jarvis_watch_offline_pending';

/** The watch's own replies that mean "I could not reach Jarvis" (watch.yaml ask_jarvis). */
const OFFLINE_REPLIES = [
  /^the watch is offline/i,
  /^jarvis isn't set up yet/i,
  /^couldn't reach jarvis/i,
  /^jarvis returned http/i,
  /^unreadable reply from jarvis/i,
];

/** Whether the watch's reply says it could not get the question to Jarvis (or gave none at all). */
export function isWatchOfflineAnswer(answer: string): boolean {
  const text = answer.trim();
  return text === '' || OFFLINE_REPLIES.some((re) => re.test(text));
}

export interface PendingWatchQuestion {
  id: string;
  question: string;
  queuedAt: number;
}

export function readPending(): PendingWatchQuestion[] {
  try {
    const raw = JSON.parse(localStorage.getItem(PENDING_KEY) ?? '[]') as unknown;
    return Array.isArray(raw)
      ? raw.filter((p): p is PendingWatchQuestion => Boolean(p) && typeof (p as PendingWatchQuestion).id === 'string')
      : [];
  } catch {
    return [];
  }
}

function writePending(list: PendingWatchQuestion[]): void {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify(list.slice(-20)));
  } catch {
    // Storage full or blocked: the request itself is still queued natively.
  }
}

export type WatchAskResult = { answered: string } | { queued: string } | { failed: string };

/** Ask through the watch; when the watch cannot reach Jarvis, queue it offline instead. */
export async function askWatchOrQueue(question: string, now: () => number = Date.now): Promise<WatchAskResult> {
  const text = question.trim();
  if (!text) return { failed: 'Type a question first' };
  const reply = await askWatch(text);
  if (!isWatchOfflineAnswer(reply)) return { answered: reply };
  try {
    const { id } = await OfflineMode.queue({ kind: 'ask', body: { text } });
    writePending([...readPending(), { id, question: text, queuedAt: now() }]);
    return { queued: id };
  } catch (e) {
    return { failed: e instanceof Error ? e.message : 'Could not queue the question' };
  }
}

/** Short enough for one watch notice (the firmware keeps 480 characters of an answer). */
const WATCH_TEXT_MAX = 400;

/**
 * Send any answered watch questions to the watch (while it is connected) and
 * forget them; expired ones are told to the watch as such. Returns how many
 * were delivered.
 */
export async function deliverWatchAnswers(): Promise<number> {
  const pending = readPending();
  if (pending.length === 0 || !isWatchConnected()) return 0;
  let requests;
  try {
    ({ requests } = await OfflineMode.requests());
  } catch {
    return 0;
  }
  const byId = new Map(requests.map((r) => [r.id, r]));
  const keep: PendingWatchQuestion[] = [];
  let delivered = 0;
  for (const p of pending) {
    const r = byId.get(p.id);
    if (r?.status === 'answered' && typeof r.answer === 'string') {
      await notifyWatch('Jarvis', r.answer.slice(0, WATCH_TEXT_MAX));
      delivered += 1;
    } else if (r?.status === 'expired') {
      await notifyWatch('Jarvis', `No answer in time to: ${p.question}`.slice(0, WATCH_TEXT_MAX));
    } else if (r) {
      keep.push(p);
    }
    // A request this phone no longer holds (cleared, or another sign-in) is dropped.
  }
  writePending(keep);
  return delivered;
}
