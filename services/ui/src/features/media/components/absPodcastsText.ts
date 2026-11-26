import type { AbsPodcastShow } from '../types';

/**
 * What a podcast row says, kept out of the component so the list can be tested
 * without rendering it (and so the file exports components only, which is what
 * Fast Refresh needs).
 */

/** "11 episodes · newest 2026-07-28 (120 days old)" */
export function lineFor(show: AbsPodcastShow): string {
  const episodes = `${show.episodes} ${show.episodes === 1 ? 'episode' : 'episodes'}`;
  if (!show.newest_episode_at) return `${episodes} · no episodes yet`;
  const days = show.days_since_episode;
  const when = days === null ? '' : days === 0 ? ' (today)' : ` (${days} days old)`;
  return `${episodes} · newest ${shortDate(show.newest_episode_at)}${when}`;
}

/**
 * The one thing worth saying about a show, or '' when nothing is wrong.
 *
 * The order is what the listener can act on first. A missing feed address is
 * unfixable from here. Episodes sitting in the feed that Audiobookshelf has not
 * taken is the fault the family actually notices, and it is a better answer than
 * "not checked since July" because it says how much is waiting.
 */
export function problemFor(show: AbsPodcastShow): string {
  if (show.needs_feed) return 'No feed address saved for this show';
  if (show.feed_newer) {
    const one = show.feed_newer === 1;
    return `${show.feed_newer} ${one ? 'episode' : 'episodes'} in its feed that have not been taken`;
  }
  if (show.stale_check) return 'Its feed has not been checked';
  if (show.behind) return 'Nothing new for a while';
  return '';
}

export function shortDate(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toISOString().slice(0, 10);
}
