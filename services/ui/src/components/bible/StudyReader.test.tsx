import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ImportedBookReader from './ImportedBookReader';
import StudyBlocks from './StudyBlocks';
import { blockAt, startingPiece } from './studyNarration';
import type { BibleImportedBook, BibleImportedBookDetail, StudyBlock, StudyNarrationPlan } from '../../types/api';

/**
 * The structured study reader and its read-aloud. The book is made up: its
 * blocks are what the import normaliser produces (ids, scripture refs,
 * footnotes, questions), and the audio is four placeholder bytes.
 */

const apiMock = vi.hoisted(() => ({
  getBibleImportedBookChapter: vi.fn(),
  askBibleBookQuestion: vi.fn(),
  createNote: vi.fn(),
  postActivity: vi.fn(),
  getBibleVoices: vi.fn(),
  getStudyNarrationPlan: vi.fn(),
  getStudyNarrationChunk: vi.fn(),
  getBibleBookAsset: vi.fn(),
}));

const nativeMock = vi.hoisted(() => ({ native: false }));
const jarvisMedia = vi.hoisted(() => ({
  update: vi.fn(() => Promise.resolve()),
  stop: vi.fn(() => Promise.resolve()),
  reclaim: vi.fn(() => Promise.resolve({ playing: false })),
  addListener: vi.fn(() => Promise.resolve({ remove: vi.fn() })),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));
vi.mock('../../plugins/jarvisMedia', () => ({ default: jarvisMedia }));
vi.mock('@capacitor/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@capacitor/core')>();
  return {
    ...actual,
    Capacitor: { ...actual.Capacitor, isNativePlatform: () => nativeMock.native, getPlatform: () => (nativeMock.native ? 'android' : 'web') },
  };
});

const book: BibleImportedBook = {
  id: 9,
  title: 'A Made-up Study',
  author: 'Test Author',
  kind: 'study',
  source: 'upload',
  source_path: '',
  chapter_count: 2,
  created_at: null,
  format_version: 2,
  reformattable: true,
};

const chapters: BibleImportedBookDetail['chapters'] = [
  { ordinal: 1, section_id: 's-lesson-1-the-vine', label: 'Lesson 1: The Vine', characters: 900 },
  { ordinal: 2, section_id: 's-lesson-2-the-shepherd', label: 'Lesson 2: The Shepherd', characters: 700 },
];

function blocksFor(ordinal: number): StudyBlock[] {
  const lesson = ordinal === 1 ? 'The Vine' : 'The Shepherd';
  return [
    { id: `h-${ordinal}`, type: 'heading', level: 1, text: `Lesson ${ordinal}: ${lesson}` },
    {
      id: `p-${ordinal}a`,
      type: 'paragraph',
      text: 'Placeholder study prose that cites John 15:5 for the reader.',
      refs: [{ start: 36, end: 45, ref: 'John 15:5' }],
      notes: ['1'],
    },
    { id: `p-${ordinal}b`, type: 'paragraph', text: 'A second placeholder paragraph.' },
    { id: `l-${ordinal}`, type: 'list', items: ['First point', 'Second point'], ordered: false, text: 'First point\nSecond point' },
    { id: `k-${ordinal}`, type: 'question', text: 'What does it mean to abide?' },
    { id: `n-${ordinal}`, type: 'note', marker: '1', text: 'A placeholder footnote.' },
  ];
}

const preferences = { page: 'dark' as const, font_scale: 1, line_height: 1.6, theme: 'serif' as const, read_aloud_voice: '' };

function plan(ordinal: number, count = 2): StudyNarrationPlan {
  return {
    book_id: 9,
    ordinal,
    section_id: chapters[ordinal - 1].section_id!,
    voice: 'default',
    count,
    characters: 200,
    cached_count: 0,
    all_cached: false,
    chunks: [
      { index: 0, characters: 60, cached: false, blocks: [{ block_id: `h-${ordinal}`, start: 0, end: 20 }, { block_id: `p-${ordinal}a`, start: 21, end: 60 }] },
      { index: 1, characters: 60, cached: false, blocks: [{ block_id: `p-${ordinal}b`, start: 0, end: 30 }, { block_id: `k-${ordinal}`, start: 31, end: 60 }] },
    ].slice(0, count),
  };
}

function audio(): HTMLAudioElement {
  return screen.getByTestId('study-read-aloud-audio') as HTMLAudioElement;
}

function renderReader(props: Partial<Parameters<typeof ImportedBookReader>[0]> = {}) {
  const onOpenRef = vi.fn();
  const onPreferences = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ImportedBookReader
        book={book}
        chapters={chapters}
        preferences={preferences}
        onPreferences={onPreferences}
        onClose={vi.fn()}
        onOpenRef={onOpenRef}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onOpenRef, onPreferences };
}

