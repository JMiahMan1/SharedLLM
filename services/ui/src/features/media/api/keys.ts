/**
 * The one react-query key factory for media (BUG-63). Two screens asking for
 * the same data share a cache entry; two different requests never collide on
 * a key. Invalidate everything media with `mediaKeys.all`.
 */
export const mediaKeys = {
  all: ['media'] as const,
  home: () => [...mediaKeys.all, 'home'] as const,
  inProgressBooks: () => [...mediaKeys.all, 'abs-in-progress'] as const,
  search: (q: string, types?: string) => [...mediaKeys.all, 'search', q, types ?? 'all'] as const,
  item: (uri: string) => [...mediaKeys.all, 'item', uri] as const,
  detail: (uri: string) => [...mediaKeys.all, 'detail', uri] as const,
  library: (tab: string, libraryId?: string) => [...mediaKeys.all, 'library', tab, libraryId ?? ''] as const,
  favorites: () => [...mediaKeys.all, 'favorites'] as const,
  podcastEpisodes: (absId: string) => [...mediaKeys.all, 'podcast-episodes', absId] as const,
  status: () => [...mediaKeys.all, 'status'] as const,
  entities: () => [...mediaKeys.all, 'entities'] as const,
};
