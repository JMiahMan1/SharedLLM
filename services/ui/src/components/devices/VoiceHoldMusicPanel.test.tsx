import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import VoiceHoldMusicPanel from './VoiceHoldMusicPanel';
import * as lib from '../../lib/voiceHoldMusic';
import {
  acceptList, clipProblem, linesFromText, linesProblem, type HoldMusicSettings,
} from '../../lib/voiceHoldMusic';

vi.mock('../../lib/voiceHoldMusic', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, getHoldMusic: vi.fn(), setHoldMusic: vi.fn(), uploadHoldMusic: vi.fn(), deleteHoldMusicClip: vi.fn() };
});
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const m = vi.mocked(lib);
const LIMITS = { types: ['wav', 'mp3', 'ogg', 'm4a'], max_bytes: 5 * 1024 * 1024, max_seconds: 60, max_lines: 20, max_line_chars: 160 };

function settings(over: Partial<HoldMusicSettings> = {}): HoldMusicSettings {
  return {
    source: 'gameshow',
    sources: [{ id: 'gameshow', label: 'Game-show thinking' }, { id: 'builtin', label: 'Built-in tune' },
      { id: 'upload', label: 'Upload your own' }],
    lines: [], upload: null, limits: LIMITS, ...over,
  };
}

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  m.getHoldMusic.mockResolvedValue(settings());
});

describe('voiceHoldMusic helpers', () => {
  it('checks a clip by its name and size before it is sent', () => {
    expect(clipProblem({ name: 'theme.MP3', size: 1000 }, LIMITS)).toBeNull();
    expect(clipProblem({ name: 'notes.txt', size: 10 }, LIMITS)).toBe('Choose a wav, mp3, ogg or m4a file');
    expect(clipProblem({ name: 'noext', size: 10 }, LIMITS)).toBe('Choose a wav, mp3, ogg or m4a file');
    expect(clipProblem({ name: 'big.wav', size: 6 * 1048576 }, LIMITS)).toBe('That file is 6.0 MB; the most is 5 MB');
    expect(clipProblem({ name: 'empty.ogg', size: 0 }, LIMITS)).toBe('That file is empty');
  });

  it('reads one line per row and says what would be refused', () => {
    expect(linesFromText('  Hold on.  \n\n Cue   the music.\n')).toEqual(['Hold on.', 'Cue the music.']);
    expect(linesProblem(['x'.repeat(161)], LIMITS)).toBe('Each line can be at most 160 characters');
    expect(linesProblem(Array.from({ length: 21 }, (_, i) => `L${i}`), LIMITS)).toBe('At most 20 lines');
    expect(linesProblem(['Fine.'], LIMITS)).toBeNull();
  });

  it('offers the file chooser only audio types', () => {
    expect(acceptList(LIMITS)).toBe('.wav,audio/wav,.mp3,audio/mpeg,.ogg,audio/ogg,.m4a,audio/mp4');
  });
});

