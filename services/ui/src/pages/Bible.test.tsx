import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import Bible from './Bible';
import { api } from '../services/api';
import type {
  BibleAchievementsResponse,
  BibleBookInfo,
  BibleDailyResponse,
  BibleDevotionalResponse,
  BibleEditionInfo,
  BibleMark,
  BiblePassage,
  BibleSearchResult,
  BibleStateResponse,
  BibleStats,
  BibleVersionInfo,
} from '../types/api';

vi.mock('../hooks/useHaptics', () => ({
  useHaptics: () => ({ trigger: vi.fn(), isEnabled: () => true, setEnabled: vi.fn() }),
}));

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getBibleState: vi.fn(),
      getBibleVersions: vi.fn(),
      getBibleBooks: vi.fn(),
      getBibleMarks: vi.fn(),
      getBiblePassage: vi.fn(),
      searchBible: vi.fn(),
      getBibleDaily: vi.fn(),
      getBibleAchievements: vi.fn(),
      getBibleStats: vi.fn(),
      getBibleActivityFeed: vi.fn(),
      putBibleState: vi.fn(),
      putBibleMark: vi.fn(),
      deleteBibleMark: vi.fn(),
      recordBibleEvent: vi.fn(),
      createNote: vi.fn(),
      getBlbLink: vi.fn(),
      getBibleStudyNotes: vi.fn(),
      getBibleEditions: vi.fn(),
    },
  };
});

const mocked = vi.mocked(api);

const VERSIONS: BibleVersionInfo[] = [
  { code: 'kjv', name: 'King James Version', language: 'en', license_class: 'public_domain', rights_holder: '', installed: true, verse_count: 31102, imported_at: '2026-01-01T00:00:00', editions: 0, primary: false, provider: '', note: '' },
  { code: 'asv', name: 'American Standard Version', language: 'en', license_class: 'public_domain', rights_holder: '', installed: true, verse_count: 31086, imported_at: '2026-01-01T00:00:00', editions: 0, primary: false, provider: '', note: '' },
  {
    code: 'esv',
    name: 'English Standard Version',
    language: 'en',
    license_class: 'licensed',
    rights_holder: 'Crossway',
    installed: false,
    verse_count: 0,
    imported_at: null,
    editions: 1,
    primary: false,
    provider: '',
    note: 'English Standard Version (Crossway) is copyrighted, so its text is not bundled.',
  },
];

const BOOKS: BibleBookInfo[] = [
  { osis: 'Gen', name: 'Genesis', order: 1, chapters: 50 },
  { osis: 'John', name: 'John', order: 43, chapters: 21 },
  { osis: 'Ps', name: 'Psalms', order: 19, chapters: 150 },
];

const STATE = (overrides: Partial<BibleStateResponse> = {}): BibleStateResponse => ({
  username: 'tester',
  position: { book: 'Ps', chapter: 23, verse: 1 },
  preferences: {
    default_version: 'kjv',
    default_edition: '',
    favorite_version: '',
    compare_version: '',
    cross_version_notes: false,
    font_scale: 1,
    line_height: 1.6,
    theme: 'serif',
    read_aloud_voice: '',
    split_view: 'compare',
  },
  ...overrides,
});

const PASSAGE = (
  reference: string,
  chapter: number,
  text: string,
  version = 'kjv',
): BiblePassage => ({
  version,
  requested: reference,
  reference,
  spans: [
    {
      book: reference.startsWith('Ps') ? 'Ps' : 'John',
      book_name: reference.startsWith('Ps') ? 'Psalms' : 'John',
      chapter_start: chapter,
      chapter_end: chapter,
      verse_start: 1,
      verse_end: 3,
      whole_book: false,
      display: reference,
    },
  ],
  verses: [1, 2, 3].map((v) => ({
    version,
    osis: reference.startsWith('Ps') ? 'Ps' : 'John',
    book_name: reference.startsWith('Ps') ? 'Psalms' : 'John',
    chapter,
    verse: v,
    reference: `${reference.split(':')[0]}:${v}`,
    text: `${text} (${v})`,
  })),
  count: 3,
});

