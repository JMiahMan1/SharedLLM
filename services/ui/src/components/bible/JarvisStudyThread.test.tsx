import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import JarvisStudyThread from './JarvisStudyThread';
import type { BibleStudyStreamEvent } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({ streamBibleStudy: vi.fn(), getBiblePassage: vi.fn() }));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const START: Extract<BibleStudyStreamEvent, { type: 'start' }> = {
  type: 'start',
  reference: 'John 3:16',
  version: 'nkjv',
  edition: 'nkjv-tmn',
  edition_name: 'NKJV Study Bible',
  depth: 'adult',
  verses_used: 1,
  notes_used: 3,
};

type OnEvent = (e: BibleStudyStreamEvent) => void;

/** A stream that sends these events, in order, then ends. */
function streams(events: BibleStudyStreamEvent[]) {
  apiMock.streamBibleStudy.mockImplementation(async (_ref: string, _q: string, _opts: unknown, onEvent: OnEvent) => {
    for (const event of events) onEvent(event);
  });
}

function answering(text: string, opts: { verses?: number; notes?: number; references?: string[] } = {}) {
  streams([
    { ...START, verses_used: opts.verses ?? 1, notes_used: opts.notes ?? 3 },
    { type: 'text', text: text.slice(0, 10) },
    { type: 'text', text: text.slice(10) },
    { type: 'done', answer: text, references: opts.references ?? [] },
  ]);
}

function renderPanel(props: Partial<React.ComponentProps<typeof JarvisStudyThread>> = {}) {
  const onClose = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <JarvisStudyThread passage="John 3:16" version="nkjv" edition="nkjv-tmn" onClose={onClose} {...props} />
    </QueryClientProvider>,
  );
  return { onClose };
}

