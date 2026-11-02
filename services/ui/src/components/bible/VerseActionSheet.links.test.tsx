import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import VerseActionSheet from './VerseActionSheet';
import { verseShareText } from '../../lib/verseShare';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));
vi.mock('../chat/familyDirectory', () => ({ useFamilyDirectory: () => ({ byUsername: new Map(), byNextcloud: new Map() }) }));
vi.mock('../chat/ConversationAvatar', () => ({ default: () => <span data-testid="avatar" /> }));

const apiMock = vi.hoisted(() => ({
  getBlbLink: vi.fn(),
  getBibleInterlinear: vi.fn(),
  getBibleLexiconStatus: vi.fn(),
  getBibleLexiconVerse: vi.fn(),
  getBibleLexiconEntry: vi.fn(),
  recordBibleEvent: vi.fn(),
  getTalkConversations: vi.fn(),
  sendTalkMessage: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const VERSE = { reference: 'John 3:16', text: 'For God so loved the world', book: 'John', chapter: 3, verse: 16 } as never;

function renderSheet(onShared = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <VerseActionSheet verse={VERSE} marks={[]} version="nkjv" onClose={vi.fn()} onToggleMark={vi.fn()} onShared={onShared} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onShared };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.recordBibleEvent.mockResolvedValue({});
  apiMock.getBibleLexiconStatus.mockResolvedValue({ installed: false, entries: 0, occurrences: 0, source: '' });
  apiMock.getBlbLink.mockImplementation(async (ref: string, tool?: string, version?: string) => ({
    ref,
    tool: tool ?? null,
    url: tool ? `https://blb.test/${version}/jhn/3/16/t_conc_1000016` : `https://blb.test/${version}/jhn/3/16/`,
  }));
});

describe('Blue Letter Bible links', () => {
  it('are real links in the reader’s translation, so a tap opens them', async () => {
    apiMock.getBibleInterlinear.mockResolvedValue({ ref: 'John 3:16', available: false, attribution: null, verses: [] });
    renderSheet();
    await waitFor(() => expect(screen.getByTestId('bible-action-blb').tagName).toBe('A'));
    expect(screen.getByTestId('bible-action-blb')).toHaveAttribute('href', 'https://blb.test/nkjv/jhn/3/16/');
    expect(screen.getByTestId('bible-action-blb')).toHaveAttribute('target', '_blank');
    expect(apiMock.getBlbLink).toHaveBeenCalledWith('John 3:16', undefined, 'nkjv');
  });

  it('falls back to Blue Letter Bible’s interlinear when none is imported', async () => {
    apiMock.getBibleInterlinear.mockResolvedValue({ ref: 'John 3:16', available: false, attribution: null, verses: [] });
    renderSheet();
    await waitFor(() => expect(screen.getByTestId('bible-action-interlinear').tagName).toBe('A'));
    expect(screen.getByTestId('bible-action-interlinear')).toHaveAttribute('href', 'https://blb.test/nkjv/jhn/3/16/t_conc_1000016');
    expect(screen.getByTestId('bible-action-interlinear')).toHaveTextContent('Interlinear on Blue Letter Bible');
  });

  it('shows the imported interlinear in the app when there is one', async () => {
    apiMock.getBibleInterlinear.mockResolvedValue({
      ref: 'John 3:16',
      available: true,
      attribution: 'Interlinear data: STEPBible.org, CC BY 4.0',
      verses: [{
        reference: 'John 3:16',
        language: 'greek',
        words: [{ w: 'ἠγάπησεν', t: 'ēgapēsen', e: 'loved', s: 'G0025', g: 'V-AAI-3S', l: 'ἀγαπάω', d: 'to love', x: 'https://blb.test/lexicon/g25/kjv/tr/0-1/' }],
      }],
    });
    renderSheet();
    await waitFor(() => expect(screen.getByTestId('bible-action-interlinear').tagName).toBe('BUTTON'));
    await userEvent.click(screen.getByTestId('bible-action-interlinear'));
    const panel = await screen.findByTestId('bible-interlinear-panel');
    expect(panel).toHaveTextContent('ἠγάπησεν');
    expect(panel).toHaveTextContent('loved');
    expect(screen.getByRole('link', { name: /ἠγάπησεν/ })).toHaveAttribute('href', 'https://blb.test/lexicon/g25/kjv/tr/0-1/');
    expect(panel).toHaveTextContent('STEPBible');
  });
});

describe('sharing to the family chat', () => {
  it('sends the verse to the conversation picked, with an optional message', async () => {
    apiMock.getBibleInterlinear.mockResolvedValue({ ref: 'John 3:16', available: false, attribution: null, verses: [] });
    apiMock.getTalkConversations.mockResolvedValue({
      status: 'SUCCESS',
      detail: { conversations: [{ token: 'fam', display_name: 'Summers Family', type: 2 }, { token: 'mich', display_name: 'Michele', type: 1 }] },
    });
    apiMock.sendTalkMessage.mockResolvedValue({ status: 'SUCCESS', message: 'sent' });
    const { onShared } = renderSheet();
    await userEvent.click(screen.getByTestId('bible-action-share-chat'));
    await userEvent.type(screen.getByTestId('bible-share-chat-note'), 'For tonight');
    await userEvent.click(await screen.findByRole('button', { name: /Summers Family/ }));
    await waitFor(() =>
      expect(apiMock.sendTalkMessage).toHaveBeenCalledWith({
        token: 'fam',
        message: verseShareText('John 3:16', 'For God so loved the world', 'nkjv', 'For tonight'),
      }),
    );
    expect(onShared).toHaveBeenCalledWith('chat');
    expect(screen.queryByTestId('bible-share-chat-sheet')).not.toBeInTheDocument();
  });
});

describe('verseShareText', () => {
  it('matches the chat’s verse line', () => {
    expect(verseShareText('John 3:16', ' For God so loved ', 'nkjv')).toBe('📖 John 3:16 (NKJV): "For God so loved"');
    expect(verseShareText('John 3:16', 'For God', undefined, '  ')).toBe('📖 John 3:16: "For God"');
  });
});

describe('Word study', () => {
  it('is offered once a Strong’s concordance is installed, and opens on the verse', async () => {
    apiMock.getBibleInterlinear.mockResolvedValue({ ref: 'John 3:16', available: false, attribution: null, verses: [] });
    apiMock.getBibleLexiconStatus.mockResolvedValue({ installed: true, entries: 14000, occurrences: 200000, source: 's' });
    apiMock.getBibleLexiconVerse.mockResolvedValue({ reference: 'John 3:16', source: 'concordance', words: [] });
    renderSheet();
    await userEvent.click(await screen.findByTestId('bible-action-word-study'));
    expect(await screen.findByTestId('bible-word-study')).toBeInTheDocument();
    expect(apiMock.getBibleLexiconVerse).toHaveBeenCalledWith('John 3:16');
  });

  it('is not offered without a concordance', async () => {
    apiMock.getBibleInterlinear.mockResolvedValue({ ref: 'John 3:16', available: false, attribution: null, verses: [] });
    renderSheet();
    await waitFor(() => expect(apiMock.getBibleLexiconStatus).toHaveBeenCalled());
    expect(screen.queryByTestId('bible-action-word-study')).toBeNull();
  });

  it('opens the verse word for word, each numbered word a tap from its entry, with every action still below', async () => {
    apiMock.getBibleInterlinear.mockResolvedValue({ ref: 'John 3:16', available: false, attribution: null, verses: [] });
    apiMock.getBibleLexiconStatus.mockResolvedValue({ installed: true, entries: 14000, occurrences: 200000, source: 's' });
    apiMock.getBibleLexiconVerse.mockResolvedValue({
      reference: 'John 3:16', source: 'concordance', words: [], text_version: 'kjv',
      tokens: [
        { text: 'For' }, { text: ' ' }, { text: 'God', strongs: 'G2316', inferred: false }, { text: ' so ' },
        { text: 'loved', strongs: 'G25', inferred: true }, { text: ' ' }, { text: 'world', strongs: 'G2889', inferred: false }, { text: ',' },
      ],
    });
    apiMock.getBibleLexiconEntry.mockRejectedValue(new Error('not needed'));
    renderSheet();
    const view = await screen.findByTestId('bible-word-for-word');
    expect(view).toHaveTextContent('≈G25');
    expect(screen.getByRole('button', { name: "world, Strong's G2889" })).toHaveTextContent('world,');
    for (const action of ['highlight', 'bookmark', 'study', 'ask', 'memorize', 'copy', 'share', 'blb', 'interlinear', 'word-study']) {
      expect(screen.getByTestId(`bible-action-${action}`)).toBeInTheDocument();
    }
    await userEvent.click(screen.getByRole('button', { name: "loved, Strong's G25 (inferred)" }));
    expect(await screen.findByTestId('bible-word-study')).toBeInTheDocument();
    expect(apiMock.getBibleLexiconEntry).toHaveBeenCalledWith('G25', 0, 50);
  });
});