const DAILY = (overrides: Partial<BibleDailyResponse> = {}): BibleDailyResponse => ({
  day: '2026-10-03',
  verse_of_day: {
    day: '2026-10-03',
    day_of_year: 276,
    version: 'kjv',
    scope: 'all',
    osis: 'John',
    book: 'John',
    book_name: 'John',
    chapter: 3,
    verse: 16,
    reference: 'John 3:16',
    text: 'For God so loved the world.',
  },
  devotional: {
    source: 'blb',
    entry: {
      source: 'blb',
      work: 'dbdbg',
      title: 'Day by Day by Grace',
      day_of_year: 276,
      kind: 'link',
      reference: '',
      text: '',
      url: 'https://bible.test/devotionals/dbdbg/view.cfm?doy=276',
    },
    skipped: [],
  },
  sources: [],
  streaks: { read_streak_current: 4, read_streak_longest: 11, open_streak_current: 6, days_read: 30, chapters_read: 122 },
  position: { book: 'Ps', chapter: 23, verse: 1 },
  ...overrides,
});

const DEVOTIONAL_MISSING = (): BibleDevotionalResponse => ({
  source: 'blb',
  entry: null,
  skipped: [{ source: 'local', reason: 'bible_devotional_dir is not configured' }],
  reason: 'No devotional source had an entry for 2026-10-03.',
});

const STATS = (): BibleStats => ({
  username: 'tester',
  read_streak_current: 4,
  read_streak_longest: 11,
  open_streak_current: 6,
  open_streak_longest: 20,
  days_read: 30,
  metrics: { chapters_total: 122, books_read: 5 },
  days_opened: 40,
  marks: 3,
  highlights: 2,
  bookmarks: 1,
  position: { book: 'Ps', chapter: 23, verse: 1 },
  last_read_at: '2026-10-03T06:00:00',
  window: {},
});

const ACHIEVEMENTS = (): BibleAchievementsResponse => ({
  username: 'tester',
  points: 45,
  newly_earned: [
    { id: 'read_streak_7', name: 'Seven Day Streak', description: 'Read seven days in a row', points: 15, earned_on: '2026-10-03' },
  ],
  earned: [
    { id: 'read_streak_7', name: 'Seven Day Streak', description: 'Read seven days in a row', points: 15, earned_on: '2026-10-03' },
  ],
  next_up: [
    { id: 'chapters_total_100', name: 'Century of Chapters', description: 'Read 100 chapters', points: 20, current: 22, target: 100, remaining: 78, percent: 22, unit: 'chapters' },
  ],
  stars: { granted: 1, status: 'granted' },
  pending_rules: ['quiz_perfect'],
  announced: { posted: 1, status: 'posted' },
});

const MARKS = (): { marks: BibleMark[] } => ({
  marks: [
    {
      id: 7,
      version_code: 'kjv',
      osis: 'Ps',
      book_name: 'Psalms',
      chapter: 23,
      verse_start: 1,
      verse_end: 1,
      kind: 'highlight',
      color: 'yellow',
      note_path: '',
      note_preview: '',
      created_at: '2026-10-02T00:00:00',
      updated_at: '2026-10-02T00:00:00',
    },
  ],
});

const renderPage = (entry = '/bible') => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Bible />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

