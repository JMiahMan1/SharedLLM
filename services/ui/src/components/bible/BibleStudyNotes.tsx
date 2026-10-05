import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookMarked, Globe2, Info, X } from 'lucide-react';
import { api } from '../../services/api';
import type { BibleStudyNoteKind, BibleStudyNotesResponse } from '../../types/api';

interface BibleStudyNotesProps {
  /** What the reader asked about; not named `ref`, which React reserves. */
  passage: string;
  version: string;
  /** Widen to a chapter or range; still a single verse by default. */
  scope?: string;
  /** Which study Bible to read; empty means the server's default for the translation. */
  edition?: string;
  /** Include commentary written for another translation. Off unless asked for. */
  crossVersion?: boolean;
  /** Called when the reader picks a different study Bible, so it can be remembered. */
  onEditionChange?: (code: string) => void;
  onCrossVersionChange?: (on: boolean) => void;
  onClose: () => void;
}

const KIND_LABELS: Record<BibleStudyNoteKind, string> = {
  commentary: 'Commentary',
  footnote: 'Footnotes',
  introduction: 'Introductions',
  heading: 'Headings',
};

const KIND_STYLES: Record<BibleStudyNoteKind, string> = {
  commentary: 'text-amber-200 border-amber-400/40',
  footnote: 'text-sky-200 border-sky-400/40',
  introduction: 'text-emerald-200 border-emerald-400/40',
  heading: 'text-purple-200 border-purple-400/40',
};

const ORDER: BibleStudyNoteKind[] = ['commentary', 'introduction', 'heading', 'footnote'];

/**
 * The study apparatus for a passage, opened from a verse.
 *
 * Deliberately not part of the reader: the verses come back from `/passages`
 * with no commentary in them, and this panel asks `/study/notes` for the
 * material separately, so a plain reading surface stays plain and a study Bible
 * is still a study Bible one tap away.
 *
 * Two choices are made here rather than by the server, because they are the
 * reader's to make: **which study Bible** explains this translation (several can
 * be installed over one set of words) and whether commentary **written for a
 * different translation** may join in. The second is off until tapped, and every
 * note is labelled with its translation and edition, because a note written
 * against the NIV wording is not a note on the NKJV wording.
 *
 * Every kind the study Bible actually carries is offered as a filter. When it
 * carries none, the server's own sentence is shown rather than an empty panel —
 * that sentence is the difference between "nothing to read" and "nothing is
 * installed".
 */
