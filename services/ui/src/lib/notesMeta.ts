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
  rose: 'rgba(251, 113, 133, 0.22)',
  amber: 'rgba(251, 191, 36, 0.22)',
  emerald: 'rgba(52, 211, 153, 0.22)',
  sky: 'rgba(56, 189, 248, 0.22)',
  violet: 'rgba(167, 139, 250, 0.22)',
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

/**
 * Every note's metadata in one read. A page drawing many cards reads this
 * once per render; getNoteMeta per card and per sort comparison parsed the
 * whole stored map hundreds of times on each keystroke.
 */
export function readNoteMetaMap(): Readonly<Record<string, NoteMeta>> {
  return readAll();
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
export function sortNotes<T extends { title: string; path: string }>(
  notes: T[],
  meta: Readonly<Record<string, NoteMeta>> = readAll(),
): T[] {
  const pinned = (n: T) => (meta[noteKey(n.title, n.path)]?.pinned ? 1 : 0);
  return [...notes].sort((a, b) => pinned(b) - pinned(a));
}

/** The `# title` + `Category:` header the notes handler writes on top of a note. */
const STORED_HEADER = /^[ \t]*\n*# [^\n]*\nCategory: [^\n]*\n(?:[ \t]*\n)?/;

/**
 * A note's own text, without the stored header (or a stack of them, from
 * saves before the server stripped it). This is what the editor shows, so
 * saving does not write the header into the body again. Mirrors
 * `note_body` in services/execution/handlers/note.py.
 */
export function noteBody(content: string): string {
  let text = content;
  for (let m = STORED_HEADER.exec(text); m; m = STORED_HEADER.exec(text)) text = text.slice(m[0].length);
  return text;
}

/** A card preview: the note's text without its stored header. */
export function notePreview(content: string, max = 220): string {
  const body = noteBody(content).trim();
  return body.length > max ? `${body.slice(0, max)}…` : body;
}

/**
 * The body as a person should read it: the stored header and a leading heading
 * that repeats the title are removed, the way Keep treats the first line as the
 * title rather than part of the body.
 */
export function noteDisplayBody(content: string, title?: string): string {
  let body = noteBody(content).trim();
  const wanted = title?.trim();
  if (wanted) {
    const escaped = wanted.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    body = body.replace(new RegExp(`^#{1,6}\\s*${escaped}\\s*\\n+`, 'i'), '').trim();
  }
  return body;
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

/**
 * Inline #labels. Nextcloud Notes already reads hashtags out of note bodies,
 * so labels live in the markdown itself rather than in a side table.
 */
const TAG_PATTERN = /(^|[\s(])#([\p{L}\p{N}][\p{L}\p{N}_/-]{0,31})/gu;

/** Every #label in a note, lowercased, in the order it appears. */
export function extractTags(content: string | undefined): string[] {
  if (!content) return [];
  const found: string[] = [];
  for (const match of content.matchAll(TAG_PATTERN)) {
    const tag = match[2].toLowerCase();
    if (!found.includes(tag)) found.push(tag);
  }
  return found;
}

export function hasTag(content: string | undefined, tag: string): boolean {
  return extractTags(content).includes(tag.toLowerCase());
}

/** A note is a checklist when it has checklist lines; otherwise it is prose. */
export function noteKind(content: string | undefined): 'list' | 'note' {
  return content && parseChecklist(content).length > 0 ? 'list' : 'note';
}

export interface NoteQuery {
  text: string;
  tags: string[];
  colors: string[];
  kinds: Array<'list' | 'note'>;
}

/**
 * Keep-style search: bare words plus `tag:`/`label:`, `colour:`/`color:` and
 * `type:list|note` operators, so a shelf can be narrowed the way Keep does.
 */
export function parseNoteQuery(raw: string): NoteQuery {
  const query: NoteQuery = { text: '', tags: [], colors: [], kinds: [] };
  const words: string[] = [];
  for (const token of raw.trim().split(/\s+/)) {
    if (!token) continue;
    const [key, value] = token.split(/:(.+)/);
    const name = key.toLowerCase();
    const setting = (value ?? '').toLowerCase();
    if (!setting) {
      if (token.startsWith('#')) query.tags.push(token.slice(1).toLowerCase());
      else words.push(token);
      continue;
    }
    if (name === 'tag' || name === 'label') query.tags.push(setting);
    else if (name === 'colour' || name === 'color') query.colors.push(setting);
    else if (name === 'type' && (setting === 'list' || setting === 'note')) query.kinds.push(setting);
    else words.push(token);
  }
  query.text = words.join(' ').toLowerCase();
  return query;
}

export function matchesNoteQuery(
  note: { title: string },
  content: string | undefined,
  query: NoteQuery,
  color: string = 'default',
): boolean {
  const tags = extractTags(content);
  if (query.tags.some((tag) => !tags.includes(tag))) return false;
  if (query.colors.length > 0 && !query.colors.includes(color)) return false;
  if (query.kinds.length > 0 && !query.kinds.includes(noteKind(content))) return false;
  if (query.text) {
    const haystack = `${note.title} ${noteBody(content ?? '')}`.toLowerCase();
    if (!haystack.includes(query.text)) return false;
  }
  return true;
}

/** The labels on a shelf, most-used first, for the filter chips. */
export function topTags(
  notes: Array<{ path: string }>,
  contents: Record<string, string | undefined>,
  limit = 8,
): Array<{ tag: string; count: number }> {
  const counts = new Map<string, number>();
  for (const note of notes) {
    for (const tag of extractTags(contents[note.path])) {
      counts.set(tag, (counts.get(tag) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))
    .slice(0, limit);
}

/**
 * The shared notes index stored at Notes/.jarvis-notes.json.
 *
 * Nextcloud Notes keeps favourites in its own database and knows nothing
 * about archive or trash, so Jarvis keeps the presentation layer here: a
 * dotfile, invisible to the Notes app and to the markdown itself.
 */
export interface NotesIndexEntry {
  pinned?: boolean;
  color?: NoteColor;
  /** Where an archived/trashed note came from, for Restore. */
  previousPath?: string;
  /** A timed reminder: when it fires (ISO) and the timer that fires it. */
  reminder?: NoteReminder;
  /** A place reminder ("when I get home"): geo's id, the place and the movement. */
  placeReminder?: NotePlaceReminder;
}

export interface NoteReminder {
  at: string;
  timerId: string;
}

export interface NotePlaceReminder {
  id: string;
  /** The place's zone id (`zone.home`). */
  zone: string;
  /** Its name when the reminder was set, for the card. */
  zoneName: string;
  on: 'arrive' | 'leave';
}

/** "Arriving at Home" / "Leaving Work": how a place reminder reads on a card. */
export function placeReminderText(reminder: NotePlaceReminder): string {
  return `${reminder.on === 'arrive' ? 'Arriving at' : 'Leaving'} ${reminder.zoneName}`;
}

export interface NotesIndex {
  version: number;
  notes: Record<string, NotesIndexEntry>;
}

export const EMPTY_NOTES_INDEX: NotesIndex = { version: 1, notes: {} };

export const ARCHIVE_PREFIX = 'Notes/Archive/';
export const TRASH_PREFIX = 'Notes/Trash/';

/** Validate whatever the server returned before it reaches React state. */
export function parseNotesIndex(raw: unknown): NotesIndex | null {
  let value = raw;
  if (typeof raw === 'string') {
    try {
      value = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== 'object') return null;
  const candidate = value as { version?: unknown; notes?: unknown };
  if (!candidate.notes || typeof candidate.notes !== 'object' || Array.isArray(candidate.notes)) {
    return null;
  }
  const notes: Record<string, NotesIndexEntry> = {};
  for (const [path, entry] of Object.entries(candidate.notes as Record<string, unknown>)) {
    if (!entry || typeof entry !== 'object') continue;
    const source = entry as NotesIndexEntry;
    const clean: NotesIndexEntry = {};
    if (typeof source.pinned === 'boolean') clean.pinned = source.pinned;
    if (typeof source.color === 'string') clean.color = source.color;
    if (typeof source.previousPath === 'string') clean.previousPath = source.previousPath;
    const reminder = source.reminder as Partial<NoteReminder> | undefined;
    if (
      reminder &&
      typeof reminder.at === 'string' &&
      typeof reminder.timerId === 'string' &&
      !Number.isNaN(Date.parse(reminder.at))
    ) {
      clean.reminder = { at: reminder.at, timerId: reminder.timerId };
    }
    const place = source.placeReminder as Partial<NotePlaceReminder> | undefined;
    if (
      place &&
      typeof place.id === 'string' &&
      typeof place.zone === 'string' &&
      typeof place.zoneName === 'string' &&
      (place.on === 'arrive' || place.on === 'leave')
    ) {
      clean.placeReminder = { id: place.id, zone: place.zone, zoneName: place.zoneName, on: place.on };
    }
    notes[path] = clean;
  }
  return { version: typeof candidate.version === 'number' ? candidate.version : 1, notes };
}

export function indexEntry(index: NotesIndex | undefined, path: string): NotesIndexEntry {
  return index?.notes[path] ?? {};
}

/** Pure patch, matching setNoteMeta: an empty entry is dropped. */
export function setIndexEntry(index: NotesIndex, path: string, patch: NotesIndexEntry): NotesIndex {
  const next = { ...indexEntry(index, path), ...patch };
  // `reminder: undefined` in a patch clears it; leave no empty key behind.
  if (!next.reminder) delete next.reminder;
  if (!next.placeReminder) delete next.placeReminder;
  const notes = { ...index.notes };
  if (
    !next.pinned &&
    (!next.color || next.color === 'default') &&
    !next.previousPath &&
    !next.reminder &&
    !next.placeReminder
  ) {
    delete notes[path];
  } else {
    notes[path] = next;
  }
  return { ...index, notes };
}

export function forgetIndexEntry(index: NotesIndex, path: string): NotesIndex {
  if (!(path in index.notes)) return index;
  const notes = { ...index.notes };
  delete notes[path];
  return { ...index, notes };
}

export function isArchivedPath(path: string): boolean {
  return path.startsWith(ARCHIVE_PREFIX);
}

export function isTrashedPath(path: string): boolean {
  return path.startsWith(TRASH_PREFIX);
}

/**
 * Where a note lives when it is archived/trashed.
 *
 * A folder inside Notes/ is kept inside the archive/trash: two notes both
 * called "Plan.md" in different folders used to flatten to the same path and
 * the second move overwrote the first. A note already archived (or trashed)
 * stays where it is.
 */
export function archivedPathFor(path: string, prefix: string = ARCHIVE_PREFIX): string {
  const clean = path.replace(/^\/+/, '');
  if (clean.startsWith(prefix)) return clean;
  const relative = clean.startsWith('Notes/') ? clean.slice('Notes/'.length) : (clean.split('/').pop() ?? clean);
  return `${prefix}${relative}`;
}

/**
 * Merge this device's old localStorage pins/colours into the shared index.
 * The first device to load after the upgrade uploads its map; later devices
 * only fill gaps, so nobody's pins are overwritten by a stale copy.
 */
export function mergeLegacyMeta(index: NotesIndex, notes: Array<{ title: string; path: string }>): NotesIndex {
  const legacy = readAll();
  if (Object.keys(legacy).length === 0) return index;
  let next = index;
  for (const note of notes) {
    const old = legacy[noteKey(note.title, note.path)];
    if (!old) continue;
    const current = indexEntry(next, note.path);
    if (current.pinned !== undefined || current.color !== undefined) continue;
    next = setIndexEntry(next, note.path, { pinned: old.pinned, color: old.color });
  }
  return next;
}

/** Forget this device's pre-index pins once they live in the shared index. */
export function clearLegacyMeta(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // Storage unavailable: the migration simply runs again next time.
  }
}

/** Sort by the shared index: pinned first, then backend order (modified desc). */
export function sortNotesByIndex<T extends { title: string; path: string }>(
  notes: T[],
  index: NotesIndex | undefined,
): T[] {
  const pinned = (n: T) => (indexEntry(index, n.path).pinned ? 1 : 0);
  return [...notes].sort((a, b) => pinned(b) - pinned(a));
}

/**
 * A visit's notes list and index, kept per account so the next visit paints
 * the shelf before the network answers. The live queries still revalidate.
 */
export interface NotesSnapshot {
  savedAt: number;
  notes: unknown[];
  index: NotesIndex;
}

export function notesSnapshotKey(scope: string): string {
  return `jarvis_notes_snapshot_v1:${scope}`;
}

export function readNotesSnapshot(scope: string): NotesSnapshot | null {
  try {
    const raw = localStorage.getItem(notesSnapshotKey(scope));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<NotesSnapshot>;
    const index = parseNotesIndex(parsed.index);
    if (!index || !Array.isArray(parsed.notes) || typeof parsed.savedAt !== 'number') return null;
    return { savedAt: parsed.savedAt, notes: parsed.notes, index };
  } catch {
    return null;
  }
}

export function writeNotesSnapshot(scope: string, notes: unknown[], index: NotesIndex): void {
  const key = notesSnapshotKey(scope);
  try {
    localStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), notes, index }));
    return;
  } catch {
    // Quota: keep the list's shape (titles and paths) and drop note bodies.
  }
  try {
    const trimmed = notes.map((note) => {
      const rest = { ...(note as Record<string, unknown>) };
      delete rest.content;
      return rest;
    });
    localStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), notes: trimmed, index }));
  } catch {
    // Storage is full or unavailable: the page just loads from the network.
  }
}

/** How the shelf is laid out: Keep's masonry grid or one full-width column. */
export type NotesLayout = 'grid' | 'list';

const LAYOUT_KEY = 'jarvis_notes_layout_v1';

/**
 * This viewer's last chosen layout, or `grid` when none is stored (or storage
 * is unavailable, e.g. a private window). It is a per-device convenience, so
 * it never goes into the shared index.
 */
export function readNotesLayout(): NotesLayout {
  try {
    return localStorage.getItem(LAYOUT_KEY) === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

export function writeNotesLayout(layout: NotesLayout): void {
  try {
    localStorage.setItem(LAYOUT_KEY, layout);
  } catch {
    // Storage unavailable: the choice lasts for this visit only.
  }
}
