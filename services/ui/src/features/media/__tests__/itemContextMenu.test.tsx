import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { renderWithProviders } from '../../../test/render';
import { chooseOutput, mediaItem, serveHome } from '../../../test/mediaScreens';

const web = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock('../../../context/MediaPlayerContext', () => ({ useSharedMAWebPlayer: () => web.state }));

import Media from '../../../pages/Media';

function player() {
  return {
    isConnected: true, connectionState: 'CONNECTED', isPlaying: false, mediaTitle: null, mediaArtist: null, mediaImage: null,
    mediaUri: null, mediaFavorite: null, position: 0, duration: 0, volume: 70, muted: false, error: null, reconnectAttempts: 0,
    maxReconnectAttempts: 10, playerId: 'me', connect: vi.fn(), reconnect: vi.fn(), play: vi.fn().mockResolvedValue(undefined),
    enqueue: vi.fn().mockResolvedValue(undefined), pause: vi.fn(), stop: vi.fn(), next: vi.fn(), previous: vi.fn(), seek: vi.fn(),
    setVolume: vi.fn(), setMuted: vi.fn(), maCommand: vi.fn(),
    listMaPlayers: vi.fn().mockResolvedValue([{ player_id: 'office', name: 'Office MA', available: true, state: 'idle', powered: true }]),
  };
}

const SONG = mediaItem({
  uri: 'library://track/7', name: 'Night Song', artist: 'The Band', artist_uri: 'library://artist/4',
  album: 'First', album_uri: 'library://album/9', favorite: false,
});

let maSent: Array<{ command: string; args: Record<string, unknown> }> = [];
let haPlays: Record<string, unknown>[] = [];
let favorites: Array<{ uri: string; favorite: boolean }> = [];

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function renderMedia() {
  return renderWithProviders(
    <>
      <Routes><Route path="/media/*" element={<Media />} /></Routes>
      <Where />
    </>,
    { route: '/media' },
  );
}

async function openMenu(name = 'Night Song') {
  await userEvent.click(await screen.findByRole('button', { name: `More for ${name}` }));
  return within(screen.getByRole('menu', { name: `Actions for ${name}` }));
}

beforeEach(() => {
  web.state = player();
  maSent = [];
  haPlays = [];
  favorites = [];
  serveHome({ recent: [SONG] });
  server.use(
    http.post('/execute/media/status', () => HttpResponse.json({
      status: 'SUCCESS', detail: { active: null, all_players: [{ entity_id: 'media_player.den', friendly_name: 'Den Speaker', state: 'idle' }] },
    })),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
    http.post('/api/media/ma-command', async ({ request }) => {
      maSent.push((await request.json()) as { command: string; args: Record<string, unknown> });
      return HttpResponse.json({ status: 'SUCCESS', result: [] });
    }),
    http.post('/execute/media/play', async ({ request }) => {
      haPlays.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ status: 'SUCCESS' });
    }),
    http.post('/execute/media/state/sync', () => HttpResponse.json({ status: 'SUCCESS' })),
    http.post('/api/media/favorite', async ({ request }) => {
      favorites.push((await request.json()) as { uri: string; favorite: boolean });
      return HttpResponse.json({ status: 'SUCCESS', favorite: true });
    }),
  );
});

describe('ItemContextMenu (P5-T7)', () => {
  it('queues next on this device through the web player', async () => {
    renderMedia();
    await userEvent.click((await openMenu()).getByRole('menuitem', { name: 'Play next' }));
    await waitFor(() => expect(web.state.enqueue).toHaveBeenCalledWith('library://track/7', 'next'));
    expect(web.state.play).not.toHaveBeenCalled();
  });

  it('adds to a Music Assistant speaker\'s queue through the gateway', async () => {
    renderMedia();
    await chooseOutput(/Office MA/);
    await userEvent.click((await openMenu()).getByRole('menuitem', { name: 'Add to queue' }));
    await waitFor(() => expect(maSent).toContainEqual({
      command: 'player_queues/play_media', args: { queue_id: 'office', media: ['library://track/7'], option: 'add' },
    }));
    expect(web.state.enqueue).not.toHaveBeenCalled();
  });

  it('queues on a Home Assistant speaker through the execution service', async () => {
    renderMedia();
    await chooseOutput(/Den Speaker/);
    await userEvent.click((await openMenu()).getByRole('menuitem', { name: 'Play next' }));
    await waitFor(() => expect(haPlays).toContainEqual(expect.objectContaining({
      entity_id: 'media_player.den', query: 'library://track/7', enqueue: 'next',
    })));
  });

  it('goes to the artist and the album', async () => {
    renderMedia();
    await userEvent.click((await openMenu()).getByRole('menuitem', { name: 'Go to artist' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/media/artist/library/4');
  });

  it('UX-31 favorites optimistically and rolls back when it fails', async () => {
    renderMedia();
    await userEvent.click((await openMenu()).getByRole('menuitem', { name: 'Add to favorites' }));
    await waitFor(() => expect(favorites).toEqual([{ uri: 'library://track/7', favorite: true }]));
    expect((await openMenu()).getByRole('menuitem', { name: 'Remove from favorites' })).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');

    server.use(http.post('/api/media/favorite', () => HttpResponse.json({ detail: 'nope' }, { status: 500 })));
    await userEvent.click((await openMenu()).getByRole('menuitem', { name: 'Remove from favorites' }));
    await waitFor(async () => expect((await openMenu()).getByRole('menuitem', { name: 'Remove from favorites' })).toBeInTheDocument(), { timeout: 4000 });
  });

  it('works from the keyboard: arrows move, Escape closes and returns focus', async () => {
    renderMedia();
    const trigger = await screen.findByRole('button', { name: 'More for Night Song' });
    trigger.focus();
    await userEvent.keyboard('{ArrowDown}');
    const menu = screen.getByRole('menu');
    expect(within(menu).getByRole('menuitem', { name: 'Play next' })).toHaveFocus();
    await userEvent.keyboard('{ArrowDown}');
    expect(within(menu).getByRole('menuitem', { name: 'Add to queue' })).toHaveFocus();
    await userEvent.keyboard('{End}');
    expect(within(menu).getByRole('menuitem', { name: 'Add to favorites' })).toHaveFocus();
    await userEvent.keyboard('{ArrowDown}');
    expect(within(menu).getByRole('menuitem', { name: 'Play next' })).toHaveFocus();
    await userEvent.keyboard('{ArrowUp}{Home}');
    expect(within(menu).getByRole('menuitem', { name: 'Play next' })).toHaveFocus();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('closes on a click outside, and an artist can be queued now that it plays', async () => {
    serveHome({ recent: [mediaItem({ uri: 'library://artist/4', name: 'The Band', media_type: 'artist' })] });
    renderMedia();
    const menu = await openMenu('The Band');
    // An artist plays now - Music Assistant expands it into every track it
    // has - so Play next and Add to queue are offered for it too.
    expect(menu.getByRole('menuitem', { name: 'Play next' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('heading', { name: 'Listen Now' }));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
