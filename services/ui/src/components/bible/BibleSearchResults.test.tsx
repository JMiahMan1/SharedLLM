import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleSearchResults from './BibleSearchResults';
import { effectiveQuery, highlighted, questionText, scopeIn } from './searchText';
import { api } from '../../services/api';
import type { BibleSearchResult, BibleVersionInfo } from '../../types/api';

vi.mock('../../services/api', () => ({ api: { searchBible: vi.fn(), getBiblePassage: vi.fn(), askBibleSearch: vi.fn() } }));
const mocked = vi.mocked(api);

const VERSIONS = [
  { code: 'kjv', name: 'King James Version', installed: true },
  { code: 'web', name: 'World English Bible', installed: true },
] as BibleVersionInfo[];

function page(over: Partial<BibleSearchResult> = {}): BibleSearchResult {
  return {
    version: 'kjv',
    query: 'faith',
    total: 2,
    count: 2,
    offset: 0,
    sort: 'rank',
    index: 'fts',
    by_book: [
      { osis: 'Rom', name: 'Romans', count: 1 },
      { osis: 'Jas', name: 'James', count: 1 },
    ],
    parsed: { all: ['faith'], without: [], scopes: [], versions: ['kjv'], strongs: [], has: [] },
    results: [
      { osis: 'Rom', book_name: 'Romans', chapter: 1, verse: 17, reference: 'Romans 1:17', version: 'kjv',
        text: 'The just shall live by faith.', highlights: [[23, 28]], versions: ['kjv'] },
      { osis: 'Jas', book_name: 'James', chapter: 2, verse: 17, reference: 'James 2:17', version: 'kjv',
        text: 'Even so faith, if it hath not works, is dead.', highlights: [[8, 13]], versions: ['kjv', 'web'] },
    ],
    ...over,
  };
}

function renderResults(props: Partial<Parameters<typeof BibleSearchResults>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpen = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <BibleSearchResults term="faith" version="kjv" versions={VERSIONS} onOpen={onOpen} onClear={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
  return { onOpen };
}

beforeEach(() => {
  localStorage.clear();
  mocked.searchBible.mockReset().mockResolvedValue(page());
  mocked.getBiblePassage.mockReset();
  mocked.askBibleSearch.mockReset();
});

describe('BibleSearchResults', () => {
  it('shows how many verses matched, marks the words and opens a verse', async () => {
    const { onOpen } = renderResults();
    expect(await screen.findByTestId('bible-search-summary')).toHaveTextContent('2 verses · faith');
    const marks = await screen.findAllByText('faith', { selector: 'mark' });
    expect(marks).toHaveLength(2);
    fireEvent.click(screen.getByText('Romans 1:17'));
    expect(onOpen).toHaveBeenCalledWith('Romans 1:17');
  });

  it('turns the filters into the search', async () => {
    renderResults();
    await screen.findByTestId('bible-search-summary');
    fireEvent.click(screen.getByTestId('bible-search-match-any'));
    await waitFor(() =>
      expect(mocked.searchBible).toHaveBeenLastCalledWith('faith', expect.objectContaining({ match: 'any' })),
    );
    fireEvent.change(screen.getByTestId('bible-search-where'), { target: { value: 'gospels' } });
    await waitFor(() =>
      expect(mocked.searchBible).toHaveBeenLastCalledWith('faith in:gospels', expect.objectContaining({ match: 'any' })),
    );
    fireEvent.click(screen.getByTestId('bible-search-every'));
    await waitFor(() =>
      expect(mocked.searchBible).toHaveBeenLastCalledWith('faith in:gospels ver:all', expect.anything()),
    );
    fireEvent.click(screen.getByTestId('bible-search-sort'));
    await waitFor(() => expect(mocked.searchBible).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ sort: 'canon' })));
    // The match setting and "all translations" are remembered for the next search.
    expect(JSON.parse(localStorage.getItem('jarvis_bible_search_prefs') ?? '{}')).toEqual({ match: 'any', every: true });
  });

  it('narrows to a book from the chart and back', async () => {
    renderResults();
    const chart = await screen.findByTestId('bible-search-books');
    fireEvent.click(within(chart).getByLabelText('James, 1 verse'));
    await waitFor(() => expect(mocked.searchBible).toHaveBeenLastCalledWith('faith in:Jas', expect.anything()));
  });

  it('pages through long results', async () => {
    mocked.searchBible.mockResolvedValueOnce(page({ total: 3 }));
    mocked.searchBible.mockResolvedValueOnce(page({
      total: 3, count: 1, offset: 2,
      results: [{ osis: 'Heb', book_name: 'Hebrews', chapter: 11, verse: 1, reference: 'Hebrews 11:1', text: 'Now faith is', highlights: [] }],
    }));
    renderResults();
    fireEvent.click(await screen.findByTestId('bible-search-more'));
    expect(await screen.findByText('Hebrews 11:1')).toBeInTheDocument();
    expect(mocked.searchBible).toHaveBeenLastCalledWith('faith', expect.objectContaining({ offset: 2 }));
    expect(screen.queryByTestId('bible-search-more')).not.toBeInTheDocument();
  });

  it('shows the verses around a hit', async () => {
    mocked.getBiblePassage.mockResolvedValue({
      version: 'kjv', requested: '', reference: 'Romans 1:15-19', spans: [], count: 2,
      verses: [
        { osis: 'Rom', book_name: 'Romans', chapter: 1, verse: 16, reference: 'Romans 1:16', text: 'For I am not ashamed' },
        { osis: 'Rom', book_name: 'Romans', chapter: 1, verse: 17, reference: 'Romans 1:17', text: 'The just shall live by faith.' },
      ],
    } as never);
    renderResults();
    fireEvent.click(await screen.findByLabelText('Verses around Romans 1:17'));
    expect(await screen.findByTestId('bible-search-context')).toHaveTextContent('For I am not ashamed');
    expect(mocked.getBiblePassage).toHaveBeenCalledWith('Romans 1:15-19', 'kjv');
  });

  it("says what was wrong with the search in the server's words", async () => {
    mocked.searchBible.mockRejectedValue({ response: { data: { detail: "'Hezekiah' is not a book or section of the Bible" } } });
    renderResults({ term: 'grace in:Hezekiah' });
    expect(await screen.findByTestId('bible-search-error')).toHaveTextContent('Hezekiah');
  });

  it('lists the translations a verse matched in when searching them all', async () => {
    localStorage.setItem('jarvis_bible_search_prefs', JSON.stringify({ match: 'all', every: true }));
    renderResults();
    await screen.findByText('James 2:17');
    expect(screen.getByTitle('World English Bible')).toHaveTextContent('web');
  });
});

