import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { renderWithProviders } from '../../../test/render';
import { mediaItem } from '../../../test/mediaScreens';

const web = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock('../../../context/MediaPlayerContext', () => ({ useSharedMAWebPlayer: () => web.state }));

import Media from '../../../pages/Media';
import { lineFor, problemFor, shortDate } from '../components/absPodcastsText';

const today = new Date();
const daysAgo = (days: number) => new Date(today.getTime() - days * 86_400_000).toISOString();

const show = (over: Record<string, unknown> = {}) => ({
  library_id: 'pods',
  library_name: 'Podcasts',
  id: 'show',
  title: 'A Show',
  author: 'Someone',
  cover_path: '',
  episodes: 11,
  newest_episode_at: daysAgo(3),
  newest_episode_title: 'An Episode',
  last_checked_at: daysAgo(1),
  days_since_episode: 3,
  days_since_check: 1,
  auto_download: false,
  has_feed: true,
  needs_feed: false,
  never_checked: false,
  stale_check: false,
  behind: false,
  feed_newer: null,
  feed_newest_at: null,
  ...over,
});

const report = (shows: unknown[]) => ({
  status: 'SUCCESS',
  libraries: [{ id: 'pods', name: 'Podcasts' }],
  shows,
  behind_days: 21,
  stale_check_days: 7,
});

const renderAt = (route: string) =>
  renderWithProviders(<Routes><Route path="/media/*" element={<Media />} /></Routes>, { route });

beforeEach(() => {
  web.state = {
    isConnected: true, connectionState: 'CONNECTED', isPlaying: false, mediaTitle: null, mediaArtist: null,
    mediaImage: null, mediaUri: null, mediaFavorite: null, position: 0, duration: 0, volume: 70, muted: false,
    error: null, reconnectAttempts: 0, maxReconnectAttempts: 10, playerId: 'me', connect: vi.fn(),
    reconnect: vi.fn(), play: vi.fn(), enqueue: vi.fn(), pause: vi.fn(), stop: vi.fn(), next: vi.fn(),
    previous: vi.fn(), seek: vi.fn(), setVolume: vi.fn(), setMuted: vi.fn(), maCommand: vi.fn(),
    listMaPlayers: vi.fn().mockResolvedValue([]),
  };
  server.use(
    http.post('/execute/media/status', () =>
      HttpResponse.json({ status: 'SUCCESS', detail: { active: null, all_players: [] } })),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
  );
});

