import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import { chooseOutput, mediaItem, openOutputPicker, serveHome } from './mediaScreens';

// sendspin-js cannot be bundled by vitest: stub the app-wide web player.
const p = vi.hoisted(() => ({
  state: {} as Record<string, unknown>,
}));
vi.mock('../context/MediaPlayerContext', () => ({
  useSharedMAWebPlayer: () => p.state,
}));

import Media from '../pages/Media';

const NARNIA = '08d24fad-32a7-422d-8374-8501fdbe55d5';

function player(overrides: Record<string, unknown> = {}) {
  return {
    isConnected: true,
    connectionState: 'CONNECTED',
    isPlaying: false,
    mediaTitle: null,
    mediaArtist: null,
    mediaImage: null,
    mediaUri: null,
    mediaFavorite: null,
    position: 0,
    duration: 0,
    volume: 70,
    muted: false,
    error: null,
    reconnectAttempts: 0,
    maxReconnectAttempts: 10,
    playerId: 'my-web-player',
    connect: vi.fn().mockResolvedValue(undefined),
    reconnect: vi.fn().mockResolvedValue(undefined),
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    next: vi.fn().mockResolvedValue(undefined),
    previous: vi.fn().mockResolvedValue(undefined),
    seek: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    listMaPlayers: vi.fn().mockResolvedValue([]),
    maCommand: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

const IDLE_STATUS = { status: 'SUCCESS', detail: { active: null, available: [], all_players: [] } };

beforeEach(() => {
  p.state = player();
  server.use(
    http.post('/execute/media/status', () => HttpResponse.json(IDLE_STATUS)),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
    http.get('/api/media/audiobookshelf/last-played', () => HttpResponse.json({
      status: 'SUCCESS',
      books: [
        { id: NARNIA, title: 'The Chronicles of Narnia', author: 'C.S. Lewis', progress: 8, last_played: 1791050360525, library_id: 'l' },
        { id: 'done-book', title: 'Finished Book', author: 'Someone', progress: 100, is_complete: true, last_played: 1791736021981, library_id: 'l' },
      ],
    })),
    http.post('/api/media/ma-library-uri', async ({ request }) => {
      const body = (await request.json()) as { abs_item_id: string };
      return HttpResponse.json({ status: 'SUCCESS', abs_item_id: body.abs_item_id, ma_uri: 'library://audiobook/260', title: 'x' });
    }),
  );
  serveHome({ playlists: [mediaItem({ name: 'Road Trip', uri: 'library://playlist/6', media_type: 'playlist' })] });
});

describe('Media page: this device (web player)', () => {
  it('plays a book from Continue listening through the resolved Music Assistant URI', async () => {
    renderWithProviders(<Media />);
    await userEvent.click(await screen.findByRole('button', { name: /Chronicles of Narnia/i }));
    await waitFor(() => expect(p.state.play).toHaveBeenCalledWith('library://audiobook/260'));
    // The old guess, library://audiobookshelf/book/<uuid>, left the player silent.
    expect(JSON.stringify((p.state.play as ReturnType<typeof vi.fn>).mock.calls)).not.toContain('audiobookshelf');
  });

  it('leaves finished books out of Continue listening', async () => {
    renderWithProviders(<Media />);
    await screen.findByRole('button', { name: /Chronicles of Narnia/i });
    expect(screen.queryByText('Finished Book')).not.toBeInTheDocument();
  });

  it('plays a playlist on the web player', async () => {
    renderWithProviders(<Media />);
    await userEvent.click(await screen.findByRole('button', { name: /Road Trip/i }));
    await waitFor(() => expect(p.state.play).toHaveBeenCalledWith('library://playlist/6'));
  });

  it('shows what is playing and resumes rather than restarting it', async () => {
    p.state = player({ isPlaying: false, mediaTitle: 'Hey', mediaArtist: 'Leela', mediaUri: 'library://track/747', duration: 214, position: 30 });
    renderWithProviders(<Media />);
    expect(await screen.findByText('Hey')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Play' }));
    // Resume: no URI, so MA continues from 0:30 instead of replaying the track.
    await waitFor(() => expect(p.state.play).toHaveBeenCalledWith());
  });

  it('stops the web player from the card', async () => {
    p.state = player({ isPlaying: true, mediaTitle: 'Hey', mediaUri: 'library://track/747', duration: 214 });
    renderWithProviders(<Media />);
    await userEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    expect(p.state.stop).toHaveBeenCalled();
  });

  it('UX-04 offers Try again once automatic retries have given up', async () => {
    p.state = player({ isConnected: false, connectionState: 'FAILED', error: 'Lost the connection to Music Assistant.' });
    renderWithProviders(<Media />);
    expect(await screen.findByText(/Couldn’t connect this device/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(p.state.reconnect).toHaveBeenCalled();
  });

  it('says the connection is being retried instead of a silent dead player', async () => {
    p.state = player({ isConnected: false, connectionState: 'RECONNECTING', reconnectAttempts: 2 });
    renderWithProviders(<Media />);
    expect(await screen.findByText(/reconnecting \(try 2 of 10\)/i)).toBeInTheDocument();
  });
});

describe('Media page: device picker', () => {
  it('lists a speaker once even when both Home Assistant and Music Assistant report it', async () => {
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json({
        status: 'SUCCESS',
        detail: {
          active: null,
          available: [],
          all_players: [
            { entity_id: 'media_player.office_tv_3', friendly_name: 'Office TV', state: 'idle', ma_player_id: 'up309587157251', icon_kind: 'speaker' },
          ],
        },
      })),
    );
    p.state = player({
      listMaPlayers: vi.fn().mockResolvedValue([
        { player_id: 'up309587157251', name: 'Office TV', available: true, state: 'idle', powered: true },
        { player_id: 'upa4e88d93e22f', name: 'RV TV', available: true, state: 'idle', powered: true },
        { player_id: 'my-web-player', name: "Jeremiah's Web Player (Mobile)", available: true, state: 'idle', powered: true },
      ]),
    });
    renderWithProviders(<Media />);
    const picker = await openOutputPicker();
    expect(await picker.findByRole('button', { name: /RV TV/ })).toBeInTheDocument();
    expect(picker.getAllByRole('button', { name: /Office TV/ })).toHaveLength(1);
    // Our own web player is the "This device" row, not a second entry.
    expect(picker.queryByRole('button', { name: /Jeremiah's Web Player/ })).not.toBeInTheDocument();
    expect(picker.getByRole('button', { name: /This device/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('plays on a Home Assistant speaker through the execution service', async () => {
    const plays: unknown[] = [];
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json({
        status: 'SUCCESS',
        detail: { active: null, available: [], all_players: [{ entity_id: 'media_player.kitchen', friendly_name: 'Kitchen', state: 'idle' }] },
      })),
      http.post('/execute/media/state/sync', () => HttpResponse.json({ status: 'SUCCESS' })),
      http.post('/execute/audiobookshelf', async ({ request }) => {
        plays.push(await request.json());
        return HttpResponse.json({ status: 'SUCCESS', message: 'ok' });
      }),
    );
    renderWithProviders(<Media />);
    await chooseOutput(/Kitchen/);
    await userEvent.click(await screen.findByRole('button', { name: /Chronicles of Narnia/i }));
    await waitFor(() => expect(plays).toHaveLength(1));
    expect(plays[0]).toMatchObject({ action: 'play', book_id: NARNIA, entity_id: 'media_player.kitchen' });
    expect(p.state.play).not.toHaveBeenCalled();
  });
});

describe('Media page: when a provider is down', () => {
  it('says the music could not load (not "nothing here") and retries', async () => {
    let calls = 0;
    server.use(
      http.get('/api/media/home', () => {
        calls += 1;
        return HttpResponse.json({ detail: 'down' }, { status: 502 });
      }),
    );
    renderWithProviders(<Media />);
    expect(await screen.findByText('Couldn’t load your music', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByText('Nothing here yet')).not.toBeInTheDocument();
    const before = calls;
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(calls).toBeGreaterThan(before));
  });

  it('keeps the audiobook shelf up and says which service is down (§4.5 partial)', async () => {
    serveHome({ errors: { ma: 'Music Assistant is unreachable: ClientConnectorError', abs: null } });
    renderWithProviders(<Media />);
    expect(await screen.findByText(/Music Assistant did not answer/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Chronicles of Narnia/i })).toBeInTheDocument();
  });

  it('UX-40 invites a first-run user to connect their services', async () => {
    serveHome({ errors: { ma: 'Music Assistant is not configured', abs: null } });
    server.use(http.get('/api/media/audiobookshelf/last-played', () => HttpResponse.json({ status: 'SUCCESS', books: [] })));
    renderWithProviders(<Media />);
    expect(await screen.findByText('Connect your music')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open settings' })).toHaveAttribute('href', '/identity');
  });
});

describe('Media page: browse', () => {
  it('opens an Audiobookshelf podcast to its episodes and plays the one picked (BUG-43)', async () => {
    server.use(
      http.get('/api/media/audiobookshelf/search', () => HttpResponse.json({
        status: 'SUCCESS', books: [], authors: [],
        podcasts: [{ id: 'pod-1', title: 'Daily Pod', author: 'Host' }],
      })),
    );
    const asked: string[] = [];
    const episodeUri = 'audiobookshelf--x://podcast_episode/pod-1 ep-9';
    server.use(
      http.get('/api/media/podcast-episodes', ({ request }) => {
        asked.push(new URL(request.url).searchParams.get('abs_item_id') || '');
        return HttpResponse.json({
          status: 'SUCCESS',
          episodes: [{ uri: episodeUri, name: 'Monday news', duration: 1200, published: null, fully_played: null, resume_position_ms: null }],
        });
      }),
    );
    renderWithProviders(<Media />);
    await userEvent.click(await screen.findByRole('button', { name: /Browse all media/i }));
    const dialog = await screen.findByRole('dialog', { name: /browse media/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /Audiobooks/i }));
    await userEvent.type(within(dialog).getByRole('searchbox', { name: 'Search' }), 'daily');
    await userEvent.click(await within(dialog).findByRole('button', { name: /Daily Pod/ }));
    await userEvent.click(await within(dialog).findByRole('button', { name: /^Monday news/ }));
    await waitFor(() => expect(p.state.play).toHaveBeenCalledWith(episodeUri));
    expect(asked).toEqual(['pod-1']);
  });
});
