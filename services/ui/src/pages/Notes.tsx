import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useReducer, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import SendAsSelector from '../components/chat/SendAsSelector';
import { useSendAsPref } from '../components/chat/sendAsPref';
import {
  Archive,
  ArchiveRestore,
  Check,
  CheckSquare,
  Copy,
  History,
  LayoutGrid,
  Loader2,
  Palette,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  Rows3,
  Search,
  Sparkles,
  Square,
  Trash2,
  X,
} from 'lucide-react';
import toast from 'react-hot-toast';
import Modal from '../components/ui/Modal';
import { api } from '../services/api';
import { saveNote } from '../lib/noteSave';
import RichNoteEditor, { type RichNoteEditorHandle } from '../components/notes/RichNoteEditor';
import NoteHistory from '../components/notes/NoteHistory';
import { richEditable } from '../lib/noteRichText';
import { remarkNoteSpans } from '../lib/remarkNoteSpans';
import { errorText } from '../lib/errorText';
import { showUndoToast } from '../lib/undoToast';
import { isTypingTarget } from '../lib/keyboard';
import { clearNoteDraft, readNoteDraft, readNoteDrafts, writeNoteDraft } from '../lib/noteDrafts';
import {
  ARCHIVE_PREFIX,
  EMPTY_NOTES_INDEX,
  NOTE_COLORS,
  NOTE_COLOR_BG,
  TRASH_PREFIX,
  archivedPathFor,
  clearLegacyMeta,
  extractTags,
  forgetIndexEntry,
  hasTag,
  indexEntry,
  isArchivedPath,
  isTrashedPath,
  matchesNoteQuery,
  mergeLegacyMeta,
  noteBody,
  noteDisplayBody,
  parseChecklist,
  parseNoteQuery,
  parseNotesIndex,
  readNotesSnapshot,
  setIndexEntry,
  sortNotesByIndex,
  readNotesLayout,
  topTags,
  writeNotesLayout,
  writeNotesSnapshot,
  type NoteColor,
  type NotesIndex,
  type NotesLayout,
} from '../lib/notesMeta';

interface NoteSummary {
  title: string;
  path: string;
  size: number;
  modified: string;
  /** The note's stored text, for the newest notes (the list's previews). */
  content?: string;
}

interface DraftState {
  title: string;
  content: string;
  path?: string;
  /** Where the note was opened from, to tell a rename or move from an edit. */
  original?: { title: string; category: string };
  /** Sub-folder inside Notes/ ("" = Notes itself): only Notes/ is listed here. */
  folder: string;
  color: NoteColor;
  pinned: boolean;
  /** The file version this edit started from; a save sends it as If-Match. */
  etag?: string;
  /** The note's text as opened, to spot a change elsewhere when there is no version. */
  base?: string;
  /** This edit's id in the device's draft store (the path, or `new:<time>`). */
  draftKey?: string;
  /** Set when the editor opened a stored unsaved draft (when it was kept). */
  restoredAt?: number;
}

/** What a save came to: saved (with the new version), refused as a conflict, or failed. */
type PersistResult =
  | { status: 'saved'; path: string; title: string; etag?: string }
  | { status: 'conflict' }
  | { status: 'failed'; message: string };

/** The stored text the page learned after the editor opened from the list's copy. */
interface FreshNote {
  content: string;
  etag?: string;
}

/** How long typing must pause before an open note saves itself. */
const AUTOSAVE_MS = 1500;

const STORAGE = 'nextcloud';
/** The folder this page lists (recursively); notes are saved inside it. */
const NOTES_DIR = 'Notes';
const EMPTY_DRAFT: DraftState = {
  title: '',
  content: '',
  folder: '',
  color: 'default',
  pinned: false,
};

type NotesView = 'notes' | 'archive' | 'trash';

/** "Notes/Work/a.md" -> "Work"; "Notes/a.md" -> "". */
function folderFromPath(path: string | undefined): string {
  const parts = (path ?? '').split('/').filter(Boolean).slice(0, -1);
  return (parts[0] === NOTES_DIR ? parts.slice(1) : parts).join('/');
}

/** The handler's category (a directory) for a folder inside Notes/. */
function categoryFor(folder: string): string {
  const clean = folder.trim().replace(/^\/+|\/+$/g, '');
  return clean ? `${NOTES_DIR}/${clean}` : NOTES_DIR;
}

/**
 * The visual form of a markdown note.
 *
 * The stored file stays plain markdown for Nextcloud Notes; this only decides
 * how it looks in Jarvis. Tailwind has no typography plugin here, so every
 * element carries its own classes instead of relying on `prose`.
 */
