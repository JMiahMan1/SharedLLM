/**
 * Per-user note presentation metadata (pin + colour).
 *
 * Kept client-side on purpose: the notes themselves live in Nextcloud as plain
 * markdown, and these are view preferences, not note content. Nothing here is
 * ever sent to the LLM or to RAG.
 */
export type NoteColor = 'default' | 'rose' | 'amber' | 'emerald' | 'sky' | 'violet';

export interface NoteMeta {
  pinned?: boolean;
  color?: NoteColor;
}

const KEY = 'jarvis_notes_meta_v1';

type MetaMap = Record<string, NoteMeta>;

export const NOTE_COLORS: Array<{ id: NoteColor; label: string; swatch: string }> = [
  { id: 'default', label: 'Default', swatch: '#64748B' },
  { id: 'rose', label: 'Rose', swatch: '#FB7185' },
  { id: 'amber', label: 'Amber', swatch: '#FBBF24' },
  { id: 'emerald', label: 'Emerald', swatch: '#34D399' },
  { id: 'sky', label: 'Sky', swatch: '#38BDF8' },
  { id: 'violet', label: 'Violet', swatch: '#A78BFA' },
];

export const NOTE_COLOR_BG: Record<NoteColor, string> = {
  default: 'rgba(148, 163, 184, 0.06)',
  rose: 'rgba(251, 113, 133, 0.10)',
  amber: 'rgba(251, 191, 36, 0.10)',
  emerald: 'rgba(52, 211, 153, 0.10)',
  sky: 'rgba(56, 189, 248, 0.10)',
  violet: 'rgba(167, 139, 250, 0.10)',
};

export function noteKey(title: string, path?: string): string {
  return path || title;
}

function readAll(): MetaMap {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as MetaMap) : {};
  } catch {
    return {};
  }
}

function writeAll(meta: MetaMap): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(meta));
  } catch {
    // Storage full/unavailable: presentation prefs simply don't persist.
  }
}

export function getNoteMeta(key: string): NoteMeta {
  return readAll()[key] ?? {};
}

export function setNoteMeta(key: string, patch: NoteMeta): NoteMeta {
  const all = readAll();
  const next = { ...all[key], ...patch };
  if (!next.pinned && (!next.color || next.color === 'default')) {
    delete all[key];
  } else {
    all[key] = next;
  }
  writeAll(all);
  return next;
}

export function forgetNoteMeta(key: string): void {
  const all = readAll();
  if (key in all) {
    delete all[key];
    writeAll(all);
  }
}

/** Sort: pinned first, then the order the backend returned (modified desc). */
export function sortNotes<T extends { title: string; path: string }>(notes: T[]): T[] {
  return [...notes].sort((a, b) => {
    const ap = getNoteMeta(noteKey(a.title, a.path)).pinned ? 1 : 0;
    const bp = getNoteMeta(noteKey(b.title, b.path)).pinned ? 1 : 0;
    return bp - ap;
  });
}

/** Strip the stored `# title` / `Category:` header so previews show content. */
export function notePreview(content: string, max = 220): string {
  const body = content
    .replace(/^#\s.*$/m, '')
    .replace(/^Category:\s.*$/m, '')
    .trim();
  return body.length > max ? `${body.slice(0, max)}…` : body;
}

export interface ChecklistItem {
  text: string;
  checked: boolean;
}

/** Parse markdown checklist lines so they can be rendered as real checkboxes. */
export function parseChecklist(content: string): ChecklistItem[] {
  const items: ChecklistItem[] = [];
  for (const line of content.split('\n')) {
    const unchecked = line.match(/^\s*-\s\[ \]\s+(.*)$/);
    if (unchecked) {
      items.push({ text: unchecked[1].trim(), checked: false });
      continue;
    }
    const checked = line.match(/^\s*-\s\[[xX]\]\s+(.*)$/);
    if (checked) {
      items.push({ text: checked[1].trim(), checked: true });
    }
  }
  return items;
}