describe('meaning search and Ask Jarvis', () => {
  it('searches by meaning with one translation and says when the index is still being prepared', async () => {
    localStorage.setItem('jarvis_bible_search_prefs', JSON.stringify({ match: 'all', every: true }));
    mocked.searchBible.mockResolvedValue(page({ mode: 'meaning', index: 'meaning', meaning_coverage: 0.4 }));
    renderResults({ term: 'verses about worry' });
    fireEvent.click(await screen.findByTestId('bible-search-match-meaning'));
    await waitFor(() =>
      expect(mocked.searchBible).toHaveBeenLastCalledWith(
        'verses about worry',
        expect.objectContaining({ match: 'meaning', sort: undefined }),
      ),
    );
    expect(await screen.findByTestId('bible-search-summary')).toHaveTextContent('closest in meaning');
    expect(screen.getByTestId('bible-search-summary')).toHaveTextContent('40% of verses');
    // One translation and one order in meaning mode.
    expect(screen.queryByTestId('bible-search-every')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bible-search-sort')).not.toBeInTheDocument();
  });

  it('asks Jarvis with the search as the question and shows only real references', async () => {
    mocked.askBibleSearch.mockResolvedValue({
      question: 'faith', version: 'kjv', depth: 'teen', retrieval: 'meaning',
      answer: 'Faith rests on God (Romans 1:17).',
      verses: page().results,
      references: [{ reference: 'Romans 1:17', in_results: true }],
    });
    const { onOpen } = renderResults();
    fireEvent.click(await screen.findByTestId('bible-search-ask-toggle'));
    expect(screen.getByTestId('bible-search-ask-input')).toHaveValue('faith');
    fireEvent.click(screen.getByRole('button', { name: 'Teen' }));
    fireEvent.click(screen.getByTestId('bible-search-ask-go'));
    const answer = await screen.findByTestId('bible-search-ask-answer');
    expect(answer).toHaveTextContent('Faith rests on God');
    expect(answer).toHaveTextContent('From 2 verses found by meaning');
    expect(mocked.askBibleSearch).toHaveBeenCalledWith('faith', { version: 'kjv', scope: undefined, depth: 'teen' });
    expect(within(answer).getByText('Romans 1:17')).toBeInTheDocument();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("shows why Jarvis could not answer", async () => {
    mocked.askBibleSearch.mockRejectedValue({ response: { data: { detail: 'Study help could not reach the language model' } } });
    renderResults();
    fireEvent.click(await screen.findByTestId('bible-search-ask-toggle'));
    fireEvent.click(screen.getByTestId('bible-search-ask-go'));
    expect(await screen.findByTestId('bible-search-ask-error')).toHaveTextContent('could not reach');
  });
});

describe('search text helpers', () => {
  it('adds the filters once', () => {
    expect(effectiveQuery(' faith ', 'nt', true)).toBe('faith in:nt ver:all');
    expect(effectiveQuery('faith ver:esv', '', true)).toBe('faith ver:esv');
  });

  it('turns a search into a question and its scope', () => {
    expect(questionText('faith in:1thess ver:all')).toBe('faith');
    expect(questionText('what about worry? has:note')).toBe('what about worry?');
    expect(scopeIn('faith in:"1 Cor" ver:all')).toBe('1 Cor');
    expect(scopeIn('faith')).toBe('');
  });

  it('merges overlapping highlights', () => {
    render(<p data-testid="t">{highlighted('abcdefgh', [[1, 4], [3, 6]])}</p>);
    expect(screen.getByTestId('t').querySelectorAll('mark')).toHaveLength(1);
    expect(screen.getByTestId('t').querySelector('mark')).toHaveTextContent('bcdef');
  });
});
