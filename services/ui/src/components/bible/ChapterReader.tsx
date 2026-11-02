import { Bookmark, BookMarked, NotebookPen } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleMark, BibleVerse } from '../../types/api';
import { DIFF_OUTLINE, MARK_EDGE, MARK_WASH } from './readingPage';
import { marksOnVerse } from './verseMarks';
import DiffBadge from './DiffBadge';
import { paragraphs } from './paragraphs';

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
  /**
   * "flow" runs the verses on in paragraphs, the way a printed Bible (and the
   * Bible App) sets them; "verses" puts one verse to a line. A comparison is
   * always one verse to a line, since its rows must line up.
   */
  layout?: 'flow' | 'verses';
  /** `"chapter:verse"` of the verse whose actions are open, drawn underlined. */
  selectedKey?: string | null;
}

/**
 * Scripture, set in paragraphs (the default) or one verse per block, each
 * verse its own tap target.
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
  layout = 'flow',
  selectedKey = null,
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

  const verseLabel = (verse: BibleVerse, studied: boolean, noted: boolean, bookmarked: boolean, compareMissing = false, differs = false) =>
    `${verse.reference}. ${verse.text}${studied ? '. Has study notes.' : ''}${noted ? '. Has your note.' : ''}${
      bookmarked ? '. Bookmarked.' : ''
    }${compareMissing ? '. Not in the compared translation.' : ''}${differs ? `. Worded differently in ${differingFrom ?? 'the other translation'}.` : ''}`;

  if (!comparing && layout === 'flow') {
    const fontClass = theme === 'serif' ? 'font-serif' : 'font-sans';
    return (
      <div
        className={`mx-auto max-w-[36rem] space-y-[0.9em] text-[var(--page-ink)] ${fontClass}`}
        style={{ fontSize: `${1.125 * fontScale}rem`, lineHeight: Math.max(lineHeight, 1.75) }}
        data-testid="bible-reader"
        data-layout="flow"
      >
        {paragraphs(verses).map((group) => (
          <p key={`${group[0].chapter}:${group[0].verse}`} className="indent-[1.25em] break-words">
            {group.map((verse) => {
              const own = marksOnVerse(marks, verse);
              const mark = own.find((m) => m.kind === 'highlight');
              const color = mark && MARK_WASH[mark.color] ? mark.color : 'yellow';
              const key = `${verse.chapter}:${verse.verse}`;
              const studied = studiedVerses?.has(key) ?? false;
              const noted = own.some((m) => m.kind === 'note' && Boolean(m.note_path));
              const bookmarked = own.some((m) => m.kind === 'bookmark');
              const speaking = Boolean(speakingVerses && verse.verse >= speakingVerses.start && verse.verse <= speakingVerses.end);
              const selected = selectedKey === key;
              const tap = () => {
                void trigger('light');
                onVerseTap(verse);
              };
              return (
                <span key={`${verse.osis}-${key}`}>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={tap}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        tap();
                      }
                    }}
                    aria-label={verseLabel(verse, studied, noted, bookmarked)}
                    aria-pressed={selected}
                    data-testid={`bible-verse-${verse.chapter}-${verse.verse}`}
                    data-verse-key={key}
                    data-speaking={speaking || undefined}
                    className={`cursor-pointer rounded-[3px] decoration-[var(--page-muted)] underline-offset-4 outline-none transition-colors hover:bg-[var(--page-hover)] focus-visible:ring-2 focus-visible:ring-amber-400/70 ${
                      selected ? 'underline decoration-dashed decoration-1' : ''
                    } ${speaking ? 'bg-sky-500/15' : ''}`}
                    style={mark ? { backgroundColor: MARK_WASH[color], boxShadow: `inset 0 -2px 0 ${MARK_EDGE[color]}` } : undefined}
                  >
                    <sup className="mr-[0.2em] ml-[0.1em] select-none font-sans text-[0.58em] font-semibold text-[var(--page-number)]">
                      {verse.verse}
                    </sup>
                    {verse.text}
                    {(studied || bookmarked || noted) && (
                      <span className="ml-1 inline-flex translate-y-[-0.1em] items-center gap-0.5 align-middle font-sans">
                        {studied && (
                          <span className="text-[var(--page-number)]" title="Has study notes" data-testid={`bible-verse-studied-${verse.chapter}-${verse.verse}`}>
                            <BookMarked size={11} />
                          </span>
                        )}
                        {bookmarked && (
                          <span className="text-purple-400" title="Bookmarked" data-testid={`bible-verse-bookmarked-${verse.chapter}-${verse.verse}`}>
                            <Bookmark size={11} />
                          </span>
                        )}
                        {noted && (
                          <span className="text-sky-500" title="You wrote a note about this verse" data-testid={`bible-verse-noted-${verse.chapter}-${verse.verse}`}>
                            <NotebookPen size={11} />
                          </span>
                        )}
                      </span>
                    )}
                  </span>{' '}
                </span>
              );
            })}
          </p>
        ))}
      </div>
    );
  }

  return (
    <div className={`space-y-1 ${comparing ? '' : 'mx-auto max-w-[68ch]'}`} data-testid="bible-reader" data-layout="verses">
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
            aria-label={verseLabel(verse, studied, noted, bookmarked, comparing && !compareText, differs)}
            data-testid={`bible-verse-${verse.chapter}-${verse.verse}`}
            data-verse-key={`${verse.chapter}:${verse.verse}`}
            data-speaking={speaking || undefined}
            data-differs={differs || undefined}
            aria-pressed={selectedKey === `${verse.chapter}:${verse.verse}`}
            className={`w-full text-left rounded-lg px-2 py-1 min-h-11 pointer-coarse:min-h-11 border-l-2 border-transparent hover:bg-[var(--page-hover)] transition-colors ${
              selectedKey === `${verse.chapter}:${verse.verse}` ? 'underline decoration-dashed decoration-1 underline-offset-4 decoration-[var(--page-muted)] ' : ''
            }${
              speaking ? 'bg-sky-500/10 ring-1 ring-inset ring-sky-400/50' : ''
            } ${differs ? DIFF_OUTLINE : ''}`}
            style={mark ? { backgroundColor: MARK_WASH[color], borderLeftColor: MARK_EDGE[color] } : undefined}
          >
            <span className={`flex gap-2 items-baseline ${comparing ? 'flex-col items-stretch gap-1 sm:grid sm:grid-cols-2 sm:gap-4' : ''}`}>
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