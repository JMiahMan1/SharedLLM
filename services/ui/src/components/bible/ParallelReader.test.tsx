import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ParallelReader, { type PrimaryPane } from './ParallelReader';
import { PARALLEL_PREFS_KEY } from './parallelPrefs';
import type { BibleVerse } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const verse = (version: string, n: number, text: string): BibleVerse => ({
  version,
  osis: 'Matt',
  book_name: 'Matthew',
  chapter: 17,
  verse: n,
  reference: `Matthew 17:${n}`,
  text,
});

// The second translation leaves out 17:21, as NIV2011 does.
const PRIMARY = [19, 20, 21, 22].map((n) => verse('kjv', n, `KJV ${n}`));
const COMPARE = [19, 20, 22].map((n) => verse('niv', n, `NIV ${n}`));

/**
 * happy-dom has no layout, so give every verse a box: the reader's own
 * translation sets each verse 40px tall and the comparison 20px, which is
 * exactly the mismatch a scroll ratio gets wrong.
 */
let original: typeof Element.prototype.getBoundingClientRect;
beforeEach(() => {
  original = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const el = this as HTMLElement;
    const pane = el.closest('[role="region"]') as HTMLElement | null;
    if (!el.dataset.verseKey || !pane) return original.call(this);
    const height = pane.dataset.testid === 'bible-parallel-primary' ? 40 : 20;
    const index = Array.from(pane.querySelectorAll('[data-verse-key]')).indexOf(el);
    return { top: index * height - pane.scrollTop, height } as DOMRect;
  };
});
afterEach(() => {
  Element.prototype.getBoundingClientRect = original;
});

/** The reader's own pane as the page renders it: sized and marked as told. */
const primaryPane = (verses: BibleVerse[]) =>
  function Primary({ fontScale, differingVerses }: PrimaryPane) {
    return verses.map((v) => (
      <p
        key={v.verse}
        data-verse-key={`17:${v.verse}`}
        data-testid={`primary-${v.verse}`}
        data-differs={differingVerses?.has(`17:${v.verse}`) || undefined}
        style={{ fontSize: `${fontScale}rem` }}
      >
        {v.text}
      </p>
    ));
  };

function renderPanes(overrides: Partial<Parameters<typeof ParallelReader>[0]> = {}) {
  const primary = overrides.primaryVerses ?? PRIMARY;
  return render(
    <ParallelReader
      primaryVerses={primary}
      passageKey="kjv|Matt 17"
      primaryName="King James Version"
      compareName="New International Version"
      compareVerses={COMPARE}
      compareLoading={false}
      compareError={null}
      onCompareRetry={vi.fn()}
      fontScale={1}
      lineHeight={1.6}
      theme="serif"
      {...overrides}
    >
      {(pane) => primaryPane(primary)(pane)}
    </ParallelReader>,
  );
}

function scroll(pane: HTMLElement, top: number) {
  fireEvent.pointerDown(pane);
  pane.scrollTop = top;
  fireEvent.scroll(pane);
}