function NotePreview({ text, className = '' }: { text: string; className?: string }) {
  return (
    <div className={`text-xs text-slate-300 ${className}`} data-testid="note-preview">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkNoteSpans]}
        components={{
          p: (props) => <p className="mt-1 first:mt-0 leading-snug" {...props} />,
          h1: (props) => <h1 className="mt-2 first:mt-0 text-sm font-bold text-slate-100" {...props} />,
          h2: (props) => <h2 className="mt-2 first:mt-0 text-sm font-semibold text-slate-100" {...props} />,
          h3: (props) => <h3 className="mt-2 first:mt-0 text-xs font-semibold text-slate-100" {...props} />,
          ul: (props) => <ul className="mt-1 list-disc space-y-0.5 pl-4" {...props} />,
          ol: (props) => <ol className="mt-1 list-decimal space-y-0.5 pl-4" {...props} />,
          li: (props) => <li className="leading-snug" {...props} />,
          a: (props) => <a className="text-purple-300 underline" {...props} />,
          strong: (props) => <strong className="font-semibold text-slate-100" {...props} />,
          em: (props) => <em className="italic" {...props} />,
          code: (props) => <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-[11px]" {...props} />,
          pre: (props) => <pre className="mt-1 overflow-x-auto rounded-lg bg-black/40 p-2 font-mono text-[11px]" {...props} />,
          blockquote: (props) => <blockquote className="mt-1 border-l-2 border-white/20 pl-2 text-slate-400" {...props} />,
          hr: (props) => <hr className="my-2 border-white/10" {...props} />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

/**
 * Notes: a Keep-style surface over the Nextcloud markdown notes the rest of
 * Jarvis already uses. Note text stays plain markdown; pins, colours and the
 * archive/trash origin live in Notes/.jarvis-notes.json, a dotfile the
 * Nextcloud Notes app never sees.
 */
export default function Notes() {
  const [search, setSearch] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [view, setView] = useState<NotesView>('notes');
  const [layout, setLayout] = useState<NotesLayout>(readNotesLayout);
  const toggleLayout = () => {
    const next: NotesLayout = layout === 'grid' ? 'list' : 'grid';
    setLayout(next);
    writeNotesLayout(next);
  };
  /** The open editor: `draft` is null while the note is being read. */
  const [editing, setEditing] = useState<{ draft: DraftState | null; title: string; fresh?: FreshNote } | null>(null);
  const editorRef = useRef<NoteEditorHandle>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const isAdmin = Boolean(me?.is_admin);
  const [sendAs, setSendAs] = useSendAsPref(isAdmin, 'notes');
  const asUser = isAdmin && sendAs === 'admin' ? ('admin' as const) : undefined;

  // One request: the list carries the newest notes' text for the previews.
  // It used to be one read per card, and 24 reads at once filled the server's
  // connections to Nextcloud, so the editor's own read then timed out.
  // The last visit's list is kept in localStorage so this page paints before
  // the network answers; the request below still revalidates it in the
  // background and replaces it when it lands.
  const scope = asUser ?? 'me';
  const listKey = useMemo(() => ['notes-list', scope] as const, [scope]);
  const snapshot = useMemo(() => readNotesSnapshot(scope), [scope]);
  const { data: listRes, isLoading, isFetching, refetch } = useQuery({
    queryKey: listKey,
    queryFn: () => api.listNotes({ storage: STORAGE, as_user: asUser, previews: true }),
    staleTime: 30_000,
    initialData: snapshot
      ? ({ status: 'SUCCESS', detail: { notes: snapshot.notes } } as unknown as Awaited<
          ReturnType<typeof api.listNotes>
        >)
      : undefined,
    initialDataUpdatedAt: snapshot?.savedAt,
  });

  const notes = useMemo<NoteSummary[]>(
    () => ((listRes?.status === 'SUCCESS' && (listRes.detail?.notes as NoteSummary[])) || []),
    [listRes]
  );

  const indexKey = useMemo(() => ['notes-index', asUser ?? 'me'] as const, [asUser]);
  const indexQuery = useQuery({
    queryKey: indexKey,
    queryFn: async (): Promise<NotesIndex> => {
      const res = await api.getNotesMeta({ storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not load the notes index');
      return parseNotesIndex(res.detail?.meta) ?? EMPTY_NOTES_INDEX;
    },
    retry: false,
    staleTime: 30_000,
    initialData: snapshot?.index,
    initialDataUpdatedAt: snapshot?.savedAt,
  });
  const index = indexQuery.data ?? EMPTY_NOTES_INDEX;

  // Keep the last good list on this device. The next visit seeds the queries
  // above with it, so the shelf never waits on WebDAV to show anything.
  useEffect(() => {
    if (!listRes || listRes.status !== 'SUCCESS' || !indexQuery.isSuccess) return;
    writeNotesSnapshot(scope, notes, index);
  }, [listRes, notes, index, indexQuery.isSuccess, scope]);

  const persistIndex = useCallback(
    async (update: NotesIndex | ((current: NotesIndex) => NotesIndex)) => {
      // Read the freshest cached index instead of a render-time closure:
      // bulk archive/delete taps happen before React re-renders, and a stale
      // base made each write drop the previous action's entry.
      const current = queryClient.getQueryData<NotesIndex>(indexKey) ?? EMPTY_NOTES_INDEX;
      const next = typeof update === 'function' ? update(current) : update;
      queryClient.setQueryData(indexKey, next);
      try {
        const res = await api.writeNotesMeta({
          meta: next as unknown as Record<string, unknown>,
          storage: STORAGE,
          as_user: asUser,
        });
        if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not save note settings');
      } catch (err) {
        toast.error(errorText(err, 'Could not save note settings'));
        void queryClient.invalidateQueries({ queryKey: indexKey });
      }
    },
    [queryClient, indexKey, asUser],
  );

  // One-time upgrade from the old per-device localStorage pins/colours.
  const migrated = useRef(false);
  useEffect(() => {
    if (migrated.current || !indexQuery.isSuccess || notes.length === 0) return;
    migrated.current = true;
    const merged = mergeLegacyMeta(index, notes);
    if (JSON.stringify(merged) !== JSON.stringify(index)) {
      clearLegacyMeta();
      void persistIndex(merged);
    }
  }, [index, indexQuery.isSuccess, notes, persistIndex]);

  const mainNotes = useMemo(() => notes.filter((n) => !isArchivedPath(n.path) && !isTrashedPath(n.path)), [notes]);
  const archivedNotes = useMemo(() => notes.filter((n) => isArchivedPath(n.path)), [notes]);
  const trashedNotes = useMemo(() => notes.filter((n) => isTrashedPath(n.path)), [notes]);

  const contents = useMemo(() => {
    const map: Record<string, string> = {};
    for (const note of notes) {
      if (typeof note.content === 'string') map[note.path] = note.content;
    }
    return map;
  }, [notes]);

  const loadError = listRes != null && listRes.status !== 'SUCCESS';

  const refresh = useCallback(async () => {
    await refetch();
  }, [refetch]);

  const parsedQuery = useMemo(() => parseNoteQuery(search), [search]);
  const tagChips = useMemo(() => topTags(mainNotes, contents), [mainNotes, contents]);

  const visible = useMemo(() => {
    const source = view === 'archive' ? archivedNotes : view === 'trash' ? trashedNotes : mainNotes;
    const filtered = source.filter((note) => {
      const body = contents[note.path];
      if (tagFilter && !hasTag(body, tagFilter)) return false;
      return matchesNoteQuery(note, body, parsedQuery, indexEntry(index, note.path).color);
    });
    return sortNotesByIndex(filtered, index);
  }, [view, mainNotes, archivedNotes, trashedNotes, tagFilter, parsedQuery, contents, index]);

  // Unsaved edits this device kept (lib/noteDrafts.ts). Re-read whenever the
  // editor closes, so a draft left behind by a failed save is offered at once.
  const [draftsVersion, bumpDrafts] = useReducer((n: number) => n + 1, 0);
  const storedDrafts = useMemo(() => {
    void draftsVersion;
    return readNoteDrafts(scope);
  }, [scope, draftsVersion]);

  const closeEditor = useCallback(() => {
    setEditing(null);
    bumpDrafts();
  }, []);

  const openEditor = useCallback(async (note?: NoteSummary, options: { ignoreDraft?: boolean } = {}) => {
    if (!note) {
      setEditing({ draft: { ...EMPTY_DRAFT }, title: '' });
      return;
    }
    // Unsaved changes to this note survived a closed app: open those, with
    // the version they started from, so saving them still checks for a
    // change made elsewhere since.
    const stored = options.ignoreDraft ? null : readNoteDraft(scope, note.path);
    if (stored) {
      setEditing({ draft: { ...stored, draftKey: note.path, restoredAt: stored.savedAt }, title: stored.title || note.title });
      return;
    }
    const meta = indexEntry(index, note.path);
    const folder = folderFromPath(note.path);
    /** The draft as it is stored, so every open path paints the same shape. */
    const draftFor = (text: string, etag?: string): DraftState => ({
      title: note.title,
      // The editor shows the note's own text; the server adds the header.
      content: noteBody(text),
      path: note.path,
      original: { title: note.title, category: categoryFor(folder) },
      folder,
      color: (meta.color ?? 'default') as NoteColor,
      pinned: Boolean(meta.pinned),
      etag,
      base: noteBody(text),
    });
    const cached = contents[note.path];
    if (typeof cached === 'string') {
      // The list already carried this note's text, so open it now and read
      // the stored file in the background. The editor decides what the
      // fresh copy means: untouched, it shows it; edited, it keeps the
      // typing and only adopts the version when the text had not changed.
      setEditing({ draft: draftFor(cached), title: note.title });
      try {
        const res = await api.readNote(note.title, STORAGE, note.path, asUser);
        if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not read that note');
        const fresh: FreshNote = {
          content: noteBody(res.message ?? ''),
          etag: (res.detail?.etag as string | null | undefined) ?? undefined,
        };
        setEditing((current) => (current && current.draft?.path === note.path ? { ...current, fresh } : current));
      } catch (err) {
        // The cache may be stale, so an untouched editor closes instead of
        // risking a save over a change made elsewhere. An edited one stays:
        // its save checks the stored text before writing.
        if (!editorRef.current?.isDirty()) {
          setEditing((current) => (current && current.draft?.path === note.path ? null : current));
        }
        toast.error(errorText(err, 'Could not read that note'));
      }
      return;
    }
    // Not in this visit's previews: the sheet opens at once and fills when
    // the read lands. Always the stored text, never the preview: saving a
    // stale copy would overwrite a change made elsewhere since the list loaded.
    setEditing({ draft: null, title: note.title });
    let content: string;
    let etag: string | undefined;
    try {
      const res = await api.readNote(note.title, STORAGE, note.path, asUser);
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not read that note');
      content = res.message ?? '';
      etag = (res.detail?.etag as string | null | undefined) ?? undefined;
    } catch (err) {
      // An editor opened empty would save over the note, so it stays closed.
      setEditing(null);
      toast.error(errorText(err, 'Could not read that note'));
      return;
    }
    setEditing((current) =>
      current && current.title === note.title && current.draft === null
        ? { title: note.title, draft: draftFor(content, etag) }
        : current,
    );
  }, [asUser, index, contents, scope]);

  /** Drop this device's unsaved copy of a note and open the stored one. */
  // Counts reopenings of the same note, so the editor remounts on the stored
  // text instead of keeping the state it was seeded with.
  const [reopenCount, bumpReopen] = useReducer((n: number) => n + 1, 0);
  const reopenStored = useCallback(
    (path: string | undefined, draftKey: string | undefined) => {
      if (draftKey) clearNoteDraft(scope, draftKey);
      setEditing(null);
      bumpDrafts();
      bumpReopen();
      const note = path ? notes.find((n) => n.path === path) : undefined;
      if (note) void openEditor(note, { ignoreDraft: true });
    },
    [scope, notes, openEditor],
  );

  // Desktop shortcuts on the shelf: C takes a note (as in Keep), / searches
  // the notes. Never while typing in a field or with a modifier held, and not
  // while a note is open (the editor has its own). Registered on the document
  // so they run before the app-wide media shortcuts on the window, whose `/`
  // would otherwise open the global search instead.
  const searchRef = useRef<HTMLInputElement>(null);
  const shelfKeysRef = useRef({ editorOpen: false, openNew: () => {} });
  useEffect(() => {
    shelfKeysRef.current = { editorOpen: Boolean(editing), openNew: () => void openEditor() };
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
      if (isTypingTarget(event.target) || shelfKeysRef.current.editorOpen) return;
      if (event.key === 'c' || event.key === 'C') {
        event.preventDefault();
        shelfKeysRef.current.openNew();
      } else if (event.key === '/') {
        event.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // Deep link from the dashboard's Quick Notes widget and the Android home
  // screen widget: /notes?note=<path> opens a note, /notes?new=1 starts a new
  // one. The list must land first because opening reads by path; once consumed
  // the query is dropped so closing the editor does not immediately reopen it.
  const requestedNote = searchParams.get('note');
  const requestedNew = searchParams.get('new') === '1';
  const deepLinkHandled = useRef<string | null>(null);
  useEffect(() => {
    if (requestedNew) {
      if (deepLinkHandled.current === 'new') return;
      deepLinkHandled.current = 'new';
      // Defer the open one tick: opening synchronously in an effect cascades
      // renders, and this keeps the behaviour identical to the note deep link.
      const timer = window.setTimeout(() => {
        void openEditor();
        const next = new URLSearchParams(searchParams);
        next.delete('new');
        setSearchParams(next, { replace: true });
      }, 0);
      return () => window.clearTimeout(timer);
    }
    if (!requestedNote || requestedNote === deepLinkHandled.current) return;
    const match = notes.find((n) => n.path === requestedNote || n.title === requestedNote);
    if (!match) return;
    deepLinkHandled.current = requestedNote;
    // Defer the open one tick: opening synchronously in an effect cascades
    // renders, and the notes are already in hand by the time it runs.
    const timer = window.setTimeout(() => {
      void openEditor(match);
      const next = new URLSearchParams(searchParams);
      next.delete('note');
      setSearchParams(next, { replace: true });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [requestedNew, requestedNote, notes, openEditor, searchParams, setSearchParams]);

  /**
   * Write the editor's draft. Never overwrites a newer stored note silently:
   * an in-place save sends the version it was opened at (Nextcloud refuses a
   * stale one), and a save without a version, or one that renames or moves
   * the file, first compares the stored text with what was opened. `force`
   * skips both checks; it backs the conflict prompt's "Overwrite".
   */
  const persistDraft = async (draft: DraftState, force = false): Promise<PersistResult> => {
    const title = draft.title.trim() || draft.content.split('\n')[0].slice(0, 60).trim() || 'Untitled note';
    // A different folder is a move; saving outside Notes/ would hide the note.
    const category = categoryFor(draft.folder);
    const previous = draft.path && draft.original ? { ...draft.original, path: draft.path } : undefined;
    const moving = Boolean(previous && (previous.title !== title || previous.category !== category));
    try {
      if (!force && draft.path && draft.base !== undefined && (moving || !draft.etag)) {
        const res = await api.readNote(previous?.title ?? title, STORAGE, draft.path, asUser);
        if (res.status !== 'SUCCESS') {
          return { status: 'failed', message: res.message || 'Could not check the stored note before saving' };
        }
        if (noteBody(res.message ?? '') !== draft.base) return { status: 'conflict' };
      }
      // write (replace), never append; append is quick-capture only.
      const saved = await saveNote({
        title,
        content: draft.content,
        category,
        previous,
        asUser,
        ifMatch: force ? undefined : draft.etag,
      });
      if (saved.conflict) return { status: 'conflict' };
      const res = saved.response;
      if (res.status !== 'SUCCESS') return { status: 'failed', message: res.message || 'Save failed' };
      const savedPath = saved.path ?? draft.path ?? '';
      // The index is shared and slow to write: only touch it when this save
      // changed what it records (a new path, pin or colour).
      const known = indexEntry(queryClient.getQueryData<NotesIndex>(indexKey), savedPath);
      if (
        (draft.path && savedPath !== draft.path) ||
        Boolean(known.pinned) !== draft.pinned ||
        (known.color ?? 'default') !== draft.color
      ) {
        await persistIndex((current) => {
          let next = current;
          if (draft.path && savedPath !== draft.path) next = forgetIndexEntry(next, draft.path);
          if (savedPath) next = setIndexEntry(next, savedPath, { pinned: draft.pinned, color: draft.color });
          return next;
        });
      }
      if (saved.leftOldCopy) toast.error('Saved under the new name, but the old copy is still there');
      return { status: 'saved', path: savedPath, title, etag: saved.etag };
    } catch (err) {
      return { status: 'failed', message: errorText(err, 'Save failed') };
    }
  };

  /** Save the editor's text as a new note beside the one that changed elsewhere. */
  const keepBoth = async (draft: DraftState): Promise<PersistResult> => {
    const base = draft.title.trim() || 'Untitled note';
    const taken = new Set(notes.filter((n) => folderFromPath(n.path) === draft.folder).map((n) => n.title));
    let title = `${base} (my copy)`;
    for (let n = 2; taken.has(title); n += 1) title = `${base} (my copy ${n})`;
    return persistDraft({ ...draft, title, path: undefined, original: undefined, etag: undefined, base: undefined }, true);
  };

  const finishEditing = (saved: boolean) => {
    closeEditor();
    if (saved) void refresh();
  };

  /** Move one note; resolves to where it landed, or null when the move failed. */
  const moveNote = async (note: NoteSummary, destination: string, previousPath?: string | null): Promise<string | null> => {
    // Paint the move before the network answers: WebDAV MOVE plus the index
    // write can outlast the old 15s window while the previews are busy, and a
    // card that stays put for that long reads as "the move did not work".
    type ListData = Awaited<ReturnType<typeof api.listNotes>>;
    const previousList = queryClient.getQueryData<ListData>(listKey);
    queryClient.setQueryData<ListData>(listKey, (current) => {
      if (!current || current.status !== 'SUCCESS') return current;
      const notesNow = (current.detail?.notes as NoteSummary[] | undefined) ?? [];
      return {
        ...current,
        detail: {
          ...(current.detail ?? {}),
          notes: notesNow.map((n) => (n.path === note.path ? { ...n, path: destination } : n)),
        },
      };
    });
    setEditing(null);
    try {
      const res = await api.moveNote({ path: note.path, destination, storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not move the note');
      // The server can rename the destination when a file is already there,
      // so use the path it reports as the note's new home.
      const movedTo = (res.detail?.path as string | undefined) || destination;
      await persistIndex((current) => {
        const existing = indexEntry(current, note.path);
        const next = forgetIndexEntry(current, note.path);
        // previousPath describes where this note came from; a fresh move sets its own.
        const rest = { ...existing };
        delete rest.previousPath;
        return setIndexEntry(next, movedTo, previousPath ? { ...rest, previousPath } : rest);
      });
      // Reconcile in the background; the shelf is already showing the move.
      void refresh();
      return movedTo;
    } catch (err) {
      // The card was only moved optimistically, so put it back where it was.
      if (previousList) queryClient.setQueryData(listKey, previousList);
      toast.error(errorText(err, 'Could not move the note'));
      void refresh();
      return null;
    }
  };

  /**
   * The index's previousPath to give a note moved back to `from` by Undo: a
   * note going back into Archive or Trash keeps the origin it had there, a
   * note going back to the shelf needs none.
   */
  const undoPreviousFor = (from: string) =>
    isArchivedPath(from) || isTrashedPath(from) ? (indexEntry(index, from).previousPath ?? null) : null;

  /**
   * Archive, trash or restore with an Undo toast. The toast shows at once (the
   * card has already moved); Undo waits for the move to land, then moves the
   * note back through the same move route a Restore uses.
   */
  const undoableMove = (note: NoteSummary, destination: string, previousPath: string | null, done: string) => {
    const backPrevious = undoPreviousFor(note.path);
    const landed = moveNote(note, destination, previousPath);
    const toastId = showUndoToast(`${done} “${note.title}”`, () => {
      void landed.then((movedTo) => {
        if (movedTo) void moveNote({ ...note, path: movedTo }, note.path, backPrevious);
      });
    });
    void landed.then((movedTo) => {
      if (!movedTo) toast.dismiss(toastId);
    });
  };

  const archiveNote = (note: NoteSummary) => {
    undoableMove(note, archivedPathFor(note.path, ARCHIVE_PREFIX), indexEntry(index, note.path).previousPath ?? note.path, 'Archived');
  };

  const trashNote = (note: NoteSummary) => {
    undoableMove(note, archivedPathFor(note.path, TRASH_PREFIX), indexEntry(index, note.path).previousPath ?? note.path, 'Moved to trash');
  };

  const restoreDestinationFor = (note: NoteSummary) => {
    const entry = indexEntry(index, note.path);
    const base = note.path.split('/').pop() ?? note.title;
    return entry.previousPath && !isArchivedPath(entry.previousPath) && !isTrashedPath(entry.previousPath)
      ? entry.previousPath
      : `${NOTES_DIR}/${base}`;
  };

  const exitSelection = () => {
    setSelectMode(false);
    setSelected(new Set());
  };

  const toggleSelected = (path: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  /**
   * Move many notes in one pass: the moves run one after another, then a
   * single index write records all of them, so a bulk action cannot lose the
   * earlier entries the way a per-card write used to.
   */
  const bulkMove = async (
    notes: NoteSummary[],
    destinationFor: (note: NoteSummary) => string,
    previousFor?: (note: NoteSummary) => string | null,
    /** Past-tense verb for an Undo toast ("Archived"); omitted for an Undo itself. */
    done?: string,
  ) => {
    if (!notes.length || bulkBusy) return;
    setBulkBusy(true);
    const moved: Array<{ from: string; to: string; previousPath?: string | null; note: NoteSummary; backPrevious: string | null }> = [];
    const failed: string[] = [];
    for (const note of notes) {
      try {
        const destination = destinationFor(note);
        const res = await api.moveNote({ path: note.path, destination, storage: STORAGE, as_user: asUser });
        if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not move the note');
        moved.push({
          from: note.path,
          to: (res.detail?.path as string | undefined) || destination,
          previousPath: previousFor?.(note),
          note,
          backPrevious: undoPreviousFor(note.path),
        });
      } catch {
        failed.push(note.title);
      }
    }
    if (moved.length) {
      await persistIndex((current) => {
        let next = current;
        for (const move of moved) {
          const existing = indexEntry(next, move.from);
          next = forgetIndexEntry(next, move.from);
          const rest = { ...existing };
          delete rest.previousPath;
          next = setIndexEntry(next, move.to, move.previousPath ? { ...rest, previousPath: move.previousPath } : rest);
        }
        return next;
      });
    }
    setBulkBusy(false);
    exitSelection();
    if (failed.length) toast.error(`Could not move ${failed.length} note${failed.length === 1 ? '' : 's'}: ${failed.join(', ')}`);
    if (moved.length && done) {
      // Undo puts back only the notes that actually moved, each where it was.
      const back = new Map(moved.map((m) => [m.to, m]));
      showUndoToast(`${done} ${moved.length} note${moved.length === 1 ? '' : 's'}`, () => {
        void bulkMove(
          moved.map((m) => ({ ...m.note, path: m.to })),
          (note) => back.get(note.path)?.from ?? note.path,
          (note) => back.get(note.path)?.backPrevious ?? null,
        );
      });
    } else if (moved.length && !failed.length) {
      toast.success(`Moved ${moved.length} note${moved.length === 1 ? '' : 's'} back`);
    }
    void refresh();
  };

  const bulkDelete = async (notes: NoteSummary[]) => {
    if (!notes.length || bulkBusy) return;
    setBulkBusy(true);
    const deleted: string[] = [];
    const failed: string[] = [];
    for (const note of notes) {
      try {
        const res = await api.deleteNote(note.title, STORAGE, note.path, asUser);
        if (res.status !== 'SUCCESS') throw new Error(res.message || 'Delete failed');
        deleted.push(note.path);
      } catch {
        failed.push(note.title);
      }
    }
    if (deleted.length) {
      await persistIndex((current) => {
        let next = current;
        for (const path of deleted) next = forgetIndexEntry(next, path);
        return next;
      });
    }
    setBulkBusy(false);
    exitSelection();
    if (failed.length) toast.error(`Could not delete ${failed.length} note${failed.length === 1 ? '' : 's'}: ${failed.join(', ')}`);
    else if (deleted.length) toast.success(`Deleted ${deleted.length} note${deleted.length === 1 ? '' : 's'}`);
    void refresh();
  };

  const selectedNotes = visible.filter((note) => selected.has(note.path));

  const restoreNote = (note: NoteSummary) => {
    undoableMove(note, restoreDestinationFor(note), null, 'Restored');
  };

  const destroyNote = async (note: NoteSummary) => {
    if (!window.confirm(`Permanently delete "${note.title}"? This cannot be undone.`)) return;
    try {
      const res = await api.deleteNote(note.title, STORAGE, note.path, asUser);
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Delete failed');
        return;
      }
      await persistIndex((current) => forgetIndexEntry(current, note.path));
      setEditing(null);
      toast.success('Note deleted');
      await refresh();
    } catch {
      toast.error('Delete failed');
    }
  };

  const emptyTrash = async () => {
    if (!window.confirm(`Permanently delete all ${trashedNotes.length} notes in the trash?`)) return;
    try {
      const res = await api.emptyNotesTrash({ storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Could not empty the trash');
        return;
      }
      await persistIndex((current) => {
        let next = current;
        for (const note of trashedNotes) next = forgetIndexEntry(next, note.path);
        return next;
      });
      toast.success('Trash emptied');
      await refresh();
    } catch {
      toast.error('Could not empty the trash');
    }
  };

  const duplicateNote = async (note: NoteSummary) => {
    try {
      const res = await api.duplicateNote({ path: note.path, title: note.title, storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Could not duplicate the note');
        return;
      }
      toast.success('Note duplicated');
      await refresh();
    } catch {
      toast.error('Could not duplicate the note');
    }
  };

  const togglePin = (note: NoteSummary) => {
    void persistIndex((current) => setIndexEntry(current, note.path, { pinned: !indexEntry(current, note.path).pinned }));
  };

  const setColor = (note: NoteSummary, color: NoteColor) => {
    void persistIndex((current) => setIndexEntry(current, note.path, { color }));
  };

  const toggleChecklistItem = async (note: NoteSummary, item: string) => {
    try {
      const res = await api.checkOffNote({ title: note.title, item, path: note.path, storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Could not update the checklist');
        return;
      }
      // The server forgot its copy of this note, so the list reads it again.
      await refresh();
    } catch {
      toast.error('Could not update the checklist');
    }
  };

  const [syncing, setSyncing] = useState(false);
  const syncRag = async () => {
    setSyncing(true);
    try {
      const res = await api.syncNotesRag({ storage: STORAGE });
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'RAG sync failed');
        return;
      }
      toast.success(res.message || 'Notes indexed into Jarvis memory');
    } catch {
      toast.error('RAG sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const openDraftKey = editing?.draft ? (editing.draft.draftKey ?? editing.draft.path) : undefined;
  const pendingDrafts = Object.entries(storedDrafts)
    .filter(([id]) => id !== openDraftKey)
    .sort(([, a], [, b]) => b.savedAt - a.savedAt);

  const tabs: Array<{ id: NotesView; label: string; count: number }> = [
    { id: 'notes', label: 'Notes', count: mainNotes.length },
    { id: 'archive', label: 'Archive', count: archivedNotes.length },
    { id: 'trash', label: 'Trash', count: trashedNotes.length },
  ];

  return (
    <div className="space-y-5 max-w-6xl mx-auto pb-24">
      {/* Header */}
      <div className="glass-panel p-5 rounded-2xl border border-white/10 flex flex-col sm:flex-row sm:items-center gap-4 justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Notes</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            {mainNotes.length} {mainNotes.length === 1 ? 'note' : 'notes'} · Nextcloud markdown · synced to Jarvis memory on demand
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isAdmin && (
            <SendAsSelector
              value={sendAs}
              onChange={setSendAs}
              disabled={Boolean(editing)}
            />
          )}
          {visible.length > 0 && (
            <button
              type="button"
              onClick={() => (selectMode ? exitSelection() : setSelectMode(true))}
              aria-pressed={selectMode}
              data-testid="select-notes"
              className="glass-button min-h-11 px-3 py-2 text-xs"
            >
              {selectMode ? 'Done' : 'Select'}
            </button>
          )}
          <div className="relative min-w-40 flex-1 sm:flex-none">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              ref={searchRef}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                // Escape leaves the search box, back to the shelf's shortcuts.
                if (e.key === 'Escape') e.currentTarget.blur();
              }}
              placeholder="Search notes (tag:…)"
              aria-label="Search notes"
              title="Search notes (/)"
              className="glass-input min-h-11 pl-8 pr-3 py-2 text-base sm:text-sm w-full sm:w-56"
            />
          </div>
          <button
            type="button"
            onClick={() => void syncRag()}
            disabled={syncing}
            className="glass-button min-h-11 px-3 py-2 text-xs disabled:opacity-60"
            title="Index notes into Jarvis memory, so you can ask Jarvis about them"
          >
            {syncing ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Teach Jarvis
          </button>
          <button
            type="button"
            onClick={toggleLayout}
            data-testid="notes-layout"
            className="glass-button flex min-h-11 min-w-11 items-center justify-center px-3 py-2 text-xs"
            title={layout === 'grid' ? 'List view' : 'Grid view'}
            aria-label={layout === 'grid' ? 'Show notes as a list' : 'Show notes as a grid'}
          >
            {layout === 'grid' ? <Rows3 size={14} /> : <LayoutGrid size={14} />}
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            className="glass-button flex min-h-11 min-w-11 items-center justify-center px-3 py-2 text-xs"
            title="Refresh"
            aria-label="Refresh notes"
          >
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Quick capture */}
      {view === 'notes' && (
        <button
          type="button"
          onClick={() => void openEditor()}
          data-testid="quick-capture"
          className="w-full min-h-12 glass-panel rounded-2xl border border-white/10 px-4 py-3 text-left text-sm text-slate-400 hover:border-purple-500/40 transition-colors flex items-center gap-3"
        >
          <Plus size={16} className="text-purple-400" />
          Take a note…
          {/* The shortcut hint only where there is a keyboard-and-mouse desktop. */}
          <kbd className="ml-auto hidden rounded border border-white/15 px-1.5 text-[10px] text-slate-500 [@media(hover:hover)]:inline">C</kbd>
        </button>
      )}

      {/* Unsaved edits this device kept when the app closed mid-edit. */}
      {pendingDrafts.length > 0 && (
        <div data-testid="note-drafts" className="glass-panel space-y-2 rounded-2xl border border-amber-400/30 p-3" role="region" aria-label="Unsaved drafts">
          {pendingDrafts.map(([id, draft]) => (
            <div key={id} className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
              <span className="min-w-0 flex-1">
                Unsaved {draft.path ? 'changes to' : 'new note'} <strong className="text-slate-100">{draft.title.trim() || 'Untitled note'}</strong>
                <span className="text-slate-500"> · kept {new Date(draft.savedAt).toLocaleString()}</span>
              </span>
              <button
                type="button"
                className="glass-button min-h-9 px-3 py-1 text-xs"
                onClick={() => setEditing({ draft: { ...draft, draftKey: id, restoredAt: draft.savedAt }, title: draft.title })}
              >
                Restore
              </button>
              <button
                type="button"
                className="min-h-9 rounded-xl px-3 py-1 text-xs text-rose-300 hover:bg-white/5"
                onClick={() => {
                  clearNoteDraft(scope, id);
                  bumpDrafts();
                }}
              >
                Discard
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Views: keep archive and trash out of the main shelf, like Keep. */}
      <div className="flex items-center gap-2">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setView(tab.id)}
            aria-pressed={view === tab.id}
            className={`min-h-11 rounded-xl px-3 py-2 text-xs transition-colors ${
              view === tab.id ? 'bg-white/10 text-slate-100' : 'text-slate-400 hover:bg-white/5'
            }`}
          >
            {tab.label}
            {tab.count > 0 && <span className="ml-1.5 text-slate-500">{tab.count}</span>}
          </button>
        ))}
        {view === 'trash' && trashedNotes.length > 0 && (
          <button type="button" onClick={() => void emptyTrash()} className="ml-auto min-h-11 px-3 py-2 text-xs text-rose-300 hover:bg-white/5 rounded-xl">
            Empty trash
          </button>
        )}
      </div>

      {selectMode && (
        <div
          data-testid="bulk-bar"
          className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-2xl border border-purple-500/30 bg-slate-900/95 px-3 py-2 backdrop-blur"
        >
          <span className="text-xs text-slate-300">{selectedNotes.length} selected</span>
          <button
            type="button"
            onClick={() =>
              setSelected(selectedNotes.length === visible.length ? new Set() : new Set(visible.map((note) => note.path)))
            }
            className="min-h-9 rounded-full px-3 text-[11px] text-slate-300 hover:bg-white/10"
          >
            {selectedNotes.length === visible.length ? 'Clear all' : 'Select all'}
          </button>
          {view === 'notes' && (
            <>
              <button
                type="button"
                disabled={!selectedNotes.length || bulkBusy}
                onClick={() =>
                  void bulkMove(
                    selectedNotes,
                    (note) => archivedPathFor(note.path, ARCHIVE_PREFIX),
                    (note) => indexEntry(index, note.path).previousPath ?? note.path,
                    'Archived',
                  )
                }
                className="min-h-9 rounded-full px-3 text-[11px] text-slate-200 hover:bg-white/10 disabled:opacity-50"
              >
                Archive
              </button>
              <button
                type="button"
                disabled={!selectedNotes.length || bulkBusy}
                onClick={() =>
                  void bulkMove(
                    selectedNotes,
                    (note) => archivedPathFor(note.path, TRASH_PREFIX),
                    (note) => indexEntry(index, note.path).previousPath ?? note.path,
                    'Moved to trash:',
                  )
                }
                className="min-h-9 rounded-full px-3 text-[11px] text-rose-300 hover:bg-white/10 disabled:opacity-50"
              >
                Trash
              </button>
            </>
          )}
          {view === 'archive' && (
            <button
              type="button"
              disabled={!selectedNotes.length || bulkBusy}
              onClick={() =>
                void bulkMove(
                  selectedNotes,
                  (note) => archivedPathFor(note.path, TRASH_PREFIX),
                  (note) => indexEntry(index, note.path).previousPath ?? note.path,
                  'Moved to trash:',
                )
              }
              className="min-h-9 rounded-full px-3 text-[11px] text-rose-300 hover:bg-white/10 disabled:opacity-50"
            >
              Trash
            </button>
          )}
          {view !== 'notes' && (
            <button
              type="button"
              disabled={!selectedNotes.length || bulkBusy}
              onClick={() => void bulkMove(selectedNotes, restoreDestinationFor, () => null, 'Restored')}
              className="min-h-9 rounded-full px-3 text-[11px] text-emerald-300 hover:bg-white/10 disabled:opacity-50"
            >
              Restore
            </button>
          )}
          {view === 'trash' && (
            <button
              type="button"
              disabled={!selectedNotes.length || bulkBusy}
              onClick={() => void bulkDelete(selectedNotes)}
              className="min-h-9 rounded-full px-3 text-[11px] text-rose-300 hover:bg-white/10 disabled:opacity-50"
            >
              Delete forever
            </button>
          )}
          <button
            type="button"
            onClick={exitSelection}
            className="ml-auto min-h-9 rounded-full px-3 text-[11px] text-slate-400 hover:bg-white/10"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Labels: inline #tags, which Nextcloud Notes reads as tags too. */}
      {view === 'notes' && tagChips.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" aria-label="Filter by label">
          <button
            type="button"
            aria-pressed={!tagFilter}
            onClick={() => setTagFilter('')}
            className={`min-h-9 rounded-full px-3 py-1 text-[11px] transition-colors ${
              tagFilter ? 'text-slate-400 hover:bg-white/5' : 'bg-white/10 text-slate-100'
            }`}
          >
            All labels
          </button>
          {tagChips.map(({ tag, count }) => (
            <button
              key={tag}
              type="button"
              aria-pressed={tagFilter === tag}
              onClick={() => setTagFilter(tagFilter === tag ? '' : tag)}
              className={`min-h-9 rounded-full px-3 py-1 text-[11px] transition-colors ${
                tagFilter === tag ? 'bg-purple-500/20 text-purple-100' : 'text-slate-400 hover:bg-white/5'
              }`}
            >
              #{tag} <span className="text-slate-500">{count}</span>
            </button>
          ))}
        </div>
      )}

      {/* Grid */}
      {isLoading && notes.length === 0 ? (
        <div className="flex items-center justify-center py-16 text-slate-500 text-sm gap-2">
          <Loader2 size={16} className="animate-spin" /> Loading notes…
        </div>
      ) : visible.length === 0 ? (
        <div className="glass-panel rounded-2xl border border-white/5 p-10 text-center">
          <p className="text-sm text-slate-400">
            {search
              ? 'No notes match that search.'
              : loadError
                ? (listRes?.message || 'Could not load notes. Is Nextcloud connected?')
                : view === 'archive'
                  ? 'Nothing archived.'
                  : view === 'trash'
                    ? 'Trash is empty.'
                    : 'No notes yet. Take one above.'}
          </p>
        </div>
      ) : (
        <div
          data-testid="notes-shelf"
          data-layout={layout}
          className={layout === 'grid' ? 'columns-1 sm:columns-2 lg:columns-3 gap-4 [column-fill:_balance]' : 'mx-auto max-w-2xl'}
        >
          {visible.map((note) => {
            const body = contents[note.path];
            const meta = indexEntry(index, note.path);
            const color = (meta.color ?? 'default') as NoteColor;
            const checklist = body ? parseChecklist(body) : [];
            const tags = extractTags(body);
            return (
              <article
                key={note.path}
                data-testid="note-card"
                className={`mb-4 break-inside-avoid rounded-2xl border p-4 transition-colors cursor-pointer relative group ${
                  selectMode && selected.has(note.path) ? 'border-purple-400/70 ring-2 ring-purple-500/40' : 'border-white/10 hover:border-purple-500/40'
                }`}
                style={{ background: NOTE_COLOR_BG[color] }}
                onClick={() => {
                  if (selectMode) {
                    toggleSelected(note.path);
                    return;
                  }
                  void openEditor(note);
                }}
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-semibold text-slate-100 text-sm leading-snug">{note.title}</h3>
                  <div className="flex items-center gap-1 shrink-0">
                    {selectMode ? (
                      <span
                        aria-hidden
                        className={`flex h-6 w-6 items-center justify-center rounded-full border ${
                          selected.has(note.path) ? 'border-purple-400 bg-purple-500 text-white' : 'border-white/30 text-transparent'
                        }`}
                      >
                        <Check size={14} />
                      </span>
                    ) : (
                      <button
                        type="button"
                        aria-label={meta.pinned ? 'Unpin note' : 'Pin note'}
                        title={meta.pinned ? 'Unpin' : 'Pin'}
                        // Hover-revealed only where there is hover: on a phone it is always there.
                        className={`-mr-2 -mt-2 flex h-11 w-11 items-center justify-center rounded-xl hover:bg-white/10 ${meta.pinned ? 'text-amber-300' : 'text-slate-500 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 focus-visible:opacity-100'}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          togglePin(note);
                        }}
                      >
                        {meta.pinned ? <Pin size={14} /> : <PinOff size={14} />}
                      </button>
                    )}
                  </div>
                </div>

                {checklist.length > 0 ? (
                  <ul className="mt-1 space-y-0.5">
                    {checklist.slice(0, 6).map((item, itemIndex) => (
                      <li key={`${itemIndex}-${item.text}`}>
                        <button
                          type="button"
                          aria-label={`${item.checked ? 'Uncheck' : 'Check'} ${item.text}`}
                          className="-mx-1 flex min-h-11 w-full items-center gap-2 rounded-lg px-1 text-left text-xs text-slate-300 hover:bg-white/5 [@media(hover:hover)]:min-h-7"
                          onClick={(e) => {
                            e.stopPropagation();
                            void toggleChecklistItem(note, item.text);
                          }}
                        >
                          <span className={`shrink-0 ${item.checked ? 'text-emerald-400' : 'text-slate-500'}`}>
                            {item.checked ? <CheckSquare size={15} /> : <Square size={15} />}
                          </span>
                          <span className={item.checked ? 'line-through text-slate-500' : ''}>{item.text}</span>
                        </button>
                      </li>
                    ))}
                    {checklist.length > 6 && (
                      <li className="text-[10px] text-slate-500 pl-5">+{checklist.length - 6} more</li>
                    )}
                  </ul>
                ) : (
                  <div className="mt-2 max-h-32 overflow-hidden">
                    {body !== undefined ? (
                      <NotePreview text={noteDisplayBody(body, note.title)} />
                    ) : (
                      <p className="text-xs text-slate-400">{Math.max(1, Math.round(note.size / 1024))} KB</p>
                    )}
                  </div>
                )}

                {tags.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {tags.slice(0, 3).map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        className="rounded-full bg-white/5 px-1.5 py-0.5 text-[10px] text-purple-200 hover:bg-white/10"
                        onClick={(e) => {
                          e.stopPropagation();
                          setTagFilter(tag);
                        }}
                      >
                        #{tag}
                      </button>
                    ))}
                    {tags.length > 3 && <span className="text-[10px] text-slate-500">+{tags.length - 3}</span>}
                  </div>
                )}

                <div className="mt-3 flex items-center justify-between text-[10px] text-slate-500">
                  <span>{new Date(note.modified).toLocaleDateString()}</span>
                  {/* Quick actions for mouse users; on a phone they live in the editor. */}
                  {!selectMode && (
                    <div className="flex items-center gap-1 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:transition-opacity [@media(hover:hover)]:group-hover:opacity-100">
                    {view === 'notes' && (
                      <>
                        <div className="hidden items-center gap-1 [@media(hover:hover)]:flex">
                          <Palette size={12} />
                          {NOTE_COLORS.map((c) => (
                            <button
                              key={c.id}
                              type="button"
                              aria-label={`Colour ${c.label}`}
                              title={c.label}
                              className={`h-3 w-3 rounded-full border ${color === c.id ? 'border-white' : 'border-transparent'}`}
                              style={{ background: c.swatch }}
                              onClick={(e) => {
                                e.stopPropagation();
                                setColor(note, c.id);
                              }}
                            />
                          ))}
                        </div>
                        <button
                          type="button"
                          aria-label={`Archive ${note.title}`}
                          title="Archive"
                          className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 hover:bg-white/10 hover:text-slate-200"
                          onClick={(e) => {
                            e.stopPropagation();
                            archiveNote(note);
                          }}
                        >
                          <Archive size={14} />
                        </button>
                      </>
                    )}
                    <button
                      type="button"
                      aria-label={`Duplicate ${note.title}`}
                      title="Duplicate"
                      className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 hover:bg-white/10 hover:text-slate-200"
                      onClick={(e) => {
                        e.stopPropagation();
                        void duplicateNote(note);
                      }}
                    >
                      <Copy size={14} />
                    </button>
                    {view === 'notes' && (
                      <button
                        type="button"
                        aria-label={`Move ${note.title} to trash`}
                        title="Move to trash"
                        className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 hover:bg-white/10 hover:text-rose-300"
                        onClick={(e) => {
                          e.stopPropagation();
                          trashNote(note);
                        }}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                    {view !== 'notes' && (
                      <button
                        type="button"
                        aria-label={`Restore ${note.title}`}
                        title="Restore"
                        className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 hover:bg-white/10 hover:text-emerald-300"
                        onClick={(e) => {
                          e.stopPropagation();
                          restoreNote(note);
                        }}
                      >
                        <ArchiveRestore size={14} />
                      </button>
                    )}
                    {view === 'trash' && (
                      <button
                        type="button"
                        aria-label={`Delete ${note.title} permanently`}
                        title="Delete permanently"
                        className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 hover:bg-white/10 hover:text-rose-300"
                        onClick={(e) => {
                          e.stopPropagation();
                          void destroyNote(note);
                        }}
                      >
                        <X size={14} />
                      </button>
                    )}
                    </div>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      {/* Editor */}
      {editing && (
        <Modal
          isOpen
          // Closing saves pending changes first (as in Keep); the editor
          // decides, so a refused save keeps the sheet open on the conflict.
          onClose={() => (editorRef.current ? void editorRef.current.requestClose() : closeEditor())}
          title={editing.title || editing.draft?.path ? 'Edit note' : 'New note'}
          size="lg"
        >
          {editing.draft ? (
            <NoteEditor
              // A different note, or the stored copy replacing kept changes,
              // is a fresh editor: its state is only seeded at mount.
              key={`${editing.draft.draftKey ?? editing.draft.path ?? 'new'}|${editing.draft.restoredAt ?? ''}|${reopenCount}`}
              ref={editorRef}
              initial={editing.draft}
              fresh={editing.fresh}
              scope={scope}
              asUser={asUser}
              onPersist={persistDraft}
              onKeepBoth={keepBoth}
              onDone={finishEditing}
              onUseStored={() => reopenStored(editing.draft?.path, editing.draft?.draftKey ?? editing.draft?.path)}
              onTrash={
                editing.draft.path
                  ? () => {
                      // By path: the title in the editor may already be edited.
                      const note = notes.find((n) => n.path === editing.draft?.path);
                      if (!note) return;
                      // Trashing is the answer to any unsaved edits; Undo brings the stored note back.
                      clearNoteDraft(scope, note.path);
                      bumpDrafts();
                      trashNote(note);
                    }
                  : undefined
              }
            />
          ) : (
            <div className="flex min-h-40 items-center justify-center gap-2 text-sm text-slate-400" role="status">
              <Loader2 size={16} className="animate-spin" /> Opening note…
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}


/** What the page asks of the open editor (Modal close, a failed background read). */
interface NoteEditorHandle {
  /** Save pending changes, then close; a refused save keeps the sheet open on the conflict. */
  requestClose: () => Promise<void>;
  /** True while the editor holds changes the server does not have. */
  isDirty: () => boolean;
}

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  /** `snapshot` is the text that failed, so autosave waits for a new change before retrying. */
  | { kind: 'error'; message: string; snapshot: string };

/** The parts of a draft a save writes; equal snapshots mean nothing to save. */
function snapshotOf(draft: DraftState): string {
  return JSON.stringify([draft.title, draft.content, draft.folder, draft.color, draft.pinned]);
}

/**
 * The note editor. Its draft lives here, so typing re-renders this sheet
 * only, not the page and every card behind it.
 *
 * Autosave: an open note that keeps its title and folder saves itself
 * AUTOSAVE_MS after typing pauses, in place and only over the version it was
 * opened at. A new note, or a rename or move, waits for Save or close (one
 * file per name, not one per keystroke). Every change the server does not
 * have yet is also kept on this device (lib/noteDrafts.ts), so a closed app
 * loses nothing.
 */
const NoteEditor = forwardRef<
  NoteEditorHandle,
  {
    initial: DraftState;
    /** The stored text, when it arrived after the sheet opened from the list's copy. */
    fresh?: FreshNote;
    /** Whose notes these are (the draft store is per account). */
    scope: string;
    onPersist: (draft: DraftState, force?: boolean) => Promise<PersistResult>;
    onKeepBoth: (draft: DraftState) => Promise<PersistResult>;
    /** Close the sheet; `saved` asks the page to reload the list. */
    onDone: (saved: boolean) => void;
    /** Drop this device's unsaved copy and show the stored note instead. */
    onUseStored: () => void;
    onTrash?: () => void;
    /** Whose Nextcloud the note is in, for its version history. */
    asUser?: 'admin';
  }
>(function NoteEditor({ initial, fresh, scope, onPersist, onKeepBoth, onDone, onUseStored, onTrash, asUser }, handle) {
  const [showHistory, setShowHistory] = useState(false);
  const [draft, setDraft] = useState<DraftState>(initial);
  const [labelDraft, setLabelDraft] = useState('');
  // Chosen per text: notes the visual editor could not save back unchanged
  // (tables, code, links …) are edited as text instead.
  const [rich, setRich] = useState(() => richEditable(initial.content));
  const [richKey, setRichKey] = useState(0);
  // What the server holds. A restored draft is by definition not stored yet.
  const [lastSaved, setLastSaved] = useState<string | null>(() => (initial.restoredAt ? null : snapshotOf(initial)));
  const [version, setVersion] = useState<{ etag?: string; base?: string }>({ etag: initial.etag, base: initial.base });
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle' });
  const [conflict, setConflict] = useState(false);
  const [draftKey] = useState(() => initial.draftKey ?? initial.path ?? `new:${Date.now()}`);
  const [seenFresh, setSeenFresh] = useState(fresh);
  const richRef = useRef<RichNoteEditorHandle>(null);

  const snapshot = snapshotOf(draft);
  const dirty = snapshot !== lastSaved;
  const renaming = Boolean(
    draft.original && (draft.title.trim() !== draft.original.title || categoryFor(draft.folder) !== draft.original.category),
  );

  // The stored file arrived after the sheet opened from the list's copy.
  // Untouched, show it; edited, keep the typing and adopt the version only
  // when the stored text is what the edit started from. Otherwise the next
  // save compares texts and reports the conflict instead of overwriting.
  if (fresh !== seenFresh) {
    setSeenFresh(fresh);
    if (fresh && !dirty) {
      const next = { ...draft, content: fresh.content };
      setDraft(next);
      setLastSaved(snapshotOf(next));
      setVersion({ etag: fresh.etag, base: fresh.content });
      if (fresh.content !== draft.content) {
        setRich(richEditable(fresh.content));
        setRichKey((k) => k + 1);
      }
    } else if (fresh && fresh.content === version.base) {
      setVersion({ etag: fresh.etag, base: version.base });
    }
  }

  // Saves run one at a time, each with the version the previous one left,
  // so an autosave and a Save tap never race each other into a false conflict.
  const versionRef = useRef(version);
  useEffect(() => {
    versionRef.current = version;
  }, [version]);
  const onPersistRef = useRef(onPersist);
  useEffect(() => {
    onPersistRef.current = onPersist;
  });
  const chainRef = useRef<Promise<unknown>>(Promise.resolve());
  const savedAnyRef = useRef(false);
  const wroteDraftRef = useRef(false);

  const persist = useCallback((d: DraftState, force = false): Promise<PersistResult> => {
    const run = chainRef.current.then(async () => {
      setSaveState({ kind: 'saving' });
      const result = await onPersistRef.current({ ...d, ...versionRef.current }, force);
      if (result.status === 'saved') {
        savedAnyRef.current = true;
        versionRef.current = { etag: result.etag, base: d.content };
        setVersion(versionRef.current);
        setLastSaved(snapshotOf(d));
        setDraft((current) => ({
          ...current,
          path: result.path,
          original: { title: result.title, category: categoryFor(d.folder) },
        }));
        setConflict(false);
        setSaveState({ kind: 'saved' });
      } else if (result.status === 'conflict') {
        setConflict(true);
        setSaveState({ kind: 'idle' });
      } else {
        setSaveState({ kind: 'error', message: result.message, snapshot: snapshotOf(d) });
      }
      return result;
    });
    chainRef.current = run.catch(() => undefined);
    return run;
  }, []);

  const canAutosave =
    Boolean(draft.path) &&
    !renaming &&
    dirty &&
    !conflict &&
    saveState.kind !== 'saving' &&
    !(saveState.kind === 'error' && saveState.snapshot === snapshot);
  useEffect(() => {
    if (!canAutosave) return;
    const timer = window.setTimeout(() => void persist(draft), AUTOSAVE_MS);
    return () => window.clearTimeout(timer);
  }, [canAutosave, draft, persist]);

  // Keep what the server does not have on this device; forget it once saved.
  useEffect(() => {
    const blank = !draft.path && !draft.title.trim() && !draft.content.trim();
    if (dirty && !blank) {
      writeNoteDraft(scope, draftKey, {
        path: draft.path,
        title: draft.title,
        content: draft.content,
        folder: draft.folder,
        color: draft.color,
        pinned: draft.pinned,
        original: draft.original,
        etag: version.etag,
        base: version.base,
      });
      wroteDraftRef.current = true;
    } else if (wroteDraftRef.current) {
      clearNoteDraft(scope, draftKey);
      wroteDraftRef.current = false;
    }
  }, [dirty, draft, version, scope, draftKey]);

  const forgetDraft = () => {
    clearNoteDraft(scope, draftKey);
    wroteDraftRef.current = false;
  };

  const closingRef = useRef(false);
  const requestClose = async () => {
    if (closingRef.current) return;
    const blank = !draft.path && !draft.title.trim() && !draft.content.trim();
    if (!dirty || blank) {
      forgetDraft();
      onDone(savedAnyRef.current);
      return;
    }
    if (conflict) {
      // Nothing is lost: the draft stays on this device and is offered back.
      toast('Your version is kept on this device until you choose what to keep.');
      onDone(savedAnyRef.current);
      return;
    }
    closingRef.current = true;
    const result = await persist(draft);
    closingRef.current = false;
    if (result.status === 'saved') {
      forgetDraft();
      onDone(true);
    } else if (result.status === 'failed') {
      toast.error(`Not saved: ${result.message}. Your changes are kept on this device.`);
      onDone(savedAnyRef.current);
    }
    // A conflict keeps the sheet open on the choice.
  };

  useImperativeHandle(handle, () => ({ requestClose, isDirty: () => dirty }));

  const save = async () => {
    const result = await persist(draft);
    if (result.status === 'saved') {
      forgetDraft();
      toast.success('Note saved');
      onDone(true);
    } else if (result.status === 'failed') {
      toast.error(result.message);
    }
  };

  // Ctrl/Cmd+S saves and Ctrl/Cmd+Enter saves and closes, from anywhere in
  // the sheet, fields included: a modifier chord is never typing. Escape
  // closes through the Modal. The browser's own Save page is kept out.
  const editorKeysRef = useRef({ save: () => {}, close: () => {} });
  useEffect(() => {
    editorKeysRef.current = { save: () => void save(), close: () => void requestClose() };
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey || event.repeat) return;
      if (event.key === 's' || event.key === 'S') {
        event.preventDefault();
        editorKeysRef.current.save();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        editorKeysRef.current.close();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const keepBoth = async () => {
    setSaveState({ kind: 'saving' });
    const result = await onKeepBoth(draft);
    if (result.status === 'saved') {
      forgetDraft();
      toast.success(`Saved your version as “${result.title}”`);
      onDone(true);
      return;
    }
    setSaveState(
      result.status === 'failed'
        ? { kind: 'error', message: result.message, snapshot }
        : { kind: 'idle' },
    );
  };

  const takeStored = () => {
    forgetDraft();
    onUseStored();
  };

  const status =
    saveState.kind === 'saving'
      ? 'Saving…'
      : saveState.kind === 'error' && saveState.snapshot === snapshot
        ? `Not saved: ${saveState.message}`
        : !dirty
          ? saveState.kind === 'saved' ? 'Saved' : ''
          : canAutosave
            ? 'Unsaved changes'
            : 'Kept on this device until you save';

  return (
    <div className="space-y-3">
      {initial.restoredAt && !conflict && (
        <p className="text-[11px] text-amber-200" role="note">
          Showing unsaved changes this device kept {new Date(initial.restoredAt).toLocaleString()}.{' '}
          <button type="button" className="underline" onClick={takeStored}>
            Discard them
          </button>
        </p>
      )}
      {conflict && (
        <div role="alert" className="space-y-2 rounded-xl border border-amber-400/40 bg-amber-500/10 p-3 text-xs text-amber-100">
          <p>
            This note changed somewhere else since you opened it, so your edits were not saved over it. Your text is
            still here.
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="glass-button min-h-9 px-3 py-1 text-xs" onClick={() => void keepBoth()}>
              Keep both
            </button>
            <button type="button" className="glass-button min-h-9 px-3 py-1 text-xs" onClick={takeStored}>
              Use the stored version
            </button>
            <button
              type="button"
              className="glass-button min-h-9 px-3 py-1 text-xs text-rose-300"
              onClick={() => void persist(draft, true)}
            >
              Overwrite with mine
            </button>
          </div>
        </div>
      )}
      <input
        value={draft.title}
        onChange={(e) => setDraft({ ...draft, title: e.target.value })}
        placeholder="Title"
        aria-label="Note title"
        className="glass-input min-h-11 w-full text-base font-semibold"
      />
      {rich ? (
        <RichNoteEditor
          key={richKey}
          ref={richRef}
          initialMarkdown={draft.content}
          onChange={(content) => setDraft((d) => ({ ...d, content }))}
        />
      ) : (
        <>
          <p className="text-[11px] text-slate-400" role="note">
            This note uses formatting the visual editor cannot keep exactly, so it is open as text.
          </p>
          <textarea
            value={draft.content}
            onChange={(e) => setDraft({ ...draft, content: e.target.value })}
            placeholder="Write something…  Use - [ ] for a checklist item and #label to tag it"
            aria-label="Note content"
            rows={12}
            className="glass-input w-full text-base sm:text-sm font-mono resize-y"
          />
        </>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {!rich && (
          <button
            type="button"
            className="glass-button min-h-11 px-3 py-1.5 text-xs"
            onClick={() =>
              setDraft({ ...draft, content: `${draft.content}${draft.content.endsWith('\n') || !draft.content ? '' : '\n'}- [ ] ` })
            }
          >
            <CheckSquare size={13} /> Checklist item
          </button>
        )}
        <label className="text-xs text-slate-400 flex items-center gap-2">
          Folder
          <input
            value={draft.folder}
            onChange={(e) => setDraft({ ...draft, folder: e.target.value })}
            aria-label="Note folder"
            placeholder="(none)"
            className="glass-input min-h-11 px-2 py-1 text-base sm:text-xs w-32"
          />
        </label>
        <div className="flex items-center gap-2">
          <input
            value={labelDraft}
            onChange={(e) => setLabelDraft(e.target.value)}
            placeholder="Label"
            aria-label="Note label"
            className="glass-input min-h-11 px-2 py-1 text-base sm:text-xs w-28"
          />
          <button
            type="button"
            className="glass-button min-h-11 px-3 py-1.5 text-xs"
            onClick={() => {
              const tag = labelDraft.trim().replace(/^#+/, '').replace(/\s+/g, '-');
              if (!tag) return;
              if (rich) {
                richRef.current?.appendLabel(tag);
                setLabelDraft('');
                return;
              }
              // Nextcloud Notes reads #labels straight out of the markdown.
              const needsBreak = draft.content.length > 0 && !draft.content.endsWith('\n');
              setDraft({ ...draft, content: `${draft.content}${needsBreak ? '\n' : ''}#${tag} ` });
              setLabelDraft('');
            }}
          >
            Add label
          </button>
        </div>
        <button
          type="button"
          className={`glass-button min-h-11 px-3 py-1.5 text-xs ${draft.pinned ? 'text-amber-300' : ''}`}
          onClick={() => setDraft({ ...draft, pinned: !draft.pinned })}
          aria-pressed={draft.pinned}
        >
          <Pin size={13} /> {draft.pinned ? 'Pinned' : 'Pin'}
        </button>
        <div className="flex items-center">
          {NOTE_COLORS.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-label={`Colour ${c.label}`}
              aria-pressed={draft.color === c.id}
              className="flex h-11 w-11 items-center justify-center"
              onClick={() => setDraft({ ...draft, color: c.id })}
            >
              <span
                className={`h-6 w-6 rounded-full border-2 ${draft.color === c.id ? 'border-white' : 'border-transparent'}`}
                style={{ background: c.swatch }}
              />
            </button>
          ))}
        </div>
      </div>
      {showHistory && initial.path && (
        <NoteHistory
          path={initial.path}
          asUser={asUser}
          dirty={dirty}
          onRestored={() => {
            // The stored note is now the restored text: drop the edit and show it.
            forgetDraft();
            onUseStored();
          }}
        />
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-white/5">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="glass-button min-h-11 px-4 py-2 text-sm"
            onClick={() => void save()}
            title="Save (Ctrl+S)"
            disabled={saveState.kind === 'saving'}
          >
            {saveState.kind === 'saving' ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Save
          </button>
          <button
            type="button"
            className="glass-button min-h-11 px-3 py-2 text-xs"
            onClick={() => void requestClose()}
            title="Save and close (Ctrl+Enter or Esc)"
          >
            <X size={13} /> Close
          </button>
          <span className="text-[11px] text-slate-500" aria-live="polite" data-testid="note-save-status">
            {status}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {initial.path && (
            <button
              type="button"
              className="glass-button min-h-11 px-3 py-2 text-xs"
              aria-pressed={showHistory}
              onClick={() => setShowHistory(!showHistory)}
            >
              <History size={13} /> History
            </button>
          )}
          {onTrash && (
            <button type="button" className="glass-button min-h-11 px-3 py-2 text-xs text-rose-300" onClick={onTrash}>
              <Trash2 size={13} /> Move to trash
            </button>
          )}
        </div>
      </div>
    </div>
  );
});