beforeEach(() => {
  mocked.getBibleState.mockReset().mockResolvedValue(STATE());
  mocked.getBibleVersions.mockReset().mockResolvedValue({ versions: VERSIONS });
  mocked.getBibleBooks.mockReset().mockResolvedValue({ version: 'kjv', books: BOOKS });
  mocked.getBibleMarks.mockReset().mockResolvedValue(MARKS());
  mocked.getBiblePassage
    .mockReset()
    .mockImplementation(async (ref: string, version = 'kjv') => {
      const chapter = Number(ref.split(/\s+/)[1]?.split(':')[0] ?? 1);
      const text =
        version === 'asv'
          ? 'The Lord is my shepherd, I shall not want'
          : ref.startsWith('Ps')
            ? 'The LORD is my shepherd'
            : 'For God so loved the world';
      const passage = PASSAGE(ref.startsWith('Ps') ? 'Psalms 23' : ref, chapter, text, version);
      // One verse the second translation genuinely does not have, so a gap in a
      // comparison is exercised rather than assumed.
      return version === 'asv'
        ? { ...passage, verses: passage.verses.filter((v) => v.verse !== 3), count: 2 }
        : passage;
    });
  mocked.searchBible.mockReset().mockResolvedValue({ query: 'faith', version: 'kjv', results: [], count: 0 } as BibleSearchResult);
  mocked.getBibleDaily.mockReset().mockResolvedValue(DAILY());
  mocked.getBibleAchievements.mockReset().mockResolvedValue(ACHIEVEMENTS());
  mocked.getBibleStats.mockReset().mockResolvedValue(STATS());
  mocked.getBibleActivityFeed.mockReset().mockResolvedValue({
    entries: [
      { username: 'sam', days_read: 9, chapters_read: 41, books_read: 2, read_streak_current: 9, read_streak_longest: 9, achievements: [], points: 0 },
    ],
    members: ['sam', 'tester'],
  });
  mocked.putBibleState.mockReset().mockResolvedValue(undefined as never);
  mocked.putBibleMark.mockReset().mockResolvedValue({ mark: MARKS().marks[0] });
  mocked.deleteBibleMark.mockReset().mockResolvedValue({ deleted: 1 });
  mocked.recordBibleEvent.mockReset().mockResolvedValue({ ok: true });
  mocked.createNote.mockReset().mockResolvedValue({ status: 'SUCCESS', message: 'saved', service: 'note' });
  mocked.getBlbLink.mockReset().mockResolvedValue({ ref: 'John 3:16', url: 'https://bible.test/jhn/3/16', tool: null });
  mocked.getBibleEditions.mockReset().mockResolvedValue({
    version: 'kjv',
    default: 'kjv',
    editions: [],
    other_translations: [],
  });
  mocked.getBibleStudyNotes.mockReset().mockResolvedValue({
    version: 'kjv',
    edition: 'kjv',
    edition_name: 'King James Version',
    cross_version: false,
    other_translations: [],
    editions: [],
    requested: 'Psalms 23',
    reference: 'Psalms 23',
    kinds: [],
    available_kinds: {},
    notes: [],
    count: 0,
    note: 'kjv carries no study notes',
  });
});

