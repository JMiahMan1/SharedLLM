import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FocusReader from './FocusReader';
import { ambienceFor } from './focusAmbience';
import type { BibleVerse } from '../../types/api';

const VERSES: BibleVerse[] = [
  {
    version: 'nkjv',
    osis: 'Ps',
    book_name: 'Psalms',
    chapter: 23,
    verse: 1,
    reference: 'Psalms 23:1',
    text: 'The LORD is my shepherd; I shall not want.',
  },
  {
    version: 'nkjv',
    osis: 'Ps',
    book_name: 'Psalms',
    chapter: 23,
    verse: 2,
    reference: 'Psalms 23:2',
    text: 'He makes me to lie down in green pastures.',
  },
];

function renderFocus(over: Partial<React.ComponentProps<typeof FocusReader>> = {}) {
  const handlers = { onPrev: vi.fn(), onNext: vi.fn(), onClose: vi.fn() };
  render(
    <FocusReader
      reference="Psalms 23"
      verses={VERSES}
      marks={[]}
      fontScale={1}
      lineHeight={1.6}
      theme="serif"
      {...handlers}
      {...over}
    />,
  );
  return handlers;
}

describe('reading a chapter as a page', () => {
  it('shows the whole chapter with its own reference', () => {
    renderFocus();
    expect(screen.getByTestId('bible-focus-reference')).toHaveTextContent('Psalms 23');
    expect(screen.getByTestId('bible-focus-verse-23-1')).toHaveTextContent('I shall not want');
    expect(screen.getByTestId('bible-focus-verse-23-2')).toHaveTextContent('green pastures');
  });

  it('takes the reader on to the next chapter and back', async () => {
    const handlers = renderFocus();
    await userEvent.click(screen.getByTestId('bible-focus-next'));
    expect(handlers.onNext).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByTestId('bible-focus-prev'));
    expect(handlers.onPrev).toHaveBeenCalledTimes(1);
  });

  it('leaves focus mode from the close control', async () => {
    const handlers = renderFocus();
    await userEvent.click(screen.getByTestId('bible-focus-close'));
    expect(handlers.onClose).toHaveBeenCalledTimes(1);
  });

  it('hides the controls when the text is tapped, and brings them back', async () => {
    renderFocus();
    const header = screen.getByTestId('bible-focus-reference').closest('div')!.parentElement!;
    expect(header.className).toContain('opacity-100');

    await userEvent.click(screen.getByTestId('bible-focus-text'));
    expect(header.className).toContain('opacity-0');

    await userEvent.click(screen.getByTestId('bible-focus-text'));
    expect(header.className).toContain('opacity-100');
  });

  it('says it is opening the chapter rather than showing a blank page', () => {
    renderFocus({ verses: [], loading: true });
    expect(screen.getByTestId('bible-focus-loading')).toBeInTheDocument();
  });

  it('tints a verse that is highlighted, and leaves the rest alone', () => {
    renderFocus({
      marks: [
        {
          id: 1,
          version_code: 'nkjv',
          osis: 'Ps',
          book_name: 'Psalms',
          chapter: 23,
          verse_start: 2,
          verse_end: null,
          kind: 'highlight',
          color: 'green',
          note_path: '',
          note_preview: '',
          created_at: null,
          updated_at: null,
        },
      ],
    });
    expect(screen.getByTestId('bible-focus-verse-23-2').className).toContain('emerald');
    expect(screen.getByTestId('bible-focus-verse-23-1').className).not.toContain('emerald');
  });
});

describe('the wash behind the text', () => {
  it('is a night colour before dawn and after the evening', () => {
    expect(ambienceFor(2)).toContain('indigo');
    expect(ambienceFor(22)).toContain('violet');
  });

  it('lightens through the working day', () => {
    expect(ambienceFor(7)).toContain('amber');
    expect(ambienceFor(12)).toContain('sky');
    expect(ambienceFor(19)).toContain('orange');
  });

  it('depends only on the hour it is given', () => {
    expect(ambienceFor(12)).toBe(ambienceFor(12));
    expect(ambienceFor(12)).not.toBe(ambienceFor(22));
  });
});
