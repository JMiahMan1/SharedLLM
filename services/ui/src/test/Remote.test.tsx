import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import Remote from '../pages/Remote';

const STATUS = {
  status: 'SUCCESS',
  detail: {
    active: null,
    available: [],
    all_players: [
      { entity_id: 'web_player', friendly_name: 'Web Player', state: 'idle' },
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'idle', icon_kind: 'speaker', volume_level: 0.3 },
      { entity_id: 'media_player.office_tv', friendly_name: 'Office TV Remote', state: 'on', icon_kind: 'tv', volume_level: 0.8, is_volume_muted: false, app_name: 'YouTube' },
      { entity_id: 'media_player.loft_tv', friendly_name: 'Loft TV', state: 'off', icon_kind: 'tv' },
      { entity_id: 'media_player.rv_tv', friendly_name: 'RV TV', state: 'unavailable', icon_kind: 'tv' },
    ],
  },
};

let sent: Array<Record<string, unknown>> = [];

beforeEach(() => {
  sent = [];
  server.use(
    http.post('/execute/media/status', () => HttpResponse.json(STATUS)),
    http.post('/execute/media/transport', async ({ request }) => {
      sent.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ status: 'SUCCESS', message: 'ok' });
    }),
  );
});

describe('Remote page', () => {
  it('starts on the first TV that is on and shows its real volume', async () => {
    renderWithProviders(<Remote />);
    expect(await screen.findByRole('button', { name: /Office TV Remote/, pressed: true })).toBeInTheDocument();
    expect(await screen.findByText('80%')).toBeInTheDocument();
    expect(screen.getByText('YouTube')).toBeInTheDocument();
    // The browser's own player is not a TV to remote-control.
    expect(screen.queryByRole('button', { name: /^Web Player/ })).not.toBeInTheDocument();
  });

  it('sends each d-pad key as itself (they all used to send "home")', async () => {
    renderWithProviders(<Remote />);
    await screen.findByRole('button', { name: /Office TV Remote/, pressed: true });
    for (const label of ['Up', 'Down', 'Left', 'Right', 'OK', 'Back', 'Home', 'Menu']) {
      await userEvent.click(screen.getByRole('button', { name: label }));
    }
    await waitFor(() => expect(sent).toHaveLength(8));
    expect(sent.map((s) => s.command)).toEqual(['up', 'down', 'left', 'right', 'select', 'back', 'home', 'menu']);
    expect(sent.every((s) => s.entity_id === 'media_player.office_tv')).toBe(true);
  });

  it('mutes with an explicit state (there is no "unmute" command)', async () => {
    renderWithProviders(<Remote />);
    await screen.findByRole('button', { name: /Office TV Remote/, pressed: true });
    await userEvent.click(screen.getByRole('button', { name: 'Mute' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ command: 'volume_mute', muted: true });
  });

  it('turns an off TV on with power_on, and an on TV off with power_off', async () => {
    renderWithProviders(<Remote />);
    await userEvent.click(await screen.findByRole('button', { name: /Loft TV/ }));
    await userEvent.click(await screen.findByRole('button', { name: /Turn on/ }));
    await userEvent.click(screen.getByRole('button', { name: /Office TV Remote/ }));
    await userEvent.click(await screen.findByRole('button', { name: /Turn off/ }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent.map((s) => [s.entity_id, s.command])).toEqual([
      ['media_player.loft_tv', 'power_on'],
      ['media_player.office_tv', 'power_off'],
    ]);
  });

  it('steps the volume from the device level', async () => {
    renderWithProviders(<Remote />);
    await screen.findByRole('button', { name: /Office TV Remote/, pressed: true });
    await userEvent.click(screen.getByRole('button', { name: 'Volume up' }));
    await waitFor(() => expect(sent).toHaveLength(1), { timeout: 2000 });
    expect(sent[0]).toMatchObject({ command: 'volume_set', volume_level: 0.85 });
  });

  it('says when a device is offline instead of showing dead buttons', async () => {
    renderWithProviders(<Remote />);
    await userEvent.click(await screen.findByRole('button', { name: /RV TV/ }));
    expect(await screen.findByText('RV TV is offline')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Up' })).not.toBeInTheDocument();
  });
});
