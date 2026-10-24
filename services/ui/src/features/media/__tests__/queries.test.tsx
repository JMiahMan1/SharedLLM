import { describe, expect, it } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import Ajv2020 from 'ajv/dist/2020';
import { server } from '../../../test/setup';
import schemas from '../__generated__/schemas.json';
import homeFixture from '../../../../e2e/fixtures/media/media-home.json';
import searchFixture from '../../../../e2e/fixtures/media/media-search.json';
import itemFixture from '../../../../e2e/fixtures/media/media-item.json';
import bookFixture from '../../../../e2e/fixtures/media/media-item-book.json';
import libraryFixture from '../../../../e2e/fixtures/media/media-library.json';
import favoritesFixture from '../../../../e2e/fixtures/media/media-favorites.json';
import progressFixture from '../../../../e2e/fixtures/media/abs-progress.json';
import { mediaKeys } from '../api/keys';
import { absItemId, getMediaLibrary, postAbsProgress } from '../api/client';
import {
  MEDIA_QUERY_DEFAULTS, useMediaFavorites, useMediaHome, useMediaItem, useMediaLibrary, useMediaSearch, usePlaylists,
  useRecent, useBooksInProgress, usePodcastEpisodes,
} from '../api/queries';

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, Wrapper };
}

describe('media fixtures', () => {
  // The hermetic e2e and msw both serve these files; they must be what the
  // gateway really sends.
  it.each([
    ['MediaHomeResponse', homeFixture],
    ['MediaSearchResponse', searchFixture],
    ['MediaItemDetail', itemFixture],
    ['MediaItemDetail', bookFixture],
    ['MediaLibraryResponse', libraryFixture],
    ['MediaFavoritesResponse', favoritesFixture],
    ['AbsProgressResponse', progressFixture],
  ])('%s fixture matches the gateway schema', (name, data) => {
    const validate = new Ajv2020({ allErrors: true, strict: false }).compile((schemas as Record<string, object>)[name]);
    expect(validate(data), JSON.stringify(validate.errors)).toBe(true);
  });
});

describe('mediaKeys', () => {
  it('BUG-63 gives every request its own key under one root', () => {
    expect(mediaKeys.playlists()).toEqual(['media', 'playlists']);
    expect(mediaKeys.search('abba')).toEqual(['media', 'search', 'abba', 'all']);
    expect(mediaKeys.search('abba', 'tracks')).not.toEqual(mediaKeys.search('abba'));
    expect(mediaKeys.library('albums')).not.toEqual(mediaKeys.library('albums', 'lib-1'));
    for (const key of [mediaKeys.home(), mediaKeys.item('x'), mediaKeys.favorites(), mediaKeys.podcastEpisodes('p')]) {
      expect(key[0]).toBe('media');
    }
  });
});

