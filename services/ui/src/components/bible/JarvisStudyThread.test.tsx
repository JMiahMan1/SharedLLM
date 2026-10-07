import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import JarvisStudyThread from './JarvisStudyThread';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({ askBibleStudy: vi.fn() }));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

function answer(over: Partial<Record<string, unknown>> = {}) {
  return {
    reference: 'John 3:16',
    version: 'nkjv',
    edition: 'nkjv-tmn',
    edition_name: 'NKJV Study Bible',
    question: 'What does this mean?',
    answer: 'It is about love for the whole world.',
    verses_used: 1,
    notes_used: 3,
    ...over,
  };
}

function renderPanel(props: Partial<React.ComponentProps<typeof JarvisStudyThread>> = {}) {
  const onClose = vi.fn();
  render(
    <JarvisStudyThread
      passage="John 3:16"
      version="nkjv"
      edition="nkjv-tmn"
      onClose={onClose}
      {...props}
    />,
  );
  return { onClose };
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

  it('asks with the reader\'s translation and study Bible', async () => {
    apiMock.askBibleStudy.mockResolvedValue(answer());
    renderPanel();
    await userEvent.type(screen.getByTestId('bible-ask-question'), 'What does this mean?');
    await userEvent.click(screen.getByTestId('bible-ask-submit'));

    await waitFor(() => expect(apiMock.askBibleStudy).toHaveBeenCalledTimes(1));
    expect(apiMock.askBibleStudy).toHaveBeenCalledWith('John 3:16', 'What does this mean?', {
      version: 'nkjv',
      edition: 'nkjv-tmn',
      crossVersion: false,
    });
    expect(await screen.findByTestId('bible-ask-answer')).toHaveTextContent(
      'It is about love for the whole world.',
    );
  });

  it('says how much the answer had to read, so "it does not say" is distinguishable', async () => {
    apiMock.askBibleStudy.mockResolvedValue(answer({ verses_used: 4, notes_used: 0 }));
    renderPanel();
    await userEvent.type(screen.getByTestId('bible-ask-question'), 'Why?');
    await userEvent.click(screen.getByTestId('bible-ask-submit'));
    const provenance = await screen.findByTestId('bible-ask-provenance');
    expect(provenance).toHaveTextContent('Read 4 verses and 0 notes');
  });

  it('shows a failure as the model\'s own sentence and keeps the question to retry', async () => {
    apiMock.askBibleStudy.mockRejectedValue(
      new Error('Study help could not reach the language model: gateway down'),
    );
    renderPanel();
    await userEvent.type(screen.getByTestId('bible-ask-question'), 'Why?');
    await userEvent.click(screen.getByTestId('bible-ask-submit'));

    const failure = await screen.findByTestId('bible-ask-error');
    expect(failure).toHaveTextContent('gateway down');
    expect(screen.queryByTestId('bible-ask-answer')).not.toBeInTheDocument();
    // The question stays in the box: the reader can press Ask again.
    expect(screen.getByTestId('bible-ask-question')).toHaveValue('Why?');
  });

  it('offers starter questions that fill the box without asking on their own', async () => {
    renderPanel();
    const starters = screen.getByTestId('bible-ask-starters');
    const first = starters.querySelector('button')!;
    await userEvent.click(first);
    expect(screen.getByTestId('bible-ask-question')).toHaveValue(first.textContent);
    expect(apiMock.askBibleStudy).not.toHaveBeenCalled();
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
