import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import ActiveMediaWidget from '../components/widgets/ActiveMediaWidget';
import type { UserWidgetSettings } from '../types/widget';

const settings = {
  widget_key: 'active_media',
  visibility: 'visible',
  order_index: 0,
} as UserWidgetSettings;

const props = { settingsButton: null, userSettings: settings, onTogglePin: () => {} };

/** Mirrors the real media/status payload: the now-playing player is detail.active. */
const nowPlaying = (position: number, duration: number) => ({
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
      position,
      duration,
    },
    available: [],
    all_players: [
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'playing' },
    ],
  },
});

describe('ActiveMediaWidget — time display', () => {
  beforeEach(() => {
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json(nowPlaying(3725, 4000))),
    );
  });

  afterEach(() => server.resetHandlers());

  it('shows an audiobook clock with hours', async () => {
    renderWithProviders(<ActiveMediaWidget {...props} />);

    await screen.findByText('The Way of Kings');
    // 3725s / 4000s used to read "62:05 / 66:40".
    expect(screen.getByText('1:02:05 / 1:06:40')).toBeInTheDocument();
    expect(screen.queryByText(/66:40/)).not.toBeInTheDocument();
  });

  it('keeps a short track on the minutes-only clock', async () => {
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json(nowPlaying(65, 214))),
    );
    renderWithProviders(<ActiveMediaWidget {...props} />);

    await screen.findByText('The Way of Kings');
    expect(screen.getByText('1:05 / 3:34')).toBeInTheDocument();
  });
});