import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import { chooseOutput, mediaItem, serveHome } from './mediaScreens';

// sendspin-js cannot be bundled by vitest, so stub the shared web player. The
// remote path must never touch its connect()/maCommand() methods.
const player = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));

vi.mock('../context/MediaPlayerContext', () => ({
  useSharedMAWebPlayer: () => player.state,
}));

import Media from '../pages/Media';

type MaCommand = { command: string; args: Record<string, unknown> };

let commands: MaCommand[] = [];
let remoteState = 'idle';

function webPlayer(overrides: Record<string, unknown> = {}) {
  return {
    isConnected: false,
    connectionState: 'DISCONNECTED',
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
    playerId: 'this-web-player',
    connect: vi.fn().mockResolvedValue(undefined),
    reconnect: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    stop: vi.fn(),
    next: vi.fn(),
    previous: vi.fn(),
    seek: vi.fn(),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    listMaPlayers: vi.fn().mockResolvedValue([]),
    maCommand: vi.fn(),
    ...overrides,
  };
}

const IDLE_STATUS = {
  status: 'SUCCESS',
  detail: { active: null, available: [], all_players: [] },
};

const RECENT = [mediaItem({ name: 'Remote Song', artist: 'Remote Artist', uri: 'library://track/1' })];

beforeEach(() => {
  commands = [];
  remoteState = 'idle';
  player.state = webPlayer();
  server.use(
    http.post('/execute/media/status', () => HttpResponse.json(IDLE_STATUS)),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
    http.get('/api/media/audiobookshelf/last-played', () =>
      HttpResponse.json({ status: 'SUCCESS', books: [] })
    ),
    http.post('/api/media/ma-command', async ({ request }) => {
      const body = (await request.json()) as MaCommand;
      commands.push(body);
      if (body.command === 'players/all') {
        return HttpResponse.json({
          status: 'SUCCESS',
          result: [
            {
              player_id: 'remote-1',
              name: 'Kitchen MA',
              available: true,
              state: remoteState,
              powered: true,
            },
          ],
        });
      }
      if (body.command === 'players/cmd/play') remoteState = 'playing';
      if (body.command === 'players/cmd/pause') remoteState = 'paused';
      if (body.command === 'players/cmd/stop') remoteState = 'idle';
      return HttpResponse.json({ status: 'SUCCESS', result: {} });
    })
  );
  serveHome({ recent: RECENT });
});

async function selectRemoteSpeaker() {
  await chooseOutput(/Kitchen MA/i);
}

describe('Media page: remote Music Assistant speakers', () => {
  it('lists and starts a remote speaker without connecting the browser player', async () => {
    renderWithProviders(<Media />);
    await selectRemoteSpeaker();

    // The gateway listed it; the shared web player was never asked to connect.
    expect(commands).toContainEqual({ command: 'players/all', args: {} });
    expect(player.state.connect).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Play' }));

    await waitFor(() =>
      expect(commands).toContainEqual({
        command: 'players/cmd/play',
        args: { player_id: 'remote-1' },
      })
    );
    expect(player.state.connect).not.toHaveBeenCalled();
    expect(player.state.maCommand).not.toHaveBeenCalled();
    expect(player.state.play).not.toHaveBeenCalled();
  });

  it('plays a library item on the remote speaker through the gateway', async () => {
    renderWithProviders(<Media />);
    await selectRemoteSpeaker();
    await userEvent.click(await screen.findByRole('button', { name: /Remote Song/i }));

    await waitFor(() =>
      expect(commands).toContainEqual({
        command: 'player_queues/play_media',
        args: {
          queue_id: 'remote-1',
          media: ['library://track/1'],
          option: 'replace',
        },
      })
    );
    expect(player.state.connect).not.toHaveBeenCalled();
    expect(player.state.maCommand).not.toHaveBeenCalled();
    expect(player.state.play).not.toHaveBeenCalled();
  });

  it('sends pause and next to the remote speaker', async () => {
    remoteState = 'playing';
    renderWithProviders(<Media />);
    await selectRemoteSpeaker();

    await userEvent.click(await screen.findByRole('button', { name: 'Pause' }));
    await waitFor(() =>
      expect(commands).toContainEqual({
        command: 'players/cmd/pause',
        args: { player_id: 'remote-1' },
      })
    );

    await userEvent.click(screen.getByRole('button', { name: 'Next track' }));
    await waitFor(() =>
      expect(commands).toContainEqual({
        command: 'player_queues/next',
        args: { queue_id: 'remote-1' },
      })
    );
    expect(player.state.connect).not.toHaveBeenCalled();
    expect(player.state.maCommand).not.toHaveBeenCalled();
  });

  it('sends volume and mute changes to the remote speaker', async () => {
    renderWithProviders(<Media />);
    await selectRemoteSpeaker();

    fireEvent.change(screen.getByLabelText('Volume'), { target: { value: '55' } });
    await waitFor(() =>
      expect(commands).toContainEqual({
        command: 'players/cmd/volume_set',
        args: { player_id: 'remote-1', volume_level: 55 },
      })
    );

    await userEvent.click(screen.getByRole('button', { name: 'Mute' }));
    await waitFor(() =>
      expect(commands).toContainEqual({
        command: 'players/cmd/volume_mute',
        args: { player_id: 'remote-1', muted: true },
      })
    );
    expect(player.state.connect).not.toHaveBeenCalled();
    expect(player.state.maCommand).not.toHaveBeenCalled();
  });
});
