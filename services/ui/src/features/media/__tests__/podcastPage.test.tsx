import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { renderWithProviders } from '../../../test/render';
import episodesFixture from '../../../../e2e/fixtures/media/podcast-episodes.json';

const web = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock('../../../context/MediaPlayerContext', () => ({ useSharedMAWebPlayer: () => web.state }));

import Media from '../../../pages/Media';

function player() {
  return {
    isConnected: true, connectionState: 'CONNECTED', isPlaying: false, mediaTitle: null, mediaArtist: null, mediaImage: null,
    mediaUri: null, mediaFavorite: null, position: 0, duration: 0, volume: 70, muted: false, error: null, reconnectAttempts: 0,
    maxReconnectAttempts: 10, playerId: 'me', connect: vi.fn(), reconnect: vi.fn(), play: vi.fn().mockResolvedValue(undefined),
    enqueue: vi.fn().mockResolvedValue(undefined), pause: vi.fn(), stop: vi.fn(), next: vi.fn(), previous: vi.fn(), seek: vi.fn(),
    setVolume: vi.fn(), setMuted: vi.fn(), maCommand: vi.fn(), listMaPlayers: vi.fn().mockResolvedValue([]),
  };
}

const PODCAST = {
  uri: 'abs://pod-1', name: 'The Quiet Hour', media_type: 'podcast', artist: 'Ada Lane', album: '', image: '/api/items/pod-1/cover',
  duration: null, description: 'Slow conversations.', children: [], errors: { ma: null, abs: null },
};

const renderAt = (route: string) => renderWithProviders(<Routes><Route path="/media/*" element={<Media />} /></Routes>, { route });

let asked: Array<{ id: string | null; title: string | null }> = [];
beforeEach(() => {
  web.state = player();
  asked = [];
  server.use(
    http.get('/api/media/item', () => HttpResponse.json(PODCAST)),
    http.get('/api/media/podcast-episodes', ({ request }) => {
      const p = new URL(request.url).searchParams;
      asked.push({ id: p.get('abs_item_id'), title: p.get('title') });
      return HttpResponse.json(episodesFixture);
    }),
    http.post('/execute/media/status', () => HttpResponse.json({ status: 'SUCCESS', detail: { active: null, all_players: [] } })),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
  );
});

describe('PodcastPage (P5-T6)', () => {
  it('BUG-43 lists an Audiobookshelf podcast\'s episodes newest first with their state', async () => {
    renderAt('/media/podcast/pod-1');
    expect(await screen.findByRole('heading', { name: 'The Quiet Hour', level: 1 })).toBeInTheDocument();
    const list = within(await screen.findByRole('list', { name: 'Episodes' }));
    const rows = list.getAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('Episode 2: Slow Mornings');
    expect(rows[0]).toHaveTextContent('31m left');
    expect(rows[1]).toHaveTextContent('Played');
    expect(asked).toEqual([{ id: 'pod-1', title: 'The Quiet Hour' }]);
  });

  it('plays the latest episode, and any one picked (journey 9)', async () => {
    renderAt('/media/podcast/pod-1');
    await userEvent.click(await screen.findByRole('button', { name: 'Play latest' }));
    await waitFor(() => expect(web.state.play).toHaveBeenCalledWith('audiobookshelf--abs1://podcast_episode/pod-1 ep-2'));
    await userEvent.click(screen.getByRole('button', { name: 'Play Episode 1: Starting Over' }));
    await waitFor(() => expect(web.state.play).toHaveBeenLastCalledWith('audiobookshelf--abs1://podcast_episode/pod-1 ep-1'));
  });

  it('lists a Music Assistant podcast\'s own episodes', async () => {
    server.use(http.get('/api/media/item', () => HttpResponse.json({
      ...PODCAST, uri: 'library://podcast/3',
      children: [{ uri: 'library://podcast_episode/9', name: 'Pilot', media_type: 'podcast_episode', artist: '', artist_uri: '', album: '', album_uri: '', image: '', duration: 600, index: 1 }],
    })));
    renderAt('/media/podcast/library/3');
    await userEvent.click(await screen.findByRole('button', { name: 'Play Pilot' }));
    await waitFor(() => expect(web.state.play).toHaveBeenCalledWith('library://podcast_episode/9'));
    expect(asked).toEqual([]);
  });

  it('says when Music Assistant cannot find the podcast\'s episodes, or there are none', async () => {
    server.use(http.get('/api/media/podcast-episodes', () => HttpResponse.json({ detail: 'not in MA' }, { status: 404 })));
    const a = renderAt('/media/podcast/pod-1');
    expect(await screen.findByText('Couldn’t load the episodes', {}, { timeout: 5000 })).toBeInTheDocument();
    a.unmount();
    server.use(http.get('/api/media/podcast-episodes', () => HttpResponse.json({ status: 'SUCCESS', episodes: [] })));
    renderAt('/media/podcast/pod-1');
    expect(await screen.findByText('No episodes yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Play latest' })).not.toBeInTheDocument();
  });

  it('says when the podcast is gone', async () => {
    server.use(http.get('/api/media/item', () => HttpResponse.json({ detail: 'x' }, { status: 404 })));
    renderAt('/media/podcast/gone');
    expect(await screen.findByText('This podcast isn’t in your library', {}, { timeout: 5000 })).toBeInTheDocument();
  });
});
