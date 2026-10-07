import { useState } from 'react';
import {
  Bookmark,
  BookmarkCheck,
  BookMarked,
  ExternalLink,
  Highlighter,
  Link2,
  NotebookPen,
  ScrollText,
  Share2,
  Sparkles,
  X,
} from 'lucide-react';
import BibleStudyNotes from './BibleStudyNotes';
import JarvisStudyThread from './JarvisStudyThread';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleMark, BibleVerse } from '../../types/api';

interface VerseActionSheetProps {
  verse: BibleVerse;
  marks: BibleMark[];
  /** Translation the verse came from; the study panel reports what it carries. */
  version: string;
  /** Which study Bible explains it; more than one can be installed per translation. */
  edition?: string;
  crossVersion?: boolean;
  onEditionChange?: (code: string) => void;
  onCrossVersionChange?: (on: boolean) => void;
  onClose: () => void;
  onToggleMark: (kind: 'highlight' | 'bookmark', color: string) => void;
  /** Called after a note and its pointer are both stored, to refresh the marks. */
  onNoteSaved?: () => void;
  onShared?: (target: 'clipboard' | 'system') => void;
}

const COLORS: Array<{ id: string; label: string; className: string }> = [
  { id: 'yellow', label: 'Yellow', className: 'bg-amber-400' },
  { id: 'green', label: 'Green', className: 'bg-emerald-400' },
  { id: 'blue', label: 'Blue', className: 'bg-sky-400' },
  { id: 'pink', label: 'Pink', className: 'bg-pink-400' },
  { id: 'purple', label: 'Purple', className: 'bg-purple-400' },
];

const ACTION_CLASS =
  'w-full flex items-center gap-3 px-3 py-3 rounded-xl text-left text-sm min-h-11 pointer-coarse:min-h-11 hover:bg-white/5 transition-colors';

/**
 * What you can do to a verse, as a bottom sheet on a phone and a centred dialog
 * on a desktop.
 *
 * Note bodies go to Notes (Nextcloud) rather than into a second store: the bible
 * service only ever stores an index row pointing at the note, so there is one
 * copy of what was written and it is already synced and searchable.
 *
 * Study notes are the one thing here that is not stored on the verse: they live
 * in their own table and are fetched on demand, so the reader can stay a plain
 * reader.
 */
