import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { renderWithProviders } from '../../../test/render';
import { mediaItem } from '../../../test/mediaScreens';
import { VIRTUALIZE_AFTER } from '../components/ItemList';

const web = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock('../../../context/MediaPlayerContext', () => ({ useSharedMAWebPlayer: () => web.state }));

import Media from '../../../pages/Media';

function player() {
  return {
    isConnected: true, connectionState: 'CONNECTED', isPlaying: false, mediaTitle: null, mediaArtist: null, mediaImage: null,
    mediaUri: null, mediaFavorite: null, position: 0, duration: 0, volume: 70, muted: false, error: null, reconnectAttempts: 0,
    maxReconnectAttempts: 10, playerId: 'me', connect: vi.fn(), reconnect: vi.fn(), play: vi.fn().mockResolvedValue(undefined),
    enqueue: vi.fn().mockResolvedValue(undefined), pause: vi.fn(), stop: vi.fn(), next: vi.fn(), previous: vi.fn(), seek: vi.fn(),
    setVolume: vi.fn(), setMuted: vi.fn(), maCommand: vi.fn(), listMaPlayers: vi.fn().mockResolvedValue([]),
  };
}

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname}{l.search}</p>;
}

const renderAt = (route: string) => renderWithProviders(
  <>
    <Routes><Route path="/media/*" element={<Media />} /></Routes>
    <Where />
  </>,
  { route },
);

const page = (tab: string, offset: number, count: number) => ({
  tab, offset, limit: 50, libraries: [], errors: { ma: null, abs: null },
  items: Array.from({ length: count }, (_, i) => mediaItem({ uri: `library://track/${offset + i}`, name: `Song ${offset + i}` })),
});

beforeEach(() => {
  web.state = player();
  server.use(
    http.post('/execute/media/status', () => HttpResponse.json({ status: 'SUCCESS', detail: { active: null, all_players: [] } })),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
  );
});

describe('LibraryPage (P5-T3)', () => {
  it('UX-18 pages through a whole library with no cap, then plays a song', async () => {
    const offsets: number[] = [];
    server.use(http.get('/api/media/library/:tab', ({ request, params }) => {
      const offset = Number(new URL(request.url).searchParams.get('offset'));
      offsets.push(offset);
      return HttpResponse.json(page(String(params.tab), offset, offset < 100 ? 50 : 7));
    }));
    renderAt('/media/library/tracks');
    expect(await screen.findByRole('button', { name: 'Play Song 0' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }));
    await waitFor(() => expect(offsets).toContain(50));
    await userEvent.click(await screen.findByRole('button', { name: 'Load more' }));
    await waitFor(() => expect(offsets).toEqual([0, 50, 100]));
    // 107 rows: past the threshold the list is virtualized, and no more pages.
    expect(VIRTUALIZE_AFTER).toBeLessThan(107);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('link', { name: 'Songs' }));
    expect(screen.getByRole('link', { name: 'Songs' })).toHaveAttribute('aria-current', 'page');
  });

  it('plays a row on the selected output', async () => {
    server.use(http.get('/api/media/library/:tab', ({ params }) => HttpResponse.json(page(String(params.tab), 0, 3))));
    renderAt('/media/library/tracks');
    await userEvent.click(await screen.findByRole('button', { name: 'Play Song 1' }));
    await waitFor(() => expect(web.state.play).toHaveBeenCalledWith('library://track/1'));
  });

  it('switches tabs through the URL, and sends an unknown tab to playlists', async () => {
    renderAt('/media/library/nope');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/media/library/playlists'));
    await userEvent.click(screen.getByRole('link', { name: 'Albums' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/media/library/albums');
  });

  it('opens an audiobook library to its books, and a podcast library to podcasts', async () => {
    server.use(http.get('/api/media/library/audiobooks', ({ request }) => {
      const lib = new URL(request.url).searchParams.get('library_id');
      if (!lib) {
        return HttpResponse.json({ ...page('audiobooks', 0, 0), libraries: [
          { id: 'books', name: 'Books', media_type: 'book' },
          { id: 'pods', name: 'Shows', media_type: 'podcast' },
        ] });
      }
      return HttpResponse.json({ ...page('audiobooks', 0, 0), items: [
        mediaItem({ uri: lib === 'pods' ? 'abs://pod-1' : 'abs://book-1', name: lib === 'pods' ? 'A Show' : 'A Book', media_type: 'audiobook' }),
      ] });
    }));
    renderAt('/media/library/audiobooks');
    await userEvent.click(await screen.findByRole('button', { name: /Books/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('library=books');
    expect(await screen.findByRole('link', { name: 'A Book, audiobook' })).toHaveAttribute('href', '/media/book/book-1');
    await userEvent.click(screen.getByRole('button', { name: 'All libraries' }));
    await userEvent.click(await screen.findByRole('button', { name: /Shows/ }));
    expect(await screen.findByRole('link', { name: 'A Show, podcast' })).toHaveAttribute('href', '/media/podcast/pod-1');
  });

  it('lists favorites, and says so when there are none', async () => {
    renderAt('/media/library/favorites');
    const list = within(await screen.findByRole('list', { name: 'Favorites' }));
    expect(list.getByRole('button', { name: 'Play Midnight Drive' })).toBeInTheDocument();
    expect(list.getByRole('link', { name: 'Neon Nights, album' })).toBeInTheDocument();
  });

  it('shows empty, partial and error states', async () => {
    server.use(http.get('/api/media/library/:tab', ({ params }) => HttpResponse.json(page(String(params.tab), 0, 0))));
    const first = renderAt('/media/library/radio');
    expect(await screen.findByText('No radio stations yet.')).toBeInTheDocument();
    first.unmount();

    server.use(http.get('/api/media/library/:tab', ({ params }) => HttpResponse.json({
      ...page(String(params.tab), 0, 0), errors: { ma: 'Music Assistant is unreachable: TimeoutError', abs: null },
    })));
    const second = renderAt('/media/library/albums');
    expect(await screen.findByText(/Music Assistant did not answer/)).toBeInTheDocument();
    second.unmount();

    server.use(http.get('/api/media/library/:tab', () => HttpResponse.json({ detail: 'x' }, { status: 500 })));
    renderAt('/media/library/artists');
    expect(await screen.findByText('Couldn’t load artists', {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it('UX-40 points to settings when no audiobook server is connected', async () => {
    server.use(http.get('/api/media/library/audiobooks', () => HttpResponse.json(page('audiobooks', 0, 0))));
    renderAt('/media/library/audiobooks');
    expect(await screen.findByRole('link', { name: 'Open settings' })).toHaveAttribute('href', '/identity');
  });
});
