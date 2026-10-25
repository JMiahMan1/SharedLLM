import { useParams } from 'react-router-dom';
import { Play } from 'lucide-react';
import { isAxiosError } from 'axios';
import { useMediaItem, usePodcastEpisodes } from '../api/queries';
import { formatTimeLeft } from '../format';
import { maUriFromRoute } from '../items';
import { serviceProblem } from '../copy';
import { Artwork } from '../components/Artwork';
import { EmptyState, ErrorRetry, PartialNotice, RowSkeletons } from '../components/StateViews';
import { TrackRow, type RowItem } from '../components/TrackRow';
import { muted, primaryButton, text } from '../components/ui';
import { useMediaScreen } from './MediaScreen';

interface Episode extends RowItem {
  published: number | null;
  played: boolean;
  /** 0..1 listened, when started. */
  progress: number | null;
}

function episodeLine(e: Episode): string {
  const parts: string[] = [];
  if (e.published) parts.push(new Date(e.published).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }));
  if (e.played) parts.push('Played');
  else if (e.progress && e.duration) parts.push(formatTimeLeft(e.progress * e.duration, e.duration) ?? '');
  else if (e.duration) parts.push(`${Math.round(e.duration / 60)} min`);
  return parts.filter(Boolean).join(' · ');
}

/**
 * A podcast (§4.2, BUG-43): cover, description and its episodes newest
 * first, each with its date, length and whether it was played (or how much
 * is left), and Play latest. An Audiobookshelf podcast (`/media/podcast/<id>`)
 * plays through the episode URIs Music Assistant gives it
 * (`/api/media/podcast-episodes`); a Music Assistant podcast
 * (`/media/podcast/<provider>/<id>`) lists its own.
 */
export default function PodcastPage() {
  const { provider, id = '' } = useParams();
  const isAbs = !provider;
  const uri = isAbs ? `abs://${id}` : maUriFromRoute('podcast', provider, id);
  const detail = useMediaItem(uri);
  const absEpisodes = usePodcastEpisodes(isAbs && detail.data ? id : null, detail.data?.name ?? '');
  const { playback } = useMediaScreen();

  if (detail.isLoading) return <RowSkeletons label="Loading podcast" />;
  if (detail.isError || !detail.data) {
    const notFound = isAxiosError(detail.error) && detail.error.response?.status === 404;
    return notFound
      ? <EmptyState title="This podcast isn’t in your library" action={{ label: 'Go to your library', to: '/media/library/podcasts' }} />
      : <ErrorRetry title="Couldn’t load this podcast" detail="The media service did not answer." onRetry={() => void detail.refetch()} />;
  }

  const podcast = detail.data;
  const episodes: Episode[] = isAbs
    ? (absEpisodes.data?.episodes ?? []).map((e) => ({
        uri: e.uri,
        name: e.name,
        media_type: 'episode',
        image: podcast.image,
        duration: e.duration,
        published: e.published,
        played: Boolean(e.fully_played),
        progress: e.resume_position_ms && e.duration ? Math.min(1, e.resume_position_ms / 1000 / e.duration) : null,
      }))
    : podcast.children.map((c) => ({
        uri: c.uri, name: c.name, media_type: 'episode', image: c.image || podcast.image, duration: c.duration,
        published: null, played: false, progress: null,
      }));
  const episodesLoading = isAbs && absEpisodes.isLoading;
  const episodesFailed = isAbs && absEpisodes.isError;
  const childProblem = isAbs ? null : serviceProblem('Music Assistant', podcast.errors.ma);
  const latest = episodes[0];
  const latestKey = latest ? `play:${latest.uri}` : '';

  return (
    <>
      <header className="flex flex-col gap-5 sm:flex-row sm:items-end">
        <div className="w-44 shrink-0 sm:w-52">
          <Artwork image={podcast.image} kind="podcast" size={208} fill />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <p className={`text-xs font-medium uppercase tracking-wide ${muted}`}>Podcast</p>
          <h1 className={`text-[clamp(1.5rem,4vw,2.25rem)] font-bold leading-tight ${text}`}>{podcast.name}</h1>
          {podcast.artist ? <p className={`text-sm ${muted}`}>{podcast.artist}</p> : null}
          {latest ? (
            <div className="pt-2">
              <button
                type="button"
                onClick={() => void playback.play({ kind: 'uri', uri: latest.uri, title: latest.name }, latestKey)}
                disabled={playback.busy === latestKey}
                className={primaryButton}
              >
                <Play size={18} aria-hidden />Play latest
              </button>
            </div>
          ) : null}
        </div>
      </header>

      {podcast.description ? <p className={`line-clamp-4 max-w-3xl whitespace-pre-line text-sm ${muted}`}>{podcast.description}</p> : null}

      <section aria-labelledby="episodes" className="space-y-2">
        <h2 id="episodes" className={`text-lg font-semibold ${text}`}>Episodes</h2>
        {childProblem ? <PartialNotice>{childProblem}</PartialNotice> : null}
        {episodesLoading ? <RowSkeletons label="Loading episodes" />
          : episodesFailed ? <ErrorRetry title="Couldn’t load the episodes" detail="Your music library could not find this podcast." onRetry={() => void absEpisodes.refetch()} />
            : !episodes.length ? (childProblem ? null : <EmptyState title="No episodes yet" />)
              : (
                <ul aria-label="Episodes" className="space-y-1">
                  {episodes.map((e) => <TrackRow key={e.uri} item={e} subtitle={episodeLine(e)} />)}
                </ul>
              )}
      </section>
    </>
  );
}
