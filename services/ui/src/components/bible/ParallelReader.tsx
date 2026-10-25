import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link2, Link2Off } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleVerse } from '../../types/api';
import { anchorAt, measureVerses, scrollTopFor } from './verseAnchor';
import { DIFF_THRESHOLD, diffVerses, differingKeys, verseKey } from './verseDiff';
import { DIFF_OUTLINE } from './readingPage';
import DiffBadge from './DiffBadge';
import { nudgeSteps, paneSize, readParallelPrefs, writeParallelPrefs, type PaneName, type ParallelPrefs } from './parallelPrefs';

/** What the reader's own pane is told to render with. */
export interface PrimaryPane {
  fontScale: number;
  /** Keys of verses worded differently in the other translation; absent while marking is off. */
  differingVerses?: Set<string>;
}

interface ParallelReaderProps {
  /** The reader's own translation, rendered with this pane's size and marks (verses carry `data-verse-key`). */
  children: (primary: PrimaryPane) => ReactNode;
  /** The reader's own verses, compared against `compareVerses` for the difference marks. */
  primaryVerses: BibleVerse[];
  /** Changes when the primary passage changes, so both panes start at the top. */
  passageKey: string;
  primaryName: string;
  compareName: string;
  compareVerses: BibleVerse[];
  compareLoading: boolean;
  compareError: string | null;
  onCompareRetry: () => void;
  /** The reader's own type size; each pane steps up or down from it. */
  fontScale: number;
  lineHeight: number;
  theme: 'serif' | 'sans';
}

type Pane = PaneName;

type Row ={ kind: 'verse'; verse: BibleVerse } | { kind: 'missing'; chapter: number; verse: number };

const schedule = (fn: () => void) =>
  typeof requestAnimationFrame === 'function' ? requestAnimationFrame(fn) : window.setTimeout(fn, 0);

const order = (chapter: number, verse: number) => chapter * 1000 + verse;

/**
 * Two translations as two whole chapters, each in its own scrolling pane
 * (`split_view = parallel`), side by side from `sm` up and stacked on a phone.
 *
 * While the panes are linked, scrolling one keeps the other on the same verse.
 * The sync is anchored on verses, not on scroll ratio, because two
 * translations are never the same height (see `verseAnchor.ts`). Only the pane
 * the reader is actually driving -- the last one touched, wheeled, clicked or
 * focused -- moves the other, so the programmatic scroll it causes can never
 * echo back and fight the reader's thumb. Unlinking lets each pane scroll on
 * its own, which is how a reader checks a word three verses ahead in the
 * other translation without losing their place.
 *
 * Each pane has its own A−/A+ (steps from the reader's size, remembered on
 * this device, `parallelPrefs.ts`). "Mark differences" outlines the verses the
 * two translations word materially differently (`verseDiff.ts`), with a legend;
 * a verse one side leaves out says "Not in this translation." and is never
 * called a difference.
 */
