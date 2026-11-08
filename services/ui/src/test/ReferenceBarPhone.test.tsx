import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import ReferenceBar from '../components/bible/ReferenceBar';
import type { BibleBookInfo, BiblePreferences, BibleVersionInfo } from '../types/api';

vi.mock('../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const BOOKS: BibleBookInfo[] = [
  { osis: 'John', name: 'John', order: 43, chapters: 21 },
  { osis: 'Ps', name: 'Psalms', order: 19, chapters: 150 },
];

const VERSIONS: BibleVersionInfo[] = [
  {
    code: 'kjv',
    name: 'King James Version',
    language: 'en',
    license_class: 'public_domain',
    rights_holder: '',
    installed: true,
    verse_count: 31102,
    imported_at: '2026-01-01T00:00:00',
    editions: 0,
    primary: false,
    provider: '',
    note: '',
  },
];

const PREFERENCES: BiblePreferences = {
  default_version: 'kjv',
  default_edition: '',
  favorite_version: '',
  compare_version: '',
  cross_version_notes: false,
  show_notes: false,
  font_scale: 1,
  line_height: 1.6,
  theme: 'serif',
  read_aloud_voice: '',
  split_view: 'compare',
};

const noop = () => {};

function renderBar() {
  return render(
    <ReferenceBar
      books={BOOKS}
      versions={VERSIONS}
      position={{ book: 'John', chapter: 3, verse: 1 }}
      preferences={PREFERENCES}
      input=""
      onInput={noop}
      onSubmit={noop}
      onPosition={noop}
      onPreferences={noop}
      onStep={noop}
    />,
  );
}

const realMatchMedia = window.matchMedia;

/**
 * jsdom ships no matchMedia, and `services/ui/src/test/setup.ts` does not stub
 * it, so the desktop layout is what every other test sees. This installs one
 * that answers the phone query when asked to.
 */
function pretendPhone(isPhone: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: isPhone && query.includes('max-width'),
    media: query,
    onchange: null,
    addEventListener: noop,
    removeEventListener: noop,
    addListener: noop,
    removeListener: noop,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => {
  window.matchMedia = realMatchMedia;
});

describe('the reference bar on a phone', () => {
  it('is one line: the passage and version pills and a few icons, nothing else', () => {
    pretendPhone(true);
    renderBar();

    expect(screen.getByTestId('bible-phone-bar')).toBeInTheDocument();
    expect(screen.getByTestId('bible-browse-open')).toHaveTextContent('John 3:1');
    expect(screen.getByTestId('bible-version-open')).toBeInTheDocument();
    // The controls are not merely hidden -- they are not in the document
    // until their panel opens, so nothing can be tabbed to or found twice.
    expect(screen.queryByTestId('bible-version-select')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bible-book-select')).not.toBeInTheDocument();
  });

  it('opens the passage chooser as a sheet: search, book and chapter, and the books', () => {
    pretendPhone(true);
    renderBar();

    fireEvent.click(screen.getByTestId('bible-browse-open'));

    const sheet = screen.getByTestId('bible-browse-sheet');
    expect(sheet).toHaveClass('fixed');
    expect(screen.getByTestId('bible-ref-input')).toBeInTheDocument();
    expect(screen.getByTestId('bible-book-select')).toBeInTheDocument();
    // The book in hand is open on its chapters, the chapter in hand marked.
    expect(screen.getByTestId('bible-pick-John-3')).toHaveAttribute('aria-current', 'true');
  });

  it('picks a chapter from the grid and gets out of the way', () => {
    pretendPhone(true);
    const onPosition = vi.fn();
    render(
      <ReferenceBar
        books={BOOKS}
        versions={VERSIONS}
        position={{ book: 'John', chapter: 3, verse: 1 }}
        preferences={PREFERENCES}
        input=""
        onInput={noop}
        onSubmit={noop}
        onPosition={onPosition}
        onPreferences={noop}
      />,
    );

    fireEvent.click(screen.getByTestId('bible-browse-open'));
    fireEvent.click(screen.getByTestId('bible-pick-John-5'));

    expect(onPosition).toHaveBeenCalledWith({ book: 'John', chapter: 5, verse: 1 });
    expect(screen.queryByTestId('bible-browse-sheet')).not.toBeInTheDocument();
  });

  it('closes the sheet once a passage has been chosen', () => {
    pretendPhone(true);
    const onPosition = vi.fn();
    render(
      <ReferenceBar
        books={BOOKS}
        versions={VERSIONS}
        position={{ book: 'John', chapter: 3, verse: 1 }}
        preferences={PREFERENCES}
        input=""
        onInput={noop}
        onSubmit={noop}
        onPosition={onPosition}
        onPreferences={noop}
      />,
    );

    fireEvent.click(screen.getByTestId('bible-browse-open'));
    fireEvent.click(screen.getByTestId('bible-chapter-down'));

    expect(onPosition).toHaveBeenCalledWith({ chapter: 2 });
    expect(screen.queryByTestId('bible-browse-sheet')).not.toBeInTheDocument();
  });

  it('closes the sheet on the scrim without moving the reader', () => {
    pretendPhone(true);
    const onPosition = vi.fn();
    render(
      <ReferenceBar
        books={BOOKS}
        versions={VERSIONS}
        position={{ book: 'John', chapter: 3, verse: 1 }}
        preferences={PREFERENCES}
        input=""
        onInput={noop}
        onSubmit={noop}
        onPosition={onPosition}
        onPreferences={noop}
      />,
    );

    fireEvent.click(screen.getByTestId('bible-browse-open'));
    fireEvent.click(screen.getByTestId('bible-browse-scrim'));

    expect(screen.queryByTestId('bible-browse-sheet')).not.toBeInTheDocument();
    expect(onPosition).not.toHaveBeenCalled();
  });

  it('keeps compare and focus in text settings on a narrow screen', () => {
    pretendPhone(true);
    const onFocus = vi.fn();
    render(
      <ReferenceBar
        books={BOOKS}
        versions={VERSIONS}
        position={{ book: 'John', chapter: 3, verse: 1 }}
        preferences={PREFERENCES}
        input=""
        onInput={noop}
        onSubmit={noop}
        onPosition={noop}
        onPreferences={noop}
        onFocus={onFocus}
      />,
    );

    fireEvent.click(screen.getByTestId('bible-display-toggle'));
    fireEvent.click(screen.getByTestId('bible-focus-open-narrow'));
    expect(onFocus).toHaveBeenCalled();
    expect(screen.queryByTestId('bible-display-panel')).not.toBeInTheDocument();
  });
});

describe('the reference bar everywhere else', () => {
  it('is the same header, its panels dropping down rather than sheets', () => {
    // No stub: this is what jsdom reports, and what a desktop browser does.
    renderBar();

    expect(screen.getByTestId('bible-reference-bar')).toBeInTheDocument();
    expect(screen.queryByTestId('bible-phone-bar')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('bible-version-open'));
    expect(screen.getByTestId('bible-version-select')).toBeInTheDocument();
    expect(screen.getByTestId('bible-version-panel').parentElement).toHaveClass('absolute');
  });
});
