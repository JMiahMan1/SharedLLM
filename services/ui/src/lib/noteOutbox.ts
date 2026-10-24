/**
 * Offline note edits: the device's drafts (lib/noteDrafts.ts) double as the
 * outbox. A save that could not reach the server because the device is
 * offline marks its draft `queued`; when the connection comes back the Notes
 * page replays each queued draft through the ordinary save, with the version
 * the edit started from, so a note changed elsewhere meanwhile becomes a
 * conflict to resolve, never an overwrite.
 */
import { readNoteDrafts, writeNoteDraft, clearNoteDraft, type NoteDraft } from './noteDrafts';

/** True when a failed request means "no connection", not "the server said no". */
export function isOfflineError(err: unknown): boolean {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return true;
  const e = err as { response?: unknown; code?: string; isAxiosError?: boolean } | null;
  if (!e || e.response) return false;
  return ['ERR_NETWORK', 'ECONNREFUSED', 'ENOTFOUND'].includes(e.code ?? '');
}

/** The browser's own idea of the connection (true when it cannot tell). */
export function deviceOnline(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine !== false;
}

/** What one replayed save came to. */
export type ReplayOutcome =
  | { status: 'saved' }
  | { status: 'conflict' }
  | { status: 'failed'; message: string; offline?: boolean };

export interface ReplaySummary {
  saved: number;
  conflicts: number;
  failed: number;
  /** Still offline: the rest stay queued for the next time. */
  stillOffline: boolean;
}

/**
 * Replay every queued draft of this account, oldest first, one at a time.
 *
 * `save` is the page's own save (If-Match / stored-text check included).
 * `storedText` reads the note as it is now, for a refused save: when it
 * already holds this edit (the first attempt landed but its answer was
 * lost), the edit is done, not a conflict. `skip` leaves the draft open in
 * the editor alone; the editor saves that one itself.
 */
export async function replayQueuedDrafts(
  scope: string,
  save: (draft: NoteDraft) => Promise<ReplayOutcome>,
  storedText: (draft: NoteDraft) => Promise<string | null>,
  skip?: string,
): Promise<ReplaySummary> {
  const summary: ReplaySummary = { saved: 0, conflicts: 0, failed: 0, stillOffline: false };
  const queued = Object.entries(readNoteDrafts(scope))
    .filter(([id, draft]) => draft.queued && id !== skip)
    .sort(([, a], [, b]) => a.savedAt - b.savedAt);
  for (const [id, draft] of queued) {
    const outcome = await save(draft);
    if (outcome.status === 'saved') {
      clearNoteDraft(scope, id);
      summary.saved += 1;
      continue;
    }
    if (outcome.status === 'failed' && outcome.offline) {
      summary.stillOffline = true;
      break;
    }
    // No longer queued: a conflict waits for a choice, a refusal for a look.
    const rest = { ...draft, queued: false };
    if (outcome.status === 'conflict') {
      const stored = draft.path ? await storedText(draft).catch(() => null) : null;
      if (stored !== null && stored === draft.content) {
        clearNoteDraft(scope, id);
        summary.saved += 1;
        continue;
      }
      writeNoteDraft(scope, id, { ...rest, conflict: true, failure: undefined });
      summary.conflicts += 1;
    } else {
      writeNoteDraft(scope, id, { ...rest, failure: outcome.message });
      summary.failed += 1;
    }
  }
  return summary;
}