export default function ParallelReader({
  children,
  primaryVerses,
  passageKey,
  primaryName,
  compareName,
  compareVerses,
  compareLoading,
  compareError,
  onCompareRetry,
  fontScale,
  lineHeight,
  theme,
}: ParallelReaderProps) {
  const { trigger } = useHaptics();
  const [linked, setLinked] = useState(true);
  const [prefs, setPrefs] = useState<ParallelPrefs>(readParallelPrefs);
  const primaryRef = useRef<HTMLDivElement>(null);
  const compareRef = useRef<HTMLDivElement>(null);
  const driver = useRef<Pane>('primary');
  const pending = useRef(false);

  const updatePrefs = (next: ParallelPrefs) => {
    setPrefs(next);
    writeParallelPrefs(next);
  };
  const sizes = {
    primary: paneSize(fontScale, prefs.steps.primary),
    compare: paneSize(fontScale, prefs.steps.compare),
  };
  const nudge = (pane: PaneName, direction: 1 | -1) => {
    void trigger('light');
    updatePrefs({ ...prefs, steps: { ...prefs.steps, [pane]: nudgeSteps(fontScale, prefs.steps[pane], direction) } });
  };

  const comparable = compareVerses.length > 0 && primaryVerses.length > 0;
  const diff = useMemo(
    () => (comparable ? diffVerses(primaryVerses, compareVerses) : new Map()),
    [comparable, primaryVerses, compareVerses],
  );
  const differing = useMemo(() => differingKeys(diff), [diff]);
  const marking = prefs.showDiff && comparable;

  // The comparison in verse order, with a line where it leaves a verse out.
  const rows = useMemo<Row[]>(() => {
    const have = new Set(compareVerses.map(verseKey));
    const missing: Row[] = primaryVerses
      .filter((verse) => !have.has(verseKey(verse)))
      .map((verse) => ({ kind: 'missing', chapter: verse.chapter, verse: verse.verse }));
    const present: Row[] = compareVerses.map((verse) => ({ kind: 'verse', verse }));
    const at = (row: Row) => (row.kind === 'verse' ? order(row.verse.chapter, row.verse.verse) : order(row.chapter, row.verse));
    return [...present, ...missing].sort((a, b) => at(a) - at(b));
  }, [primaryVerses, compareVerses]);

  const follow = useCallback((from: Pane) => {
    const source = from === 'primary' ? primaryRef.current : compareRef.current;
    const target = from === 'primary' ? compareRef.current : primaryRef.current;
    if (!source || !target) return;
    const anchor = anchorAt(measureVerses(source), source.scrollTop);
    if (!anchor) return;
    const top = scrollTopFor(measureVerses(target), anchor);
    if (top !== null) target.scrollTop = top;
  }, []);

  // A new chapter opens at its first verse in both panes.
  useEffect(() => {
    if (primaryRef.current) primaryRef.current.scrollTop = 0;
    if (compareRef.current) compareRef.current.scrollTop = 0;
  }, [passageKey]);

  // Relinking, the second translation arriving, or a pane changing size lines
  // it up with the reader's own place rather than leaving it wherever it was.
  useEffect(() => {
    if (linked) follow('primary');
  }, [linked, compareVerses, follow, sizes.primary.scale, sizes.compare.scale]);

  const onScroll = (pane: Pane) => () => {
    if (!linked || driver.current !== pane || pending.current) return;
    pending.current = true;
    schedule(() => {
      pending.current = false;
      follow(pane);
    });
  };

  const drive = (pane: Pane) => () => {
    driver.current = pane;
  };
  const paneHandlers = (pane: Pane) => ({
    onScroll: onScroll(pane),
    onPointerDown: drive(pane),
    onTouchStart: drive(pane),
    onWheel: drive(pane),
    onKeyDown: drive(pane),
    onFocus: drive(pane),
  });

  const paneClass =
    'relative max-h-[38vh] sm:max-h-[70vh] overflow-y-auto overscroll-contain rounded-xl border border-[var(--page-rule)] p-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/60';
  const labelClass = 'text-[11px] uppercase tracking-wider text-[var(--page-muted)] truncate';
  const toolClass =
    'min-h-11 pointer-coarse:min-h-11 px-3 rounded-xl border border-[var(--page-rule)] text-[12px] text-[var(--page-muted)] hover:bg-[var(--page-hover)] flex items-center gap-1.5';
  const fontFamily = theme === 'serif' ? 'font-serif' : 'font-sans';

  const paneHeader = (pane: PaneName, name: string, testId?: string) => (
    <div className="flex items-center justify-between gap-2">
      <p className={labelClass} data-testid={testId}>
        {name}
      </p>
      <div className="flex shrink-0 items-center gap-1" role="group" aria-label={`Text size for ${name}`}>
        <button
          type="button"
          onClick={() => nudge(pane, -1)}
          disabled={sizes[pane].smallest}
          aria-label={`Smaller text in ${name}`}
          data-testid={`bible-parallel-${pane}-smaller`}
          className="min-h-11 min-w-11 rounded-lg text-[12px] font-sans text-[var(--page-muted)] hover:bg-[var(--page-hover)] disabled:opacity-40"
        >
          A−
        </button>
        <button
          type="button"
          onClick={() => nudge(pane, 1)}
          disabled={sizes[pane].largest}
          aria-label={`Larger text in ${name}`}
          data-testid={`bible-parallel-${pane}-larger`}
          className="min-h-11 min-w-11 rounded-lg text-[15px] font-sans text-[var(--page-muted)] hover:bg-[var(--page-hover)] disabled:opacity-40"
        >
          A+
        </button>
      </div>
    </div>
  );

  return (
    <div className="space-y-2" data-testid="bible-parallel">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            void trigger('light');
            updatePrefs({ ...prefs, showDiff: !prefs.showDiff });
          }}
          aria-pressed={prefs.showDiff}
          aria-controls="bible-diff-legend"
          data-testid="bible-parallel-diff"
          className={toolClass}
        >
          <span aria-hidden="true" className="font-sans font-semibold">≠</span>
          Mark differences
        </button>
        <button
          type="button"
          onClick={() => {
            void trigger('light');
            setLinked((on) => !on);
          }}
          aria-pressed={linked}
          data-testid="bible-parallel-link"
          className={toolClass}
        >
          {linked ? <Link2 size={14} /> : <Link2Off size={14} />}
          {linked ? 'Scrolling together' : 'Scrolling separately'}
        </button>
      </div>
      {prefs.showDiff && (
        <div
          id="bible-diff-legend"
          role="note"
          aria-live="polite"
          data-testid="bible-diff-legend"
          className="rounded-xl border border-[var(--page-rule)] px-3 py-2 text-[12px] leading-relaxed text-[var(--page-muted)] space-y-1"
        >
          {comparable ? (
            <>
              <p className="flex items-center gap-2">
                <span className={`inline-flex items-center rounded px-1 py-0.5 ${DIFF_OUTLINE}`}>
                  <DiffBadge />
                </span>
                <span data-testid="bible-diff-count">
                  {differing.size === 0
                    ? 'No verse here is worded very differently.'
                    : `${differing.size} ${differing.size === 1 ? 'verse is' : 'verses are'} worded differently: fewer than ${Math.round(
                        DIFF_THRESHOLD * 100,
                      )}% of their words are shared.`}
                </span>
              </p>
              <p>
                “Not in this translation.” marks a verse one side leaves out; that is never counted as a difference.
              </p>
            </>
          ) : (
            <p>Differences are marked once both translations have loaded.</p>
          )}
        </div>
      )}
      <div className="flex flex-col gap-3 sm:grid sm:grid-cols-2">
        <div className="min-w-0 space-y-1">
          {paneHeader('primary', primaryName)}
          <div
            ref={primaryRef}
            role="region"
            aria-label={`${primaryName}, your translation`}
            tabIndex={0}
            className={paneClass}
            data-testid="bible-parallel-primary"
            {...paneHandlers('primary')}
          >
            {children({ fontScale: sizes.primary.scale, differingVerses: marking ? differing : undefined })}
          </div>
        </div>
        <div className="min-w-0 space-y-1">
          {paneHeader('compare', compareName, 'bible-compare-legend')}
          <div
            ref={compareRef}
            role="region"
            aria-label={`${compareName}, for comparison`}
            tabIndex={0}
            className={paneClass}
            data-testid="bible-parallel-compare"
            {...paneHandlers('compare')}
          >
            {compareLoading ? (
              <div className="space-y-3 py-2" data-testid="bible-parallel-compare-loading">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className="skeleton h-5 rounded w-full" />
                ))}
              </div>
            ) : compareError ? (
              <div className="p-4 text-center space-y-3" data-testid="bible-parallel-compare-error">
                <p className="text-sm leading-relaxed text-[var(--page-ink)]">
                  {compareName} could not be loaded: {compareError}
                </p>
                <button
                  type="button"
                  onClick={onCompareRetry}
                  className="glass-button px-4 py-2.5 text-sm min-h-11"
                >
                  Try again
                </button>
              </div>
            ) : compareVerses.length === 0 ? (
              <p className="p-4 text-center text-sm text-[var(--page-muted)]" data-testid="bible-parallel-compare-empty">
                {compareName} has no text for this passage.
              </p>
            ) : (
              <div className="space-y-1">
                {rows.map((row) => {
                  const textStyle = { fontSize: `${1.125 * sizes.compare.scale}rem`, lineHeight };
                  if (row.kind === 'missing') {
                    // No `data-verse-key`: scroll sync lands a missing verse at
                    // the end of the one before it, which is where this line is.
                    return (
                      <p
                        key={`missing-${row.chapter}-${row.verse}`}
                        data-testid={`bible-parallel-missing-${row.chapter}-${row.verse}`}
                        className="flex gap-2 items-baseline px-2 py-1"
                      >
                        <sup className="text-[0.6em] text-[var(--page-number)] font-sans font-semibold select-none shrink-0">
                          {row.verse}
                        </sup>
                        <em className={`not-italic opacity-70 text-[var(--page-muted)] ${fontFamily}`} style={textStyle}>
                          Not in this translation.
                        </em>
                      </p>
                    );
                  }
                  const { verse } = row;
                  const key = verseKey(verse);
                  const differs = marking && differing.has(key);
                  const onlyHere = comparable && diff.get(key)?.status === 'only-compare';
                  return (
                    <p
                      key={`${verse.osis}-${verse.chapter}-${verse.verse}`}
                      data-verse-key={key}
                      data-testid={`bible-parallel-${verse.chapter}-${verse.verse}`}
                      data-differs={differs || undefined}
                      className={`flex gap-2 items-baseline rounded-lg px-2 py-1 min-h-11 ${differs ? DIFF_OUTLINE : ''}`}
                    >
                      <sup className="text-[0.6em] text-[var(--page-number)] font-sans font-semibold select-none shrink-0">
                        {verse.verse}
                      </sup>
                      {differs && <DiffBadge />}
                      <span className={`text-[var(--page-ink)] ${fontFamily}`} style={textStyle}>
                        {verse.text}
                        {differs && <span className="sr-only"> (worded differently in {primaryName})</span>}
                        {onlyHere && (
                          <span
                            className="block text-[0.8em] text-[var(--page-muted)]"
                            data-testid={`bible-parallel-only-${verse.chapter}-${verse.verse}`}
                          >
                            Not in {primaryName}.
                          </span>
                        )}
                      </span>
                    </p>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
