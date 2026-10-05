import { BookMarked } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleMark, BibleVerse } from '../../types/api';

interface ChapterReaderProps {
  verses: BibleVerse[];
  marks: BibleMark[];
  /** `"chapter:verse"` keys of verses that carry study material in this translation. */
  studiedVerses?: Set<string>;
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
  return marks.find((m) => m.chapter === verse.chapter && m.verse_start <= verse.verse);
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
 */
export default function ChapterReader({
  verses,
  marks,
  studiedVerses,
  fontScale,
  lineHeight,
  theme,
  loading = false,
  error = null,
  onVerseTap,
  onRetry,
}: ChapterReaderProps) {
  const { trigger } = useHaptics();

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
      {verses.map((verse) => {
        const mark = markFor(marks, verse);
        const tint = mark ? (MARK_TINTS[mark.color] ?? MARK_TINTS.yellow) : '';
        const studied = studiedVerses?.has(`${verse.chapter}:${verse.verse}`) ?? false;
        return (
          <button
            key={`${verse.osis}-${verse.chapter}-${verse.verse}`}
            type="button"
            onClick={() => {
              void trigger('light');
              onVerseTap(verse);
            }}
            aria-label={`${verse.reference}. ${verse.text}${studied ? '. Has study notes.' : ''}`}
            data-testid={`bible-verse-${verse.chapter}-${verse.verse}`}
            className={`w-full text-left rounded-lg px-2 py-1 min-h-11 pointer-coarse:min-h-11 hover:bg-white/[0.04] transition-colors ${
              mark ? `border-l-2 ${tint}` : 'border-l-2 border-transparent'
            }`}
          >
            <span className="flex gap-2 items-baseline">
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
              {studied && (
                <span
                  className="ml-auto shrink-0 self-center text-amber-400/70 font-sans"
                  title="Has study notes"
                  data-testid={`bible-verse-studied-${verse.chapter}-${verse.verse}`}
                >
                  <BookMarked size={12} />
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}