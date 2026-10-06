import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';

// sendspin-js cannot be bundled by vitest, so stub the web player hook. The
// audiobook play path goes through it, so the stubs are the assertion surface.
const maCommand = vi.fn().mockResolvedValue(undefined);
const webPlay = vi.fn().mockResolvedValue(undefined);

vi.mock('../lib/maWebPlayer', () => ({
  useMAWebPlayer: () => ({
    isConnected: true,
    connectionState: 'CONNECTED',
    isPlaying: false,
    mediaTitle: null,
    mediaUri: null,
    mediaFavorite: false,
    position: 0,
    connect: vi.fn().mockResolvedValue(undefined),
    reconnect: vi.fn(),
    play: webPlay,
    pause: vi.fn().mockResolvedValue(undefined),
    next: vi.fn().mockResolvedValue(undefined),
    previous: vi.fn().mockResolvedValue(undefined),
    seek: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn().mockResolvedValue(undefined),
    setMuted: vi.fn().mockResolvedValue(undefined),
    listMaPlayers: vi.fn().mockResolvedValue([
      { player_id: 'up7c9e874d5efc', name: 'A25-27JN MacBook Pro', available: true, state: 'idle', powered: true },
    ]),
    maCommand,
  }),
}));

import Media from '../pages/Media';

const NARNIA = '08d24fad-32a7-422d-8374-8501fdbe55d5';

const LAST_PLAYED = {
  status: 'ok',
  books: [
    {
      id: NARNIA,
      title: 'The Chronicles of Narnia',
      author: 'C.S. Lewis',
      progress: 0.1,
      last_played: '2026-09-25T08:30:00Z',
      library_id: 'lib-books',
    },
  ],
};

const IDLE = {
  status: 'SUCCESS',
  detail: { active: null, available: [], all_players: [] },
};

/** Capture what the browser asks the gateway to resolve. */
let resolveCalls: Array<{ abs_item_id: string; title: string }> = [];

/**
 * Select a device, then click the audiobook's play button.
 *
 * The device picker is a row of buttons (no <select>), and the book only appears
 * once a target is chosen, so this walks the real user path rather than calling a
 * handler directly.
 */
async function playOnMaPlayer(bookTitle: string) {
  const device = await screen.findByRole('button', { name: /A25-27JN MacBook Pro/i });
  await userEvent.click(device);
  const play = await screen.findByRole('button', { name: new RegExp(bookTitle, 'i') });
  await userEvent.click(play);
}

let userEvent: typeof import('@testing-library/user-event').default;

beforeEach(async () => {
  userEvent = (await import('@testing-library/user-event')).default;
  maCommand.mockClear();
  webPlay.mockClear();
  resolveCalls = [];
  server.use(
    http.post('/execute/media/status', () => HttpResponse.json(IDLE)),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
    http.get('/api/media/audiobookshelf/libraries', () =>
      HttpResponse.json({ status: 'SUCCESS', libraries: [{ id: 'lib-books', name: 'Books' }] })
    ),
    http.get('/api/media/audiobookshelf/last-played', () => HttpResponse.json(LAST_PLAYED)),
    http.get('/api/media/audiobookshelf/library/:libraryId', () =>
      HttpResponse.json({ status: 'SUCCESS', books: LAST_PLAYED.books })
    ),
    http.get('/api/media/audiobookshelf/search', () =>
      HttpResponse.json({ status: 'SUCCESS', books: LAST_PLAYED.books })
    ),
    http.get('/api/media/music-assistant/recent', () =>
      HttpResponse.json({ status: 'SUCCESS', recent: [] })
    ),
    http.get('/api/media/music-assistant/playlists', () =>
      HttpResponse.json({ status: 'SUCCESS', playlists: [] })
    ),
    http.get('/api/logs', () => HttpResponse.json([])),
    http.post('/api/media/ma-library-uri', async ({ request }) => {
      const body = (await request.json()) as { abs_item_id: string; title: string };
      resolveCalls.push(body);
      return HttpResponse.json({
        status: 'SUCCESS',
        abs_item_id: body.abs_item_id,
        ma_uri: 'library://audiobook/260',
        title: body.title,
      });
    })
  );
});

describe('Media page — playing an audiobook on a Music Assistant player', () => {
  it('asks the gateway for the MA library URI instead of sending audiobookshelf://', async () => {
    renderWithProviders(<Media />);
    await playOnMaPlayer('Narnia');

    await waitFor(() => expect(maCommand).toHaveBeenCalled());
    const [command, args] = maCommand.mock.calls[0];
    expect(command).toBe('player_queues/play_media');

    // The original bug: MA was handed `audiobookshelf://<uuid>`, which it has no
    // media controller for, so the player stayed silent.
    const sent = JSON.stringify(args);
    expect(sent).not.toContain('audiobookshelf://');
    expect(sent).toContain('library://audiobook/260');
  });

  it('asks for the bare Audiobookshelf item id, with the title to search by', async () => {
    renderWithProviders(<Media />);
    await playOnMaPlayer('Narnia');

    await waitFor(() => expect(resolveCalls).toHaveLength(1));
    expect(resolveCalls[0].abs_item_id).toBe(NARNIA);
    // The title makes MA's search accurate; an id-only search often misses.
    expect(resolveCalls[0].title).toMatch(/Narnia/i);
  });

  it('sends the resolved URI inside a media list', async () => {
    renderWithProviders(<Media />);
    await playOnMaPlayer('Narnia');

    await waitFor(() => expect(maCommand).toHaveBeenCalled());
    const args = maCommand.mock.calls[0][1];
    expect(Array.isArray(args.media)).toBe(true);
    expect(args.media).toEqual(['library://audiobook/260']);
  });

  it('surfaces a lookup miss instead of falling back to an unplayable URI', async () => {
    server.use(
      http.post('/api/media/ma-library-uri', async ({ request }) => {
        // Still record the request: the point is that the lookup WAS attempted
        // and that its failure is what stopped playback.
        resolveCalls.push((await request.json()) as { abs_item_id: string; title: string });
        return HttpResponse.json(
          { detail: 'Music Assistant does not have this book in its library' },
          { status: 404 }
        );
      })
    );
    renderWithProviders(<Media />);
    await playOnMaPlayer('Narnia');

    // Nothing is sent to MA — a fallback URI would reproduce the original bug.
    await waitFor(() => expect(resolveCalls).toHaveLength(1));
    expect(maCommand).not.toHaveBeenCalled();
    expect(webPlay).not.toHaveBeenCalled();
  });
});