import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ParallelReader from './ParallelReader';
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

function renderPanes(overrides: Partial<Parameters<typeof ParallelReader>[0]> = {}) {
  return render(
    <ParallelReader
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
      {PRIMARY.map((v) => (
        <p key={v.verse} data-verse-key={`17:${v.verse}`}>
          {v.text}
        </p>
      ))}
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
        <p>KJV</p>
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
});
