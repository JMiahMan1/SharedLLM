import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { renderWithProviders } from '../../../test/render';
import { mediaItem, serveHome } from '../../../test/mediaScreens';
import { useMediaStore } from '../store/mediaStore';
import { detailPath, itemKind, itemSubtitle, maUriFromRoute, parseMaUri, playRequestFor } from '../items';
import { serviceProblem } from '../copy';

const web = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock('../../../context/MediaPlayerContext', () => ({ useSharedMAWebPlayer: () => web.state }));

import Media from '../../../pages/Media';

function player() {
  return {
    isConnected: false, connectionState: 'DISCONNECTED', isPlaying: false, mediaTitle: null, mediaArtist: null, mediaImage: null,
    mediaUri: null, mediaFavorite: null, position: 0, duration: 0, volume: 70, muted: false, error: null, reconnectAttempts: 0,
    maxReconnectAttempts: 10, playerId: 'me', connect: vi.fn(), reconnect: vi.fn(), play: vi.fn().mockResolvedValue(undefined),
    enqueue: vi.fn(), pause: vi.fn(), stop: vi.fn(), next: vi.fn(), previous: vi.fn(), seek: vi.fn(), setVolume: vi.fn(),
    setMuted: vi.fn(), listMaPlayers: vi.fn().mockResolvedValue([]), maCommand: vi.fn(),
  };
}

describe('Listen Now', () => {
  it('UX-33 shows skeleton shelves at card size while loading', async () => {
    web.state = player();
    server.use(http.get('/api/media/home', async () => {
      await delay('infinite');
      return HttpResponse.json({});
    }));
    renderWithProviders(<Media />);
    expect(await screen.findByRole('status', { name: 'Loading recently played' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Loading your playlists' })).toBeInTheDocument();
  });

  it('UX-44 shows time left on a book with a known length, else the percentage', async () => {
    web.state = player();
    serveHome({ recent: [mediaItem({ uri: 'abs://b1', name: 'Long Book', media_type: 'audiobook', duration: 36000 })] });
    server.use(http.get('/api/media/audiobookshelf/last-played', () => HttpResponse.json({
      status: 'ok',
      books: [
        { id: 'b1', title: 'Long Book', author: 'A', progress: 25, last_played: 2, library_id: 'l' },
        { id: 'b2', title: 'Short Book', author: 'B', progress: 0.5, last_played: 1, library_id: 'l' },
      ],
    })));
    renderWithProviders(<Media />);
    const shelf = within(await screen.findByRole('region', { name: 'Continue listening' }));
    expect(await shelf.findByText('7h 30m left')).toBeInTheDocument();
    expect(shelf.getByText('50% listened')).toBeInTheDocument();
  });

  it('pauses a speaker from the Playing in your home strip without taking it over', async () => {
    web.state = player();
    serveHome();
    const sent: unknown[] = [];
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json({
        status: 'SUCCESS',
        detail: { active: null, all_players: [{ entity_id: 'media_player.den', friendly_name: 'Den', state: 'playing', media_title: 'News' }] },
      })),
      http.post('/execute/media/transport', async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({ status: 'SUCCESS' });
      }),
    );
    renderWithProviders(<Media />);
    await userEvent.click(await screen.findByRole('button', { name: 'Pause Den' }));
    expect(sent).toEqual([{ entity_id: 'media_player.den', command: 'pause' }]);
    expect(useMediaStore.getState().activeOutputId).toBe('');
  });
});

describe('item helpers', () => {
  it('knows each kind from media_type or the URI', () => {
    expect(itemKind({ media_type: 'track', uri: '' })).toBe('track');
    expect(itemKind({ media_type: 'podcast_episode', uri: '' })).toBe('episode');
    expect(itemKind({ media_type: 'book', uri: '' })).toBe('audiobook');
    expect(itemKind({ media_type: '', uri: 'library://album/3' })).toBe('album');
    expect(itemKind({ media_type: 'mystery', uri: 'nope' })).toBe('other');
  });

  it('maps items to play requests', () => {
    expect(playRequestFor({ uri: 'library://track/1', name: 'T', media_type: 'track' })).toEqual({ kind: 'uri', uri: 'library://track/1', title: 'T' });
    expect(playRequestFor({ uri: 'abs://b1', name: 'B', media_type: 'audiobook' })).toEqual({ kind: 'abs', id: 'b1', title: 'B' });
    expect(playRequestFor({ uri: 'abs://p1', name: 'P', media_type: 'podcast' })).toBeNull();
    expect(playRequestFor({ uri: 'library://artist/1', name: 'A', media_type: 'artist' })).toBeNull();
    expect(playRequestFor({ uri: '', name: 'X', media_type: 'track' })).toBeNull();
  });

  it('routes collections to their detail pages and back', () => {
    expect(detailPath({ uri: 'library://album/12', media_type: 'album' })).toBe('/media/album/library/12');
    expect(detailPath({ uri: 'spotify--x://playlist/ab', media_type: 'playlist' })).toBe('/media/playlist/spotify--x/ab');
    expect(detailPath({ uri: 'abs://b1', media_type: 'audiobook' })).toBe('/media/book/b1');
    expect(detailPath({ uri: 'abs://p1', media_type: 'podcast' })).toBe('/media/podcast/p1');
    expect(detailPath({ uri: 'library://track/1', media_type: 'track' })).toBeNull();
    expect(detailPath({ uri: 'abs://author/a', media_type: 'author' })).toBeNull();
    expect(parseMaUri('library://album/12')).toEqual({ provider: 'library', type: 'album', id: '12' });
    expect(maUriFromRoute('album', 'library', '12')).toBe('library://album/12');
  });

  it('writes subtitles from what the item carries', () => {
    expect(itemSubtitle({ media_type: 'track', uri: '', artist: 'A', album: 'B' })).toBe('A · B');
    expect(itemSubtitle({ media_type: 'playlist', uri: '', artist: 'A' })).toBe('Playlist');
    expect(itemSubtitle({ media_type: 'album', uri: '' })).toBe('Album');
  });

  it('says plainly which service is down', () => {
    expect(serviceProblem('Music Assistant', null)).toBeNull();
    expect(serviceProblem('Music Assistant', 'Music Assistant is not configured')).toBe('Music Assistant is not connected yet.');
    expect(serviceProblem('Audiobookshelf', 'Audiobookshelf: timeout')).toBe('Audiobookshelf did not answer. timeout');
  });
});