describe('Bible page', () => {
  it('opens on the chapter the reader last had open', async () => {
    renderPage();

    await waitFor(() => expect(screen.getByTestId('bible-chapter-heading')).toHaveTextContent('Psalms 23'));
    expect(mocked.getBiblePassage).toHaveBeenCalledWith('Ps 23', 'kjv');
  });

  it('records the app open once so the streak has something to count', async () => {
    renderPage();
    await screen.findByTestId('bible-chapter-heading');
    expect(mocked.recordBibleEvent).toHaveBeenCalledWith('app_open');
  });

  it('follows a deep link straight to the passage that was linked', async () => {
    renderPage('/bible?ref=John%203:16');

    await waitFor(() => expect(screen.getByTestId('bible-chapter-heading')).toHaveTextContent('John 3'));
    expect(mocked.getBiblePassage).toHaveBeenCalledWith('John 3:16', 'kjv');
  });

  it('writes the chapter it actually showed back so another device resumes there', async () => {
    renderPage('/bible?ref=John%203:16');

    await screen.findByTestId('bible-chapter-heading');
    await waitFor(() =>
      expect(mocked.putBibleState).toHaveBeenCalledWith({ book: 'John', chapter: 3, verse: 1 })
    );
    expect(mocked.recordBibleEvent).toHaveBeenCalledWith('chapter_complete', 'John 3');
  });

  it('saves a display change to the server instead of only to this device', async () => {
    renderPage();

    await screen.findByTestId('bible-chapter-heading');
    fireEvent.click(screen.getByTestId('bible-display-toggle'));
    fireEvent.click(screen.getByTestId('bible-theme-sans'));

    await waitFor(() => expect(mocked.putBibleState).toHaveBeenCalledWith({ theme: 'sans' }));
  });

  it('treats a word as a search and a reference as a passage', async () => {
    renderPage();

    await screen.findByTestId('bible-chapter-heading');
    fireEvent.change(screen.getByTestId('bible-ref-input'), { target: { value: 'shepherd' } });
    fireEvent.click(screen.getByTestId('bible-ref-go'));

    expect(await screen.findByTestId('bible-search-results')).toHaveTextContent('Results for “shepherd”');
    await waitFor(() => expect(mocked.searchBible).toHaveBeenCalledWith('shepherd', { version: 'kjv', limit: 40 }));
  });

  it('does not search for a one-letter word', async () => {
    renderPage();

    await screen.findByTestId('bible-chapter-heading');
    fireEvent.change(screen.getByTestId('bible-ref-input'), { target: { value: 'a' } });
    fireEvent.click(screen.getByTestId('bible-ref-go'));

    await screen.findByTestId('bible-search-results');
    expect(mocked.searchBible).not.toHaveBeenCalled();
  });

  it('steps to the next chapter, and rolls into the next book at the end', async () => {
    renderPage();

    await waitFor(() => expect(screen.getByTestId('bible-chapter-heading')).toHaveTextContent('Psalms 23'));
    fireEvent.click(screen.getByTestId('bible-next-chapter'));

    await waitFor(() => expect(mocked.getBiblePassage).toHaveBeenCalledWith('Ps 24', 'kjv'));
  });

  it('highlights a tapped verse and records the tap', async () => {
    renderPage();

    fireEvent.click(await screen.findByTestId('bible-verse-23-3'));
    fireEvent.click(await screen.findByTestId('bible-action-highlight'));

    await waitFor(() =>
      expect(mocked.putBibleMark).toHaveBeenCalledWith({ ref: 'Psalms 23:3', kind: 'highlight', color: 'yellow', version_code: 'kjv' })
    );
    expect(mocked.recordBibleEvent).toHaveBeenCalledWith('verse_tapped', 'Psalms 23:3');
  });

  it('does not let a highlight on one verse swallow the next one', async () => {
    renderPage();

    fireEvent.click(await screen.findByTestId('bible-verse-23-2'));
    fireEvent.click(await screen.findByTestId('bible-action-highlight'));

    await waitFor(() =>
      expect(mocked.putBibleMark).toHaveBeenCalledWith({ ref: 'Psalms 23:2', kind: 'highlight', color: 'yellow', version_code: 'kjv' })
    );
    expect(mocked.deleteBibleMark).not.toHaveBeenCalled();
  });

  it('removes a single-verse highlight when the verse is already highlighted', async () => {
    renderPage();

    fireEvent.click(await screen.findByTestId('bible-verse-23-1'));
    fireEvent.click(await screen.findByTestId('bible-action-highlight'));

    await waitFor(() => expect(mocked.deleteBibleMark).toHaveBeenCalledWith(7));
    expect(mocked.putBibleMark).not.toHaveBeenCalled();
  });

  it('never tells a reader to run a command when no corpus is installed', async () => {
    mocked.getBibleVersions.mockResolvedValue({ versions: [] });

    renderPage();

    const notice = await screen.findByTestId('bible-no-versions');
    expect(notice).toHaveTextContent('Admin > Bible');
    expect(notice).not.toHaveTextContent('python');
    expect(notice).not.toHaveTextContent('import_corpus');
    expect(notice).not.toHaveTextContent('import_pdf');
    expect(notice.querySelector('code')).toBeNull();
  });

  it("shows the server's own reason rather than a local guess", async () => {
    mocked.getBibleVersions.mockResolvedValue({
      versions: [],
      message: 'No Bible text is installed on this server. The kjv translation is meant to be available as the fallback.',
    });

    renderPage();

    expect(await screen.findByTestId('bible-no-versions')).toHaveTextContent(
      'kjv translation is meant to be available as the fallback'
    );
  });

  it('keeps reading when only catalogued-but-uninstalled translations exist', async () => {
    mocked.getBibleVersions.mockResolvedValue({ versions: VERSIONS.filter((v) => !v.installed) });

    renderPage();

    expect(await screen.findByTestId('bible-no-versions')).toBeInTheDocument();
  });

  it('falls back to an installed translation and says why when the saved one is missing', async () => {
    mocked.getBibleState.mockResolvedValue(
      STATE({
        preferences: { ...STATE().preferences, default_version: 'esv' },
      })
    );

    renderPage();

    const notice = await screen.findByTestId('bible-saved-version-missing');
    expect(notice).toHaveTextContent('English Standard Version');
    expect(notice).toHaveTextContent('not on this server');
    expect(notice).toHaveTextContent('copyrighted');
    await screen.findByTestId('bible-chapter-heading');
    expect(mocked.getBiblePassage).toHaveBeenCalledWith(expect.any(String), 'kjv');
  });

  it('offers uninstalled translations in the picker with a reason', async () => {
    mocked.getBibleVersions.mockResolvedValue({ versions: VERSIONS });

    renderPage('/bible?ref=John%203');

    const select = (await screen.findByTestId('bible-version-select')) as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels.some((l) => l?.includes('English Standard Version'))).toBe(true);
    expect(labels.some((l) => l?.includes('not on this server'))).toBe(true);
  });

  it('surfaces the reference the server could not parse', async () => {
    mocked.getBiblePassage.mockRejectedValue(new Error('Unparseable reference: "John 3:16-4"'));

    renderPage('/bible?ref=John%203:16');

    expect(await screen.findByTestId('bible-reader-error')).toHaveTextContent('Unparseable reference');
  });

  it('shows the devotional and the streak on the Today tab', async () => {
    renderPage();

    fireEvent.click(screen.getByTestId('bible-tab-today'));

    expect(await screen.findByText('Day by Day by Grace')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('bible-today')).toHaveTextContent('John 3:16'));
    expect(screen.getByTestId('bible-today')).toHaveTextContent('4 day streak');
  });

  it('names every devotional source that was skipped instead of showing nothing', async () => {
    mocked.getBibleDaily.mockResolvedValue(DAILY({ devotional: DEVOTIONAL_MISSING() }));

    renderPage();
    fireEvent.click(screen.getByTestId('bible-tab-today'));

    expect(await screen.findByTestId('bible-devotional-skipped')).toHaveTextContent('bible_devotional_dir is not configured');
    await waitFor(() => expect(screen.getByTestId('bible-today')).toHaveTextContent('No devotional source had an entry'));
  });

  it('shows stats, a newly earned badge and the family reading feed on Progress', async () => {
    renderPage();

    fireEvent.click(screen.getByTestId('bible-tab-progress'));

    expect(await screen.findByTestId('bible-new-badge')).toHaveTextContent('Seven Day Streak');
    expect(await screen.findByText('sam')).toBeInTheDocument();
  });

  it('explains that later-phase badge rules are not counted yet', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('bible-tab-progress'));

    expect(await screen.findByText(/badge[s]? arrive with those features/)).toBeInTheDocument();
  });

  it('keeps Read the default tab even when the devotional link was the entry point', async () => {
    renderPage();

    expect((await screen.findByTestId('bible-tab-read')).getAttribute('aria-selected')).toBe('true');
  });

  it('marks a verse that carries study material and leaves the others unmarked', async () => {
    mocked.getBibleStudyNotes.mockResolvedValue({
      version: 'kjv',
      edition: 'kjv',
      edition_name: 'King James Version',
      cross_version: false,
      other_translations: [],
      editions: [],
      requested: 'John 3',
      reference: 'John 3',
      kinds: ['commentary'],
      available_kinds: { commentary: 4 },
      notes: [
        {
          osis: 'John',
          book: 'John',
          chapter: 3,
          verse: 2,
          reference: 'John 3:2',
          kind: 'commentary',
          ordinal: 1,
          body: 'God so loved…',
          source: 'test',
          version: 'kjv',
          version_name: 'King James Version',
          edition: 'kjv',
          edition_name: 'King James Version',
        },
      ],
      count: 1,
      note: null,
    });

    renderPage('/bible?ref=John%203:16');

    await waitFor(() =>
      expect(mocked.getBibleStudyNotes).toHaveBeenCalledWith('John 3', 'kjv', {
        edition: undefined,
        crossVersion: false,
      }),
    );
    expect(await screen.findByTestId('bible-verse-studied-3-2')).toBeInTheDocument();
    expect(screen.queryByTestId('bible-verse-studied-3-3')).not.toBeInTheDocument();
    expect(screen.getByTestId('bible-verse-3-2')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('Has study notes'),
    );
    expect(screen.getByTestId('bible-verse-3-3')).toHaveAttribute(
      'aria-label',
      expect.not.stringContaining('Has study notes'),
    );
  });

  it('does not ask for study notes while a search result list is showing', async () => {
    renderPage();

    await screen.findByTestId('bible-chapter-heading');
    await waitFor(() =>
      expect(mocked.getBibleStudyNotes).toHaveBeenCalledWith('Psalms 23', 'kjv', {
        edition: undefined,
        crossVersion: false,
      }),
    );
    mocked.getBibleStudyNotes.mockClear();

    fireEvent.change(screen.getByTestId('bible-ref-input'), { target: { value: 'shepherd' } });
    fireEvent.click(screen.getByTestId('bible-ref-go'));

    await screen.findByTestId('bible-search-results');
    expect(mocked.getBibleStudyNotes).not.toHaveBeenCalled();
  });

  describe('several study Bibles over one translation', () => {
    const EDITIONS: BibleEditionInfo[] = [
      {
        code: 'nkjv',
        version: 'nkjv',
        name: 'New King James Version',
        publisher: 'Thomas Nelson',
        language: 'en',
        license_class: 'licensed',
        rights_holder: 'Thomas Nelson',
        installed: true,
        note_count: 12055,
        note_kinds: ['commentary'],
        note: '',
      },
      {
        code: 'nkjv-macarthur',
        version: 'nkjv',
        name: 'The MacArthur Study Bible',
        publisher: 'MacArthur Bible Ministries',
        language: 'en',
        license_class: 'licensed',
        rights_holder: 'MacArthur Bible Ministries',
        installed: true,
        note_count: 9800,
        note_kinds: ['commentary', 'footnote'],
        note: '',
      },
    ];

    beforeEach(() => {
      mocked.getBibleState.mockResolvedValue(
        STATE({
          preferences: {
            ...STATE().preferences,
            default_version: 'nkjv',
            default_edition: 'nkjv-macarthur',
          },
        }),
      );
      mocked.getBibleVersions.mockResolvedValue({
        versions: [
          { ...VERSIONS[0], code: 'nkjv', name: 'New King James Version', editions: 2 },
          ...VERSIONS.slice(1),
        ],
      });
      mocked.getBibleEditions.mockResolvedValue({
        version: 'nkjv',
        default: 'nkjv',
        editions: EDITIONS,
        other_translations: [],
      });
    });

    it('asks the server for the study Bible the reader chose, not just the translation', async () => {
      renderPage();
      await screen.findByTestId('bible-chapter-heading');
      await waitFor(() =>
        expect(mocked.getBibleEditions).toHaveBeenCalledWith('nkjv'),
      );
      await waitFor(() =>
        expect(mocked.getBibleStudyNotes).toHaveBeenCalledWith(
          'Psalms 23',
          'nkjv',
          { edition: 'nkjv-macarthur', crossVersion: false },
        ),
      );
    });

    it('offers both study Bibles over the same translation, with how much each carries', async () => {
      renderPage();
      const picker = (await screen.findByTestId('bible-edition-select')) as HTMLSelectElement;
      expect(picker.value).toBe('nkjv-macarthur');
      const options = Array.from(picker.options).map((o) => o.textContent);
      expect(options).toEqual([
        'New King James Version · 12,055 notes',
        'The MacArthur Study Bible · 9,800 notes',
      ]);
    });

    it('remembers a study Bible change so the next device opens the same one', async () => {
      renderPage();
      fireEvent.change(await screen.findByTestId('bible-edition-select'), {
        target: { value: 'nkjv' },
      });
      await waitFor(() =>
        expect(mocked.putBibleState).toHaveBeenCalledWith({ default_edition: 'nkjv' }),
      );
      await waitFor(() =>
        expect(mocked.getBibleStudyNotes).toHaveBeenCalledWith('Psalms 23', 'nkjv', {
          edition: 'nkjv',
          crossVersion: false,
        }),
      );
    });

    it('marks the translation in hand as a favourite', async () => {
      renderPage();
      const toggle = await screen.findByTestId('bible-favorite-toggle');
      expect(toggle).toHaveAttribute('aria-pressed', 'false');
      const shown = (await screen.findByTestId('bible-version-select')) as HTMLSelectElement;
      const code = shown.value;
      fireEvent.click(toggle);
      await waitFor(() =>
        expect(mocked.putBibleState).toHaveBeenCalledWith({ favorite_version: code }),
      );
    });

    it('takes the favourite back off the same button', async () => {
      const shown = 'nkjv';
      mocked.getBibleState.mockResolvedValue(
        STATE({ preferences: { ...STATE().preferences, default_version: shown, favorite_version: shown } }),
      );
      renderPage();
      const toggle = await screen.findByTestId('bible-favorite-toggle');
      await waitFor(() => expect(toggle).toHaveAttribute('aria-pressed', 'true'));
      await waitFor(() =>
        expect(screen.getByTestId('bible-favorite-note')).toHaveTextContent(
          'New King James Version',
        ),
      );
      fireEvent.click(toggle);
      await waitFor(() =>
        expect(mocked.putBibleState).toHaveBeenCalledWith({ favorite_version: '' }),
      );
    });
  });

  it('keeps notes from another translation off until the reader asks for them', async () => {
    mocked.getBibleStudyNotes.mockResolvedValue({
      version: 'nkjv',
      edition: 'nkjv',
      edition_name: 'New King James Version',
      cross_version: false,
      other_translations: [
        {
          version: 'niv',
          version_name: 'New International Version',
          edition: 'niv-life',
          edition_name: 'Life Application Study Bible',
          note_count: 11000,
        },
      ],
      editions: [],
      requested: 'John 3',
      reference: 'John 3',
      kinds: ['commentary'],
      available_kinds: { commentary: 4 },
      notes: [],
      count: 0,
      note: 'Ask for notes from other translations to add the NIV Life Application Study Bible.',
    });

    renderPage('/bible?ref=John%203:16');
    fireEvent.click(await screen.findByTestId('bible-verse-3-2'));
    fireEvent.click(await screen.findByTestId('bible-action-study'));

    const toggle = await screen.findByTestId('bible-study-cross-version');
    expect(toggle).not.toBeChecked();
    expect(await screen.findByTestId('bible-study-empty')).toHaveTextContent(
      'NIV Life Application Study Bible',
    );

    await waitFor(() =>
      expect(mocked.getBibleStudyNotes).toHaveBeenCalledWith(
        'John 3:2',
        'kjv',
        expect.objectContaining({ crossVersion: false }),
      ),
    );

    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).toBeChecked());

    await waitFor(() =>
      expect(mocked.putBibleState).toHaveBeenCalledWith({ cross_version_notes: true }),
    );
    await waitFor(() =>
      expect(mocked.getBibleStudyNotes).toHaveBeenCalledWith(
        'John 3:2',
        'kjv',
        expect.objectContaining({ crossVersion: true }),
      ),
    );
  });
});
describe('comparing two translations', () => {
  const openCompare = async () => {
    renderPage();
    await screen.findByTestId('bible-chapter-heading');
    const toggle = await screen.findByTestId('bible-compare-toggle');
    fireEvent.click(toggle);
    return toggle;
  };

  it('offers the comparison only for translations other than the one being read', async () => {
    await openCompare();

    // kjv is what the reader is reading, so asv is the only thing left to
    // compare against -- offering kjv would render the same words twice.
    await waitFor(() => expect(mocked.putBibleState).toHaveBeenCalledWith({ compare_version: 'asv' }));
    const select = await screen.findByTestId('bible-compare-select');
    expect(within(select).queryByRole('option', { name: /King James/ })).toBeNull();
    expect(within(select).getByRole('option', { name: /American Standard/ })).toBeTruthy();
  });

  it('shows the second translation beside the first', async () => {
    mocked.getBibleState.mockResolvedValue(
      STATE({ preferences: { ...STATE().preferences, compare_version: 'asv' } }),
    );
    renderPage();

    expect(await screen.findByTestId('bible-compare-legend')).toHaveTextContent(
      'American Standard Version alongside',
    );
    expect(await screen.findByTestId('bible-compare-23-1')).toHaveTextContent(
      'The Lord is my shepherd, I shall not want (1)',
    );
    // The reader's own text is still there and is still the primary one.
    expect(screen.getByTestId('bible-verse-23-1')).toHaveTextContent('The LORD is my shepherd (1)');
    await waitFor(() => expect(mocked.getBiblePassage).toHaveBeenCalledWith('Ps 23', 'asv'));
  });

  it('says so when the second translation has no such verse', async () => {
    mocked.getBibleState.mockResolvedValue(
      STATE({ preferences: { ...STATE().preferences, compare_version: 'asv' } }),
    );
    renderPage();

    expect(await screen.findByTestId('bible-compare-missing-23-3')).toHaveTextContent(
      'Not in this translation.',
    );
    // Absent, not dropped: the verse row itself is still rendered.
    expect(screen.getByTestId('bible-verse-23-3')).toBeTruthy();
  });

  it('stops comparing without disturbing the translation being read', async () => {
    mocked.getBibleState.mockResolvedValue(
      STATE({ preferences: { ...STATE().preferences, compare_version: 'asv' } }),
    );
    renderPage();

    const toggle = await screen.findByTestId('bible-compare-toggle');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(toggle);

    await waitFor(() => expect(mocked.putBibleState).toHaveBeenCalledWith({ compare_version: '' }));
    await waitFor(() => expect(screen.queryByTestId('bible-compare-legend')).toBeNull());
    expect(screen.queryByTestId('bible-compare-select')).toBeNull();
  });
});
