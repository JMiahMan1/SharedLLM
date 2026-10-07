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
  it('shows one line with the passage in hand, not the whole control panel', () => {
    pretendPhone(true);
    renderBar();

    expect(screen.getByTestId('bible-phone-bar')).toBeInTheDocument();
    expect(screen.getByTestId('bible-browse-open')).toHaveTextContent('John 3');
    // The rest of the controls are not merely hidden -- they are not in the
    // document, so nothing can be tabbed to or found twice.
    expect(screen.queryByTestId('bible-reference-bar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bible-version-select')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bible-book-select')).not.toBeInTheDocument();
  });

  it('keeps stepping chapters available without opening anything', () => {
    pretendPhone(true);
    const onStep = vi.fn();
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
        onStep={onStep}
      />,
    );

    fireEvent.click(screen.getByTestId('bible-next-chapter'));
    expect(onStep).toHaveBeenCalledWith(1);
    expect(screen.queryByTestId('bible-browse-sheet')).not.toBeInTheDocument();
  });

  it('opens the chooser as a sheet holding every control', () => {
    pretendPhone(true);
    renderBar();

    fireEvent.click(screen.getByTestId('bible-browse-open'));

    expect(screen.getByTestId('bible-browse-sheet')).toBeInTheDocument();
    expect(screen.getByTestId('bible-reference-bar')).toBeInTheDocument();
    expect(screen.getByTestId('bible-version-select')).toBeInTheDocument();
    expect(screen.getByTestId('bible-book-select')).toBeInTheDocument();
    expect(screen.getByTestId('bible-ref-input')).toBeInTheDocument();
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
        onStep={noop}
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
        onStep={noop}
      />,
    );

    fireEvent.click(screen.getByTestId('bible-browse-open'));
    fireEvent.click(screen.getByTestId('bible-browse-scrim'));

    expect(screen.queryByTestId('bible-browse-sheet')).not.toBeInTheDocument();
    expect(onPosition).not.toHaveBeenCalled();
  });
});

describe('the reference bar everywhere else', () => {
  it('is the full bar, with no phone header and no sheet', () => {
    // No stub: this is what jsdom reports, and what a desktop browser does.
    renderBar();

    expect(screen.getByTestId('bible-reference-bar')).toBeInTheDocument();
    expect(screen.getByTestId('bible-version-select')).toBeInTheDocument();
    expect(screen.queryByTestId('bible-phone-bar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bible-browse-sheet')).not.toBeInTheDocument();
  });
});
