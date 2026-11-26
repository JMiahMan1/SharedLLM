import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { CircleAlert, Mic2 } from 'lucide-react';
import type { AbsPodcastShow, AbsPodcastsResponse } from '../types';
import { mediaKeys } from '../api/keys';
import { setPodcastAutoDownload } from '../api/client';
import { lineFor, problemFor } from './absPodcastsText';
import { muted, row, text } from './ui';

/**
 * The podcasts the family actually subscribes to, and why one has stopped
 * moving. A show can be quiet for three unrelated reasons, and they need three
 * different answers, so each is named instead of one vague "stale":
 *
 *   - Audiobookshelf was never given the show's feed address, so it has nothing
 *     to read;
 *   - the feed has an address but nobody polls it (Audiobookshelf runs that on
 *     its own schedule, which can be switched off on the server);
 *   - the show is simply between episodes, or new episodes arrive and nothing
 *     downloads them.
 *
 * Nothing here knows a show by name: the list is the server's own.
 */
export function AbsPodcasts({ report }: { report: AbsPodcastsResponse }) {
  const shows = report.shows ?? [];
  if (!shows.length) return null;
  const attention = shows.filter((s) => s.needs_feed || s.stale_check || s.behind).length;

  return (
    <section className="space-y-3" aria-label="Podcasts in Audiobookshelf" data-testid="abs-podcasts">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className={`text-lg font-semibold ${text}`}>Your podcasts</h2>
        <p className={`text-xs ${muted}`}>
          {shows.length} {shows.length === 1 ? 'show' : 'shows'}
          {attention ? ` · ${attention} need a look` : ' · all up to date'}
        </p>
      </div>
      <ul className="space-y-1">
        {shows.map((show) => (
          <ShowRow key={show.id} show={show} />
        ))}
      </ul>
      {shows.some((s) => s.stale_check) ? (
        <p className={`flex items-start gap-2 text-xs ${muted}`}>
          <CircleAlert size={14} className="mt-0.5 shrink-0" aria-hidden />
          Audiobookshelf reads these feeds on its own schedule. If every show says it has not been checked,
          that schedule is off on the server (Settings, then Tasks, in Audiobookshelf).
        </p>
      ) : null}
    </section>
  );
}

function ShowRow({ show }: { show: AbsPodcastShow }) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => setPodcastAutoDownload(show.id, enabled),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: mediaKeys.absPodcasts() }),
  });

  return (
    <li data-testid={`abs-podcast-show-${show.id}`}>
      <div className={`${row} items-start gap-3`}>
        <Mic2 size={18} className={`mt-1 shrink-0 ${muted}`} aria-hidden />
        <Link to={`/media/podcast/${encodeURIComponent(show.id)}`} className="min-w-0 flex-1">
          <span className={`block truncate text-sm font-medium ${text}`}>{show.title}</span>
          <span className={`block text-xs ${muted}`}>{lineFor(show)}</span>
          {problemFor(show) ? (
            <span className="mt-1 inline-flex items-center gap-1 text-xs text-[var(--site-warn-text,inherit)]">
              <CircleAlert size={12} aria-hidden />
              {problemFor(show)}
            </span>
          ) : null}
        </Link>
        <button
          type="button"
          data-testid={`abs-podcast-auto-${show.id}`}
          aria-pressed={show.auto_download}
          aria-label={`Download new episodes of ${show.title} automatically`}
          onClick={() => toggle.mutate(!show.auto_download)}
          disabled={toggle.isPending}
          className="shrink-0 rounded-xl px-2 py-1 text-xs font-medium min-h-11"
        >
          {show.auto_download ? 'Downloading' : 'Download new'}
        </button>
      </div>
    </li>
  );
}

export default AbsPodcasts;
