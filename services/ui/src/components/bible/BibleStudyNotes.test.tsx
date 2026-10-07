import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import BibleStudyNotes from './BibleStudyNotes';
import VerseActionSheet from './VerseActionSheet';
import type { BibleEditionInfo, BibleStudyNotesResponse, BibleVerse } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBibleStudyNotes: vi.fn(),
  recordBibleEvent: vi.fn(),
  createNote: vi.fn(),
  getBlbLink: vi.fn(),
  putBibleMark: vi.fn(),
  readNote: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const VERSE: BibleVerse = {
  version: 'nkjv',
  osis: 'John',
  book_name: 'John',
  chapter: 3,
  verse: 16,
  reference: 'John 3:16',
  text: 'For God so loved the world that He gave His only begotten Son…',
};

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

const NOTE_FIELDS = {
  version: 'nkjv',
  version_name: 'New King James Version',
  edition: 'nkjv',
  edition_name: 'New King James Version',
};

const RESPONSE = (over: Partial<BibleStudyNotesResponse> = {}): BibleStudyNotesResponse => ({
  version: 'nkjv',
  edition: 'nkjv',
  edition_name: 'New King James Version',
  cross_version: false,
  other_translations: [],
  editions: EDITIONS,
  requested: 'John 3:16',
  reference: 'John 3:16',
  kinds: ['commentary', 'footnote'],
  available_kinds: { commentary: 12055, footnote: 32281 },
  notes: [
    {
      osis: 'John',
      book: 'John',
      chapter: 3,
      verse: 16,
      reference: 'John 3:16',
      kind: 'commentary',
      ordinal: 1,
      body: 'The Greek word for "begotten" is monogenes…',
      source: 'nkjv-study.epub',
      ...NOTE_FIELDS,
    },
    {
      osis: 'John',
      book: 'John',
      chapter: 3,
      verse: 16,
      reference: 'John 3:16',
      kind: 'footnote',
      ordinal: 1,
      body: '1:16 alludes to Gen. 3:15.',
      source: 'nkjv-study.epub',
      ...NOTE_FIELDS,
    },
  ],
  count: 2,
  note: null,
  ...over,
});

