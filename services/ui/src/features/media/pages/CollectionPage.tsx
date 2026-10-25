import { useParams } from 'react-router-dom';
import { Play, Shuffle } from 'lucide-react';
import { isAxiosError } from 'axios';
import { useMediaItem } from '../api/queries';
import type { MediaItemChild } from '../types';
import { formatDuration } from '../format';
import { itemKind, KIND_LABEL, maUriFromRoute } from '../items';
import { serviceProblem } from '../copy';
import { Artwork } from '../components/Artwork';
import { ItemContextMenu } from '../components/ItemContextMenu';
import { MediaCard } from '../components/MediaCard';
import { EmptyState, ErrorRetry, PartialNotice, RowSkeletons } from '../components/StateViews';
import { TrackRow } from '../components/TrackRow';
import { muted, primaryButton, secondaryButton, text } from '../components/ui';
import { detailPath, playRequestFor } from '../items';
import { useMediaScreen } from './MediaScreen';

export type CollectionKind = 'album' | 'artist' | 'playlist';

/** "4 songs · 52 min" from the children MA listed. */
function summary(children: MediaItemChild[], kind: CollectionKind): string {
  if (kind === 'artist') return children.length ? `${children.length} album${children.length === 1 ? '' : 's'}` : '';
  const total = children.reduce((s, c) => s + (c.duration ?? 0), 0);
  const songs = `${children.length} song${children.length === 1 ? '' : 's'}`;
  return total > 0 ? `${songs} · ${formatDuration(total)}` : songs;
}

/**
 * An album, artist or playlist (§4.2): a hero with the artwork, Play and
 * Shuffle on the selected output, then the tracks (or, for an artist, the
 * albums) from `/api/media/item`. The route carries the Music Assistant URI
 * (`/media/album/<provider>/<id>`).
 */
export default function CollectionPage({ kind }: { kind: CollectionKind }) {
  const { provider = '', id = '' } = useParams();
  const uri = maUriFromRoute(kind, provider, id);
  const q = useMediaItem(uri);
  const { playback } = useMediaScreen();
  const label = KIND_LABEL[kind].toLowerCase();

  if (q.isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
          <div className="skeleton aspect-square w-48 rounded-[var(--radius-card)] sm:w-56" />
          <div className="flex-1 space-y-2"><div className="skeleton h-8 w-2/3 rounded" /><div className="skeleton h-4 w-1/3 rounded" /></div>
        </div>
        <RowSkeletons label={`Loading ${label}`} />
      </div>
    );
  }
  if (q.isError || !q.data) {
    const notFound = isAxiosError(q.error) && q.error.response?.status === 404;
    return notFound
      ? <EmptyState title={`This ${label} isn’t in your library`} detail="It may have been removed from your music library." action={{ label: 'Go to your library', to: '/media/library' }} />
      : <ErrorRetry title={`Couldn’t load this ${label}`} detail="Your music library did not answer." onRetry={() => void q.refetch()} />;
  }

  const detail = q.data;
  const children = detail.children;
  const req = playRequestFor({ uri: detail.uri, name: detail.name, media_type: kind });
  const busyKey = `play:${detail.uri}`;
  const childProblem = serviceProblem('Music Assistant', detail.errors.ma);
  const play = async (shuffle: boolean) => {
    if (!req) return;
    await playback.play(req, busyKey);
    // Shuffle after the queue is filled, so the new queue is what gets shuffled.
    if (shuffle) await playback.setShuffle(true);
  };

  return (
    <>
      <header className="flex flex-col gap-5 sm:flex-row sm:items-end">
        <div className="w-48 shrink-0 shadow-[0_30px_80px_-20px_var(--site-glow)] sm:w-56">
          <Artwork image={detail.image} kind={kind} size={224} fill />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <p className={`text-xs font-medium uppercase tracking-wide ${muted}`}>{KIND_LABEL[kind]}</p>
          <h1 className={`text-[clamp(1.5rem,4vw,2.25rem)] font-bold leading-tight ${text}`}>{detail.name}</h1>
          <p className={`text-sm ${muted}`}>{[detail.artist, summary(children, kind)].filter(Boolean).join(' · ')}</p>
          {detail.description ? <p className={`line-clamp-3 max-w-2xl text-sm ${muted}`}>{detail.description}</p> : null}
          {kind !== 'artist' && req ? (
            <div className="flex flex-wrap items-center gap-2 pt-2">
              <button type="button" onClick={() => void play(false)} disabled={playback.busy === busyKey} className={primaryButton}>
                <Play size={18} aria-hidden />Play
              </button>
              <button type="button" onClick={() => void play(true)} disabled={playback.busy === busyKey} className={secondaryButton}>
                <Shuffle size={18} aria-hidden />Shuffle
              </button>
              <ItemContextMenu item={{ uri: detail.uri, name: detail.name, media_type: kind }} />
            </div>
          ) : null}
        </div>
      </header>

      {childProblem ? <PartialNotice>{childProblem}</PartialNotice> : null}
      {kind === 'artist' ? (
        children.length ? (
          <section aria-labelledby="artist-albums" className="space-y-3">
            <h2 id="artist-albums" className={`text-lg font-semibold ${text}`}>Albums</h2>
            <div className="flex flex-wrap gap-3 sm:gap-4">
              {children.map((album) => {
                const albumReq = playRequestFor(album);
                return (
                  <MediaCard
                    key={album.uri}
                    title={album.name}
                    image={album.image}
                    kind={itemKind(album)}
                    to={detailPath(album)}
                    busy={playback.busy === `play:${album.uri}`}
                    onPlay={albumReq ? () => void playback.play(albumReq, `play:${album.uri}`) : undefined}
                    menuItem={album}
                  />
                );
              })}
            </div>
          </section>
        ) : childProblem ? null : <EmptyState title="No albums by this artist in your library" />
      ) : children.length ? (
        <ul aria-label="Tracks" className="space-y-1">
          {children.map((track, i) => (
            <TrackRow
              key={`${track.uri}-${i}`}
              item={track}
              index={kind === 'album' ? track.index ?? i + 1 : undefined}
              showArt={kind !== 'album'}
              subtitle={track.artist || undefined}
            />
          ))}
        </ul>
      ) : childProblem ? null : <EmptyState title={`This ${label} is empty`} />}
    </>
  );
}