let played: string[] = [];

beforeEach(() => {
  window.localStorage.clear();
  played = [];
  nativeMock.native = false;
  Object.values(apiMock).forEach((fn) => fn.mockReset());
  Object.values(jarvisMedia).forEach((fn) => (fn as ReturnType<typeof vi.fn>).mockClear());
  apiMock.postActivity.mockResolvedValue({});
  apiMock.getBibleVoices.mockResolvedValue({ voices: ['af_heart', 'bm_george'], count: 2 });
  apiMock.getBibleImportedBookChapter.mockImplementation((_id: number, ordinal: number) =>
    Promise.resolve({
      book_id: 9,
      ordinal,
      section_id: chapters[ordinal - 1].section_id,
      label: chapters[ordinal - 1].label,
      text: 'plain text',
      questions: ['What does it mean to abide?'],
      blocks: blocksFor(ordinal),
    }),
  );
  apiMock.getStudyNarrationPlan.mockImplementation((_id: number, ordinal: number) => Promise.resolve(plan(ordinal)));
  apiMock.getStudyNarrationChunk.mockImplementation((_id: number, ordinal: number, index: number) =>
    Promise.resolve({ ...plan(ordinal).chunks[index], book_id: 9, ordinal, section_id: '', voice: 'default', count: 2,
      mime_type: 'audio/wav', length_bytes: 4, audio_base64: 'AAAA' }),
  );
  URL.createObjectURL = vi.fn(() => `blob:piece-${played.length + 1}`) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function play(this: HTMLMediaElement) {
    played.push(this.getAttribute('src') ?? '');
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('narration helpers', () => {
  it('maps a moment in a piece to the block being spoken', () => {
    const marks = [{ block_id: 'a', start: 0, end: 10 }, { block_id: 'b', start: 11, end: 40 }];
    expect(blockAt(marks, 0)).toBe('a');
    expect(blockAt(marks, 0.2)).toBe('a');
    expect(blockAt(marks, 0.5)).toBe('b');
    expect(blockAt(marks, 1.4)).toBe('b');
    expect(blockAt([], 0.5)).toBeNull();
  });

  it('starts at the piece holding the paragraph on screen', () => {
    expect(startingPiece(plan(1), 'k-1')).toBe(1);
    expect(startingPiece(plan(1), 'missing')).toBe(0);
    expect(startingPiece(plan(1), null)).toBe(0);
  });
});

describe('StudyBlocks', () => {
  it('draws structure, links scripture and keeps footnotes out of the sentence', async () => {
    const onOpenRef = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <StudyBlocks bookId={9} blocks={blocksFor(1)} onOpenRef={onOpenRef} fontFamily="serif" fontScale={1} lineHeight={1.6} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('heading', { name: 'Lesson 1: The Vine' })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem').map((item) => item.textContent)).toContain('First point');
    await userEvent.click(screen.getByRole('button', { name: 'Open John 15:5 in the Bible' }));
    expect(onOpenRef).toHaveBeenCalledWith('John 15:5');
    const notes = screen.getByRole('region', { name: 'Notes' });
    expect(within(notes).getByText('A placeholder footnote.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Footnote 1' })).toHaveAttribute('href', '#n-1');
    // Every block can be linked to by its id.
    expect(document.getElementById('p-1b')).not.toBeNull();
  });

  it('gives a question room for an answer, kept on this device', async () => {
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <StudyBlocks bookId={9} blocks={blocksFor(1)} fontFamily="sans" fontScale={1} lineHeight={1.6} />
      </QueryClientProvider>,
    );
    const answer = screen.getByTestId('study-answer');
    await userEvent.type(answer, 'Staying close');
    await waitFor(() => expect(window.localStorage.getItem('jarvis.study-answer.9.k-1')).toBe('Staying close'));
    unmount();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <StudyBlocks bookId={9} blocks={blocksFor(1)} fontFamily="sans" fontScale={1} lineHeight={1.6} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId('study-answer')).toHaveValue('Staying close');
  });
});

