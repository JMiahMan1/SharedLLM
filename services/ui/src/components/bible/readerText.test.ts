import { describe, expect, it } from 'vitest';

import { cleanBookPage } from './readerText';

describe('cleanBookPage', () => {
  it('drops the printed page number and joins wrapped lines into paragraphs', () => {
    const page = cleanBookPage(
      [
        '12',
        '',
        'Week 1: Introduction to Walk With Jesus',
        '',
        'Jesus said, follow me.',
        'This sentence was wrapped across',
        'several lines in the PDF.',
        '',
        'A second paragraph starts here.',
      ].join('\n'),
    );

    expect(page.heading).toBe('Week 1: Introduction to Walk With Jesus');
    expect(page.paragraphs).toEqual([
      'Jesus said, follow me. This sentence was wrapped across several lines in the PDF.',
      'A second paragraph starts here.',
    ]);
  });

  it('rejoins words hyphenated across a line break', () => {
    const page = cleanBookPage(
      ['1', '', 'Week 1: A Heading', '', 'This is a para-', 'graph about rest.'].join('\n'),
    );

    expect(page.paragraphs).toEqual(['This is a paragraph about rest.']);
  });

  it('keeps a long opening block as prose instead of promoting it to a heading', () => {
    const opening = 'This opening paragraph is long enough that it is clearly body text rather than a title, and it ends with a full stop.';
    const page = cleanBookPage([opening, '', 'A second paragraph.'].join('\n'));

    expect(page.heading).toBeNull();
    expect(page.paragraphs).toEqual([opening, 'A second paragraph.']);
  });
});
