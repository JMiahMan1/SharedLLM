import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { renderWithProviders } from '../../../test/render';
import searchFixture from '../../../../e2e/fixtures/media/media-search.json';
import { useMediaStore } from '../store/mediaStore';

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

function renderAt(route: string) {
  return renderWithProviders(
    <>
      <Routes><Route path="/media/*" element={<Media />} /></Routes>
      <Where />
    </>,
    { route },
  );
}

let queries: string[] = [];
beforeEach(() => {
  web.state = player();
  queries = [];
  server.use(
    http.get('/api/media/search', ({ request }) => {
      queries.push(new URL(request.url).searchParams.get('q') || '');
      return HttpResponse.json(searchFixture);
    }),
    http.post('/execute/media/status', () => HttpResponse.json({ status: 'SUCCESS', detail: { active: null, all_players: [] } })),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
  );
});

describe('SearchPage (P5-T2)', () => {
  it('groups results and plays the top result with Enter (journey 3)', async () => {
    renderAt('/media/search');
    const box = screen.getByRole('searchbox', { name: 'Search music and books' });
    expect(box).toHaveFocus();
    await userEvent.type(box, 'midnight');
    expect(await screen.findByRole('heading', { name: 'Top result' })).toBeInTheDocument();
    for (const group of ['Songs', 'Artists', 'Albums', 'Playlists', 'Audiobooks', 'Podcasts', 'Authors']) {
      expect(screen.getByRole('heading', { name: group })).toBeInTheDocument();
    }
    // Debounced: one request for the whole word, and the query is in the URL.
    expect(queries).toEqual(['midnight']);
    expect(screen.getByTestId('where')).toHaveTextContent('/media/search?q=midnight');
    await userEvent.keyboard('{Enter}');
    await waitFor(() => expect(web.state.play).toHaveBeenCalledWith('library://track/1'));
    expect(useMediaStore.getState().recentSearches).toEqual(['midnight']);
  });

  it('filters to one kind with the chips, keeping the query', async () => {
    renderAt('/media/search?q=neon');
    await screen.findByRole('heading', { name: 'Top result' });
    await userEvent.click(screen.getByRole('button', { name: 'Albums' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/media/search?q=neon&type=albums');
    expect(screen.queryByRole('heading', { name: 'Top result' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Songs' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Albums' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Albums' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('moves through results with the arrow keys', async () => {
    renderAt('/media/search?q=neon');
    await screen.findByRole('heading', { name: 'Top result' });
    const box = screen.getByRole('searchbox');
    box.focus();
    await userEvent.keyboard('{ArrowDown}');
    // The top result's Play comes first.
    expect(screen.getAllByRole('button', { name: 'Play Midnight Drive' })[0]).toHaveFocus();
    await userEvent.keyboard('{ArrowDown}');
    expect(document.activeElement).not.toBe(box);
    await userEvent.keyboard('{ArrowUp}{ArrowUp}');
    expect(box).toHaveFocus();
  });

  it('UX-20 opens an author as a search of their books, never disabled', async () => {
    renderAt('/media/search?q=weir');
    const authors = within((await screen.findByRole('heading', { name: 'Authors' })).closest('section')!);
    await userEvent.click(authors.getByRole('button', { name: /Andy Weir/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/media/search?q=Andy+Weir&type=audiobooks');
  });

  it('offers recent searches when the box is empty, and clears them', async () => {
    useMediaStore.getState().actions.addRecentSearch('abba');
    renderAt('/media/search');
    await userEvent.click(screen.getByRole('button', { name: 'abba' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/media/search?q=abba');
    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }));
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(useMediaStore.getState().recentSearches).toEqual([]);
    expect(screen.getByText('Search your music library and audiobooks.')).toBeInTheDocument();
  });

  it('UX-19 shows skeletons, not a "(0)" count, while searching', async () => {
    server.use(http.get('/api/media/search', () => new Promise(() => {})));
    renderAt('/media/search?q=slow');
    expect(await screen.findByRole('status', { name: 'Searching' })).toBeInTheDocument();
    expect(screen.queryByText(/\(0\)/)).not.toBeInTheDocument();
  });

  it('says when nothing matched, when a service is down, and when search failed', async () => {
    server.use(http.get('/api/media/search', () => HttpResponse.json({
      top: null, tracks: [], artists: [], albums: [], playlists: [], audiobooks: [], podcasts: [], authors: [],
      errors: { ma: null, abs: 'Audiobookshelf is unreachable: TimeoutError' },
    })));
    const { unmount } = renderAt('/media/search?q=zzz');
    expect(await screen.findByText('No results for “zzz”')).toBeInTheDocument();
    expect(screen.getByText(/Audiobookshelf did not answer/)).toBeInTheDocument();
    unmount();

    server.use(http.get('/api/media/search', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
    renderAt('/media/search?q=zzz');
    expect(await screen.findByText('Couldn’t search', {}, { timeout: 5000 })).toBeInTheDocument();
  });
});
