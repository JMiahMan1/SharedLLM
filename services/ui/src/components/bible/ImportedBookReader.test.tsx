import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ImportedBookReader from './ImportedBookReader';
import type { BibleImportedBook, BiblePreferences } from '../../types/api';

const apiMock = vi.hoisted(() => ({
  getBibleImportedBookChapter: vi.fn(),
  askBibleBookQuestion: vi.fn(),
  createNote: vi.fn(),
  postActivity: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const book: BibleImportedBook = {
  id: 1,
  title: 'Walk With Jesus PDF - 1st Edition',
  author: '',
  kind: 'study',
  source: 'upload',
  source_path: '',
  chapter_count: 2,
  created_at: null,
};

const chapters = [
  { ordinal: 1, label: 'Page 1', characters: 120 },
  { ordinal: 2, label: 'Page 2', characters: 140 },
];

const preferences: Pick<BiblePreferences, 'page' | 'font_scale' | 'line_height' | 'theme'> = {
  page: 'dark',
  font_scale: 1,
  line_height: 1.6,
  theme: 'serif',
};

function pageText(ordinal: number): string {
  if (ordinal === 1) {
    return [
      '1',
      '',
      'Week 1: Introduction to Walk With Jesus',
      '',
      'Jesus said, follow me.',
      'This is the first paragraph of the study, wrapped across',
      'several lines exactly as the PDF stored it.',
    ].join('\n');
  }
  return [
    '2',
    '',
    'Week 1: The First Steps',
    '',
    'A second page with a different reflection.',
  ].join('\n');
}

function renderReader(onPreferences = vi.fn()) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ImportedBookReader
        book={book}
        chapters={chapters}
        preferences={preferences}
        onPreferences={onPreferences}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  apiMock.getBibleImportedBookChapter.mockReset();
  apiMock.askBibleBookQuestion.mockReset();
  apiMock.createNote.mockReset();
  apiMock.getBibleImportedBookChapter.mockImplementation((_bookId: number, ordinal: number) =>
    Promise.resolve({
      book_id: 1,
      ordinal,
      label: `Page ${ordinal}`,
      text: pageText(ordinal),
      questions: ordinal === 1 ? ['What does following Jesus mean here?'] : [],
    }),
  );
  apiMock.createNote.mockResolvedValue({ ok: true });
  apiMock.postActivity.mockReset();
  apiMock.postActivity.mockResolvedValue({ status: 'SUCCESS' });
  apiMock.askBibleBookQuestion.mockResolvedValue({
    answer: 'Following Jesus means walking with him day by day.',
    book_id: 1,
    ordinal: 1,
    question: 'What does this mean?',
  });
});

describe('imported book reader', () => {
  it('lands the open page on the activity feed so the family profile sees reading', async () => {
    renderReader();

    await vi.waitFor(() =>
      expect(apiMock.postActivity).toHaveBeenCalledWith({
        feature: 'bible',
        item: 'Walk With Jesus PDF - 1st Edition - Page 1',
        event: 'read',
      }),
    );
  });

  it('reads one page as prose instead of listing every page as a button', async () => {
    renderReader();

    expect(await screen.findByTestId('bible-study-reader-text')).toHaveTextContent(
      'This is the first paragraph of the study, wrapped across several lines exactly as the PDF stored it.',
    );
    expect(screen.getByRole('heading', { name: 'Week 1: Introduction to Walk With Jesus' })).toBeInTheDocument();
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Page 2' })).not.toBeInTheDocument();
    expect(apiMock.getBibleImportedBookChapter).toHaveBeenCalledTimes(1);
  });

  it('turns to the next page and remembers the position', async () => {
    const user = userEvent.setup();
    renderReader();

    await screen.findByTestId('bible-study-reader-text');
    await user.click(screen.getByRole('button', { name: 'Next page' }));

    await waitFor(() =>
      expect(apiMock.getBibleImportedBookChapter).toHaveBeenLastCalledWith(1, 2),
    );
    expect(await screen.findByText('A second page with a different reflection.')).toBeInTheDocument();
    expect(window.localStorage.getItem('jarvis.study-book.1.page')).toBe('2');
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument();
  });

  it('jumps to a page from the contents sheet', async () => {
    const user = userEvent.setup();
    renderReader();

    await screen.findByTestId('bible-study-reader-text');
    await user.click(screen.getByRole('button', { name: 'Table of contents' }));
    const contents = await screen.findByRole('dialog', { name: 'Contents' });
    await user.click(within(contents).getByRole('button', { name: /Page 2/ }));

    expect(await screen.findByText('Week 1: The First Steps')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'Contents' })).not.toBeInTheDocument();
  });

  it('saves the current page to Notes with its cleaned text', async () => {
    const user = userEvent.setup();
    renderReader();

    await screen.findByTestId('bible-study-reader-text');
    await user.click(screen.getByRole('button', { name: 'Page actions' }));
    await user.click(screen.getByRole('button', { name: 'Save page to Notes' }));

    await waitFor(() => expect(apiMock.createNote).toHaveBeenCalledTimes(1));
    expect(apiMock.createNote).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Walk With Jesus PDF - 1st Edition - Page 1',
        category: 'Bible',
        content: expect.stringContaining('This is the first paragraph'),
      }),
    );
    const payload = apiMock.createNote.mock.calls[0][0] as { content: string };
    expect(payload.content).not.toContain('\n\n1\n\n');
    expect(payload.content).not.toContain('wrapped across\nseveral');
  });

  it('asks Jarvis about the current page and shows the answer', async () => {
    const user = userEvent.setup();
    renderReader();

    await screen.findByTestId('bible-study-reader-text');
    await user.click(screen.getByRole('button', { name: 'Page actions' }));
    await user.click(screen.getByRole('button', { name: 'Ask Jarvis about this page' }));
    await user.type(screen.getByLabelText('Question for Jarvis'), 'What does this mean?');
    await user.click(screen.getByRole('button', { name: 'Ask' }));

    await waitFor(() =>
      expect(apiMock.askBibleBookQuestion).toHaveBeenCalledWith(1, 1, 'What does this mean?'),
    );
    expect(await screen.findByTestId('bible-study-answer')).toHaveTextContent(
      'Following Jesus means walking with him day by day.',
    );
  });

  it('shares the Bible reading settings with the rest of the Bible', async () => {
    const user = userEvent.setup();
    const onPreferences = vi.fn();
    renderReader(onPreferences);

    await screen.findByTestId('bible-study-reader-text');
    await user.click(screen.getByRole('button', { name: 'Reading settings' }));
    await user.click(screen.getByRole('button', { name: 'Larger text' }));
    expect(onPreferences).toHaveBeenCalledWith({ font_scale: 1.1 });

    await user.click(screen.getByRole('button', { name: 'Sepia page' }));
    expect(onPreferences).toHaveBeenCalledWith({ page: 'sepia' });
  });
});
