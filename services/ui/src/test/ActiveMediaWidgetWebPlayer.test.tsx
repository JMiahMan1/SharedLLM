import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import ActiveMediaWidget from '../components/widgets/ActiveMediaWidget';
import type { UserWidgetSettings } from '../types/widget';

const web = vi.hoisted(() => ({ player: null as Record<string, unknown> | null }));
vi.mock('../context/webPlayerContext', () => ({
  useOptionalWebPlayer: () => web.player,
}));

function webPlayer(over: Record<string, unknown> = {}) {
  return {
    playerId: 'me', mediaTitle: 'Hey', mediaArtist: 'Someone', mediaImage: null, mediaUri: 'library://track/1',
    isPlaying: true, position: 10, duration: 200, volume: 40, muted: false, isConnected: true,
    play: vi.fn(async () => {}), pause: vi.fn(async () => {}), next: vi.fn(async () => {}), previous: vi.fn(async () => {}),
    stop: vi.fn(async () => {}), seek: vi.fn(async () => {}), setVolume: vi.fn(), setMuted: vi.fn(), enqueue: vi.fn(),
    maCommand: vi.fn(), listMaPlayers: vi.fn(async () => []),
    ...over,
  };
}

const props = {
  settingsButton: null,
  userSettings: { widget_key: 'active_media', visibility: 'visible', order_index: 0 } as UserWidgetSettings,
  onTogglePin: () => {},
};

function status(player: Record<string, unknown> | null) {
  return {
    status: 'SUCCESS',
    detail: { active: player, all_players: player ? [player] : [] },
  };
}

const KITCHEN = {
  entity_id: 'media_player.kitchen', friendly_name: 'Kitchen', state: 'playing',
  media_title: 'Song', media_artist: 'Band', volume_level: 0.4, position: 10, duration: 200,
};

let sent: Array<Record<string, unknown>> = [];
beforeEach(() => {
  web.player = null;
  sent = [];
  server.use(http.post('/execute/media/transport', async ({ request }) => {
    sent.push((await request.json()) as Record<string, unknown>);
    return HttpResponse.json({ status: 'SUCCESS' });
  }));
});
afterEach(() => {
  vi.clearAllMocks();
});

describe('ActiveMediaWidget: which player the buttons drive', () => {
  it("controls this app's own web player directly, not the server's record of it", async () => {
    web.player = webPlayer();
    server.use(http.post('/execute/media/status', () => HttpResponse.json(status({ ...KITCHEN, entity_id: 'web_player', media_title: 'Hey' }))));
    const user = userEvent.setup();
    renderWithProviders(<ActiveMediaWidget {...props} />);
    await screen.findByText('Hey');
    expect(screen.getByText('This device')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Pause' }));
    await user.click(screen.getByRole('button', { name: 'Next track' }));
    await user.click(screen.getByRole('button', { name: 'Previous track' }));

    expect(web.player!.pause).toHaveBeenCalledTimes(1);
    expect(web.player!.next).toHaveBeenCalledTimes(1);
    expect(web.player!.previous).toHaveBeenCalledTimes(1);
    expect(sent).toEqual([]);
  });

  it('sends other players their commands through the server', async () => {
    server.use(http.post('/execute/media/status', () => HttpResponse.json(status(KITCHEN))));
    const user = userEvent.setup();
    renderWithProviders(<ActiveMediaWidget {...props} />);
    await screen.findByText('Song');

    await user.click(screen.getByRole('button', { name: 'Next track' }));
    await user.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(sent).toEqual([
      { entity_id: 'media_player.kitchen', command: 'next' },
      { entity_id: 'media_player.kitchen', command: 'pause' },
    ]));
    // The button flips at once, before the device confirms.
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
  });

  it('shows a speaker while this device only has something paused', async () => {
    web.player = webPlayer({ isPlaying: false });
    server.use(http.post('/execute/media/status', () => HttpResponse.json(status(KITCHEN))));
    renderWithProviders(<ActiveMediaWidget {...props} />);
    expect(await screen.findByText('Song')).toBeInTheDocument();
    expect(screen.getByText('Kitchen')).toBeInTheDocument();
  });

  it('falls back to the app name when the player has no title', async () => {
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json(status({
        entity_id: 'media_player.office_tv_chrome', friendly_name: 'Office TV Cast', state: 'playing', app_name: 'YouTube',
        media_title: '', media_artist: '', volume_level: 0.4, position: 0, duration: 0,
      }))),
    );
    renderWithProviders(<ActiveMediaWidget {...props} />);
    expect(await screen.findByText('Playing on YouTube')).toBeInTheDocument();
    expect(screen.getByText('Office TV Cast')).toBeInTheDocument();
    expect(screen.queryByText('Unknown Artist')).toBeNull();
  });

  it('BUG-54 seeks once on release and sends only the settled volume', async () => {
    server.use(http.post('/execute/media/status', () => HttpResponse.json(status(KITCHEN))));
    renderWithProviders(<ActiveMediaWidget {...props} />);
    await screen.findByText('Song');
    const scrubber = screen.getByRole('slider', { name: 'Playback position' });
    fireEvent.change(scrubber, { target: { value: '60' } });
    fireEvent.change(scrubber, { target: { value: '90' } });
    expect(sent).toEqual([]);
    fireEvent.pointerUp(scrubber);
    await waitFor(() => expect(sent).toEqual([{ entity_id: 'media_player.kitchen', command: 'seek', position: 90 }]));

    sent = [];
    const volume = screen.getByRole('slider', { name: 'Volume' });
    fireEvent.change(volume, { target: { value: '20' } });
    fireEvent.change(volume, { target: { value: '25' } });
    await waitFor(() => expect(sent).toEqual([{ entity_id: 'media_player.kitchen', command: 'volume_set', volume_level: 0.25 }]));
  });

  it('tells the dashboard when nothing is playing (unless pinned)', async () => {
    server.use(http.post('/execute/media/status', () => HttpResponse.json(status(null))));
    const onMediaStop = vi.fn();
    renderWithProviders(<ActiveMediaWidget {...props} onMediaStop={onMediaStop} />);
    expect(await screen.findByText('Nothing playing')).toBeInTheDocument();
    await waitFor(() => expect(onMediaStop).toHaveBeenCalled());
  });
});