function renderPanel(
  over: Partial<BibleStudyNotesResponse> = {},
  opts: {
    fail?: boolean;
    props?: Partial<React.ComponentProps<typeof BibleStudyNotes>>;
  } = {},
) {
  apiMock.getBibleStudyNotes.mockReset();
  apiMock.getBibleStudyNotes.mockImplementation(
    opts.fail
      ? () => Promise.reject(new Error('Study notes are unavailable'))
      : () => Promise.resolve(RESPONSE(over)),
  );
  const props = opts.props ?? {};
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BibleStudyNotes passage="John 3:16" version="nkjv" onClose={() => {}} {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('BibleStudyNotes', () => {
  it('asks for the passage in the translation the reader is using', async () => {
    renderPanel();
    await waitFor(() =>
      expect(apiMock.getBibleStudyNotes).toHaveBeenCalledWith('John 3:16', 'nkjv', {
        edition: undefined,
        crossVersion: false,
      }),
    );
  });

  it('shows commentary and footnotes as separate blocks', async () => {
    renderPanel();
    expect(await screen.findByTestId('bible-study-note-commentary')).toHaveTextContent('monogenes');
    expect(screen.getByTestId('bible-study-note-footnote')).toHaveTextContent('Gen. 3:15');
  });

  it('offers a filter for each kind the translation carries, with the count', async () => {
    renderPanel();
    const commentary = await screen.findByTestId('bible-study-kind-commentary');
    expect(commentary).toHaveTextContent('Commentary');
    expect(commentary).toHaveTextContent('12055');
    expect(screen.getByTestId('bible-study-kind-footnote')).toHaveTextContent('32281');
  });

  it('hides a kind when its filter is tapped and brings it back', async () => {
    const user = userEvent.setup();
    renderPanel();
    const filter = await screen.findByTestId('bible-study-kind-footnote');
    await user.click(filter);
    expect(screen.queryByTestId('bible-study-note-footnote')).not.toBeInTheDocument();
    expect(screen.getByTestId('bible-study-note-commentary')).toBeInTheDocument();
    await user.click(filter);
    expect(screen.getByTestId('bible-study-note-footnote')).toBeInTheDocument();
  });

  it('explains an empty panel instead of leaving it blank', async () => {
    renderPanel({
      notes: [],
      count: 0,
      available_kinds: {},
      note: 'kjv carries no study notes. Import one with: python -m services.bible.import_epub --import-notes',
    });
    expect(await screen.findByTestId('bible-study-empty')).toHaveTextContent('import_epub');
    expect(screen.queryByTestId('bible-study-kind-commentary')).not.toBeInTheDocument();
  });

  it('surfaces a failure with the server message and a retry that refetches', async () => {
    const user = userEvent.setup();
    renderPanel({}, { fail: true });
    expect(await screen.findByTestId('bible-study-error')).toHaveTextContent(
      'Study notes are unavailable',
    );
    apiMock.getBibleStudyNotes.mockImplementation(() => Promise.resolve(RESPONSE()));
    await user.click(screen.getByTestId('bible-study-retry'));
    expect(await screen.findByTestId('bible-study-note-commentary')).toBeInTheDocument();
  });

  it('shows a skeleton while the notes are in flight', async () => {
    apiMock.getBibleStudyNotes.mockReset();
    apiMock.getBibleStudyNotes.mockImplementation(() => new Promise(() => {}));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <BibleStudyNotes passage="John 3:16" version="nkjv" onClose={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId('bible-study-loading')).toBeInTheDocument();
  });
});

describe('VerseActionSheet study notes', () => {
  beforeEach(() => {
    apiMock.recordBibleEvent.mockReset();
    apiMock.recordBibleEvent.mockResolvedValue({});
    apiMock.getBibleStudyNotes.mockReset();
    apiMock.getBibleStudyNotes.mockResolvedValue(RESPONSE());
  });

  function renderSheet() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <VerseActionSheet verse={VERSE} marks={[]} version="nkjv" onClose={() => {}} onToggleMark={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('opens the study panel from the verse sheet', async () => {
    const user = userEvent.setup();
    renderSheet();
    expect(screen.queryByTestId('bible-study-notes')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('bible-action-study'));
    expect(await screen.findByTestId('bible-study-notes')).toBeInTheDocument();
    expect(await screen.findByTestId('bible-study-ref')).toHaveTextContent('John 3:16');
  });

  it('records that the verse was opened', async () => {
    const user = userEvent.setup();
    renderSheet();
    await user.click(screen.getByTestId('bible-action-study'));
    await waitFor(() =>
      expect(apiMock.recordBibleEvent).toHaveBeenCalledWith('verse_tapped', 'John 3:16'),
    );
  });

  it('closes the panel without closing the sheet', async () => {
    const user = userEvent.setup();
    renderSheet();
    await user.click(screen.getByTestId('bible-action-study'));
    await screen.findByTestId('bible-study-notes');
    await user.click(screen.getAllByLabelText('Close study notes')[0]);
    await waitFor(() => expect(screen.queryByTestId('bible-study-notes')).not.toBeInTheDocument());
    expect(screen.getByTestId('bible-verse-sheet')).toBeInTheDocument();
  });
});
describe('a note the reader wrote about a verse', () => {
  beforeEach(() => {
    apiMock.recordBibleEvent.mockReset();
    apiMock.recordBibleEvent.mockResolvedValue({});
    apiMock.putBibleMark.mockReset();
    apiMock.putBibleMark.mockResolvedValue({});
    apiMock.readNote.mockReset();
  });

  function renderSheet(props: Partial<React.ComponentProps<typeof VerseActionSheet>> = {}) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const onNoteSaved = vi.fn();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <VerseActionSheet
            verse={VERSE}
            marks={[]}
            version="nkjv"
            onClose={() => {}}
            onToggleMark={() => {}}
            onNoteSaved={onNoteSaved}
            {...props}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return { onNoteSaved };
  }

  it('points the verse at the note it just wrote, using the server\'s own path', async () => {
    apiMock.createNote.mockResolvedValue({
      status: 'SUCCESS',
      message: "Note 'John 3:16' created.",
      service: 'note_create',
      detail: { path: 'Bible/John 3_16.md' },
    });
    const { onNoteSaved } = renderSheet();
    await userEvent.click(screen.getByTestId('bible-action-note'));

    await waitFor(() => expect(apiMock.putBibleMark).toHaveBeenCalledTimes(1));
    expect(apiMock.putBibleMark).toHaveBeenCalledWith(
      expect.objectContaining({ ref: 'John 3:16', kind: 'note', note_path: 'Bible/John 3_16.md' }),
    );
    expect(onNoteSaved).toHaveBeenCalled();
  });

  it('says so when the note was written but no path came back', async () => {
    apiMock.createNote.mockResolvedValue({
      status: 'SUCCESS',
      message: "Note 'John 3:16' created.",
      service: 'note_create',
    });
    renderSheet();
    await userEvent.click(screen.getByTestId('bible-action-note'));

    expect(await screen.findByTestId('bible-sheet-status')).toHaveTextContent(
      /was not told where/i,
    );
    // No pointer, so no mark that would point at nothing.
    expect(apiMock.putBibleMark).not.toHaveBeenCalled();
  });

  it('offers to read the note back when the verse already has one', async () => {
    apiMock.readNote.mockResolvedValue({
      status: 'SUCCESS',
      message: '# John 3:16\nCategory: Bible\n\nWhat a verse.',
      service: 'note_read',
    });
    renderSheet({
      marks: [
        {
          id: 1,
          version_code: 'nkjv',
          osis: 'John',
          book_name: 'John',
          chapter: 3,
          verse_start: 16,
          verse_end: null,
          kind: 'note',
          color: '',
          note_path: 'Bible/John 3_16.md',
          note_preview: 'John 3:16',
          created_at: null,
          updated_at: null,
        },
      ],
    });
    await userEvent.click(screen.getByTestId('bible-action-open-note'));
    await waitFor(() =>
      expect(apiMock.readNote).toHaveBeenCalledWith('John 3:16', 'nextcloud', 'Bible/John 3_16.md'),
    );
    expect(await screen.findByTestId('bible-note-body')).toHaveTextContent('What a verse.');
  });

  it('offers no note button for a verse that has none', () => {
    renderSheet();
    expect(screen.queryByTestId('bible-action-open-note')).not.toBeInTheDocument();
  });
});
