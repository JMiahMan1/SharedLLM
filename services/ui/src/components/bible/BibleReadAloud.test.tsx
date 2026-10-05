import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleReadAloud from './BibleReadAloud';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBibleVoices: vi.fn(),
  getBibleNarration: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

/** jsdom has no media stack, so the element is driven from the test. */
function audioElement(): HTMLAudioElement {
  return screen.getByTestId('bible-read-aloud-audio') as unknown as HTMLAudioElement;
}

function narration(over: Record<string, unknown> = {}) {
  return {
    version: 'nkjv',
    reference: 'John 3',
    voice: 'af_heart',
    verse_count: 36,
    cached: false,
    mime_type: 'audio/wav',
    length_bytes: 4,
    audio_base64: 'AAAA',
    ...over,
  };
}

function renderPanel(voice = '') {
  const onVoiceChange = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BibleReadAloud
        reference="John 3"
        version="nkjv"
        voice={voice}
        onVoiceChange={onVoiceChange}
      />
    </QueryClientProvider>,
  );
  return { onVoiceChange };
}

describe('BibleReadAloud', () => {
  let blobUrls = 0;

  beforeEach(() => {
    blobUrls = 0;
    apiMock.getBibleVoices.mockResolvedValue({ voices: ['af_heart'], count: 1 });
    apiMock.getBibleNarration.mockResolvedValue(narration());
    URL.createObjectURL = vi.fn(() => {
      blobUrls += 1;
      return `blob:verse-${blobUrls}`;
    }) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
  });

  afterEach(() => vi.clearAllMocks());

  it('offers to read the passage that is on screen', async () => {
    renderPanel();
    expect(await screen.findByTestId('bible-read-aloud')).toBeTruthy();
    expect(screen.getByTestId('bible-read-aloud-play').textContent).toContain('Read this passage');
    expect(apiMock.getBibleNarration).not.toHaveBeenCalled();
  });

  it('asks the gateway for the passage in the open translation and plays it', async () => {
    renderPanel();
    await userEvent.click(screen.getByTestId('bible-read-aloud-play'));
    await waitFor(() => expect(apiMock.getBibleNarration).toHaveBeenCalledWith('John 3', 'nkjv', undefined));
    await waitFor(() => expect(audioElement().src).toBe('blob:verse-1'));
    expect(screen.getByTestId('bible-read-aloud-play').textContent).toContain('Pause');
  });

  it('says a second play of the same passage came from the cache', async () => {
    apiMock.getBibleNarration.mockResolvedValue(narration({ cached: true }));
    renderPanel();
    await userEvent.click(screen.getByTestId('bible-read-aloud-play'));
    expect(await screen.findByText(/from the narration cache/i)).toBeTruthy();
  });

  it('passes the chosen voice through and remembers the change', async () => {
    apiMock.getBibleVoices.mockResolvedValue({ voices: ['af_heart', 'af_bella'], count: 2 });
    const { onVoiceChange } = renderPanel();
    const select = (await screen.findByTestId('bible-read-aloud-voice')) as HTMLSelectElement;
    await userEvent.selectOptions(select, 'af_bella');
    expect(onVoiceChange).toHaveBeenCalledWith('af_bella');
  });

  it('narration uses the voice the reader chose', async () => {
    renderPanel('af_bella');
    await userEvent.click(await screen.findByTestId('bible-read-aloud-play'));
    await waitFor(() =>
      expect(apiMock.getBibleNarration).toHaveBeenCalledWith('John 3', 'nkjv', 'af_bella'),
    );
  });

  it('shows the server sentence when the engine cannot narrate', async () => {
    apiMock.getBibleNarration.mockRejectedValue(
      new Error('Kokoro voices missing. Install them with POST /execute/tts/download?voice_type=kokoro-v1.0'),
    );
    renderPanel();
    await userEvent.click(screen.getByTestId('bible-read-aloud-play'));
    expect(await screen.findByTestId('bible-read-aloud-message')).toHaveTextContent(
      /Kokoro voices missing/,
    );
    expect(screen.getByTestId('bible-read-aloud-play').textContent).toContain('Read this passage');
  });

  it('shows the refusal when a passage is too long to narrate', async () => {
    apiMock.getBibleNarration.mockRejectedValue(
      new Error('That passage is 200 verses. Select a shorter passage.'),
    );
    renderPanel();
    await userEvent.click(screen.getByTestId('bible-read-aloud-play'));
    expect(await screen.findByTestId('bible-read-aloud-message')).toHaveTextContent(
      'Select a shorter passage.',
    );
  });

  it('says why no voice list is available without blocking the button', async () => {
    apiMock.getBibleVoices.mockRejectedValue(new Error('EXECUTION_SVC_URL is not configured.'));
    renderPanel();
    expect(await screen.findByTestId('bible-read-aloud-voices-error')).toHaveTextContent(
      'EXECUTION_SVC_URL is not configured.',
    );
    expect(screen.getByTestId('bible-read-aloud-play')).not.toBeDisabled();
  });

  it('hides the voice picker when there is only one voice', async () => {
    renderPanel();
    await screen.findByTestId('bible-read-aloud-play');
    expect(screen.queryByTestId('bible-read-aloud-voice')).toBeNull();
  });

  it('pauses and resumes without asking for the passage again', async () => {
    renderPanel();
    const play = screen.getByTestId('bible-read-aloud-play');
    await userEvent.click(play);
    await waitFor(() => expect(audioElement().src).toBe('blob:verse-1'));
    const element = audioElement();
    vi.spyOn(element, 'pause').mockImplementation(() => {});
    vi.spyOn(element, 'play').mockResolvedValue(undefined);
    await userEvent.click(play);
    expect(element.pause).toHaveBeenCalled();
    expect(play.textContent).toContain('Resume');
    await userEvent.click(play);
    expect(element.play).toHaveBeenCalledTimes(1);
    expect(apiMock.getBibleNarration).toHaveBeenCalledTimes(1);
  });
});