import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MediaExplorer } from './MediaExplorer';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getMusicAssistantPlaylists: vi.fn().mockResolvedValue({ playlists: [] }),
      getMusicAssistantRecent: vi.fn().mockResolvedValue({ recent: [] }),
      getAudiobookshelfLibraries: vi.fn(),
      getAudiobookshelfLibrary: vi.fn(),
      searchAudiobookshelf: vi.fn(),
      getPodcastEpisodes: vi.fn(),
    },
  };
});

const EPISODE_URI = 'audiobookshelf--x://podcast_episode/pod-1 ep-2';

function open(onPlay = vi.fn().mockResolvedValue(undefined)) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MediaExplorer open onClose={() => {}} onPlay={onPlay} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('tab', { name: /Audiobooks/ }));
  return onPlay;
}

describe('MediaExplorer podcasts (BUG-43)', () => {
  beforeEach(() => {
    vi.mocked(api.getAudiobookshelfLibraries).mockResolvedValue({
      status: 'SUCCESS',
      libraries: [{ id: 'lib-p', name: 'Podcasts', media_type: 'podcast' }],
    });
    vi.mocked(api.getAudiobookshelfLibrary).mockResolvedValue({
      status: 'SUCCESS',
      books: [{ id: 'pod-1', title: 'The Show', author: 'Host' }],
    });
    vi.mocked(api.getPodcastEpisodes).mockResolvedValue({
      status: 'SUCCESS',
      episodes: [
        { uri: EPISODE_URI, name: 'Episode 2', duration: 2520, published: Date.UTC(2026, 9, 12), fully_played: null, resume_position_ms: null },
        { uri: 'audiobookshelf--x://podcast_episode/pod-1 ep-1', name: 'Episode 1', duration: 1800, published: Date.UTC(2026, 9, 5), fully_played: true, resume_position_ms: null },
      ],
    });
  });

  it('opens a podcast to its episodes and plays the one picked', async () => {
    const onPlay = open();
    fireEvent.click(await screen.findByText('Podcasts'));
    fireEvent.click(await screen.findByTestId('podcast-pod-1'));

    expect(await screen.findByText('Episode 2')).toBeInTheDocument();
    expect(api.getPodcastEpisodes).toHaveBeenCalledWith('pod-1', 'The Show');
    expect(screen.getByText('played')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Episode 2'));
    await waitFor(() => expect(onPlay).toHaveBeenCalled());
    expect(onPlay.mock.calls[0][0]).toEqual({ kind: 'uri', uri: EPISODE_URI, title: 'Episode 2' });
  });

  it('opens a podcast found by search instead of playing the whole show', async () => {
    vi.mocked(api.searchAudiobookshelf).mockResolvedValue({
      status: 'SUCCESS', books: [], authors: [],
      podcasts: [{ id: 'pod-1', title: 'The Show', author: 'Host' }],
    });
    const onPlay = open();
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'show' } });
    fireEvent.click(await screen.findByTestId('podcast-pod-1'));
    expect(await screen.findByText('Episode 1')).toBeInTheDocument();
    expect(onPlay).not.toHaveBeenCalled();
  });

  it('says why when the episodes cannot be loaded', async () => {
    vi.mocked(api.getPodcastEpisodes).mockRejectedValue(new Error('Music Assistant does not have it'));
    open();
    fireEvent.click(await screen.findByText('Podcasts'));
    fireEvent.click(await screen.findByTestId('podcast-pod-1'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load episodes');
  });
});
