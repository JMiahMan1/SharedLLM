import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import WordStudyPanel from './WordStudyPanel';
import { api } from '../../services/api';
import type { BibleLexiconEntry } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getBibleLexiconVerse: vi.fn(),
      getBibleLexiconEntry: vi.fn(),
      searchBibleLexicon: vi.fn(),
    },
  };
});

const AGAPAO: BibleLexiconEntry = {
  strongs: 'G25', language: 'greek', lemma: 'ἀγαπάω', transliteration: 'agapao', part_of_speech: 'v', gk: '26',
  definition: 'to love; in the NT usually the active love of God (for his Son; and his people)', gloss: 'to love',
  usage: [{ word: 'love', count: 73 }, { word: 'loved', count: 39 }], usage_total: 142, source: 's',
  blb_url: 'https://www.blueletterbible.org/lexicon/g25/kjv/tr/0-1/',
  occurrences: {
    source: 'concordance', total: 2, complete: false,
    verses: [
      { reference: 'John 3:16', osis: 'John', chapter: 3, verse: 16, word: 'Loved', context: 'For God so l the world' },
      { reference: '1 John 4:19', osis: '1John', chapter: 4, verse: 19, word: 'Love', context: 'We l him, because he first' },
    ],
  },
};

function renderPanel(props: Partial<Parameters<typeof WordStudyPanel>[0]> = {}) {
  const onOpenRef = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <WordStudyPanel reference="John 3:16" onOpenRef={onOpenRef} onClose={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
  return { onOpenRef };
}

beforeEach(() => vi.clearAllMocks());

describe('WordStudyPanel', () => {
  it("lists a verse's words and opens one as a lexicon entry, then a verse it is in", async () => {
    vi.mocked(api.getBibleLexiconVerse).mockResolvedValue({
      reference: 'John 3:16', source: 'concordance',
      words: [{ word: 'Loved', original: '', transliteration: 'agapao', strongs: 'G25', definition: 'to love', in_lexicon: true }],
    });
    vi.mocked(api.getBibleLexiconEntry).mockResolvedValue(AGAPAO);
    const { onOpenRef } = renderPanel();
    fireEvent.click(await screen.findByTestId('bible-word-G25'));
    expect(await screen.findByTestId('bible-word-entry')).toHaveTextContent('ἀγαπάω');
    expect(screen.getByTestId('bible-word-usage')).toHaveTextContent('love 73');
    // The definition reads as senses; a semicolon inside brackets does not split one.
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toContain(
      'in the NT usually the active love of God (for his Son; and his people)',
    );
    expect(screen.getByText('2 verses read from the concordance')).toBeInTheDocument();
    fireEvent.click(screen.getByText('1 John 4:19'));
    expect(onOpenRef).toHaveBeenCalledWith('1 John 4:19');
  });

  it('searches the lexicon', async () => {
    vi.mocked(api.getBibleLexiconVerse).mockResolvedValue({ reference: 'John 3:16', source: 'concordance', words: [] });
    vi.mocked(api.searchBibleLexicon).mockResolvedValue({
      query: 'love', results: [{ strongs: 'G26', transliteration: 'agape', definition: 'love', usage_total: 116 }],
    });
    renderPanel();
    expect(await screen.findByTestId('bible-word-none')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search the lexicon'), { target: { value: 'love' } });
    fireEvent.submit(screen.getByLabelText('Search the lexicon').closest('form') as HTMLFormElement);
    expect(await screen.findByTestId('bible-word-G26')).toHaveTextContent('agape');
    expect(api.searchBibleLexicon).toHaveBeenCalledWith('love');
  });

  it('opens straight on an entry from the interlinear', async () => {
    vi.mocked(api.getBibleLexiconEntry).mockResolvedValue(AGAPAO);
    renderPanel({ initialStrongs: 'G25' });
    expect(await screen.findByTestId('bible-word-entry')).toBeInTheDocument();
    expect(api.getBibleLexiconVerse).not.toHaveBeenCalled();
  });
});
