/**
 * Verse-anchored scroll sync for two translations read side by side.
 *
 * Two translations of the same chapter are never the same height: one renders
 * a verse in two lines, the other in four, and one may leave a verse out
 * altogether (NIV2011 has no Matthew 17:21). Matching the panes by scroll
 * *ratio* therefore drifts, so the panes are matched by *verse*: the verse at
 * the top of the pane being scrolled, and how far into it the reader is, is
 * put at the top of the other pane.
 *
 * Pure functions over measured boxes, so the arithmetic is testable without a
 * layout engine.
 */

/** A verse's box inside a scrolling pane, in the pane's scroll coordinates. */
export interface VerseBox {
  chapter: number;
  verse: number;
  /** Distance from the top of the pane's scrollable content. */
  top: number;
  height: number;
}

/** Where a pane is: the verse straddling its top edge and how far into it. */
export interface VerseAnchor {
  chapter: number;
  verse: number;
  /** 0 = the verse's first line is at the top edge, 1 = its last line has just left. */
  fraction: number;
}

/** A passage can span chapters, so verse 1 of chapter 4 comes after 3:36. */
function order(box: { chapter: number; verse: number }): number {
  return box.chapter * 1000 + box.verse;
}

/**
 * The verse at the top of a pane scrolled to `scrollTop`. Boxes are in
 * document order. `null` when the pane has no verses to anchor on.
 */
export function anchorAt(boxes: VerseBox[], scrollTop: number): VerseAnchor | null {
  if (!boxes.length) return null;
  let current = boxes[0];
  for (const box of boxes) {
    if (box.top <= scrollTop) current = box;
    else break;
  }
  const raw = current.height > 0 ? (scrollTop - current.top) / current.height : 0;
  return { chapter: current.chapter, verse: current.verse, fraction: Math.min(1, Math.max(0, raw)) };
}

/**
 * The scroll position that puts `anchor` at the top of a pane with these
 * boxes. A verse this translation lacks lands on the end of the nearest verse
 * before it, which is where the missing verse would have been. `null` when the
 * pane has no verses.
 */
export function scrollTopFor(boxes: VerseBox[], anchor: VerseAnchor): number | null {
  if (!boxes.length) return null;
  const wanted = order(anchor);
  let target: VerseBox | null = null;
  for (const box of boxes) {
    if (order(box) <= wanted) target = box;
    else break;
  }
  if (!target) return boxes[0].top;
  const fraction = order(target) === wanted ? anchor.fraction : 1;
  return target.top + fraction * target.height;
}

/**
 * Measure the verse boxes inside a scrolling pane. Every verse element carries
 * `data-verse-key="chapter:verse"`.
 */
export function measureVerses(pane: HTMLElement): VerseBox[] {
  const paneTop = pane.getBoundingClientRect().top;
  const boxes: VerseBox[] = [];
  pane.querySelectorAll<HTMLElement>('[data-verse-key]').forEach((el) => {
    const [chapter, verse] = (el.dataset.verseKey ?? '').split(':').map(Number);
    if (!Number.isFinite(chapter) || !Number.isFinite(verse)) return;
    const rect = el.getBoundingClientRect();
    boxes.push({ chapter, verse, top: rect.top - paneTop + pane.scrollTop, height: rect.height });
  });
  return boxes;
}