describe('podcasts in Audiobookshelf', () => {
  it('lists the family shows with what each one is doing', async () => {
    server.use(http.get('/api/media/audiobookshelf/podcasts', () => HttpResponse.json(report([
      show({ id: 'quiet', title: 'Quiet Show', needs_feed: true, behind: true, days_since_episode: 120 }),
      show({ id: 'fresh', title: 'Fresh Show' }),
    ]))));

    renderAt('/media/library/podcasts');

    const panel = await screen.findByTestId('abs-podcasts');
    expect(panel).toHaveTextContent('Your podcasts');
    expect(panel).toHaveTextContent('2 shows');
    expect(panel).toHaveTextContent('1 need a look');
    // The show with a problem is named first.
    expect(screen.getAllByTestId(/^abs-podcast-show-/)[0]).toHaveTextContent('Quiet Show');
    // And its problem is stated, not implied.
    expect(screen.getByTestId('abs-podcast-show-quiet')).toHaveTextContent('No feed address saved for this show');
    expect(screen.getByTestId('abs-podcast-show-fresh')).toHaveTextContent('11 episodes');
  });

  it('opens a show from the list', async () => {
    server.use(
      http.get('/api/media/audiobookshelf/podcasts', () => HttpResponse.json(report([show({ id: 'quiet', title: 'Quiet Show' })]))),
      http.get('/api/media/item', () => HttpResponse.json({ ...mediaItem({ uri: 'abs://quiet', name: 'Quiet Show', media_type: 'podcast' }), children: [] })),
    );

    renderAt('/media/library/podcasts');

    const link = await screen.findByRole('link', { name: /Quiet Show/ });
    expect(link).toHaveAttribute('href', '/media/podcast/quiet');
  });

  it('turns automatic downloading on for one show and refetches', async () => {
    const posted: unknown[] = [];
    let downloads = false;
    server.use(
      http.get('/api/media/audiobookshelf/podcasts', () =>
        HttpResponse.json(report([show({ id: 'quiet', title: 'Quiet Show', auto_download: downloads })]))),
      http.post('/api/media/audiobookshelf/podcasts/:id/auto-download', async ({ request }) => {
        posted.push(await request.json());
        downloads = true;
        return HttpResponse.json({ status: 'SUCCESS', item_id: 'quiet', auto_download: true });
      }),
    );

    renderAt('/media/library/podcasts');

    const button = await screen.findByTestId('abs-podcast-auto-quiet');
    expect(button).toHaveTextContent('Download new');
    expect(button).toHaveAttribute('aria-pressed', 'false');

    await userEvent.click(button);

    await waitFor(() => expect(posted).toEqual([{ enabled: true }]));
    await waitFor(() => expect(screen.getByTestId('abs-podcast-auto-quiet')).toHaveTextContent('Downloading'));
  });

  it('says so when Audiobookshelf refuses the change, instead of nothing happening', async () => {
    server.use(
      http.get('/api/media/audiobookshelf/podcasts', () =>
        HttpResponse.json(report([show({ id: 'quiet', title: 'Quiet Show' })]))),
      http.post('/api/media/audiobookshelf/podcasts/:id/auto-download', () =>
        HttpResponse.json({ status: 'FAILURE', message: 'Audiobookshelf URL not configured.' }, { status: 502 })),
    );

    renderAt('/media/library/podcasts');

    await userEvent.click(await screen.findByTestId('abs-podcast-auto-quiet'));

    expect(await screen.findByRole('alert')).toHaveTextContent('Audiobookshelf did not change this');
    // And the switch is still there to try again, not stuck.
    expect(screen.getByTestId('abs-podcast-auto-quiet')).toBeEnabled();
  });

  it('says where the schedule lives when nothing has been checked', async () => {
    server.use(http.get('/api/media/audiobookshelf/podcasts', () => HttpResponse.json(report([
      show({ id: 'quiet', title: 'Quiet Show', stale_check: true, never_checked: true, last_checked_at: null, days_since_check: null }),
    ]))));

    renderAt('/media/library/podcasts');

    const panel = await screen.findByTestId('abs-podcasts');
    expect(panel).toHaveTextContent('that schedule is off on the server');
    expect(screen.getByTestId('abs-podcast-show-quiet')).toHaveTextContent('Its feed has not been checked');
  });

  it('falls back to the Music Assistant library when there are no Audiobookshelf shows', async () => {
    server.use(
      http.get('/api/media/audiobookshelf/podcasts', () => HttpResponse.json(report([]))),
      http.get('/api/media/library/:tab', () =>
        HttpResponse.json({ tab: 'podcasts', offset: 0, limit: 50, libraries: [], errors: { ma: null, abs: null }, items: [mediaItem({ uri: 'library://podcast/1', name: 'An MA Podcast' })] })),
    );

    renderAt('/media/library/podcasts');

    expect(await screen.findByText('An MA Podcast')).toBeInTheDocument();
    expect(screen.queryByTestId('abs-podcasts')).not.toBeInTheDocument();
  });
});

describe('the podcast line and the problem', () => {
  it('names the newest episode and how long ago it was', () => {
    expect(lineFor(show({ newest_episode_at: '2026-07-28T07:00:00Z', days_since_episode: 120 }) as never))
      .toBe('11 episodes · newest 2026-07-28 (120 days old)');
    expect(lineFor(show({ episodes: 1, newest_episode_at: null }) as never)).toBe('1 episode · no episodes yet');
  });

  it('says one thing, and says the first thing that is actually wrong', () => {
    expect(problemFor(show() as never)).toBe('');
    expect(problemFor(show({ needs_feed: true, stale_check: true, behind: true }) as never))
      .toBe('No feed address saved for this show');
    expect(problemFor(show({ stale_check: true, behind: true }) as never)).toBe('Its feed has not been checked');
    // What the family notices: episodes in the feed that Audiobookshelf never took.
    expect(problemFor(show({ stale_check: true, behind: true, feed_newer: 12 }) as never))
      .toBe('12 episodes in its feed that have not been taken');
    expect(problemFor(show({ feed_newer: 1 }) as never)).toBe('1 episode in its feed that have not been taken');
    // Nothing waiting reads as nothing wrong, however quiet the show is.
    expect(problemFor(show({ stale_check: true, behind: true, feed_newer: 0 }) as never))
      .toBe('Its feed has not been checked');
    expect(problemFor(show({ behind: true }) as never)).toBe('Nothing new for a while');
  });

  it('keeps a date that cannot be read instead of inventing one', () => {
    expect(shortDate('not a date')).toBe('not a date');
    expect(shortDate('2026-07-28T07:00:00Z')).toBe('2026-07-28');
  });
});
