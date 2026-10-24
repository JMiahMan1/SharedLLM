import { apiClient } from '../../../services/api';
import type {
  AbsProgressRequest,
  AbsProgressResponse,
  MediaFavoritesResponse,
  MediaHomeResponse,
  MediaItemDetail,
  MediaLibraryResponse,
  MediaSearchResponse,
} from '../types';

/**
 * Typed wrappers for the unified media endpoints (§7.5). The older
 * per-service routes stay on `services/api.ts`; these are the ones the media
 * screens are built on. Each throws on HTTP failure so a query shows its own
 * error state; partial upstream failures come back in `errors`.
 */

/** Library tabs the gateway serves (`_MEDIA_LIBRARY_TABS`). */
export const LIBRARY_TABS = ['playlists', 'artists', 'albums', 'tracks', 'audiobooks', 'podcasts', 'radio'] as const;
export type LibraryTab = (typeof LIBRARY_TABS)[number];

export const LIBRARY_PAGE_SIZE = 50;

export async function getMediaHome(): Promise<MediaHomeResponse> {
  const resp = await apiClient.get('/api/media/home', { timeout: 15_000 });
  return resp.data as MediaHomeResponse;
}

export async function searchMedia(q: string, types?: string, limit = 20): Promise<MediaSearchResponse> {
  const params: Record<string, string | number> = { q, limit };
  if (types) params.types = types;
  const resp = await apiClient.get('/api/media/search', { params, timeout: 20_000 });
  return resp.data as MediaSearchResponse;
}

export async function getMediaItem(uri: string): Promise<MediaItemDetail> {
  const resp = await apiClient.get('/api/media/item', { params: { uri }, timeout: 20_000 });
  return resp.data as MediaItemDetail;
}

export async function getMediaLibrary(
  tab: LibraryTab,
  opts: { offset?: number; limit?: number; orderBy?: string; libraryId?: string } = {},
): Promise<MediaLibraryResponse> {
  const params: Record<string, string | number> = {
    offset: opts.offset ?? 0,
    limit: opts.limit ?? LIBRARY_PAGE_SIZE,
  };
  if (opts.orderBy) params.order_by = opts.orderBy;
  if (opts.libraryId) params.library_id = opts.libraryId;
  const resp = await apiClient.get(`/api/media/library/${encodeURIComponent(tab)}`, { params, timeout: 20_000 });
  return resp.data as MediaLibraryResponse;
}

export async function getMediaFavorites(limit = 50): Promise<MediaFavoritesResponse> {
  const resp = await apiClient.get('/api/media/favorites', { params: { limit }, timeout: 20_000 });
  return resp.data as MediaFavoritesResponse;
}

export async function postAbsProgress(req: AbsProgressRequest): Promise<AbsProgressResponse> {
  const resp = await apiClient.post('/api/media/abs/progress', req);
  return resp.data as AbsProgressResponse;
}

/** `abs://<id>` → `<id>`; null for anything that is not an Audiobookshelf item. */
export function absItemId(uri: string | null | undefined): string | null {
  if (!uri || !uri.startsWith('abs://')) return null;
  const rest = uri.slice('abs://'.length);
  if (!rest || rest.startsWith('author/')) return null;
  return rest.split(/[/#]/)[0] || null;
}
