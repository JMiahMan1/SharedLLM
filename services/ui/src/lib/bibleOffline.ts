import type { BiblePassage } from '../types/api';

/**
 * Chapters the reader has opened, kept on the device so they still open with no
 * connection: on a plane, in a church basement, or when the server is down.
 *
 * Scripture is small (a chapter is a few kilobytes), so the last
 * `MAX_PASSAGES` passages fit comfortably in localStorage, which the Capacitor
 * WebView persists like any browser. A passage served from here is marked
 * `offline` so the reader can say so rather than pretend it is live.
 */
const PREFIX = 'bible:passage:';
const INDEX = 'bible:passage-index';
export const MAX_PASSAGES = 120;

export type OfflinePassage = BiblePassage & { offline?: boolean };

function key(version: string | undefined, ref: string): string {
  return `${PREFIX}${(version ?? '').toLowerCase()}|${ref.trim().toLowerCase()}`;
}

function readIndex(): string[] {
  try {
    const raw = localStorage.getItem(INDEX);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

export function rememberPassage(ref: string, version: string | undefined, passage: BiblePassage): void {
  try {
    const k = key(version, ref);
    localStorage.setItem(k, JSON.stringify(passage));
    const index = [k, ...readIndex().filter((entry) => entry !== k)];
    for (const old of index.slice(MAX_PASSAGES)) localStorage.removeItem(old);
    localStorage.setItem(INDEX, JSON.stringify(index.slice(0, MAX_PASSAGES)));
  } catch {
    // A full or blocked store only costs the offline copy, never the reading.
  }
}

export function recallPassage(ref: string, version: string | undefined): OfflinePassage | null {
  try {
    const raw = localStorage.getItem(key(version, ref));
    return raw ? { ...(JSON.parse(raw) as BiblePassage), offline: true } : null;
  } catch {
    return null;
  }
}

/** A failure that means "could not reach the server", as opposed to an answer. */
export function isUnreachable(error: unknown): boolean {
  const e = error as { response?: unknown; code?: string; message?: string };
  if (e?.response) return false;
  return true;
}