describe('media queries', () => {
  it('uses the shared defaults', () => {
    expect(MEDIA_QUERY_DEFAULTS).toEqual({ staleTime: 60_000, retry: 1, refetchOnWindowFocus: false });
  });

  it('loads the Listen Now payload', async () => {
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useMediaHome(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.playlists.map((p) => p.name)).toEqual(['Late Night Synth', 'Focus Flow']);
  });

  it('BUG-62 debounces search and keeps the previous results while typing', async () => {
    const seen: string[] = [];
    server.use(http.get('/api/media/search', ({ request }) => {
      const q = new URL(request.url).searchParams.get('q') || '';
      seen.push(q);
      return HttpResponse.json({ ...searchFixture, top: { ...searchFixture.top, name: q } });
    }));
    const { Wrapper } = wrapper();
    const { result, rerender } = renderHook(({ q }) => useMediaSearch(q), { wrapper: Wrapper, initialProps: { q: 'm' } });
    // One letter never searches.
    await new Promise((r) => setTimeout(r, 300));
    expect(seen).toEqual([]);
    rerender({ q: 'mi' });
    rerender({ q: 'mid' });
    rerender({ q: 'midn' });
    await waitFor(() => expect(result.current.data?.top?.name).toBe('midn'));
    // The keystrokes in between were never sent.
    expect(seen).toEqual(['midn']);
    rerender({ q: 'midni' });
    // While the next request is pending the old results stay up.
    expect(result.current.data?.top?.name).toBe('midn');
    await waitFor(() => expect(result.current.data?.top?.name).toBe('midni'));
  });

  it('loads an item detail and does nothing without a uri', async () => {
    const { Wrapper } = wrapper();
    const { result, rerender } = renderHook(({ uri }) => useMediaItem(uri), { wrapper: Wrapper, initialProps: { uri: null as string | null } });
    expect(result.current.fetchStatus).toBe('idle');
    rerender({ uri: 'abs://book-1' });
    await waitFor(() => expect(result.current.data?.name).toBe('Project Hail Mary'));
  });

  it('pages a library tab until a short page', async () => {
    const offsets: string[] = [];
    server.use(http.get('/api/media/library/:tab', ({ request }) => {
      const offset = new URL(request.url).searchParams.get('offset') || '0';
      offsets.push(offset);
      const count = offset === '0' ? 50 : 3;
      const items = Array.from({ length: count }, (_, i) => ({ ...libraryFixture.items[0], uri: `library://track/${offset}-${i}` }));
      return HttpResponse.json({ ...libraryFixture, items, offset: Number(offset) });
    }));
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useMediaLibrary('tracks'), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);
    await act(async () => { await result.current.fetchNextPage(); });
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
    expect(result.current.hasNextPage).toBe(false);
    expect(offsets).toEqual(['0', '50']);
  });

  it('does not page audiobook libraries', async () => {
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useMediaLibrary('audiobooks'), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(false);
  });

  it('loads favorites, playlists, recents, books in progress and podcast episodes', async () => {
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => ({
      fav: useMediaFavorites(),
      pl: usePlaylists(),
      recent: useRecent(),
      books: useBooksInProgress(),
      eps: usePodcastEpisodes('pod-1', 'The Quiet Hour'),
    }), { wrapper: Wrapper });
    await waitFor(() => {
      expect(result.current.fav.isSuccess).toBe(true);
      expect(result.current.pl.isSuccess).toBe(true);
      expect(result.current.recent.isSuccess).toBe(true);
      expect(result.current.books.isSuccess).toBe(true);
      expect(result.current.eps.isSuccess).toBe(true);
    });
    expect(result.current.fav.data?.items).toHaveLength(2);
    expect(result.current.eps.data?.episodes[0].name).toBe('Episode 2: Slow Mornings');
  });

  it('surfaces an HTTP failure as a query error', async () => {
    server.use(http.get('/api/media/home', () => HttpResponse.json({ detail: 'down' }, { status: 500 })));
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useMediaHome(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 15_000 });
  }, 20_000);
});

describe('media client', () => {
  it('sends library paging and the ABS library id', async () => {
    let url = '';
    server.use(http.get('/api/media/library/:tab', ({ request }) => {
      url = request.url;
      return HttpResponse.json(libraryFixture);
    }));
    await getMediaLibrary('audiobooks', { libraryId: 'lib-1', orderBy: 'name' });
    const params = new URL(url).searchParams;
    expect(params.get('library_id')).toBe('lib-1');
    expect(params.get('order_by')).toBe('name');
    expect(params.get('offset')).toBe('0');
  });

  it('posts ABS progress', async () => {
    let body: unknown = null;
    server.use(http.post('/api/media/abs/progress', async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(progressFixture);
    }));
    const res = await postAbsProgress({ item_id: 'book-1', current_time: 12, duration: 100 });
    expect(res.status).toBe('SUCCESS');
    expect(body).toEqual({ item_id: 'book-1', current_time: 12, duration: 100 });
  });

  it('reads Audiobookshelf ids out of abs:// URIs', () => {
    expect(absItemId('abs://book-1')).toBe('book-1');
    expect(absItemId('abs://book-1#chapter-2')).toBe('book-1');
    expect(absItemId('abs://pod-1/ep-3')).toBe('pod-1');
    expect(absItemId('abs://author/aut-1')).toBeNull();
    expect(absItemId('library://track/1')).toBeNull();
    expect(absItemId(null)).toBeNull();
  });
});
