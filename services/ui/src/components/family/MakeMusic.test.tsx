import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import MakeMusic from './MakeMusic';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), generateMusic: vi.fn() },
  };
});

const generateMusic = vi.mocked(api.generateMusic);

function renderMusic() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MakeMusic />
    </QueryClientProvider>,
  );
}

describe('MakeMusic', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sends the typed prompt and length', async () => {
    generateMusic.mockResolvedValue({ status: 'SUCCESS', audio_url: 'data:audio/wav;base64,AA', mime: 'audio/wav' });
    renderMusic();

    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'a slow turtle song' } });
    fireEvent.change(screen.getByLabelText('Song length'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));

    await waitFor(() =>
      expect(generateMusic).toHaveBeenCalledWith({ prompt: 'a slow turtle song', duration_s: 12 }),
    );
  });

  it('fills the prompt from a preset', () => {
    renderMusic();
    fireEvent.click(screen.getByRole('button', { name: 'Calm piano' }));
    expect((screen.getByLabelText('Song prompt') as HTMLInputElement).value).toContain('calm piano');
  });

  it('plays the song it gets back', async () => {
    generateMusic.mockResolvedValue({ status: 'SUCCESS', audio_url: 'data:audio/wav;base64,BB', mime: 'audio/wav' });
    renderMusic();

    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'tune' } });
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));

    await waitFor(() => expect(screen.getByTestId('music-audio')).toBeTruthy());
    expect(screen.getByTestId('music-audio').getAttribute('src')).toBe('data:audio/wav;base64,BB');
  });

  it('shows the backend refusal instead of a silent button', async () => {
    generateMusic.mockResolvedValue({ status: 'ERROR', message: 'Music generation is not configured' });
    renderMusic();

    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'tune' } });
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('Music generation is not configured'),
    );
    expect(screen.queryByTestId('music-audio')).toBeNull();
  });

  it('reports a thrown failure too', async () => {
    generateMusic.mockRejectedValue(new Error('Audio backend unreachable'));
    renderMusic();

    fireEvent.change(screen.getByLabelText('Song prompt'), { target: { value: 'tune' } });
    fireEvent.click(screen.getByRole('button', { name: /make the song/i }));

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Audio backend unreachable'));
  });
});