export default function VerseActionSheet({
  verse,
  marks,
  version,
  edition,
  crossVersion = false,
  onEditionChange,
  onCrossVersionChange,
  onClose,
  onToggleMark,
  onNoteSaved,
  onShared,
}: VerseActionSheetProps) {
  const [color, setColor] = useState('yellow');
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [studyOpen, setStudyOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [noteBody, setNoteBody] = useState<string | null>(null);
  const { trigger } = useHaptics();

  const highlight = marks.find((m) => m.kind === 'highlight');
  const bookmark = marks.find((m) => m.kind === 'bookmark');
  const noteMark = marks.find(
    (m) =>
      m.kind === 'note' &&
      m.note_path &&
      m.verse_start <= verse.verse &&
      (m.verse_end ?? m.verse_start) >= verse.verse,
  );

  const run = async (label: string, work: () => Promise<string | void>) => {
    setBusy(true);
    try {
      const message = await work();
      setStatus(message ?? label);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : `${label} failed`);
    } finally {
      setBusy(false);
    }
  };

  const saveNote = () =>
    run('Saved to Notes', async () => {
      const saved = await api.createNote({
        title: verse.reference,
        category: 'Bible',
        content: `${verse.reference}\n\n${verse.text}`,
      });
      await api.recordBibleEvent('note_created', verse.reference);
      const path = typeof saved.detail?.path === 'string' ? saved.detail.path : '';
      if (!path) {
        // The note was written but we were not told where, so the verse cannot
        // be marked as having one. Say that rather than claim a pointer we do
        // not have.
        return 'Saved to Notes, but the app was not told where. Reopen Notes to find it.';
      }
      try {
        await api.putBibleMark({
          ref: verse.reference,
          kind: 'note',
          version_code: version,
          note_path: path,
          note_preview: `${verse.reference} — ${verse.text.slice(0, 120)}`,
        });
      } catch {
        // The note itself is safe; only the pointer failed. Saying so is better
        // than marking the verse and letting the reader find an empty note, or
        // than hiding it and letting them write the same note twice.
        onNoteSaved?.();
        return 'Saved to Notes, but this verse could not be marked as having one.';
      }
      onNoteSaved?.();
      return 'Saved to Notes';
    });

  const openNote = () =>
    run('Note opened', async () => {
      const stored = noteMark?.note_path;
      if (!stored) return 'No note for this verse yet.';
      const read = await api.readNote(verse.reference, 'nextcloud', stored);
      if (read.status !== 'SUCCESS') return read.message || 'The note could not be read.';
      setNoteBody(read.message);
      return 'Note opened';
    });

  const copy = () =>
    run('Copied', async () => {
      await navigator.clipboard.writeText(`${verse.reference}\n${verse.text}`);
      onShared?.('clipboard');
      return 'Copied to clipboard';
    });

  const share = () =>
    run('Shared', async () => {
      const payload = { title: verse.reference, text: `${verse.reference}\n${verse.text}` };
      if (typeof navigator.share !== 'function') {
        await navigator.clipboard.writeText(payload.text);
        onShared?.('clipboard');
        return 'Sharing is not available here, so it was copied instead';
      }
      await navigator.share(payload);
      onShared?.('system');
      return 'Shared';
    });

  const openBlb = (tool?: string) =>
    run('Opened Blue Letter Bible', async () => {
      const link = await api.getBlbLink(verse.reference, tool);
      window.open(link.url, '_blank', 'noopener,noreferrer');
      await api.recordBibleEvent('blb_link_tap', verse.reference);
      return undefined;
    });

  const openStudy = () =>
    run('', async () => {
      setStudyOpen(true);
      await api.recordBibleEvent('verse_tapped', verse.reference);
      return undefined;
    });

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label={`Actions for ${verse.reference}`}>
      <button
        type="button"
        aria-label="Close verse actions"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        data-testid="bible-sheet-scrim"
      />
      <div
        className="relative w-full sm:max-w-md bg-slate-950/97 backdrop-blur-xl border border-white/10 border-b-0 sm:border-b sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] overflow-y-auto"
        data-testid="bible-verse-sheet"
      >
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold text-amber-300" data-testid="bible-sheet-ref">
              {verse.reference}
            </p>
            <p className="text-xs text-slate-400 mt-1 line-clamp-3">{verse.text}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close verse actions"
            className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-400 hover:text-white"
          >
            <X size={16} />
          </button>
        </div>

        <div className="space-y-1">
          <button
            type="button"
            disabled={busy}
            onClick={() => onToggleMark('highlight', color)}
            className={ACTION_CLASS}
            data-testid="bible-action-highlight"
          >
            {highlight ? <BookmarkCheck size={16} className="text-amber-300" /> : <Highlighter size={16} className="text-amber-300" />}
            <span className="flex-1">{highlight ? 'Remove highlight' : 'Highlight'}</span>
          </button>

          <div className="flex items-center gap-2 px-3 py-1" data-testid="bible-mark-colors">
            {COLORS.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => setColor(c.id)}
                aria-label={c.label}
                aria-pressed={color === c.id}
                className={`min-h-11 min-w-11 rounded-full flex items-center justify-center ${
                  color === c.id ? 'ring-2 ring-white/70' : 'opacity-60'
                }`}
              >
                <span className={`w-5 h-5 rounded-full ${c.className}`} />
              </button>
            ))}
          </div>

          <button
            type="button"
            disabled={busy}
            onClick={() => onToggleMark('bookmark', '')}
            className={ACTION_CLASS}
            data-testid="bible-action-bookmark"
          >
            <Bookmark size={16} className={bookmark ? 'text-purple-300' : 'text-slate-400'} />
            <span className="flex-1">{bookmark ? 'Remove bookmark' : 'Bookmark'}</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void openStudy()}
            className={ACTION_CLASS}
            data-testid="bible-action-study"
          >
            <BookMarked size={16} className="text-amber-300" />
            <span className="flex-1">Study notes</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => {
              void trigger('light');
              setAsking(true);
            }}
            className={ACTION_CLASS}
            data-testid="bible-action-ask"
          >
            <Sparkles size={16} className="text-sky-300" />
            <span className="flex-1">Ask about this verse</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void saveNote()}
            className={ACTION_CLASS}
            data-testid="bible-action-note"
          >
            <NotebookPen size={16} className="text-slate-400" />
            <span className="flex-1">Save to Notes</span>
          </button>

          {noteMark && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void openNote()}
              className={ACTION_CLASS}
              data-testid="bible-action-open-note"
            >
              <NotebookPen size={16} className="text-sky-300" />
              <span className="flex-1">Open your note</span>
            </button>
          )}

          <button
            type="button"
            disabled={busy}
            onClick={() => void copy()}
            className={ACTION_CLASS}
            data-testid="bible-action-copy"
          >
            <Link2 size={16} className="text-slate-400" />
            <span className="flex-1">Copy</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void share()}
            className={ACTION_CLASS}
            data-testid="bible-action-share"
          >
            <Share2 size={16} className="text-slate-400" />
            <span className="flex-1">Share</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void openBlb()}
            className={ACTION_CLASS}
            data-testid="bible-action-blb"
          >
            <ExternalLink size={16} className="text-slate-400" />
            <span className="flex-1">Open on Blue Letter Bible</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void openBlb('interlinear')}
            className={ACTION_CLASS}
            data-testid="bible-action-interlinear"
          >
            <ScrollText size={16} className="text-slate-400" />
            <span className="flex-1">Interlinear (Greek &amp; Hebrew)</span>
          </button>
        </div>

        {noteBody !== null && (
          <div className="mt-3 rounded-xl border border-white/10 bg-black/30 p-3" data-testid="bible-note-body">
            <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">Your note</p>
            <p className="whitespace-pre-wrap text-xs text-slate-200">{noteBody}</p>
          </div>
        )}

        {status && (
          <p className="mt-3 text-xs text-slate-400" data-testid="bible-sheet-status">
            {status}
          </p>
        )}
      </div>

      {studyOpen && (
        <BibleStudyNotes
          passage={verse.reference}
          version={version}
          edition={edition}
          crossVersion={crossVersion}
          onEditionChange={onEditionChange}
          onCrossVersionChange={onCrossVersionChange}
          onClose={() => setStudyOpen(false)}
        />
      )}

      {askOpen && (
        <JarvisStudyThread
          passage={verse.reference}
          version={version}
          edition={edition}
          crossVersion={crossVersion}
          onClose={() => setAskOpen(false)}
        />
      )}
    </div>
  );
}