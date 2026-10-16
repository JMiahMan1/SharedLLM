import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import ActiveMediaWidget from '../components/widgets/ActiveMediaWidget';
import type { UserWidgetSettings } from '../types/widget';

const webPlayer = {
  play: vi.fn(async () => {}),
  pause: vi.fn(async () => {}),
  next: vi.fn(async () => {}),
  previous: vi.fn(async () => {}),
  seek: vi.fn(async () => {}),
  setVolume: vi.fn(),
};

vi.mock('../context/webPlayerContext', () => ({
  useOptionalWebPlayer: () => webPlayer,
}));

const props = {
  settingsButton: null,
  userSettings: { widget_key: 'active_media', visibility: 'visible', order_index: 0 } as UserWidgetSettings,
  onTogglePin: () => {},
};

function playing(entityId: string) {
  return {
    status: 'SUCCESS',
    detail: {
      active: {
        entity_id: entityId,
        friendly_name: entityId,
        state: 'playing',
        media_title: 'Hey',
        media_artist: 'Someone',
        volume_level: 0.4,
        position: 10,
        duration: 200,
      },
    },
  };
}

describe('ActiveMediaWidget — which player the buttons drive', () => {
  afterEach(() => {
    server.resetHandlers();
    vi.clearAllMocks();
  });

  it("controls this app's own web player directly, not the server's record of it", async () => {
    const sent: unknown[] = [];
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json(playing('web_player'))),
      http.post('/execute/media/transport', async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({ status: 'SUCCESS' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ActiveMediaWidget {...props} />);
    await screen.findByText('Hey');

    await user.click(screen.getByRole('button', { name: /pause/i }));
    await user.click(screen.getByRole('button', { name: 'Next track' }));
    await user.click(screen.getByRole('button', { name: 'Previous track' }));

    expect(webPlayer.pause).toHaveBeenCalledTimes(1);
    expect(webPlayer.next).toHaveBeenCalledTimes(1);
    expect(webPlayer.previous).toHaveBeenCalledTimes(1);
    expect(sent).toEqual([]);
  });

  it('sends other players their commands through the server', async () => {
    const sent: Array<{ entity_id: string; command: string }> = [];
    server.use(
      http.post('/execute/media/status', () => HttpResponse.json(playing('media_player.kitchen'))),
      http.post('/execute/media/transport', async ({ request }) => {
        sent.push((await request.json()) as { entity_id: string; command: string });
        return HttpResponse.json({ status: 'SUCCESS' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ActiveMediaWidget {...props} />);
    await screen.findByText('Hey');

    await user.click(screen.getByRole('button', { name: 'Next track' }));

    expect(sent).toEqual([{ entity_id: 'media_player.kitchen', command: 'next' }]);
    expect(webPlayer.next).not.toHaveBeenCalled();
  });
});
