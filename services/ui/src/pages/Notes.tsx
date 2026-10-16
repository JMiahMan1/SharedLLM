import { useCallback, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import SendAsSelector from '../components/chat/SendAsSelector';
import { useSendAsPref } from '../components/chat/sendAsPref';
import {
  Check,
  CheckSquare,
  Loader2,
  Palette,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
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
import { errorText } from '../lib/errorText';
import {
  NOTE_COLORS,
  NOTE_COLOR_BG,
  type NoteColor,
  forgetNoteMeta,
  noteBody,
  noteKey,
  notePreview,
  parseChecklist,
  readNoteMetaMap,
  setNoteMeta,
  sortNotes,
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
}

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
 * Notes: a Keep-style surface over the Nextcloud markdown notes the rest of
 * Jarvis already uses. Pins and colours are view preferences kept on-device;
 * note content stays plain markdown in Nextcloud.
 */
export default function Notes() {
  const [search, setSearch] = useState('');
  /** The open editor: `draft` is null while the note is being read. */
  const [editing, setEditing] = useState<{ draft: DraftState | null; title: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [metaVersion, setMetaVersion] = useState(0);

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const isAdmin = Boolean(me?.is_admin);
  const [sendAs, setSendAs] = useSendAsPref(isAdmin, 'notes');
  const asUser = isAdmin && sendAs === 'admin' ? ('admin' as const) : undefined;

  const keyOf = useCallback((note: { title: string; path: string }) => noteKey(note.title, note.path), []);

  // One request: the list carries the newest notes' text for the previews.
  // It used to be one read per card, and 24 reads at once filled the server's
  // connections to Nextcloud, so the editor's own read then timed out.
  const { data: listRes, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['notes-list', asUser ?? 'me'],
    queryFn: () => api.listNotes({ storage: STORAGE, as_user: asUser, previews: true }),
  });

  const notes = useMemo<NoteSummary[]>(
    () => ((listRes?.status === 'SUCCESS' && (listRes.detail?.notes as NoteSummary[])) || []),
    [listRes]
  );

  const contents = useMemo(() => {
    const map: Record<string, string> = {};
    for (const note of notes) {
      if (typeof note.content === 'string') map[keyOf(note)] = note.content;
    }
    return map;
  }, [notes, keyOf]);

  // Pins and colours, read from storage once per change rather than per card.
  // Pin and colour edits bump metaVersion; a delete or rename changes notes.
  const metaMap = useMemo(() => {
    void metaVersion;
    void notes;
    return readNoteMetaMap();
  }, [metaVersion, notes]);

  const loadError = listRes != null && listRes.status !== 'SUCCESS';

  const refresh = useCallback(async () => {
    await refetch();
  }, [refetch]);

  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    const filtered = query
      ? notes.filter((n) => {
          const key = keyOf(n);
          const body = contents[key]?.toLowerCase() ?? '';
          return n.title.toLowerCase().includes(query) || body.includes(query);
        })
      : notes;
    return sortNotes(filtered, metaMap);
  }, [notes, contents, search, keyOf, metaMap]);

  const closeEditor = useCallback(() => setEditing(null), []);

  const openEditor = async (note?: NoteSummary) => {
    if (!note) {
      setEditing({ draft: { ...EMPTY_DRAFT }, title: '' });
      return;
    }
    // The sheet opens at once and fills when the read lands. Always the
    // stored text, never the preview: saving a stale copy would overwrite a
    // change made elsewhere since the list loaded.
    setEditing({ draft: null, title: note.title });
    let content: string;
    try {
      const res = await api.readNote(note.title, STORAGE, note.path, asUser);
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not read that note');
      content = res.message ?? '';
    } catch (err) {
      // An editor opened empty would save over the note, so it stays closed.
      setEditing(null);
      toast.error(errorText(err, 'Could not read that note'));
      return;
    }
    const meta = metaMap[keyOf(note)] ?? {};
    const folder = folderFromPath(note.path);
    setEditing((current) =>
      current && current.title === note.title && current.draft === null
        ? {
            title: note.title,
            draft: {
              title: note.title,
              // The editor shows the note's own text; the server adds the header.
              content: noteBody(content),
              path: note.path,
              original: { title: note.title, category: categoryFor(folder) },
              folder,
              color: (meta.color ?? 'default') as NoteColor,
              pinned: Boolean(meta.pinned),
            },
          }
        : current,
    );
  };

  const saveDraft = async (draft: DraftState) => {
    const title = draft.title.trim() || draft.content.split('\n')[0].slice(0, 60).trim() || 'Untitled note';
    // A different folder is a move; saving outside Notes/ would hide the note.
    const category = categoryFor(draft.folder);
    setSaving(true);
    try {
      // write (replace), never append; append is quick-capture only.
      const saved = await saveNote({
        title,
        content: draft.content,
        category,
        previous: draft.path && draft.original ? { ...draft.original, path: draft.path } : undefined,
        asUser,
      });
      const res = saved.response;
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Save failed');
        return;
      }
      const savedPath = saved.path ?? draft.path;
      if (savedPath !== draft.path && draft.path) forgetNoteMeta(noteKey(title, draft.path));
      if (saved.leftOldCopy) toast.error('Saved under the new name, but the old copy is still there');
      setNoteMeta(noteKey(title, savedPath), { pinned: draft.pinned, color: draft.color });
      setMetaVersion((v) => v + 1);
      setEditing(null);
      toast.success('Note saved');
      await refresh();
    } catch (err) {
      toast.error(errorText(err, 'Save failed'));
    } finally {
      setSaving(false);
    }
  };

  const removeNote = async (note: NoteSummary) => {
    if (!window.confirm(`Delete "${note.title}"?`)) return;
    try {
      const res = await api.deleteNote(note.title, STORAGE, note.path, asUser);
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Delete failed');
        return;
      }
      forgetNoteMeta(keyOf(note));
      setEditing(null);
      toast.success('Note deleted');
      await refresh();
    } catch {
      toast.error('Delete failed');
    }
  };

  const togglePin = (note: NoteSummary) => {
    const key = keyOf(note);
    setNoteMeta(key, { pinned: !metaMap[key]?.pinned });
    setMetaVersion((v) => v + 1);
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

  return (
    <div className="space-y-5 max-w-6xl mx-auto pb-24">
      {/* Header */}
      <div className="glass-panel p-5 rounded-2xl border border-white/10 flex flex-col sm:flex-row sm:items-center gap-4 justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Notes</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            {notes.length} {notes.length === 1 ? 'note' : 'notes'} · Nextcloud markdown · synced to Jarvis memory on demand
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isAdmin && (
            <SendAsSelector value={sendAs} onChange={setSendAs} />
          )}
          <div className="relative min-w-40 flex-1 sm:flex-none">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search notes"
              aria-label="Search notes"
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
      <button
        type="button"
        onClick={() => void openEditor()}
        data-testid="quick-capture"
        className="w-full min-h-12 glass-panel rounded-2xl border border-white/10 px-4 py-3 text-left text-sm text-slate-400 hover:border-purple-500/40 transition-colors flex items-center gap-3"
      >
        <Plus size={16} className="text-purple-400" />
        Take a note…
      </button>

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
                : 'No notes yet. Take one above.'}
          </p>
        </div>
      ) : (
        <div className="columns-1 sm:columns-2 lg:columns-3 gap-4 [column-fill:_balance]">
          {visible.map((note) => {
            const key = keyOf(note);
            const body = contents[key];
            const meta = metaMap[key] ?? {};
            const color = (meta.color ?? 'default') as NoteColor;
            const checklist = body ? parseChecklist(body) : [];
            return (
              <article
                key={key}
                data-testid="note-card"
                className="mb-4 break-inside-avoid rounded-2xl border border-white/10 p-4 hover:border-purple-500/40 transition-colors cursor-pointer relative group"
                style={{ background: NOTE_COLOR_BG[color] }}
                onClick={() => void openEditor(note)}
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-semibold text-slate-100 text-sm leading-snug">{note.title}</h3>
                  <div className="flex items-center gap-1 shrink-0">
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
                  </div>
                </div>

                {checklist.length > 0 ? (
                  <ul className="mt-1 space-y-0.5">
                    {checklist.slice(0, 6).map((item) => (
                      <li key={item.text}>
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
                  <p className="mt-2 text-xs text-slate-400 whitespace-pre-wrap">
                    {body !== undefined ? notePreview(body) : `${Math.max(1, Math.round(note.size / 1024))} KB`}
                  </p>
                )}

                <div className="mt-3 flex items-center justify-between text-[10px] text-slate-500">
                  <span>{new Date(note.modified).toLocaleDateString()}</span>
                  {/* Quick colours for mouse users; on a phone, colour is in the editor. */}
                  <div className="hidden items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 [@media(hover:hover)]:flex">
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
                          setNoteMeta(key, { color: c.id });
                          setMetaVersion((v) => v + 1);
                        }}
                      />
                    ))}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {/* Editor */}
      {editing && (
        <Modal isOpen onClose={closeEditor} title={editing.title || editing.draft?.path ? 'Edit note' : 'New note'} size="lg">
          {editing.draft ? (
            <NoteEditor
              initial={editing.draft}
              saving={saving}
              onSave={(draft) => void saveDraft(draft)}
              onCancel={closeEditor}
              onDelete={
                editing.draft.path
                  ? () => {
                      // By path: the title in the editor may already be edited.
                      const note = notes.find((n) => n.path === editing.draft?.path);
                      if (note) void removeNote(note);
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

/**
 * The note editor. Its draft lives here, so typing re-renders this sheet
 * only, not the page and every card behind it.
 */
function NoteEditor({
  initial,
  saving,
  onSave,
  onCancel,
  onDelete,
}: {
  initial: DraftState;
  saving: boolean;
  onSave: (draft: DraftState) => void;
  onCancel: () => void;
  onDelete?: () => void;
}) {
  const [draft, setDraft] = useState<DraftState>(initial);
  return (
    <div className="space-y-3">
      <input
        value={draft.title}
        onChange={(e) => setDraft({ ...draft, title: e.target.value })}
        placeholder="Title"
        aria-label="Note title"
        className="glass-input min-h-11 w-full text-base font-semibold"
      />
      <textarea
        value={draft.content}
        onChange={(e) => setDraft({ ...draft, content: e.target.value })}
        placeholder="Write something…  Use - [ ] for a checklist item"
        aria-label="Note content"
        rows={12}
        className="glass-input w-full text-base sm:text-sm font-mono resize-y"
      />
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="glass-button min-h-11 px-3 py-1.5 text-xs"
          onClick={() =>
            setDraft({ ...draft, content: `${draft.content}${draft.content.endsWith('\n') || !draft.content ? '' : '\n'}- [ ] ` })
          }
        >
          <CheckSquare size={13} /> Checklist item
        </button>
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
      <div className="flex items-center justify-between pt-2 border-t border-white/5">
        <div className="flex items-center gap-2">
          <button type="button" className="glass-button min-h-11 px-4 py-2 text-sm" onClick={() => onSave(draft)} disabled={saving}>
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Save
          </button>
          <button type="button" className="glass-button min-h-11 px-3 py-2 text-xs" onClick={onCancel}>
            <X size={13} /> Cancel
          </button>
        </div>
        {onDelete && (
          <button type="button" className="glass-button min-h-11 px-3 py-2 text-xs text-rose-300" onClick={onDelete}>
            <Trash2 size={13} /> Delete
          </button>
        )}
      </div>
    </div>
  );
}
