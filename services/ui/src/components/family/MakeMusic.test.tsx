import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import MakeMusic from './MakeMusic';
import { songStatus } from './songShapes';
import { api } from '../../services/api';
import type { SongJob } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), startSong: vi.fn(), getSong: vi.fn(), cancelSong: vi.fn() },
  };
});

const startSong = vi.mocked(api.startSong);
const getSong = vi.mocked(api.getSong);
const cancelSong = vi.mocked(api.cancelSong);

const job = (over: Partial<SongJob>): SongJob => ({ id: 'abc123', status: 'queued', position: 1, ...over });

async function advance(ms = 2600) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('MakeMusic', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    localStorage.clear();
  });
  afterEach(() => vi.useRealTimers());

  it('queues a whole song with the chosen shape and length', async () => {
    startSong.mockResolvedValue(job({ position: 3 }));
    getSong.mockResolvedValue(job({ position: 3 }));
    render(<MakeMusic />);

    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'a slow turtle song' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Pop' }));
    fireEvent.change(screen.getByLabelText('Song length'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));

    await waitFor(() =>
      expect(startSong).toHaveBeenCalledWith({ prompt: 'a slow turtle song', duration_s: 200, structure: 'pop' }),
    );
    expect(await screen.findByTestId('song-status')).toHaveTextContent('2 songs ahead');
  });

  it('a clip is one section, no structure', async () => {
    startSong.mockResolvedValue(job({}));
    getSong.mockResolvedValue(job({}));
    render(<MakeMusic />);
    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'tune' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Just a clip' }));
    expect((screen.getByLabelText('Song length') as HTMLInputElement).max).toBe('26');
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));
    await waitFor(() => expect(startSong).toHaveBeenCalledWith(expect.objectContaining({ structure: 'loop', duration_s: 12 })));
  });

  it('follows the song section by section and plays it when done', async () => {
    startSong.mockResolvedValue(job({}));
    getSong
      .mockResolvedValueOnce(job({ status: 'running', progress: { done: 1, total: 6, section: 'chorus' } }))
      .mockResolvedValueOnce(job({ status: 'done', audio_url: 'data:audio/wav;base64,BB', meta: { duration_s: 118 } }));
    render(<MakeMusic />);
    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'tune' } });
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));
    expect(await screen.findByTestId('song-status')).toHaveTextContent('Up next');

    await advance();
    await waitFor(() => expect(screen.getByTestId('song-status')).toHaveTextContent('Writing the chorus (2 of 6)'));
    await advance();
    expect(await screen.findByTestId('music-audio')).toHaveAttribute('src', 'data:audio/wav;base64,BB');
    expect(screen.getByTestId('song-status')).toHaveTextContent('1:58');
  });

  it('picks a song back up after leaving the page', async () => {
    localStorage.setItem('make-music-job', 'abc123');
    getSong.mockResolvedValue(job({ status: 'running', progress: { done: 0, total: 4, section: 'intro' } }));
    render(<MakeMusic />);
    await waitFor(() => expect(screen.getByTestId('song-status')).toHaveTextContent('Writing the intro (1 of 4)'));
    expect(getSong).toHaveBeenCalledWith('abc123');
  });

  it("shows the server's refusal", async () => {
    startSong.mockRejectedValue({ response: { data: { status: 'ERROR', message: 'Music generation is not configured' } } });
    render(<MakeMusic />);
    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'tune' } });
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Music generation is not configured');
  });

  it('a queued song can be cancelled', async () => {
    startSong.mockResolvedValue(job({ position: 2 }));
    getSong.mockResolvedValue(job({ position: 2 }));
    cancelSong.mockResolvedValue(job({ status: 'cancelled' }));
    render(<MakeMusic />);
    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'tune' } });
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));
    fireEvent.click(await screen.findByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(cancelSong).toHaveBeenCalledWith('abc123'));
    expect(await screen.findByTestId('song-status')).toHaveTextContent('Cancelled');
    expect(localStorage.getItem('make-music-job')).toBeNull();
  });

  it('fills the prompt from a preset', () => {
    render(<MakeMusic />);
    fireEvent.click(screen.getByRole('button', { name: 'Calm piano' }));
    expect((screen.getByLabelText('Song prompt') as HTMLInputElement).value).toContain('calm piano');
  });
});

describe('songStatus', () => {
  it('says plainly where the song is', () => {
    expect(songStatus(job({ position: 1 }))).toBe('Up next');
    expect(songStatus(job({ status: 'running', progress: { done: 4, total: 4, section: 'mixing' } }))).toContain('Mixing');
    expect(songStatus(job({ status: 'error', error: 'music generation failed: x' }))).toContain('failed');
  });
});
