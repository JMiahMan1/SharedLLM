import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { api } from '../../../services/api';
import { useDebounce } from '../../../hooks/useDebounce';
import { mediaKeys } from './keys';
import {
  getMediaFavorites, getMediaHome, getMediaItem, getMediaLibrary, LIBRARY_PAGE_SIZE, searchMedia, type LibraryTab,
} from './client';

/**
 * Media queries with the shared defaults (§6.3): a minute of freshness, one
 * retry, and no refetch just because the window regained focus. Every hook
 * keys through `mediaKeys`, so screens that ask for the same thing share it.
 */
export const MEDIA_QUERY_DEFAULTS = {
  staleTime: 60_000,
  retry: 1,
  refetchOnWindowFocus: false,
} as const;

/** Search waits this long after the last keystroke (BUG-62). */
export const SEARCH_DEBOUNCE_MS = 250;
/** Shorter queries return noise; nothing is requested below this. */
export const MIN_SEARCH_LENGTH = 2;

export function useMediaHome() {
  return useQuery({ queryKey: mediaKeys.home(), queryFn: getMediaHome, ...MEDIA_QUERY_DEFAULTS });
}

/** Audiobookshelf books with their progress (Continue listening). */
export function useBooksInProgress(enabled = true) {
  return useQuery({
    queryKey: mediaKeys.inProgressBooks(),
    queryFn: () => api.getAudiobookshelfLastPlayed(),
    enabled,
    ...MEDIA_QUERY_DEFAULTS,
  });
}

/**
 * Unified search, debounced (BUG-62). `query` is the raw input; the request
 * follows it 250 ms later, keeping the previous results on screen meanwhile.
 */
export function useMediaSearch(query: string, types?: string) {
  const q = useDebounce(query.trim(), SEARCH_DEBOUNCE_MS);
  const result = useQuery({
    queryKey: mediaKeys.search(q, types),
    queryFn: () => searchMedia(q, types),
    enabled: q.length >= MIN_SEARCH_LENGTH,
    placeholderData: keepPreviousData,
    ...MEDIA_QUERY_DEFAULTS,
  });
  return { ...result, debouncedQuery: q };
}

export function useMediaItem(uri: string | null) {
  return useQuery({
    queryKey: mediaKeys.item(uri ?? ''),
    queryFn: () => getMediaItem(uri!),
    enabled: !!uri,
    ...MEDIA_QUERY_DEFAULTS,
  });
}

/** One library tab, paged by `LIBRARY_PAGE_SIZE` for infinite scroll. */
export function useMediaLibrary(tab: LibraryTab, libraryId?: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: mediaKeys.library(tab, libraryId),
    queryFn: ({ pageParam }) => getMediaLibrary(tab, { offset: pageParam, limit: LIBRARY_PAGE_SIZE, libraryId }),
    initialPageParam: 0,
    // A short page is the last one. The audiobook tab with no chosen library
    // returns the list of libraries itself, which is always a single page.
    getNextPageParam: (last, pages) =>
      (tab === 'audiobooks' && !libraryId) || last.items.length < LIBRARY_PAGE_SIZE
        ? undefined
        : pages.length * LIBRARY_PAGE_SIZE,
    enabled,
    placeholderData: keepPreviousData,
    ...MEDIA_QUERY_DEFAULTS,
  });
}

export function useMediaFavorites(enabled = true) {
  return useQuery({ queryKey: mediaKeys.favorites(), queryFn: () => getMediaFavorites(), enabled, ...MEDIA_QUERY_DEFAULTS });
}

export function usePodcastEpisodes(absId: string | null, title = '') {
  return useQuery({
    queryKey: mediaKeys.podcastEpisodes(absId ?? ''),
    queryFn: () => api.getPodcastEpisodes(absId!, title),
    enabled: !!absId,
    ...MEDIA_QUERY_DEFAULTS,
    staleTime: 5 * 60_000,
  });
}
