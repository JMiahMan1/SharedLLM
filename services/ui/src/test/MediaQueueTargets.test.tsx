import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import { chooseOutput, mediaItem, serveHome } from './mediaScreens';

// "Play next" and "Add to queue" fill the queue of whichever device is
// selected: this device's web player, a Music Assistant speaker, or a Home
// Assistant speaker (whose Music Assistant queue the execution service fills).
const player = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));

vi.mock('../context/MediaPlayerContext', () => ({
  useSharedMAWebPlayer: () => player.state,
}));

import Media from '../pages/Media';

type MaCommand = { command: string; args: Record<string, unknown> };

let maCommands: MaCommand[] = [];
let executePlays: Record<string, unknown>[] = [];

function webPlayer() {
  return {
    isConnected: true, connectionState: 'CONNECTED', isPlaying: true,
    mediaTitle: 'Now', mediaArtist: null, mediaImage: null, mediaUri: null, mediaFavorite: null,
    position: 0, duration: 0, volume: 70, muted: false, error: null,
    reconnectAttempts: 0, maxReconnectAttempts: 10, playerId: 'this-web-player',
    connect: vi.fn(), reconnect: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    enqueue: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(), stop: vi.fn(), next: vi.fn(), previous: vi.fn(), seek: vi.fn(),
    setVolume: vi.fn(), setMuted: vi.fn(),
    listMaPlayers: vi.fn().mockResolvedValue([{ player_id: 'remote-1', name: 'Kitchen MA', available: true, state: 'playing' }]),
    maCommand: vi.fn(),
  };
}

beforeEach(() => {
  maCommands = [];
  executePlays = [];
  player.state = webPlayer();
  server.use(
    http.post('/execute/media/status', () => HttpResponse.json({ status: 'SUCCESS', detail: { active: null, all_players: [] } })),
    http.get('/api/entities', () => HttpResponse.json({
      entities: [{ entity_id: 'media_player.loft_tv', domain: 'media_player', friendly_name: 'Loft TV', state: 'idle' }],
    })),
    http.get('/api/media/audiobookshelf/last-played', () => HttpResponse.json({ status: 'SUCCESS', books: [] })),
    http.get('/api/media/music-assistant/recent', () => HttpResponse.json({
      status: 'SUCCESS',
      recent: [{ name: 'Worship Mix', uri: 'library://playlist/5', type: 'playlist', last_played: '2026-10-24T08:00:00Z' }],
    })),
    http.get('/api/media/music-assistant/playlists', () => HttpResponse.json({ status: 'SUCCESS', playlists: [] })),
    http.post('/api/media/ma-command', async ({ request }) => {
      const body = (await request.json()) as MaCommand;
      maCommands.push(body);
      if (body.command === 'players/all') {
        return HttpResponse.json({ status: 'SUCCESS', result: [{ player_id: 'remote-1', name: 'Kitchen MA', available: true, state: 'playing', powered: true }] });
      }
      return HttpResponse.json({ status: 'SUCCESS', result: {} });
    }),
    http.post('/execute/media/play', async ({ request }) => {
      executePlays.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ status: 'SUCCESS', message: 'ok' });
    }),
  );
  serveHome({ recent: [mediaItem({ name: 'Worship Mix', uri: 'library://playlist/5', media_type: 'playlist' })] });
});

async function openBrowse() {
  await userEvent.click(await screen.findByRole('button', { name: /Browse all media/i }));
  return within(await screen.findByRole('dialog'));
}

describe('Media page: queueing on the selected device', () => {
  it('queues on this device through the web player', async () => {
    renderWithProviders(<Media />);
    const browse = await openBrowse();
    await userEvent.click(await browse.findByRole('button', { name: 'Play Worship Mix next' }));

    await waitFor(() => expect(player.state.enqueue).toHaveBeenCalledWith('library://playlist/5', 'next'));
    expect(player.state.play).not.toHaveBeenCalled();
  });

  it('queues on a Music Assistant speaker through the gateway', async () => {
    renderWithProviders(<Media />);
    await chooseOutput(/Kitchen MA/i);
    const browse = await openBrowse();
    await userEvent.click(await browse.findByRole('button', { name: 'Add Worship Mix to the queue' }));

    await waitFor(() => expect(maCommands).toContainEqual({
      command: 'player_queues/play_media',
      args: { queue_id: 'remote-1', media: ['library://playlist/5'], option: 'add' },
    }));
    expect(player.state.enqueue).not.toHaveBeenCalled();
  });

  it('queues on a Home Assistant speaker through the execution service', async () => {
    renderWithProviders(<Media />);
    await chooseOutput(/Loft TV/i);
    const browse = await openBrowse();
    await userEvent.click(await browse.findByRole('button', { name: 'Play Worship Mix next' }));

    await waitFor(() => expect(executePlays).toContainEqual(expect.objectContaining({
      entity_id: 'media_player.loft_tv', query: 'library://playlist/5', enqueue: 'next',
    })));
    expect(player.state.enqueue).not.toHaveBeenCalled();
  });
});
