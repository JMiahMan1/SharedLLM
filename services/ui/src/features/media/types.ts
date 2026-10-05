/**
 * Wire types for the unified media endpoints (docs/MEDIA_OVERHAUL.md §7.5).
 *
 * These mirror the Pydantic models in `services/gateway/media_models.py`.
 * The JSON Schemas are generated from those models into
 * `./__generated__/schemas.json` and validated against fixtures by
 * `schemaContract.test.ts`; keep both in sync when a field changes.
 */

/** Per-service failure notes for a partial response (§4.5). */
export interface MediaErrorInfo {
  ma: string | null
  abs: string | null
}

/** A normalized playable item from either Music Assistant or ABS. */
export interface MediaItem {
  uri: string
  name: string
  media_type: string
  artist: string
  album: string
  image: string
  duration: number | null
  version: string
  favorite: boolean | null
}

/** An Audiobookshelf library (the `audiobooks` library tab). */
export interface MediaLibrary {
  id: string
  name: string
  media_type: string
}

/** One call for Listen Now (GET /api/media/home). */
export interface MediaHomeResponse {
  recent: MediaItem[]
  continue: MediaItem[]
  playlists: MediaItem[]
  favorites: MediaItem[]
  radio: MediaItem[]
  errors: MediaErrorInfo
}

/** Unified search results (GET /api/media/search). */
export interface MediaSearchResponse {
  top: MediaItem | null
  tracks: MediaItem[]
  artists: MediaItem[]
  albums: MediaItem[]
  playlists: MediaItem[]
  audiobooks: MediaItem[]
  podcasts: MediaItem[]
  authors: MediaItem[]
  errors: MediaErrorInfo
}

/** A track, chapter, album or episode under a media item detail. */
export interface MediaItemChild {
  uri: string
  name: string
  media_type: string
  image: string
  duration: number | null
  index: number | null
}

/** Album/artist/playlist/book/podcast detail with children (GET /api/media/item). */
export interface MediaItemDetail {
  uri: string
  name: string
  media_type: string
  artist: string
  album: string
  image: string
  duration: number | null
  description: string
  children: MediaItemChild[]
  errors: MediaErrorInfo
}

/** A paginated library tab (GET /api/media/library/{tab}). */
export interface MediaLibraryResponse {
  tab: string
  items: MediaItem[]
  libraries: MediaLibrary[]
  offset: number
  limit: number
  errors: MediaErrorInfo
}

/** Favorite items (GET /api/media/favorites). */
export interface MediaFavoritesResponse {
  items: MediaItem[]
  errors: MediaErrorInfo
}

/** Playback progress push (POST /api/media/abs/progress). */
export interface AbsProgressRequest {
  item_id: string
  episode_id?: string | null
  current_time: number
  duration: number
  is_finished?: boolean
}

/** Result of a progress push. */
export interface AbsProgressResponse {
  status: string
  item_id: string
  episode_id?: string | null
  message: string
}

/** Every model name present in `__generated__/schemas.json`. */
export type MediaSchemaName =
  | 'MediaErrorInfo'
  | 'MediaItem'
  | 'MediaLibrary'
  | 'MediaHomeResponse'
  | 'MediaSearchResponse'
  | 'MediaItemChild'
  | 'MediaItemDetail'
  | 'MediaLibraryResponse'
  | 'MediaFavoritesResponse'
  | 'AbsProgressRequest'
  | 'AbsProgressResponse'
