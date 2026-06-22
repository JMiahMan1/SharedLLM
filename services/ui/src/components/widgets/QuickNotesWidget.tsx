import { useState } from 'react';
import { FileText, Plus, Trash2 } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import { api } from '../../services/api';
import toast from 'react-hot-toast';

interface NoteItem {
  id: string;
  title: string;
  content: string;
  category: string;
}

const QuickNotesWidget = ({ userSettings, onTogglePin, settingsButton }: IWidgetProps) => {
  const [notes, setNotes] = useState<NoteItem[]>([]);
  const [newNote, setNewNote] = useState('');
  const [newTitle, setNewTitle] = useState('');

  const addNote = async () => {
    if (!newTitle.trim()) return;
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
    }
  };

  const deleteNote = async (id: string) => {
    const note = notes.find((n) => n.id === id);
    if (!note) return;
    try {
      await api.deleteNote(note.title);
      setNotes((prev) => prev.filter((n) => n.id !== id));
      toast.success('Note deleted');
    } catch {
      toast.error('Failed to delete note');
    }
  };

  return (
    <div className="glass-card h-full p-5 relative">
      <div className="absolute top-3 right-3 flex items-center gap-2 z-10">
        <button
          onClick={onTogglePin}
          className="text-slate-500 hover:text-purple-400 transition-colors"
          title={userSettings.is_pinned ? 'Unpin widget' : 'Pin widget'}
        >
          <FileText size={16} className={userSettings.is_pinned ? 'text-purple-400' : ''} />
        </button>
        {settingsButton}
      </div>

      <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
        <FileText size={18} className="text-blue-400" />
        Quick Notes
      </h3>

      <div className="space-y-2 mb-4">
        {notes.map((note) => (
          <div key={note.id} className="glass-card p-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-semibold text-white">{note.title}</span>
              <button
                onClick={() => deleteNote(note.id)}
                className="text-slate-500 hover:text-red-400 transition-colors"
              >
                <Trash2 size={14} />
              </button>
            </div>
            <p className="text-xs text-slate-400 line-clamp-3">{note.content}</p>
            <p className="text-[10px] text-slate-600 mt-1">{note.category}</p>
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <input
          type="text"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          className="glass-input w-full px-3 py-2 text-sm"
          placeholder="Note title..."
        />
        <textarea
          value={newNote}
          onChange={(e) => setNewNote(e.target.value)}
          className="glass-input w-full px-3 py-2 text-sm resize-none"
          placeholder="Note content..."
          rows={3}
        />
        <button
          onClick={addNote}
          className="glass-button w-full px-4 py-2 text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center justify-center gap-2"
        >
          <Plus size={16} /> Save Note
        </button>
      </div>
    </div>
  );
};

export default QuickNotesWidget;
