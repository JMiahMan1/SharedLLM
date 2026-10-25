import { Bookmark, BookMarked, NotebookPen } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleMark, BibleVerse } from '../../types/api';
import { DIFF_OUTLINE, MARK_EDGE, MARK_WASH } from './readingPage';
import { marksOnVerse } from './verseMarks';
import DiffBadge from './DiffBadge';

interface ChapterReaderProps {
  verses: BibleVerse[];
  /**
   * The reader's marks (any book; each verse picks out its own). A highlight
   * washes the verse; a bookmark or a note shows a glyph. A note lives in
   * Nextcloud and its mark only says one exists, so the glyph is a pointer
   * rather than the content.
   */
  marks: BibleMark[];
  /** `"chapter:verse"` keys of verses that carry study material in this translation. */
  studiedVerses?: Set<string>;
  /**
   * The verses of a second translation for the same passage. Absent or empty
   * means no comparison is being asked for.
   */
  compareVerses?: BibleVerse[];
  /** The comparison's own name, shown so the second column is never anonymous. */
  compareName?: string;
  /** Verses currently being read aloud, so the reader can follow the voice. */
  speakingVerses?: { start: number; end: number } | null;
  /**
   * `"chapter:verse"` keys of verses the side-by-side translation words
   * materially differently (`verseDiff.ts`), and that translation's name.
   */
  differingVerses?: Set<string>;
  differingFrom?: string;
  fontScale: number;
  lineHeight: number;
  theme: 'serif' | 'sans';
  loading?: boolean;
  error?: string | null;
  onVerseTap: (verse: BibleVerse) => void;
  onRetry?: () => void;
}

/**
 * Scripture, one verse per block, each verse a button.
 *
 * Every verse is its own tap target rather than a hover-revealed highlight: on a
 * phone there is no hover, so a hover affordance would be a feature that only
 * exists on the desktop. Type size and line spacing come from the reader's own
 * preferences, and the verse number stays in the text flow so the layout never
 * reflows when it is tapped.
 *
 * A study Bible is still a study Bible here: a verse that carries commentary or
 * footnotes is marked, without any of that material being mixed into the text.
 *
 * Two translations sit side by side from `sm` up and stack on a phone, because a
 * phone cannot show two readable columns. A verse the second translation does not
 * have says so rather than vanishing: NIV2011 genuinely omits Matthew 17:21, and
 * a gap the reader cannot see looks like a rendering fault.
 */
