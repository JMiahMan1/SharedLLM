import { describe, expect, it } from 'vitest';
import { anchorAt, scrollTopFor, type VerseBox } from './verseAnchor';

// One translation sets verse 2 in four lines, the other in two: a scroll ratio
// would put the panes on different verses, an anchor never does.
const TALL: VerseBox[] = [
  { chapter: 17, verse: 20, top: 0, height: 40 },
  { chapter: 17, verse: 21, top: 40, height: 80 },
  { chapter: 17, verse: 22, top: 120, height: 40 },
];
const SHORT: VerseBox[] = [
  { chapter: 17, verse: 20, top: 0, height: 20 },
  { chapter: 17, verse: 21, top: 20, height: 40 },
  { chapter: 17, verse: 22, top: 60, height: 20 },
];

describe('anchorAt', () => {
  it('names the verse straddling the top edge and how far into it the reader is', () => {
    expect(anchorAt(TALL, 60)).toEqual({ chapter: 17, verse: 21, fraction: 0.25 });
  });

  it('is the first verse before any scrolling', () => {
    expect(anchorAt(TALL, 0)).toEqual({ chapter: 17, verse: 20, fraction: 0 });
  });

  it('has nothing to anchor on in an empty pane', () => {
    expect(anchorAt([], 10)).toBeNull();
  });
});

describe('scrollTopFor', () => {
  it('puts the same verse, the same way in, at the top of the other pane', () => {
    const anchor = anchorAt(TALL, 60)!;
    expect(scrollTopFor(SHORT, anchor)).toBe(30);
  });

  it('lands a verse the other translation lacks where it would have been', () => {
    // NIV2011 has no Matthew 17:21: the pane stops at the end of 17:20.
    const without = SHORT.filter((b) => b.verse !== 21).map((b) =>
      b.verse === 22 ? { ...b, top: 20 } : b,
    );
    expect(scrollTopFor(without, { chapter: 17, verse: 21, fraction: 0.5 })).toBe(20);
  });

  it('orders verses across a chapter break', () => {
    const span: VerseBox[] = [
      { chapter: 3, verse: 36, top: 0, height: 30 },
      { chapter: 4, verse: 1, top: 30, height: 30 },
    ];
    expect(scrollTopFor(span, { chapter: 4, verse: 1, fraction: 0 })).toBe(30);
  });

  it('has nowhere to go in an empty pane', () => {
    expect(scrollTopFor([], { chapter: 1, verse: 1, fraction: 0 })).toBeNull();
  });
});
