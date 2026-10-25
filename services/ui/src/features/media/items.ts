import { BookOpen, Disc3, ListMusic, Mic2, Music, Radio, User, type LucideIcon } from 'lucide-react';
import type { PlayRequest } from '../../components/media/types';
import { absItemId } from './api/client';

/**
 * What a media item is and where it goes: the one mapping from the gateway's
 * `media_type` + `uri` to a play request, a detail route and an icon, shared
 * by every card, row and menu.
 */

export interface ItemLike {
  uri: string;
  name: string;
  media_type: string;
  duration?: number | null;
}

export type ItemKind = 'track' | 'album' | 'artist' | 'playlist' | 'radio' | 'audiobook' | 'podcast' | 'episode' | 'author' | 'chapter' | 'other';

export function itemKind(item: Pick<ItemLike, 'media_type' | 'uri'>): ItemKind {
  const t = (item.media_type || '').toLowerCase();
  if (t === 'track' || t === 'album' || t === 'artist' || t === 'playlist' || t === 'radio' || t === 'audiobook' || t === 'podcast' || t === 'author' || t === 'chapter') return t;
  if (t === 'podcast_episode' || t === 'episode') return 'episode';
  if (t === 'book') return 'audiobook';
  // MA URIs carry the kind: library://album/1, spotify://track/x.
  const fromUri = /^[^:]+:\/\/([a-z_]+)\//.exec(item.uri)?.[1];
  if (fromUri && fromUri !== t) return itemKind({ media_type: fromUri, uri: '' });
  return 'other';
}

export const KIND_ICON: Record<ItemKind, LucideIcon> = {
  track: Music, album: Disc3, artist: User, playlist: ListMusic, radio: Radio, audiobook: BookOpen,
  podcast: Mic2, episode: Mic2, author: User, chapter: BookOpen, other: Music,
};

export const KIND_LABEL: Record<ItemKind, string> = {
  track: 'Song', album: 'Album', artist: 'Artist', playlist: 'Playlist', radio: 'Radio', audiobook: 'Audiobook',
  podcast: 'Podcast', episode: 'Episode', author: 'Author', chapter: 'Chapter', other: 'Item',
};

/** What playing `item` asks for, or null when it is not directly playable. */
export function playRequestFor(item: ItemLike): PlayRequest | null {
  const kind = itemKind(item);
  if (kind === 'artist' || kind === 'author' || kind === 'chapter') return null;
  const absId = absItemId(item.uri);
  if (absId) {
    // An Audiobookshelf podcast plays through its episodes (MA URIs).
    if (kind === 'podcast') return null;
    return { kind: 'abs', id: absId, title: item.name, durationSec: item.duration ?? null };
  }
  return item.uri ? { kind: 'uri', uri: item.uri, title: item.name } : null;
}

/** Queueing needs a Music Assistant URI or a book Music Assistant can map. */
export function canQueue(item: ItemLike): boolean {
  return playRequestFor(item) !== null;
}

/** `library://album/12` → { provider: 'library', type: 'album', id: '12' }. */
export function parseMaUri(uri: string): { provider: string; type: string; id: string } | null {
  const m = /^([^:/]+):\/\/([a-z_]+)\/(.+)$/.exec(uri);
  return m ? { provider: m[1], type: m[2], id: m[3] } : null;
}

/** The URI a detail route stands for (the inverse of `detailPath`). */
export function maUriFromRoute(type: string, provider: string, id: string): string {
  return `${provider}://${type}/${id}`;
}

/** The detail route for a collection, or null for items that only play. */
export function detailPath(item: Pick<ItemLike, 'uri' | 'media_type'>): string | null {
  const kind = itemKind(item);
  const absId = absItemId(item.uri);
  if (absId) {
    if (kind === 'audiobook') return `/media/book/${encodeURIComponent(absId)}`;
    if (kind === 'podcast') return `/media/podcast/${encodeURIComponent(absId)}`;
    return null;
  }
  if (kind !== 'album' && kind !== 'artist' && kind !== 'playlist' && kind !== 'podcast') return null;
  const parsed = parseMaUri(item.uri);
  if (!parsed) return null;
  return `/media/${kind}/${encodeURIComponent(parsed.provider)}/${encodeURIComponent(parsed.id)}`;
}

/** "Artist · Album" style subtitle from what the item carries. */
export function itemSubtitle(item: { artist?: string; album?: string; media_type: string; uri: string }): string {
  const kind = itemKind(item);
  if (kind === 'playlist' || kind === 'radio') return KIND_LABEL[kind];
  return [item.artist, kind === 'track' ? item.album : ''].filter(Boolean).join(' · ') || KIND_LABEL[kind];
}