async function askIt(question: string) {
  await userEvent.type(screen.getByTestId('bible-ask-question'), question);
  await userEvent.click(screen.getByTestId('bible-ask-submit'));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('asking about the passage on screen', () => {
  it('names the passage so the question cannot be about somewhere else', () => {
    renderPanel();
    expect(screen.getByTestId('bible-ask-ref')).toHaveTextContent('John 3:16');
    expect(screen.getByTestId('bible-ask-panel')).toBeInTheDocument();
  });

  it("asks with the reader's translation and study Bible, and shows the streamed answer", async () => {
    answering('It is about love for the whole world.');
    renderPanel();
    await askIt('What does this mean?');

    await waitFor(() => expect(apiMock.streamBibleStudy).toHaveBeenCalledTimes(1));
    expect(apiMock.streamBibleStudy).toHaveBeenCalledWith(
      'John 3:16',
      'What does this mean?',
      { version: 'nkjv', edition: 'nkjv-tmn', crossVersion: false, depth: 'adult' },
      expect.any(Function),
      expect.any(AbortSignal),
    );
    expect(await screen.findByTestId('bible-ask-answer')).toHaveTextContent('It is about love for the whole world.');
    expect(screen.queryByTestId('bible-ask-streaming')).not.toBeInTheDocument();
  });

  it('shows the answer as it is written, before it is finished', async () => {
    let finish: () => void = () => {};
    apiMock.streamBibleStudy.mockImplementation(
      (_r: string, _q: string, _o: unknown, onEvent: OnEvent) =>
        new Promise<void>((resolve) => {
          onEvent(START);
          onEvent({ type: 'text', text: 'God so ' });
          finish = () => {
            onEvent({ type: 'done', answer: 'God so loved.', references: [] });
            resolve();
          };
        }),
    );
    renderPanel();
    await askIt('Why?');
    expect(await screen.findByTestId('bible-ask-answer')).toHaveTextContent('God so');
    expect(screen.getByTestId('bible-ask-streaming')).toBeInTheDocument();
    expect(screen.queryByTestId('bible-ask-provenance')).not.toBeInTheDocument();
    finish();
    expect(await screen.findByTestId('bible-ask-provenance')).toBeInTheDocument();
  });

  it('says how much the answer had to read, so "it does not say" is distinguishable', async () => {
    answering('The passage does not say.', { verses: 4, notes: 0 });
    renderPanel();
    await askIt('Why?');
    expect(await screen.findByTestId('bible-ask-provenance')).toHaveTextContent('Read 4 verses and 0 notes');
  });

  it("shows a failure as the model's own sentence and keeps the question to retry", async () => {
    streams([START, { type: 'error', detail: 'Study help could not reach the language model: gateway down' }]);
    renderPanel();
    await askIt('Why?');
    expect(await screen.findByTestId('bible-ask-error')).toHaveTextContent('gateway down');
    expect(screen.queryByTestId('bible-ask-answer')).not.toBeInTheDocument();
    expect(screen.getByTestId('bible-ask-question')).toHaveValue('Why?');
  });

  it('reports a stream that could not start the same way', async () => {
    apiMock.streamBibleStudy.mockRejectedValue(new Error('Study help unavailable (502)'));
    renderPanel();
    await askIt('Why?');
    expect(await screen.findByTestId('bible-ask-error')).toHaveTextContent('Study help unavailable (502)');
    expect(screen.getByTestId('bible-ask-question')).toHaveValue('Why?');
  });

  it('offers starter questions that fill the box without asking on their own', async () => {
    renderPanel();
    const first = screen.getByTestId('bible-ask-starters').querySelector('button')!;
    await userEvent.click(first);
    expect(screen.getByTestId('bible-ask-question')).toHaveValue(first.textContent);
    expect(apiMock.streamBibleStudy).not.toHaveBeenCalled();
  });

  it('stops the answer being written when the panel closes', async () => {
    let signal: AbortSignal | undefined;
    apiMock.streamBibleStudy.mockImplementation(
      (_r: string, _q: string, _o: unknown, _e: OnEvent, s: AbortSignal) => {
        signal = s;
        return new Promise(() => undefined);
      },
    );
    const user = userEvent.setup();
    const { unmount } = render(<JarvisStudyThread passage="John 3:16" version="nkjv" onClose={vi.fn()} />);
    await user.type(screen.getByTestId('bible-ask-question'), 'Why?');
    await user.click(screen.getByTestId('bible-ask-submit'));
    await waitFor(() => expect(signal).toBeDefined());
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it('closes from the scrim', async () => {
    const { onClose } = renderPanel();
    await userEvent.click(screen.getByTestId('bible-ask-scrim'));
    expect(onClose).toHaveBeenCalled();
  });

  it('will not ask an empty question', () => {
    renderPanel();
    expect(screen.getByTestId('bible-ask-submit')).toBeDisabled();
  });
});

describe('depth and references', () => {
  it('sends the chosen depth and turns named references into chips', async () => {
    const user = userEvent.setup();
    const onOpenRef = vi.fn();
    answering('See Romans 5:8 for more.', { references: ['Romans 5:8'] });
    renderPanel({ onOpenRef });
    await user.click(screen.getByTestId('bible-ask-depth-child'));
    await askIt('Why?');
    expect(apiMock.streamBibleStudy).toHaveBeenLastCalledWith(
      'John 3:16',
      'Why?',
      expect.objectContaining({ depth: 'child' }),
      expect.any(Function),
      expect.any(AbortSignal),
    );
    apiMock.getBiblePassage.mockResolvedValue({
      version: 'nkjv',
      requested: 'Romans 5:8',
      reference: 'Romans 5:8',
      spans: [],
      verses: [
        {
          version: 'nkjv',
          osis: 'Rom',
          book_name: 'Romans',
          chapter: 5,
          verse: 8,
          reference: 'Romans 5:8',
          text: 'But God demonstrates His own love toward us.',
        },
      ],
      count: 1,
    });
    // A chip shows the passage first, in the translation being studied; the
    // reader is one more tap away.
    await user.click(await screen.findByRole('button', { name: /Romans 5:8/ }));
    expect(await screen.findByTestId('reference-popover-text')).toHaveTextContent('But God demonstrates');
    expect(apiMock.getBiblePassage).toHaveBeenCalledWith('Romans 5:8', 'nkjv');
    await user.click(screen.getByTestId('reference-popover-open'));
    expect(onOpenRef).toHaveBeenCalledWith('Romans 5:8');
  });
});
