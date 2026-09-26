import { useCallback, useMemo, useState } from 'react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
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
import {
  NOTE_COLORS,
  NOTE_COLOR_BG,
  type NoteColor,
  forgetNoteMeta,
  getNoteMeta,
  noteKey,
  notePreview,
  parseChecklist,
  setNoteMeta,
  sortNotes,
} from '../lib/notesMeta';

interface NoteSummary {
  title: string;
  path: string;
  size: number;
  modified: string;
}

interface DraftState {
  title: string;
  content: string;
  path?: string;
  category: string;
  color: NoteColor;
  pinned: boolean;
}

const STORAGE = 'nextcloud';
const EMPTY_DRAFT: DraftState = {
  title: '',
  content: '',
  category: 'Notes',
  color: 'default',
  pinned: false,
};

function categoryFromPath(path: string | undefined, fallback = 'Notes'): string {
  if (!path) return fallback;
  const parts = path.split('/').filter(Boolean);
  return parts.length > 1 ? parts[0] : fallback;
}

/**
 * Notes — a Keep-style surface over the Nextcloud markdown notes the rest of
 * Jarvis already uses. Pins and colours are view preferences kept on-device;
 * note content stays plain markdown in Nextcloud.
 */
export default function Notes() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [saving, setSaving] = useState(false);
  const [metaVersion, setMetaVersion] = useState(0);

  const keyOf = useCallback((note: { title: string; path: string }) => noteKey(note.title, note.path), []);

  const { data: listRes, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['notes-list'],
    queryFn: () => api.listNotes({ storage: STORAGE }),
  });

  const notes = useMemo<NoteSummary[]>(
    () => ((listRes?.status === 'SUCCESS' && (listRes.detail?.notes as NoteSummary[])) || []),
    [listRes]
  );

  // Previews need bodies: fetch a bounded batch so a large notebook does not
  // turn one page load into hundreds of WebDAV reads.
  const previewBatch = useMemo(() => notes.slice(0, 24), [notes]);
  const contentQueries = useQueries({
    queries: previewBatch.map((n) => ({
      queryKey: ['note-content', n.path || n.title],
      queryFn: () => api.readNote(n.title, STORAGE, n.path),
      staleTime: 30_000,
    })),
  });

  const contents = useMemo(() => {
    const map: Record<string, string> = {};
    contentQueries.forEach((query, index) => {
      const note = previewBatch[index];
      if (!note) return;
      if (query.data?.status === 'SUCCESS') {
        map[keyOf(note)] = query.data.message ?? '';
      }
    });
    return map;
  }, [contentQueries, previewBatch, keyOf]);

  const loadError = listRes != null && listRes.status !== 'SUCCESS';

  const refresh = useCallback(async () => {
    await Promise.all([
      refetch(),
      queryClient.invalidateQueries({ queryKey: ['note-content'] }),
    ]);
  }, [refetch, queryClient]);

  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    const filtered = query
      ? notes.filter((n) => {
          const key = keyOf(n);
          const body = contents[key]?.toLowerCase() ?? '';
          return n.title.toLowerCase().includes(query) || body.includes(query);
        })
      : notes;
    // metaVersion forces a re-sort after a pin toggle
    void metaVersion;
    return sortNotes(filtered);
  }, [notes, contents, search, keyOf, metaVersion]);

  const openEditor = async (note?: NoteSummary) => {
    if (!note) {
      setDraft({ ...EMPTY_DRAFT });
      return;
    }
    const key = keyOf(note);
    const meta = getNoteMeta(key);
    let content = contents[key];
    if (content === undefined) {
      try {
        const res = await api.readNote(note.title, STORAGE, note.path);
        content = res.status === 'SUCCESS' ? (res.message ?? '') : '';
        queryClient.setQueryData(['note-content', note.path || note.title], res);
      } catch {
        content = '';
        toast.error('Could not read that note');
      }
    }
    setDraft({
      title: note.title,
      content,
      path: note.path,
      category: categoryFromPath(note.path),
      color: (meta.color ?? 'default') as NoteColor,
      pinned: Boolean(meta.pinned),
    });
  };

  const saveDraft = async () => {
    if (!draft) return;
    const title = draft.title.trim() || draft.content.split('\n')[0].slice(0, 60).trim() || 'Untitled note';
    const category = draft.category.trim() || 'Notes';
    setSaving(true);
    try {
      // write (replace), never append — append is quick-capture only.
      const res = await api.writeNote({
        title,
        content: draft.content,
        category,
        path: draft.path,
        storage: STORAGE,
      });
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Save failed');
        return;
      }
      const key = noteKey(title, draft.path);
      setNoteMeta(key, { pinned: draft.pinned, color: draft.color });
      setDraft(null);
      toast.success('Note saved');
      await refresh();
    } catch {
      toast.error('Save failed');
    } finally {
      setSaving(false);
    }
  };

  const removeNote = async (note: NoteSummary) => {
    try {
      const res = await api.deleteNote(note.title, STORAGE, note.path);
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Delete failed');
        return;
      }
      forgetNoteMeta(keyOf(note));
      setDraft(null);
      toast.success('Note deleted');
      await refresh();
    } catch {
      toast.error('Delete failed');
    }
  };

  const togglePin = (note: NoteSummary) => {
    const key = keyOf(note);
    const meta = getNoteMeta(key);
    setNoteMeta(key, { pinned: !meta.pinned });
    setMetaVersion((v) => v + 1);
  };

  const toggleChecklistItem = async (note: NoteSummary, item: string) => {
    try {
      const res = await api.checkOffNote({ title: note.title, item, path: note.path, storage: STORAGE });
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'Could not update the checklist');
        return;
      }
      const fresh = await api.readNote(note.title, STORAGE, note.path);
      if (fresh.status === 'SUCCESS') {
        queryClient.setQueryData(['note-content', note.path || note.title], fresh);
      }
    } catch {
      toast.error('Could not update the checklist');
    }
  };

  const syncRag = async () => {
    try {
      const res = await api.syncNotesRag({ storage: STORAGE });
      if (res.status !== 'SUCCESS') {
        toast.error(res.message || 'RAG sync failed');
        return;
      }
      toast.success('Notes synced to RAG');
    } catch {
      toast.error('RAG sync failed');
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
        <div className="flex items-center gap-2">
          <div className="relative flex-1 sm:flex-none">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search notes"
              aria-label="Search notes"
              className="glass-input pl-8 pr-3 py-2 text-sm w-full sm:w-56"
            />
          </div>
          <button type="button" onClick={() => void syncRag()} className="glass-button px-3 py-2 text-xs" title="Index notes into Jarvis memory">
            <Sparkles size={14} /> RAG
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            className="glass-button px-3 py-2 text-xs"
            title="Refresh"
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
        className="w-full glass-panel rounded-2xl border border-white/10 px-4 py-3 text-left text-sm text-slate-400 hover:border-purple-500/40 transition-colors flex items-center gap-3"
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
                ? (listRes?.message || 'Could not load notes — is Nextcloud connected?')
                : 'No notes yet — take one above.'}
          </p>
        </div>
      ) : (
        <div className="columns-1 sm:columns-2 lg:columns-3 gap-4 [column-fill:_balance]">
          {visible.map((note) => {
            const key = keyOf(note);
            const body = contents[key];
            const meta = getNoteMeta(key);
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
                      className={`p-1 rounded hover:bg-white/10 ${meta.pinned ? 'text-amber-300' : 'text-slate-500 opacity-0 group-hover:opacity-100'}`}
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
                  <ul className="mt-2 space-y-1">
                    {checklist.slice(0, 6).map((item) => (
                      <li key={item.text} className="flex items-start gap-2 text-xs text-slate-300">
                        <button
                          type="button"
                          aria-label={`${item.checked ? 'Uncheck' : 'Check'} ${item.text}`}
                          className={`mt-0.5 shrink-0 ${item.checked ? 'text-emerald-400' : 'text-slate-500'}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            void toggleChecklistItem(note, item.text);
                          }}
                        >
                          {item.checked ? <CheckSquare size={13} /> : <Square size={13} />}
                        </button>
                        <span className={item.checked ? 'line-through text-slate-500' : ''}>{item.text}</span>
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
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
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
      {draft && (
        <Modal isOpen onClose={() => setDraft(null)} title={draft.path ? 'Edit note' : 'New note'} size="lg">
          <div className="space-y-3">
            <input
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              placeholder="Title"
              aria-label="Note title"
              className="glass-input w-full text-base font-semibold"
            />
            <textarea
              value={draft.content}
              onChange={(e) => setDraft({ ...draft, content: e.target.value })}
              placeholder="Write something…  Use - [ ] for a checklist item"
              aria-label="Note content"
              rows={12}
              className="glass-input w-full text-sm font-mono resize-y"
            />
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="glass-button px-3 py-1.5 text-xs"
                onClick={() =>
                  setDraft({ ...draft, content: `${draft.content}${draft.content.endsWith('\n') || !draft.content ? '' : '\n'}- [ ] ` })
                }
              >
                <CheckSquare size={13} /> Checklist item
              </button>
              <label className="text-xs text-slate-400 flex items-center gap-2">
                Category
                <input
                  value={draft.category}
                  onChange={(e) => setDraft({ ...draft, category: e.target.value })}
                  aria-label="Note category"
                  className="glass-input px-2 py-1 text-xs w-32"
                />
              </label>
              <button
                type="button"
                className={`glass-button px-3 py-1.5 text-xs ${draft.pinned ? 'text-amber-300' : ''}`}
                onClick={() => setDraft({ ...draft, pinned: !draft.pinned })}
                aria-pressed={draft.pinned}
              >
                <Pin size={13} /> {draft.pinned ? 'Pinned' : 'Pin'}
              </button>
              <div className="flex items-center gap-1">
                {NOTE_COLORS.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    aria-label={`Colour ${c.label}`}
                    className={`h-4 w-4 rounded-full border ${draft.color === c.id ? 'border-white' : 'border-transparent'}`}
                    style={{ background: c.swatch }}
                    onClick={() => setDraft({ ...draft, color: c.id })}
                  />
                ))}
              </div>
            </div>
            <div className="flex items-center justify-between pt-2 border-t border-white/5">
              <div className="flex items-center gap-2">
                <button type="button" className="glass-button px-4 py-2 text-sm" onClick={() => void saveDraft()} disabled={saving}>
                  {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Save
                </button>
                <button type="button" className="glass-button px-3 py-2 text-xs" onClick={() => setDraft(null)}>
                  <X size={13} /> Cancel
                </button>
              </div>
              {draft.path && (
                <button
                  type="button"
                  className="glass-button px-3 py-2 text-xs text-rose-300"
                  onClick={() => {
                    const note = notes.find((n) => keyOf(n) === noteKey(draft.title, draft.path));
                    if (note) void removeNote(note);
                  }}
                >
                  <Trash2 size={13} /> Delete
                </button>
              )}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