export default function BibleStudyNotes({
  passage: requested,
  version,
  scope,
  edition,
  crossVersion = false,
  onEditionChange,
  onCrossVersionChange,
  onClose,
}: BibleStudyNotesProps) {
  const wanted = scope ?? requested;
  const [hidden, setHidden] = useState<BibleStudyNoteKind[]>([]);

  const query = useQuery({
    queryKey: ['bible-study-notes', wanted, version, edition ?? '', crossVersion],
    queryFn: () => api.getBibleStudyNotes(wanted, version, { edition, crossVersion }),
    retry: false,
    staleTime: 10 * 60 * 1000,
  });

  const data: BibleStudyNotesResponse | undefined = query.data;

  const offered = useMemo(() => {
    if (!data) return [] as BibleStudyNoteKind[];
    const kinds = ORDER.filter((k) => (data.available_kinds[k] ?? 0) > 0);
    return kinds.length > 0 ? kinds : ORDER.filter((k) => k in (data.available_kinds ?? {}));
  }, [data]);

  const installedEditions = useMemo(
    () => (data?.editions ?? []).filter((entry) => entry.installed),
    [data],
  );

  const shown = useMemo(() => {
    if (!data) return [] as BibleStudyNotesResponse['notes'];
    return [...data.notes]
      .filter((n) => !hidden.includes(n.kind))
      .sort((a, b) => {
        const byKind = ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind);
        if (byKind !== 0) return byKind;
        // Group by study Bible before by verse, so a reader comparing two
        // commentaries reads one at a time instead of alternating per verse.
        const byEdition = (a.edition_name || a.edition).localeCompare(b.edition_name || b.edition);
        if (byEdition !== 0) return byEdition;
        const byRef =
          (a.chapter - b.chapter) ||
          (a.verse - b.verse) ||
          a.reference.localeCompare(b.reference) ||
          (a.ordinal ?? 0) - (b.ordinal ?? 0);
        return byRef;
      });
  }, [data, hidden]);

  const toggle = (kind: BibleStudyNoteKind) =>
    setHidden((prev) => (prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind]));

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={`Study notes for ${wanted}`}
    >
      <button
        type="button"
        aria-label="Close study notes"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        data-testid="bible-study-scrim"
      />
      <div
        className="relative w-full sm:max-w-lg bg-slate-950/97 backdrop-blur-xl border border-white/10 border-b-0 sm:border-b sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] overflow-y-auto"
        data-testid="bible-study-notes"
      >
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-xs font-semibold text-amber-300">
              <BookMarked size={14} />
              <span className="truncate" data-testid="bible-study-ref">
                {wanted}
              </span>
            </p>
            <p className="text-[11px] text-slate-500 mt-1 uppercase tracking-wide" data-testid="bible-study-edition-name">
              {data?.edition_name || version}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close study notes"
            className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-400 hover:text-white"
          >
            <X size={16} />
          </button>
        </div>

        {query.isLoading && (
          <div className="space-y-2" data-testid="bible-study-loading">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-14 rounded-xl bg-white/5 animate-pulse" />
            ))}
          </div>
        )}

        {query.isError && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3" data-testid="bible-study-error">
            <p className="text-xs text-amber-200">
              {query.error instanceof Error ? query.error.message : 'Study notes could not be loaded'}
            </p>
            <button
              type="button"
              onClick={() => void query.refetch()}
              className="mt-2 min-h-11 pointer-coarse:min-h-11 px-4 rounded-xl bg-white/10 text-xs font-semibold"
              data-testid="bible-study-retry"
            >
              Try again
            </button>
          </div>
        )}

        {data && (
          <>
            {installedEditions.length > 1 && (
              <div className="mb-3" data-testid="bible-study-editions">
                <p className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">Study Bible</p>
                <div className="flex flex-wrap gap-2">
                  {installedEditions.map((entry) => {
                    const active = entry.code === (edition || data.edition);
                    return (
                      <button
                        key={entry.code}
                        type="button"
                        aria-pressed={active}
                        onClick={() => onEditionChange?.(entry.code)}
                        className={`min-h-11 pointer-coarse:min-h-11 px-3 rounded-full border text-xs font-semibold ${
                          active
                            ? 'border-amber-400/60 text-amber-200 bg-amber-500/10'
                            : 'border-white/10 text-slate-400'
                        }`}
                        data-testid={`bible-study-edition-${entry.code}`}
                      >
                        {entry.name}
                        {entry.note_count ? ` · ${entry.note_count}` : ''}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {data.other_translations.length > 0 && (
              <label className="mb-3 flex items-start gap-3 rounded-xl border border-white/10 bg-white/[0.02] p-3 min-h-11 pointer-coarse:min-h-11 cursor-pointer">
                <input
                  type="checkbox"
                  checked={crossVersion}
                  onChange={(event) => onCrossVersionChange?.(event.target.checked)}
                  className="mt-0.5 h-5 w-5 shrink-0 accent-amber-400"
                  data-testid="bible-study-cross-version"
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
                    <Globe2 size={12} className="text-sky-300" />
                    Notes from other translations
                  </span>
                  <span className="block text-[11px] text-slate-500 mt-0.5">
                    {data.other_translations
                      .slice(0, 3)
                      .map((entry) => `${entry.edition_name} (${entry.version_name})`)
                      .join(', ')}
                    {data.other_translations.length > 3
                      ? ` and ${data.other_translations.length - 3} more`
                      : ''}
                    . Each note is labelled with the translation it was written for.
                  </span>
                </span>
              </label>
            )}

            {offered.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-3" data-testid="bible-study-kinds">
                {offered.map((kind) => {
                  const off = hidden.includes(kind);
                  return (
                    <button
                      key={kind}
                      type="button"
                      aria-pressed={!off}
                      onClick={() => toggle(kind)}
                      className={`min-h-11 pointer-coarse:min-h-11 px-3 rounded-full border text-xs font-semibold ${KIND_STYLES[kind]} ${
                        off ? 'opacity-40 line-through' : ''
                      }`}
                      data-testid={`bible-study-kind-${kind}`}
                    >
                      {KIND_LABELS[kind]}
                      {data.available_kinds[kind] ? ` · ${data.available_kinds[kind]}` : ''}
                    </button>
                  );
                })}
              </div>
            )}

            {shown.length === 0 && (
              <p className="text-xs text-slate-400" data-testid="bible-study-empty">
                {data.note ?? `No study notes are attached to ${data.reference}.`}
              </p>
            )}

            <div className="space-y-3">
              {shown.map((note) => (
                <article
                  key={`${note.version}-${note.edition}-${note.kind}-${note.reference}-${note.ordinal}`}
                  className="rounded-xl border border-white/10 bg-white/[0.03] p-3"
                  data-testid={`bible-study-note-${note.kind}`}
                >
                  <header className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1 mb-1">
                    <span className="flex items-center gap-2">
                      <span className={`text-[11px] font-semibold uppercase tracking-wide ${KIND_STYLES[note.kind].split(' ')[0]}`}>
                        {KIND_LABELS[note.kind]}
                      </span>
                      {(crossVersion || installedEditions.length > 1) && (
                        <span
                          className="text-[10px] rounded-full border border-white/10 px-2 py-0.5 text-slate-400"
                          data-testid="bible-study-note-provenance"
                        >
                          {note.version_name}
                          {note.edition_name ? ` · ${note.edition_name}` : ''}
                        </span>
                      )}
                    </span>
                    <span className="text-[11px] text-slate-500">{note.reference}</span>
                  </header>
                  <p className="text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">{note.body}</p>
                  {note.source && (
                    <p className="mt-2 text-[10px] text-slate-600 flex items-center gap-1">
                      <Info size={10} />
                      {note.source}
                    </p>
                  )}
                </article>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}