export default function ChapterReader({
  verses,
  marks,
  studiedVerses,
  compareVerses,
  compareName,
  speakingVerses,
  differingVerses,
  differingFrom,
  fontScale,
  lineHeight,
  theme,
  loading = false,
  error = null,
  onVerseTap,
  onRetry,
}: ChapterReaderProps) {
  const { trigger } = useHaptics();
  const comparing = Boolean(compareVerses?.length);
  const byVerse = new Map<number, string>();
  for (const entry of compareVerses ?? []) {
    if (entry.chapter === verses[0]?.chapter) byVerse.set(entry.verse, entry.text);
  }

  if (loading) {
    return (
      <div className="space-y-3 py-4" data-testid="bible-reader-loading">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="skeleton h-5 rounded w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="rounded-2xl p-6 text-center space-y-3"
        data-testid="bible-reader-error"
      >
        <p className="text-sm leading-relaxed text-[var(--page-ink)]">{error}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="glass-button px-4 py-2.5 text-sm min-h-11"
          >
            Try again
          </button>
        )}
      </div>
    );
  }

  if (verses.length === 0) {
    return (
      <div className="rounded-2xl p-6 text-center" data-testid="bible-reader-empty">
        <p className="text-sm text-[var(--page-muted)]">That passage is not in the imported text.</p>
      </div>
    );
  }

  return (
    <div className={`space-y-1 ${comparing ? '' : 'mx-auto max-w-[68ch]'}`} data-testid="bible-reader">
      {comparing && (
        <p
          data-testid="bible-compare-legend"
          className="pb-2 text-[11px] uppercase tracking-wider text-[var(--page-muted)]"
        >
          {compareName ? `${compareName} alongside` : 'A second translation alongside'}
        </p>
      )}
      {verses.map((verse) => {
        const own = marksOnVerse(marks, verse);
        const mark = own.find((m) => m.kind === 'highlight');
        const color = mark && MARK_WASH[mark.color] ? mark.color : 'yellow';
        const studied = studiedVerses?.has(`${verse.chapter}:${verse.verse}`) ?? false;
        const noted = own.some((m) => m.kind === 'note' && Boolean(m.note_path));
        const bookmarked = own.some((m) => m.kind === 'bookmark');
        const compareText = comparing ? byVerse.get(verse.verse) : undefined;
        const differs = differingVerses?.has(`${verse.chapter}:${verse.verse}`) ?? false;
        const speaking = Boolean(
          speakingVerses &&
            verse.verse >= speakingVerses.start &&
            verse.verse <= speakingVerses.end
        );
        return (
          <button
            key={`${verse.osis}-${verse.chapter}-${verse.verse}`}
            type="button"
            onClick={() => {
              void trigger('light');
              onVerseTap(verse);
            }}
            aria-label={`${verse.reference}. ${verse.text}${studied ? '. Has study notes.' : ''}${
              noted ? '. Has your note.' : ''
            }${bookmarked ? '. Bookmarked.' : ''}${comparing && !compareText ? '. Not in the compared translation.' : ''}${
              differs ? `. Worded differently in ${differingFrom ?? 'the other translation'}.` : ''
            }`}
            data-testid={`bible-verse-${verse.chapter}-${verse.verse}`}
            data-verse-key={`${verse.chapter}:${verse.verse}`}
            data-speaking={speaking || undefined}
            data-differs={differs || undefined}
            className={`w-full text-left rounded-lg px-2 py-1 min-h-11 pointer-coarse:min-h-11 border-l-2 border-transparent hover:bg-[var(--page-hover)] transition-colors ${
              speaking ? 'bg-sky-500/10 ring-1 ring-inset ring-sky-400/50' : ''
            } ${differs ? DIFF_OUTLINE : ''}`}
            style={mark ? { backgroundColor: MARK_WASH[color], borderLeftColor: MARK_EDGE[color] } : undefined}
          >
            <span className={`flex gap-2 items-baseline ${comparing ? 'sm:grid sm:grid-cols-2 sm:gap-4' : ''}`}>
              <span className="flex gap-2 items-baseline min-w-0">
                <sup className="text-[0.6em] text-[var(--page-number)] font-sans font-semibold select-none shrink-0">
                  {verse.verse}
                </sup>
                {differs && <DiffBadge />}
                <span
                  className={`text-[var(--page-ink)] ${
                    theme === 'serif' ? 'font-serif' : 'font-sans'
                  }`}
                  style={{ fontSize: `${1.125 * fontScale}rem`, lineHeight }}
                >
                  {verse.text}
                </span>
              </span>
              {comparing && (
                <span className="min-w-0 pl-5 sm:pl-0 sm:border-l sm:border-[var(--page-rule)] sm:pl-4">
                  <span
                    className={`block text-[var(--page-muted)] ${
                      theme === 'serif' ? 'font-serif' : 'font-sans'
                    }`}
                    style={{ fontSize: `${1.05 * fontScale}rem`, lineHeight }}
                    data-testid={`bible-compare-${verse.chapter}-${verse.verse}`}
                  >
                    {compareText ?? (
                      <em
                        className="opacity-70 not-italic"
                        data-testid={`bible-compare-missing-${verse.chapter}-${verse.verse}`}
                      >
                        Not in this translation.
                      </em>
                    )}
                  </span>
                </span>
              )}
              {(studied || noted || bookmarked) && (
                <span className="ml-auto shrink-0 self-center flex items-center gap-1 font-sans">
                  {studied && (
                    <span
                      className="text-[var(--page-number)]"
                      title="Has study notes"
                      data-testid={`bible-verse-studied-${verse.chapter}-${verse.verse}`}
                    >
                      <BookMarked size={12} />
                    </span>
                  )}
                  {bookmarked && (
                    <span
                      className="text-purple-400"
                      title="Bookmarked"
                      data-testid={`bible-verse-bookmarked-${verse.chapter}-${verse.verse}`}
                    >
                      <Bookmark size={12} />
                    </span>
                  )}
                  {noted && (
                    <span
                      className="text-sky-500"
                      title="You wrote a note about this verse"
                      data-testid={`bible-verse-noted-${verse.chapter}-${verse.verse}`}
                    >
                      <NotebookPen size={12} />
                    </span>
                  )}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}