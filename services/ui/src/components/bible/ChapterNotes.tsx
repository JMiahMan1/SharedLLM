import { BookMarked } from 'lucide-react';
import type { BibleStudyNote, BibleStudyNoteKind } from '../../types/api';

interface ChapterNotesProps {
  notes: BibleStudyNote[];
  /** The study Bible the notes came from, so the column is never anonymous. */
  editionName?: string;
  availableKinds?: Partial<Record<BibleStudyNoteKind, number>>;
  /** True when the reader has no study Bible for this translation at all. */
  unavailable?: string | null;
  loading?: boolean;
  onOpenVerse?: (reference: string) => void;
  onClose?: () => void;
}

const KIND_LABELS: Record<BibleStudyNoteKind, string> = {
  commentary: 'Commentary',
  footnote: 'Note',
  introduction: 'Introduction',
  heading: 'Heading',
};

/**
 * The chapter's commentary, beside the text rather than behind a tap.
 *
 * The notes are already fetched for the chapter -- the reader needs to know which
 * verses carry study material anyway -- so showing them costs no extra request.
 * Each note names its verse, because a note about verse 4 sitting next to verse 7
 * is worse than no note at all, and tapping one takes the reader there.
 */
export default function ChapterNotes({
  notes,
  editionName,
  availableKinds = {},
  unavailable = null,
  loading = false,
  onOpenVerse,
  onClose,
}: ChapterNotesProps) {
  if (unavailable) {
    return (
      <div
        className="glass-panel rounded-2xl p-4 text-[12px] leading-relaxed text-slate-400"
        data-testid="bible-notes-alongside"
      >
        <p>{unavailable}</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-2" data-testid="bible-notes-alongside-loading">
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton h-16 rounded-xl" />
        ))}
      </div>
    );
  }

  if (!notes.length) {
    return (
      <div
        className="glass-panel rounded-2xl p-4 text-[12px] leading-relaxed text-slate-400"
        data-testid="bible-notes-alongside"
      >
        <p>
          No commentary for this chapter.
          {editionName ? ` ${editionName} carries none for these verses.` : ''}
        </p>
      </div>
    );
  }

  const counts = Object.entries(availableKinds).filter(([, n]) => Boolean(n));

  return (
    <div className="space-y-2" data-testid="bible-notes-alongside">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[11px] uppercase tracking-wider text-slate-500">
          {editionName ? `${editionName} on this chapter` : 'Study notes'}
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500 tabular-nums">{notes.length}</span>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Hide study notes"
              className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400"
            >
              <BookMarked size={14} />
            </button>
          )}
        </div>
      </div>
      {counts.length > 0 && (
        <p className="text-[11px] text-slate-500">
          {counts.map(([kind, n]) => `${n} ${KIND_LABELS[kind as BibleStudyNoteKind] ?? kind}`).join(' · ')}
        </p>
      )}
      <ul className="space-y-2 lg:max-h-[36rem] lg:overflow-y-auto lg:pr-1">
        {notes.map((note) => (
          <li
            key={`${note.kind}-${note.chapter}-${note.verse}-${note.ordinal}`}
            className="glass-panel rounded-xl p-3"
            data-testid={`bible-note-alongside-${note.chapter}-${note.verse}`}
          >
            <button
              type="button"
              onClick={() => onOpenVerse?.(note.reference)}
              disabled={!onOpenVerse}
              className="mb-1 flex min-h-11 items-center gap-1.5 text-left text-[11px] font-semibold text-amber-300 disabled:text-slate-500"
            >
              {note.reference}
              <span className="font-normal text-slate-500">
                {KIND_LABELS[note.kind] ?? note.kind}
              </span>
            </button>
            <p className="text-[12px] leading-relaxed text-slate-300">{note.body}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
