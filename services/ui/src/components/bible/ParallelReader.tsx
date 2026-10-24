import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link2, Link2Off } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleVerse } from '../../types/api';
import { anchorAt, measureVerses, scrollTopFor } from './verseAnchor';

type Pane = 'primary' | 'compare';

interface ParallelReaderProps {
  /** The reader's own translation, already rendered (verses carry `data-verse-key`). */
  children: ReactNode;
  /** Changes when the primary passage changes, so both panes start at the top. */
  passageKey: string;
  primaryName: string;
  compareName: string;
  compareVerses: BibleVerse[];
  compareLoading: boolean;
  compareError: string | null;
  onCompareRetry: () => void;
  fontScale: number;
  lineHeight: number;
  theme: 'serif' | 'sans';
}

const schedule = (fn: () => void) =>
  typeof requestAnimationFrame === 'function' ? requestAnimationFrame(fn) : window.setTimeout(fn, 0);

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
 */
export default function ParallelReader({
  children,
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
  const primaryRef = useRef<HTMLDivElement>(null);
  const compareRef = useRef<HTMLDivElement>(null);
  const driver = useRef<Pane>('primary');
  const pending = useRef(false);

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

  // Relinking, or the second translation arriving, lines it up with the
  // reader's own place rather than leaving it wherever it was.
  useEffect(() => {
    if (linked) follow('primary');
  }, [linked, compareVerses, follow]);

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

  return (
    <div className="space-y-2" data-testid="bible-parallel">
      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={() => {
            void trigger('light');
            setLinked((on) => !on);
          }}
          aria-pressed={linked}
          data-testid="bible-parallel-link"
          className="min-h-11 pointer-coarse:min-h-11 px-3 rounded-xl border border-[var(--page-rule)] text-[12px] text-[var(--page-muted)] hover:bg-[var(--page-hover)] flex items-center gap-1.5"
        >
          {linked ? <Link2 size={14} /> : <Link2Off size={14} />}
          {linked ? 'Scrolling together' : 'Scrolling separately'}
        </button>
      </div>
      <div className="flex flex-col gap-3 sm:grid sm:grid-cols-2">
        <div className="min-w-0 space-y-1">
          <p className={labelClass}>{primaryName}</p>
          <div
            ref={primaryRef}
            role="region"
            aria-label={`${primaryName}, your translation`}
            tabIndex={0}
            className={paneClass}
            data-testid="bible-parallel-primary"
            {...paneHandlers('primary')}
          >
            {children}
          </div>
        </div>
        <div className="min-w-0 space-y-1">
          <p className={labelClass} data-testid="bible-compare-legend">
            {compareName}
          </p>
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
                {compareVerses.map((verse) => (
                  <p
                    key={`${verse.osis}-${verse.chapter}-${verse.verse}`}
                    data-verse-key={`${verse.chapter}:${verse.verse}`}
                    data-testid={`bible-parallel-${verse.chapter}-${verse.verse}`}
                    className="flex gap-2 items-baseline px-2 py-1 min-h-11"
                  >
                    <sup className="text-[0.6em] text-[var(--page-number)] font-sans font-semibold select-none shrink-0">
                      {verse.verse}
                    </sup>
                    <span
                      className={`text-[var(--page-ink)] ${theme === 'serif' ? 'font-serif' : 'font-sans'}`}
                      style={{ fontSize: `${1.125 * fontScale}rem`, lineHeight }}
                    >
                      {verse.text}
                    </span>
                  </p>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
