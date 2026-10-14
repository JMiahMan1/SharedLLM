import { useState } from 'react';
import { ChevronLeft, ChevronRight, Minimize2 } from 'lucide-react';
import type { BibleMark, BibleVerse } from '../../types/api';
import { ambienceFor } from './focusAmbience';
import { MARK_WASH, pageStyle, toneOf } from './readingPage';

/**
 * A chapter read as a page rather than a screen.
 *
 * Everything the reader does not need while reading is gone: the toolbar, the
 * tabs, the chips. What is left is the text, at a comfortable measure and a
 * larger size, on the reader's own opaque page. A dark page takes a faint wash
 * that shifts with the hour, painted over solid paper so nothing behind the
 * overlay ever shows through the text.
 *
 * Tapping the text hides the little chrome that remains, because a reader who
 * wants no controls at all should be able to get there without leaving. The
 * controls come back the same way.
 *
 * The chapter is still the chapter: the same verses, the same marks, and the
 * same previous/next the reader already had. Focus mode is a way of looking at
 * the text, not a second copy of it, so nothing here can drift from the reader.
 */

export interface FocusReaderProps {
  reference: string;
  verses: BibleVerse[];
  marks: BibleMark[];
  fontScale: number;
  lineHeight: number;
  theme: 'serif' | 'sans';
  /** The page tone: dark, sepia or light. */
  page?: string;
  loading?: boolean;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
}

function markFor(marks: BibleMark[], verse: BibleVerse): BibleMark | undefined {
  return marks.find(
    (m) =>
      m.kind === 'highlight' &&
      m.chapter === verse.chapter &&
      m.verse_start <= verse.verse &&
      (m.verse_end ?? m.verse_start) >= verse.verse,
  );
}

export default function FocusReader({
  reference,
  verses,
  marks,
  fontScale,
  lineHeight,
  theme,
  page,
  loading = false,
  onPrev,
  onNext,
  onClose,
}: FocusReaderProps) {
  const [chrome, setChrome] = useState(true);
  const tone = toneOf(page);

  return (
    <div
      className={`fixed inset-0 z-40 overflow-y-auto ${
        tone === 'dark' ? `bg-gradient-to-b ${ambienceFor(new Date().getHours())}` : ''
      }`}
      style={pageStyle(tone)}
      data-testid="bible-focus"
    >
      <div
        className={`sticky top-0 z-10 transition-opacity ${
          chrome ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      >
        <div className="flex items-center justify-between gap-2 px-3 py-2 pt-[max(0.5rem,env(safe-area-inset-top))] border-b border-[var(--page-rule)] bg-[var(--page-paper)]">
          <button
            type="button"
            onClick={onPrev}
            aria-label="Previous chapter"
            className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-[var(--page-muted)]"
            data-testid="bible-focus-prev"
          >
            <ChevronLeft size={18} />
          </button>
          <p className="truncate text-sm text-[var(--page-ink)]" data-testid="bible-focus-reference">
            {reference}
          </p>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={onNext}
              aria-label="Next chapter"
              className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-[var(--page-muted)]"
              data-testid="bible-focus-next"
            >
              <ChevronRight size={18} />
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Leave focus mode"
              className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-[var(--page-muted)]"
              data-testid="bible-focus-close"
            >
              <Minimize2 size={18} />
            </button>
          </div>
        </div>
      </div>

      <button
        type="button"
        onClick={() => setChrome((current) => !current)}
        aria-label={chrome ? 'Hide the controls and read' : 'Show the controls'}
        data-testid="bible-focus-text"
        className="block w-full max-w-2xl mx-auto px-5 pb-[max(4rem,env(safe-area-inset-bottom))] pt-6 text-left"
      >
        {loading && verses.length === 0 && (
          <p className="py-10 text-center text-sm text-[var(--page-muted)]" data-testid="bible-focus-loading">
            Opening the chapter…
          </p>
        )}
        {verses.map((verse) => {
          const mark = markFor(marks, verse);
          return (
            <span
              key={`${verse.osis}-${verse.chapter}-${verse.verse}`}
              className="block rounded-md px-1"
              style={mark ? { backgroundColor: MARK_WASH[mark.color] ?? MARK_WASH.yellow } : undefined}
              data-mark={mark ? mark.color : undefined}
              data-testid={`bible-focus-verse-${verse.chapter}-${verse.verse}`}
            >
              <sup
                className="mr-1 font-sans text-[0.5em] font-semibold text-[var(--page-number)] select-none"
                aria-hidden="true"
              >
                {verse.verse}
              </sup>
              <span
                className={`text-[var(--page-ink)] ${theme === 'serif' ? 'font-serif' : 'font-sans'}`}
                style={{ fontSize: `${1.3 * fontScale}rem`, lineHeight: lineHeight + 0.25 }}
              >
                {verse.text}
              </span>
            </span>
          );
        })}
      </button>
    </div>
  );
}