describe('VoiceHoldMusicPanel', () => {
  it('shows the three choices with game-show thinking chosen, and upload waiting for a clip', async () => {
    wrap(<VoiceHoldMusicPanel />);
    const gameshow = await screen.findByRole('radio', { name: /Game-show thinking/ });
    expect((gameshow as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('radio', { name: /Built-in tune/ }) as HTMLInputElement).disabled).toBe(false);
    const upload = screen.getByRole('radio', { name: /Upload your own/ }) as HTMLInputElement;
    expect(upload.disabled).toBe(true);
    expect(screen.getByTestId('hold-music-upload').textContent).toContain('Upload one below first');
    expect(screen.getByRole('button', { name: /Upload a clip/ })).toBeTruthy();
  });

  it('saves a choice at once', async () => {
    m.setHoldMusic.mockResolvedValue(settings({ source: 'builtin' }));
    wrap(<VoiceHoldMusicPanel />);
    fireEvent.click(await screen.findByRole('radio', { name: /Built-in tune/ }));
    await waitFor(() => expect(m.setHoldMusic).toHaveBeenCalledWith({ source: 'builtin' }));
    await waitFor(() => expect((screen.getByRole('radio', { name: /Built-in tune/ }) as HTMLInputElement).checked).toBe(true));
  });

  it('refuses a wrong file before sending it, and sends a right one', async () => {
    m.uploadHoldMusic.mockResolvedValue(settings({
      source: 'upload', upload: { name: 'ours.mp3', type: 'mp3', seconds: 21.5, bytes: 300_000, uploaded_at: 1 },
    }));
    wrap(<VoiceHoldMusicPanel />);
    const input = await screen.findByTestId('hold-music-file');
    fireEvent.change(input, { target: { files: [new File(['hi'], 'notes.txt', { type: 'text/plain' })] } });
    expect((await screen.findByRole('alert')).textContent).toBe('Choose a wav, mp3, ogg or m4a file');
    expect(m.uploadHoldMusic).not.toHaveBeenCalled();
    const clip = new File(['ID3'], 'ours.mp3', { type: 'audio/mpeg' });
    fireEvent.change(input, { target: { files: [clip] } });
    await waitFor(() => expect(m.uploadHoldMusic).toHaveBeenCalled());
    expect(m.uploadHoldMusic.mock.calls[0][0]).toBe(clip);
    await waitFor(() => expect((screen.getByRole('radio', { name: /Upload your own/ }) as HTMLInputElement).checked).toBe(true));
    expect(screen.getByTestId('hold-music-clip').textContent).toContain('ours.mp3 · 21.5 s');
    expect(screen.getByRole('button', { name: /Replace the clip/ })).toBeTruthy();
  });

  it('shows what the hub said when it refused a clip', async () => {
    const err = Object.assign(new Error('bad'), { isAxiosError: true, response: { data: { detail: 'The clip is longer than 60 s; trim it and upload it again' } } });
    m.uploadHoldMusic.mockRejectedValue(err);
    wrap(<VoiceHoldMusicPanel />);
    fireEvent.change(await screen.findByTestId('hold-music-file'), { target: { files: [new File(['RIFF'], 'long.wav')] } });
    expect((await screen.findByRole('alert')).textContent).toContain('longer than 60 s');
  });

  it('removes the clip', async () => {
    m.getHoldMusic.mockResolvedValue(settings({
      source: 'upload', upload: { name: 'ours.wav', type: 'wav', seconds: 30, bytes: 1, uploaded_at: 1, missing: true },
    }));
    m.deleteHoldMusicClip.mockResolvedValue(settings());
    wrap(<VoiceHoldMusicPanel />);
    expect((await screen.findByTestId('hold-music-clip')).textContent).toContain('the built-in tune plays');
    fireEvent.click(screen.getByRole('button', { name: 'Remove the uploaded clip' }));
    await waitFor(() => expect(m.deleteHoldMusicClip).toHaveBeenCalled());
    await waitFor(() => expect((screen.getByRole('radio', { name: /Game-show thinking/ }) as HTMLInputElement).checked).toBe(true));
  });

  it('saves the hold lines, and goes back to the defaults', async () => {
    m.setHoldMusic.mockResolvedValueOnce(settings({ lines: ['Cue the music.', 'Thinking...'] }))
      .mockResolvedValueOnce(settings({ lines: [] }));
    wrap(<VoiceHoldMusicPanel />);
    const box = await screen.findByLabelText('Hold lines');
    const save = screen.getByRole('button', { name: 'Save lines' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(box, { target: { value: 'Cue the music.\n\nThinking...' } });
    fireEvent.click(save);
    await waitFor(() => expect(m.setHoldMusic).toHaveBeenCalledWith({ lines: ['Cue the music.', 'Thinking...'] }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use the default lines' }));
    await waitFor(() => expect(m.setHoldMusic).toHaveBeenLastCalledWith({ lines: [] }));
  });

  it('does not save lines that would be refused', async () => {
    wrap(<VoiceHoldMusicPanel />);
    fireEvent.change(await screen.findByLabelText('Hold lines'), { target: { value: 'x'.repeat(161) } });
    expect((await screen.findByRole('alert')).textContent).toBe('Each line can be at most 160 characters');
    expect((screen.getByRole('button', { name: 'Save lines' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
