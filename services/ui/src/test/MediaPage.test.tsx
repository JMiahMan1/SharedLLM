import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';

// sendspin-js cannot be bundled by vitest, so stub the web player hook.
vi.mock('../lib/maWebPlayer', () => ({
  useMAWebPlayer: () => ({
    isConnected: false,
    connectionState: 'DISCONNECTED',
    isPlaying: false,
    mediaTitle: null,
    mediaUri: null,
    mediaFavorite: false,
    position: 0,
    connect: vi.fn().mockResolvedValue(undefined),
    reconnect: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn().mockResolvedValue(undefined),
    next: vi.fn().mockResolvedValue(undefined),
    previous: vi.fn().mockResolvedValue(undefined),
    seek: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn().mockResolvedValue(undefined),
    setMuted: vi.fn().mockResolvedValue(undefined),
    listMaPlayers: vi.fn().mockResolvedValue([]),
    maCommand: vi.fn().mockResolvedValue(undefined),
  }),
}));

import Media from '../pages/Media';

const ACTIVE_REMOTE = {
  status: 'SUCCESS',
  detail: {
    active: {
      entity_id: 'media_player.office',
      name: 'Office Speaker',
      state: 'playing',
      media_title: 'Test Song',
      media_artist: 'Test Artist',
      volume_level: 0.42,
      is_volume_muted: false,
      position: 65,
      duration: 214,
      media_content_id: 'ma:track:abc123',
      media_type: 'music',
    },
    available: [],
    all_players: [
      { entity_id: 'media_player.office', name: 'Office Speaker', state: 'playing' },
    ],
  },
};

describe('Media page — active remote player', () => {
  beforeEach(() => {
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json(ACTIVE_REMOTE)),
      http.get('/api/entities', () =>
        HttpResponse.json({
          entities: [
            {
              entity_id: 'media_player.office',
              domain: 'media_player',
              friendly_name: 'Office Speaker',
              state: 'playing',
            },
          ],
        })
      ),
    );
  });

  it('follows an already-playing speaker instead of showing an empty web player', async () => {
    renderWithProviders(<Media />);

    // The playing track is shown on load…
    expect(await screen.findByText('Test Song')).toBeInTheDocument();
    // …instead of the idle local card it used to show over live music.
    expect(screen.queryByText('No Active Playback')).not.toBeInTheDocument();
    // The device was adopted, so the status line names the speaker.
    await waitFor(() =>
      expect(screen.getByText(/Playing through Office Speaker/)).toBeInTheDocument()
    );
  });

  it('does not alarm with a web-player connection banner before the user picks it', async () => {
    renderWithProviders(<Media />);
    expect(await screen.findByText('Test Song')).toBeInTheDocument();
    expect(screen.queryByText(/Player connection failed/i)).not.toBeInTheDocument();
  });
});
