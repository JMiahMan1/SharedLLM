import { http, HttpResponse } from 'msw';
import entitiesFixture from '../../../../e2e/fixtures/media/entities.json';
import groupsMediaFixture from '../../../../e2e/fixtures/media/groups-media.json';
import mediaStatusFixture from '../../../../e2e/fixtures/media/media-status.json';
import transportFixture from '../../../../e2e/fixtures/media/transport.json';
import playFixture from '../../../../e2e/fixtures/media/play.json';
import absLastPlayedFixture from '../../../../e2e/fixtures/media/abs-last-played.json';
import absExecuteFixture from '../../../../e2e/fixtures/media/abs-execute.json';
import mediaHomeFixture from '../../../../e2e/fixtures/media/media-home.json';
import mediaSearchFixture from '../../../../e2e/fixtures/media/media-search.json';
import mediaItemFixture from '../../../../e2e/fixtures/media/media-item.json';
import mediaItemBookFixture from '../../../../e2e/fixtures/media/media-item-book.json';
import mediaLibraryFixture from '../../../../e2e/fixtures/media/media-library.json';
import mediaFavoritesFixture from '../../../../e2e/fixtures/media/media-favorites.json';
import absProgressFixture from '../../../../e2e/fixtures/media/abs-progress.json';
import podcastEpisodesFixture from '../../../../e2e/fixtures/media/podcast-episodes.json';

export const mediaHandlers = [
  http.get('/api/entities', () => HttpResponse.json(entitiesFixture)),
  http.get('/api/groups/media', () => HttpResponse.json(groupsMediaFixture)),
  http.post('/api/groups/media', () =>
    HttpResponse.json({ status: 'SUCCESS', message: 'Group created' })
  ),
  http.delete('/api/groups/media/:name', () =>
    HttpResponse.json({ status: 'SUCCESS', message: 'Group deleted' })
  ),
  http.post('/execute/media/status', () => HttpResponse.json(mediaStatusFixture)),
  http.post('/execute/media/transport', () => HttpResponse.json(transportFixture)),
  http.post('/execute/media/play', () => HttpResponse.json(playFixture)),
  // Remote MA speakers are listed and controlled through the gateway.
  http.post('/api/media/ma-command', () => HttpResponse.json({ status: 'SUCCESS', result: [] })),
  // The app-wide web player reports what it plays (context/MediaPlayerContext).
  http.post('/execute/media/state/sync', () =>
    HttpResponse.json({ status: 'SUCCESS', message: 'Local playback state synced.' })
  ),
  http.get('/api/media/detail', () => HttpResponse.json({})),
  http.get('/api/media/audiobookshelf/last-played', () =>
    HttpResponse.json(absLastPlayedFixture)
  ),
  http.post('/execute/audiobookshelf', () => HttpResponse.json(absExecuteFixture)),
  // Unified media endpoints (docs/MEDIA_OVERHAUL.md §7.5), real gateway shapes.
  http.get('/api/media/home', () => HttpResponse.json(mediaHomeFixture)),
  http.get('/api/media/search', () => HttpResponse.json(mediaSearchFixture)),
  http.get('/api/media/item', ({ request }) => {
    const uri = new URL(request.url).searchParams.get('uri') || '';
    return HttpResponse.json(uri.startsWith('abs://') ? mediaItemBookFixture : mediaItemFixture);
  }),
  http.get('/api/media/library/:tab', ({ params }) =>
    HttpResponse.json({ ...mediaLibraryFixture, tab: params.tab })
  ),
  http.get('/api/media/favorites', () => HttpResponse.json(mediaFavoritesFixture)),
  http.post('/api/media/abs/progress', () => HttpResponse.json(absProgressFixture)),
  http.get('/api/media/podcast-episodes', () => HttpResponse.json(podcastEpisodesFixture)),
];
