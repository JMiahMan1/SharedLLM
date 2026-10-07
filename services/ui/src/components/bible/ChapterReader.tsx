import { BookMarked, NotebookPen } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleMark, BibleVerse } from '../../types/api';

interface ChapterReaderProps {
  verses: BibleVerse[];
  marks: BibleMark[];
  /** `"chapter:verse"` keys of verses that carry study material in this translation. */
  studiedVerses?: Set<string>;
  /**
   * `"chapter:verse"` keys of verses the reader has written a note about. A
   * note lives in Nextcloud and this only says one exists, so the glyph is a
   * pointer rather than the content.
   */
  notedVerses?: Set<string>;
  /**
   * The verses of a second translation for the same passage. Absent or empty
   * means no comparison is being asked for.
   */
  compareVerses?: BibleVerse[];
  /** The comparison's own name, shown so the second column is never anonymous. */
  compareName?: string;
  fontScale: number;
  lineHeight: number;
  theme: 'serif' | 'sans';
  loading?: boolean;
  error?: string | null;
  onVerseTap: (verse: BibleVerse) => void;
  onRetry?: () => void;
}

const MARK_TINTS: Record<string, string> = {
  yellow: 'bg-amber-400/15 border-amber-300/40',
  green: 'bg-emerald-400/15 border-emerald-300/40',
  blue: 'bg-sky-400/15 border-sky-300/40',
  pink: 'bg-pink-400/15 border-pink-300/40',
  purple: 'bg-purple-400/15 border-purple-300/40',
};

function markFor(marks: BibleMark[], verse: BibleVerse): BibleMark | undefined {
  return marks.find(
    (m) =>
      m.chapter === verse.chapter &&
      m.verse_start <= verse.verse &&
      (m.verse_end ?? m.verse_start) >= verse.verse,
  );
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
  notedVerses,
  compareVerses,
  compareName,
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
        className="glass-panel rounded-2xl p-6 text-center space-y-3"
        data-testid="bible-reader-error"
      >
        <p className="text-sm text-amber-300/90 leading-relaxed">{error}</p>
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
      <div className="glass-panel rounded-2xl p-6 text-center" data-testid="bible-reader-empty">
        <p className="text-sm text-slate-400">That passage is not in the imported text.</p>
      </div>
    );
  }

  return (
    <div className="space-y-1" data-testid="bible-reader">
      {comparing && (
        <p
          data-testid="bible-compare-legend"
          className="pb-2 text-[11px] uppercase tracking-wider text-slate-500"
        >
          {compareName ? `${compareName} alongside` : 'A second translation alongside'}
        </p>
      )}
      {verses.map((verse) => {
        const mark = markFor(marks, verse);
        const tint = mark ? (MARK_TINTS[mark.color] ?? MARK_TINTS.yellow) : '';
        const studied = studiedVerses?.has(`${verse.chapter}:${verse.verse}`) ?? false;
        const noted = notedVerses?.has(`${verse.chapter}:${verse.verse}`) ?? false;
        const compareText = comparing ? byVerse.get(verse.verse) : undefined;
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
            }${comparing && !compareText ? '. Not in the compared translation.' : ''}`}
            data-testid={`bible-verse-${verse.chapter}-${verse.verse}`}
            className={`w-full text-left rounded-lg px-2 py-1 min-h-11 pointer-coarse:min-h-11 hover:bg-white/[0.04] transition-colors ${
              mark ? `border-l-2 ${tint}` : 'border-l-2 border-transparent'
            }`}
          >
            <span className={`flex gap-2 items-baseline ${comparing ? 'sm:grid sm:grid-cols-2 sm:gap-4' : ''}`}>
              <span className="flex gap-2 items-baseline min-w-0">
                <sup className="text-[0.6em] text-amber-400/80 font-sans select-none shrink-0">
                  {verse.verse}
                </sup>
                <span
                  className={`text-slate-100 ${
                    theme === 'serif' ? 'font-serif' : 'font-sans'
                  }`}
                  style={{ fontSize: `${1 * fontScale}rem`, lineHeight }}
                >
                  {verse.text}
                </span>
              </span>
              {comparing && (
                <span className="min-w-0 pl-5 sm:pl-0 sm:border-l sm:border-white/10 sm:pl-4">
                  <span
                    className={`block text-slate-300/80 ${
                      theme === 'serif' ? 'font-serif' : 'font-sans'
                    }`}
                    style={{ fontSize: `${0.95 * fontScale}rem`, lineHeight }}
                    data-testid={`bible-compare-${verse.chapter}-${verse.verse}`}
                  >
                    {compareText ?? (
                      <em
                        className="text-slate-500 not-italic"
                        data-testid={`bible-compare-missing-${verse.chapter}-${verse.verse}`}
                      >
                        Not in this translation.
                      </em>
                    )}
                  </span>
                </span>
              )}
              {(studied || noted) && (
                <span className="ml-auto shrink-0 self-center flex items-center gap-1 font-sans">
                  {studied && (
                    <span
                      className="text-amber-400/70"
                      title="Has study notes"
                      data-testid={`bible-verse-studied-${verse.chapter}-${verse.verse}`}
                    >
                      <BookMarked size={12} />
                    </span>
                  )}
                  {noted && (
                    <span
                      className="text-sky-300/80"
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