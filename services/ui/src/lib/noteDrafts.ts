/**
 * Unsaved note edits kept on this device.
 *
 * The editor writes its draft here on every change that the server does not
 * have yet, and forgets it once a save holds the same text. If the app is
 * closed (or the phone kills the webview) mid-edit, the next visit to Notes
 * offers the draft back instead of losing it. Drafts carry the version they
 * were opened at, so saving a restored draft still refuses to overwrite a
 * note changed elsewhere in the meantime.
 *
 * This is per device and per account (the `scope`), never shared: it is
 * exactly the text the server does not have.
 */
import type { NoteColor } from './notesMeta';

export interface NoteDraft {
  /** The note's file, or undefined for a note never saved. */
  path?: string;
  title: string;
  content: string;
  folder: string;
  color: NoteColor;
  pinned: boolean;
  /** The note as stored when it was opened (title and category), to tell a rename. */
  original?: { title: string; category: string };
  /** The version the edit started from (If-Match on save). */
  etag?: string;
  /** The note's text as opened, to detect a change elsewhere when there is no version. */
  base?: string;
  /** When the draft was last written (ms). */
  savedAt: number;
  /** Saved while offline: replay it when the connection is back (lib/noteOutbox.ts). */
  queued?: boolean;
  /** Replayed, and the note had changed elsewhere meanwhile: waits for a choice. */
  conflict?: boolean;
  /** Replayed, and the server refused it: its reason. */
  failure?: string;
}

/** A draft for a note never saved is kept under this id. */
export const NEW_NOTE_DRAFT = 'new';
const MAX_DRAFTS = 20;

function key(scope: string): string {
  return `jarvis_notes_drafts_v1:${scope}`;
}

export function draftId(path: string | undefined): string {
  return path || NEW_NOTE_DRAFT;
}

function isDraft(value: unknown): value is NoteDraft {
  if (!value || typeof value !== 'object') return false;
  const d = value as Partial<NoteDraft>;
  return (
    typeof d.title === 'string' &&
    typeof d.content === 'string' &&
    typeof d.folder === 'string' &&
    typeof d.savedAt === 'number' &&
    (d.path === undefined || typeof d.path === 'string')
  );
}

/** Every stored draft for this account, by id (path, or `new`). */
export function readNoteDrafts(scope: string): Record<string, NoteDraft> {
  try {
    const raw = localStorage.getItem(key(scope));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<string, NoteDraft> = {};
    for (const [id, value] of Object.entries(parsed ?? {})) {
      if (isDraft(value)) out[id] = value;
    }
    return out;
  } catch {
    return {};
  }
}

export function readNoteDraft(scope: string, id: string): NoteDraft | null {
  return readNoteDrafts(scope)[id] ?? null;
}

function writeAll(scope: string, drafts: Record<string, NoteDraft>): void {
  try {
    if (Object.keys(drafts).length === 0) localStorage.removeItem(key(scope));
    else localStorage.setItem(key(scope), JSON.stringify(drafts));
  } catch {
    // Storage full or unavailable: the draft lasts as long as the open editor.
  }
}

export function writeNoteDraft(scope: string, id: string, draft: Omit<NoteDraft, 'savedAt'>): void {
  const drafts = readNoteDrafts(scope);
  drafts[id] = { ...draft, savedAt: Date.now() };
  // Keep the newest few; an abandoned draft should not grow storage forever.
  const ids = Object.keys(drafts).sort((a, b) => drafts[b].savedAt - drafts[a].savedAt);
  for (const stale of ids.slice(MAX_DRAFTS)) delete drafts[stale];
  writeAll(scope, drafts);
}

export function clearNoteDraft(scope: string, id: string): void {
  const drafts = readNoteDrafts(scope);
  if (!(id in drafts)) return;
  delete drafts[id];
  writeAll(scope, drafts);
}