describe('ImportedBookReader with structured sections', () => {
  it('opens at a section id and remembers it as the place to resume', async () => {
    renderReader({ startSection: 's-lesson-2-the-shepherd' });
    expect(await screen.findByRole('heading', { name: 'Lesson 2: The Shepherd' })).toBeInTheDocument();
    expect(window.localStorage.getItem('jarvis.study-book.9.section')).toBe('s-lesson-2-the-shepherd');
  });

  it('follows a page saved before a reformat to where its words are now', async () => {
    window.localStorage.setItem('jarvis.study-book.9.page', '5');
    renderReader({
      aliases: [{ old_ordinal: 5, old_section_id: '', section_id: 's-lesson-2-the-shepherd', ordinal: 2 }],
    });
    expect(await screen.findByRole('heading', { name: 'Lesson 2: The Shepherd' })).toBeInTheDocument();
  });

  it('opens a scripture reference in the Bible reader', async () => {
    const { onOpenRef } = renderReader();
    await userEvent.click(await screen.findByRole('button', { name: 'Open John 15:5 in the Bible' }));
    expect(onOpenRef).toHaveBeenCalledWith('John 15:5');
  });

  it('reads aloud from the paragraph on screen and lights up what is being read', async () => {
    renderReader();
    await screen.findByRole('heading', { name: 'Lesson 1: The Vine' });
    // jsdom has no layout: put the second paragraph's bottom below the top edge.
    const page = screen.getByTestId('bible-study-page');
    vi.spyOn(page, 'getBoundingClientRect').mockReturnValue({ top: 100, bottom: 700 } as DOMRect);
    for (const element of page.querySelectorAll<HTMLElement>('[data-block-id]')) {
      const id = element.dataset.blockId;
      const bottom = id === 'p-1b' || id === 'l-1' || id === 'k-1' || id === 'n-1' ? 400 : 50;
      vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ top: bottom - 40, bottom } as DOMRect);
    }
    await userEvent.click(screen.getByTestId('bible-study-listen'));
    await userEvent.click(screen.getByTestId('study-read-aloud-play'));
    await waitFor(() => expect(apiMock.getStudyNarrationChunk).toHaveBeenCalledWith(9, 1, 1, undefined));
    expect(apiMock.getStudyNarrationChunk).not.toHaveBeenCalledWith(9, 1, 0, undefined);
    await waitFor(() => expect(document.getElementById('p-1b')).toHaveAttribute('data-active', 'true'));

    // Half way through the piece the question is being read.
    const element = audio();
    Object.defineProperty(element, 'duration', { configurable: true, value: 10 });
    Object.defineProperty(element, 'currentTime', { configurable: true, value: 8 });
    fireEvent(element, new Event('timeupdate'));
    await waitFor(() => expect(document.getElementById('k-1')).toHaveAttribute('data-active', 'true'));
  });

  it('carries on into the next section when one ends', async () => {
    renderReader();
    await screen.findByRole('heading', { name: 'Lesson 1: The Vine' });
    await userEvent.click(screen.getByTestId('bible-study-listen'));
    await userEvent.click(screen.getByTestId('study-read-aloud-play'));
    await waitFor(() => expect(played.length).toBe(1));
    await act(async () => {
      fireEvent(audio(), new Event('ended'));
    });
    await waitFor(() => expect(played.length).toBe(2));
    await act(async () => {
      fireEvent(audio(), new Event('ended'));
    });
    expect(await screen.findByRole('heading', { name: 'Lesson 2: The Shepherd' })).toBeInTheDocument();
    await waitFor(() => expect(apiMock.getStudyNarrationPlan).toHaveBeenCalledWith(9, 2, undefined));
    await waitFor(() => expect(apiMock.getStudyNarrationChunk).toHaveBeenCalledWith(9, 2, 0, undefined));
  });

  it('stops cleanly, and holds the Android media notification as speech while reading', async () => {
    nativeMock.native = true;
    renderReader();
    await screen.findByRole('heading', { name: 'Lesson 1: The Vine' });
    await userEvent.click(screen.getByTestId('bible-study-listen'));
    await userEvent.click(screen.getByTestId('study-read-aloud-play'));
    await waitFor(() =>
      expect(jarvisMedia.update).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Lesson 1: The Vine', artist: 'A Made-up Study', playing: true, speech: true }),
      ),
    );
    await userEvent.click(screen.getByTestId('study-read-aloud-stop'));
    expect(jarvisMedia.stop).toHaveBeenCalled();
    expect(screen.queryByTestId('study-read-aloud-progress')).toBeNull();
    expect(document.querySelector('[data-active="true"]')).toBeNull();
  });

  it('uses the reader\'s chosen voice and saves a new one to the Bible preferences', async () => {
    const { onPreferences } = renderReader({ preferences: { ...preferences, read_aloud_voice: 'bm_george' } });
    await screen.findByRole('heading', { name: 'Lesson 1: The Vine' });
    await userEvent.click(screen.getByTestId('bible-study-listen'));
    const select = await screen.findByTestId('study-read-aloud-voice');
    expect(select).toHaveValue('bm_george');
    await userEvent.selectOptions(select, 'af_heart');
    expect(onPreferences).toHaveBeenCalledWith({ read_aloud_voice: 'af_heart' });
    await userEvent.click(screen.getByTestId('study-read-aloud-play'));
    await waitFor(() => expect(apiMock.getStudyNarrationPlan).toHaveBeenCalledWith(9, 1, 'bm_george'));
  });
});
