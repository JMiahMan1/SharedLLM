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

describe('Media page — long-form tracks', () => {
  // An audiobook: 1h 06m 40s long, 1h 02m 05s in.
  const LONG_TRACK = {
    status: 'SUCCESS',
    detail: {
      active: {
        entity_id: 'media_player.kitchen',
        friendly_name: 'Kitchen Speaker',
        state: 'playing',
        media_title: 'The Way of Kings',
        media_artist: 'Brandon Sanderson',
        volume_level: 0.3,
        is_volume_muted: false,
        position: 3725,
        duration: 4000,
      },
      available: [],
      all_players: [
        { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'playing' },
      ],
    },
  };

  beforeEach(() => {
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json(LONG_TRACK)),
      http.get('/api/entities', () =>
        HttpResponse.json({
          entities: [
            {
              entity_id: 'media_player.kitchen',
              domain: 'media_player',
              friendly_name: 'Kitchen Speaker',
              state: 'playing',
            },
          ],
        })
      ),
    );
  });

  it('shows elapsed and total time with hours, not minutes counted past 60', async () => {
    renderWithProviders(<Media />);

    await screen.findByText('The Way of Kings');
    // 3725s and 4000s used to read "62:05" and "66:40".
    expect(screen.getByText('1:02:05')).toBeInTheDocument();
    expect(screen.getByText('1:06:40')).toBeInTheDocument();
    expect(screen.queryByText('66:40')).not.toBeInTheDocument();
  });
});

describe('Media page — poisoned device names', () => {
  /** The exact string Music Assistant still holds for the old web player. */
  const PROFILE_BLOB =
    '{"id":1,"username":"default","display_name":"Shared/Default User","is_admin":true,' +
    '"nextcloud_user":"summers","ha_url":"https://ha.sumemail.com","skylight_email":"someone@example.com",' +
    '"audiobookshelf_api_key":null,"api_key":null}' +
    "'s Web Player (Desktop)";

  beforeEach(() => {
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json(ACTIVE_REMOTE)),
      http.get('/api/entities', () =>
        HttpResponse.json({
          entities: [
            {
              entity_id: 'media_player.id_1_username_default_display_name_shared_default_user',
              domain: 'media_player',
              // Home Assistant inherited the registered client name as friendly_name.
              friendly_name: PROFILE_BLOB,
              state: 'idle',
            },
            {
              entity_id: 'media_player.loft',
              domain: 'media_player',
              friendly_name: 'Loft TV',
              state: 'idle',
            },
          ],
        })
      ),
    );
  });

  it('shows a readable label instead of 800 characters of profile', async () => {
    renderWithProviders(<Media />);

    expect(await screen.findByText("Shared/Default User's Web Player (Desktop)")).toBeInTheDocument();
    // A real name is untouched.
    expect(screen.getByText('Loft TV')).toBeInTheDocument();
    // And nothing from the profile reaches the page.
    const body = document.body.textContent || '';
    expect(body).not.toContain('audiobookshelf_api_key');
    expect(body).not.toContain('nextcloud_user');
    expect(body).not.toContain('someone@example.com');
  });
});