describe('ParallelReader', () => {
  it('keeps the other pane on the same verse, not the same ratio', async () => {
    renderPanes();
    const primary = screen.getByTestId('bible-parallel-primary');
    const compare = screen.getByTestId('bible-parallel-compare');

    // Halfway into 17:20 in the primary (40px rows) is halfway into 17:20 in
    // the comparison (20px rows).
    scroll(primary, 60);
    await waitFor(() => expect(compare.scrollTop).toBe(30));
  });

  it('lands a verse the comparison lacks at the end of the verse before it', async () => {
    renderPanes();
    const primary = screen.getByTestId('bible-parallel-primary');
    const compare = screen.getByTestId('bible-parallel-compare');

    scroll(primary, 90); // a quarter into 17:21
    await waitFor(() => expect(compare.scrollTop).toBe(40)); // end of 17:20
  });

  it('follows whichever pane the reader is driving', async () => {
    renderPanes();
    const primary = screen.getByTestId('bible-parallel-primary');
    const compare = screen.getByTestId('bible-parallel-compare');

    scroll(compare, 40); // top of 17:22 in the comparison
    await waitFor(() => expect(primary.scrollTop).toBe(120)); // top of 17:22 in the primary
  });

  it('lets the panes scroll on their own once unlinked', async () => {
    renderPanes();
    const primary = screen.getByTestId('bible-parallel-primary');
    const compare = screen.getByTestId('bible-parallel-compare');
    const link = screen.getByTestId('bible-parallel-link');
    expect(link).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(link);
    expect(link).toHaveAttribute('aria-pressed', 'false');
    expect(link).toHaveTextContent('Scrolling separately');

    scroll(primary, 60);
    await new Promise((r) => setTimeout(r, 30));
    expect(compare.scrollTop).toBe(0);

    // Relinking lines the comparison up with where the reader is.
    fireEvent.click(link);
    await waitFor(() => expect(compare.scrollTop).toBe(30));
  });

  it('says the comparison is loading, failed or empty rather than showing a blank pane', () => {
    const retry = vi.fn();
    const { rerender } = renderPanes({ compareLoading: true, compareVerses: [] });
    expect(screen.getByTestId('bible-parallel-compare-loading')).toBeInTheDocument();

    rerender(
      <ParallelReader
        primaryVerses={PRIMARY}
        passageKey="kjv|Matt 17"
        primaryName="King James Version"
        compareName="New International Version"
        compareVerses={[]}
        compareLoading={false}
        compareError="Request failed with status code 502"
        onCompareRetry={retry}
        fontScale={1}
        lineHeight={1.6}
        theme="serif"
      >
        {() => <p>KJV</p>}
      </ParallelReader>,
    );
    expect(screen.getByTestId('bible-parallel-compare-error')).toHaveTextContent(
      'New International Version could not be loaded: Request failed with status code 502',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(retry).toHaveBeenCalled();
  });

  it('says when the comparison has no text for the passage', () => {
    renderPanes({ compareVerses: [] });
    expect(screen.getByTestId('bible-parallel-compare-empty')).toHaveTextContent(
      'New International Version has no text for this passage.',
    );
  });

  it('says where the comparison leaves a verse out, in verse order', () => {
    renderPanes();
    const missing = screen.getByTestId('bible-parallel-missing-17-21');
    expect(missing).toHaveTextContent('Not in this translation.');
    const compare = screen.getByTestId('bible-parallel-compare');
    const order = [...compare.querySelectorAll('[data-testid^="bible-parallel-"]')].map((el) => el.getAttribute('data-testid'));
    expect(order).toEqual([
      'bible-parallel-17-19',
      'bible-parallel-17-20',
      'bible-parallel-missing-17-21',
      'bible-parallel-17-22',
    ]);
  });

  describe('per-pane text size', () => {
    it('steps each pane on its own and remembers it on this device', () => {
      const { unmount } = renderPanes({ fontScale: 1 });
      fireEvent.click(screen.getByRole('button', { name: 'Larger text in New International Version' }));
      const rem = (el: Element | null) => parseFloat((el as HTMLElement | null)?.style.fontSize ?? '');
      const compareText = () => screen.getByTestId('bible-parallel-17-19').querySelector('sup + span');
      expect(rem(compareText())).toBeCloseTo(1.125 * 1.15, 5);
      // The reader's own pane did not move.
      expect(rem(screen.getByTestId('primary-19'))).toBe(1);

      fireEvent.click(screen.getByRole('button', { name: 'Smaller text in King James Version' }));
      expect(rem(screen.getByTestId('primary-19'))).toBe(0.85);
      // 0.85 is the smallest step.
      expect(screen.getByTestId('bible-parallel-primary-smaller')).toBeDisabled();

      unmount();
      renderPanes({ fontScale: 1 });
      expect(rem(screen.getByTestId('primary-19'))).toBe(0.85);
      expect(rem(compareText())).toBeCloseTo(1.125 * 1.15, 5);
      expect(JSON.parse(localStorage.getItem(PARALLEL_PREFS_KEY) ?? '{}').steps).toEqual({ primary: -1, compare: 1 });
    });
  });

  describe('marking differences', () => {
    const KJV = [
      verse('kjv', 1, 'And after six days Jesus taketh Peter, James, and John his brother, and bringeth them up into an high mountain apart,'),
      verse('kjv', 2, 'And was transfigured before them: and his face did shine as the sun, and his raiment was white as the light.'),
      verse('kjv', 21, 'Howbeit this kind goeth not out but by prayer and fasting.'),
    ];
    const OTHER = [
      verse('niv', 1, 'Six days later, three of them saw that glory.'),
      verse('niv', 2, 'And was transfigured before them; and his face did shine as the sun, and his raiment was white as the light!'),
    ];

    it('is off until asked for, then outlines only the verses worded differently in both panes', () => {
      renderPanes({ primaryVerses: KJV, compareVerses: OTHER });
      const toggle = screen.getByRole('button', { name: /Mark differences/ });
      expect(toggle).toHaveAttribute('aria-pressed', 'false');
      expect(screen.queryByTestId('bible-diff-legend')).toBeNull();
      expect(screen.getByTestId('primary-1')).not.toHaveAttribute('data-differs');

      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByTestId('bible-diff-legend')).toHaveTextContent('1 verse is worded differently');
      expect(screen.getByTestId('primary-1')).toHaveAttribute('data-differs', 'true');
      expect(screen.getByTestId('bible-parallel-17-1')).toHaveAttribute('data-differs', 'true');
      expect(screen.getByTestId('bible-parallel-17-1')).toHaveTextContent('worded differently in King James Version');
      // Punctuation only: the same verse.
      expect(screen.getByTestId('primary-2')).not.toHaveAttribute('data-differs');
      expect(screen.getByTestId('bible-parallel-17-2')).not.toHaveAttribute('data-differs');
      // Missing on one side is said, never marked as a difference.
      expect(screen.getByTestId('primary-21')).not.toHaveAttribute('data-differs');
      expect(screen.getByTestId('bible-parallel-missing-17-21')).toHaveTextContent('Not in this translation.');
      expect(JSON.parse(localStorage.getItem(PARALLEL_PREFS_KEY) ?? '{}').showDiff).toBe(true);
    });

    it('says a verse only the comparison has is not in the reader’s translation', () => {
      localStorage.setItem(PARALLEL_PREFS_KEY, JSON.stringify({ showDiff: true }));
      renderPanes({ primaryVerses: KJV.slice(0, 2), compareVerses: [...OTHER, verse('niv', 3, 'Just then there appeared before them Moses and Elijah.')] });
      expect(screen.getByTestId('bible-parallel-only-17-3')).toHaveTextContent('Not in King James Version.');
      expect(screen.getByTestId('bible-parallel-17-3')).not.toHaveAttribute('data-differs');
    });

    it('waits for both translations before marking anything', () => {
      localStorage.setItem(PARALLEL_PREFS_KEY, JSON.stringify({ showDiff: true }));
      renderPanes({ compareLoading: true, compareVerses: [] });
      expect(screen.getByTestId('bible-diff-legend')).toHaveTextContent('once both translations have loaded');
    });
  });
});
