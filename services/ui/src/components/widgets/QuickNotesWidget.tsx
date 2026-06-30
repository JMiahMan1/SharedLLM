import { useState, useEffect, useCallback } from 'react';
import { FileText, Plus, Trash2, Loader2 } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import { api } from '../../services/api';
import { WidgetCard } from './WidgetCard';
import toast from 'react-hot-toast';

interface NoteItem {
  id: string;
  title: string;
  content: string;
  category: string;
}

const QuickNotesWidget = ({ settingsButton }: IWidgetProps) => {
  const [notes, setNotes] = useState<NoteItem[]>([]);
  const [newNote, setNewNote] = useState('');
  const [newTitle, setNewTitle] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const loadNotes = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.listNotes();
      // The notes API returns an ExecutionResponse with message containing the note list
      // Parse titles from the message field if notes array not provided
      let loaded: NoteItem[] = [];
      if (typeof data === 'object' && data !== null) {
        const resp = data as { status?: string; message?: string; notes?: Array<{ id?: string; title?: string; content?: string }> };
        if (Array.isArray(resp.notes)) {
          loaded = resp.notes.map((n, idx) => ({
            id: n.id ?? `note-${idx}`,
            title: n.title ?? 'Untitled',
            content: n.content ?? '',
            category: 'Quick',
          }));
        } else if (typeof resp.message === 'string' && resp.message) {
          // Parse newline-separated titles
          loaded = resp.message
            .split('\n')
            .map((line) => line.replace(/^[-*•]\s*/, '').trim())
            .filter(Boolean)
            .map((title, idx) => ({ id: `note-${idx}`, title, content: '', category: 'Quick' }));
        }
      }
      setNotes(loaded);
    } catch {
      // Notes service may be unavailable — show graceful empty state, not error
      setNotes([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional fetch-on-mount
    loadNotes();
  }, [loadNotes]);

  const addNote = async () => {
    if (!newTitle.trim()) {
      toast.error('Please enter a title');
      return;
    }
    setSaving(true);
    try {
      await api.createNote({ title: newTitle, content: newNote });
      setNotes((prev) => [
        { id: Date.now().toString(), title: newTitle, content: newNote, category: 'Quick' },
        ...prev,
      ]);
      setNewTitle('');
      setNewNote('');
      toast.success('Note saved');
    } catch {
      toast.error('Failed to save note');
    } finally {
      setSaving(false);
    }
  };

  const deleteNote = async (id: string) => {
    const note = notes.find((n) => n.id === id);
    if (!note) return;
    // Optimistic removal
    setNotes((prev) => prev.filter((n) => n.id !== id));
    try {
      await api.deleteNote(note.title);
      toast.success('Note deleted');
    } catch {
      // Re-add on failure
      setNotes((prev) => [note, ...prev]);
      toast.error('Failed to delete note');
    }
  };

  return (
    <WidgetCard
      title="Quick Notes"
      icon="📝"
      isLoading={isLoading}
      error={error}
      onRetry={loadNotes}
      settingsButton={settingsButton}
      actions={
        notes.length > 0 ? (
          <span className="text-[10px] text-slate-500 font-mono">{notes.length} note{notes.length !== 1 ? 's' : ''}</span>
        ) : undefined
      }
    >
      <div className="flex flex-col h-full gap-3">
        {/* Notes list */}
        <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-0.5">
          {notes.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center py-6">
              <FileText size={28} className="text-slate-700 mb-2" />
              <p className="text-xs text-slate-500">No notes yet</p>
            </div>
          ) : (
            notes.map((note) => (
              <div
                key={note.id}
                className="glass-card p-3 group"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-white truncate">{note.title}</p>
                    {note.content && (
                      <p className="text-xs text-slate-400 mt-0.5 line-clamp-2">{note.content}</p>
                    )}
                  </div>
                  <button
                    onClick={() => deleteNote(note.id)}
                    className="shrink-0 p-1 text-slate-600 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                    aria-label="Delete note"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Add note form */}
        <div className="shrink-0 space-y-2 pt-2 border-t border-white/5">
          <input
            type="text"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addNote(); }}
            className="glass-input w-full px-3 py-2 text-sm"
            placeholder="Note title..."
          />
          <textarea
            value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
            className="glass-input w-full px-3 py-2 text-sm resize-none"
            placeholder="Note content..."
            rows={2}
          />
          <button
            onClick={addNote}
            disabled={saving || !newTitle.trim()}
            className="glass-button w-full px-4 py-2 text-sm text-blue-400 hover:text-blue-300"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            {saving ? 'Saving…' : 'Save Note'}
          </button>
        </div>
      </div>
    </WidgetCard>
  );
};

export default QuickNotesWidget;
