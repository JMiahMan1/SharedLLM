import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { renderWithProviders } from '../../../test/render';
import albumFixture from '../../../../e2e/fixtures/media/media-item.json';

const web = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock('../../../context/MediaPlayerContext', () => ({ useSharedMAWebPlayer: () => web.state }));

import Media from '../../../pages/Media';

function player() {
  return {
    isConnected: true, connectionState: 'CONNECTED', isPlaying: false, mediaTitle: null, mediaArtist: null, mediaImage: null,
    mediaUri: null, mediaFavorite: null, position: 0, duration: 0, volume: 70, muted: false, error: null, reconnectAttempts: 0,
    maxReconnectAttempts: 10, playerId: 'me', connect: vi.fn(), reconnect: vi.fn(), play: vi.fn().mockResolvedValue(undefined),
    enqueue: vi.fn().mockResolvedValue(undefined), pause: vi.fn(), stop: vi.fn(), next: vi.fn(), previous: vi.fn(), seek: vi.fn(),
    setVolume: vi.fn(), setMuted: vi.fn(), maCommand: vi.fn().mockResolvedValue({}), listMaPlayers: vi.fn().mockResolvedValue([]),
  };
}

const renderAt = (route: string) => renderWithProviders(
  <Routes><Route path="/media/*" element={<Media />} /></Routes>,
  { route },
);

let asked: string[] = [];
beforeEach(() => {
  web.state = player();
  asked = [];
  server.use(
    http.get('/api/media/item', ({ request }) => {
      asked.push(new URL(request.url).searchParams.get('uri') || '');
      return HttpResponse.json(albumFixture);
    }),
    http.post('/execute/media/status', () => HttpResponse.json({ status: 'SUCCESS', detail: { active: null, all_players: [] } })),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
  );
});

describe('CollectionPage (P5-T4)', () => {
  it('shows an album with numbered tracks and plays it on this device', async () => {
    renderAt('/media/album/library/1');
    expect(await screen.findByRole('heading', { name: 'Neon Nights', level: 1 })).toBeInTheDocument();
    expect(asked).toEqual(['library://album/1']);
    expect(screen.getByText(/3 songs · 10:40/)).toBeInTheDocument();
    const tracks = within(screen.getByRole('list', { name: 'Tracks' }));
    expect(tracks.getAllByRole('listitem')).toHaveLength(3);
    expect(tracks.getByText('2')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Play' }));
    await waitFor(() => expect(web.state.play).toHaveBeenCalledWith('library://album/1'));
  });

  it('Shuffle fills the queue and turns shuffle on (journey 4)', async () => {
    server.use(http.get('/api/media/item', () => HttpResponse.json({ ...albumFixture, uri: 'library://playlist/1', name: 'Mix', media_type: 'playlist' })));
    renderAt('/media/playlist/library/1');
    await userEvent.click(await screen.findByRole('button', { name: 'Shuffle' }));
    await waitFor(() => expect(web.state.maCommand).toHaveBeenCalledWith('player_queues/shuffle', { queue_id: 'me', shuffle_enabled: true }));
    expect(web.state.play).toHaveBeenCalledWith('library://playlist/1');
  });

  it('plays one track from the list', async () => {
    renderAt('/media/album/library/1');
    await userEvent.click(await screen.findByRole('button', { name: 'Play Neon Rain' }));
    await waitFor(() => expect(web.state.play).toHaveBeenCalledWith('library://track/2'));
  });

  it('shows an artist\'s albums as cards that open', async () => {
    server.use(http.get('/api/media/item', () => HttpResponse.json({
      ...albumFixture, uri: 'library://artist/1', name: 'The Synthwave Collective', media_type: 'artist', artist: '',
      children: [{ ...albumFixture.children[0], uri: 'library://album/1', name: 'Neon Nights', media_type: 'album' }],
    })));
    renderAt('/media/artist/library/1');
    expect(await screen.findByRole('heading', { name: 'Albums' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Neon Nights/ })).toHaveAttribute('href', '/media/album/library/1');
  });

  it('plays every work by an artist from the artist page', async () => {
    server.use(http.get('/api/media/item', () => HttpResponse.json({
      ...albumFixture, uri: 'library://artist/1', name: 'The Synthwave Collective', media_type: 'artist', artist: '',
      children: [{ ...albumFixture.children[0], uri: 'library://album/1', name: 'Neon Nights', media_type: 'album' }],
    })));
    renderAt('/media/artist/library/1');
    await userEvent.click(await screen.findByRole('button', { name: 'Play all' }));
    await waitFor(() => expect(web.state.play).toHaveBeenCalledWith('library://artist/1'));
  });

  it('says when an item is gone, when tracks could not load, and when it all failed', async () => {
    server.use(http.get('/api/media/item', () => HttpResponse.json({ detail: 'nope' }, { status: 404 })));
    const a = renderAt('/media/album/library/404');
    expect(await screen.findByText('This album isn’t in your library', {}, { timeout: 5000 })).toBeInTheDocument();
    a.unmount();

    server.use(http.get('/api/media/item', () => HttpResponse.json({ ...albumFixture, children: [], errors: { ma: 'Music Assistant is unreachable: TimeoutError', abs: null } })));
    const b = renderAt('/media/album/library/1');
    expect(await screen.findByText(/Music Assistant did not answer/)).toBeInTheDocument();
    b.unmount();

    server.use(http.get('/api/media/item', () => HttpResponse.json({ detail: 'x' }, { status: 502 })));
    renderAt('/media/album/library/1');
    expect(await screen.findByText('Couldn’t load this album', {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it('shows skeletons while loading', async () => {
    server.use(http.get('/api/media/item', () => new Promise(() => {})));
    renderAt('/media/playlist/library/1');
    expect(await screen.findByRole('status', { name: 'Loading playlist' })).toBeInTheDocument();
  });
});
