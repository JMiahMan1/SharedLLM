import { http, HttpResponse } from 'msw';
import entitiesFixture from '../../../../e2e/fixtures/media/entities.json';
import groupsMediaFixture from '../../../../e2e/fixtures/media/groups-media.json';
import mediaStatusFixture from '../../../../e2e/fixtures/media/media-status.json';
import transportFixture from '../../../../e2e/fixtures/media/transport.json';
import playFixture from '../../../../e2e/fixtures/media/play.json';
import maPlaylistsFixture from '../../../../e2e/fixtures/media/ma-playlists.json';
import maRecentFixture from '../../../../e2e/fixtures/media/ma-recent.json';
import maSearchFixture from '../../../../e2e/fixtures/media/ma-search.json';
import absLibrariesFixture from '../../../../e2e/fixtures/media/abs-libraries.json';
import absLibraryFixture from '../../../../e2e/fixtures/media/abs-library.json';
import absSearchFixture from '../../../../e2e/fixtures/media/abs-search.json';
import absLastPlayedFixture from '../../../../e2e/fixtures/media/abs-last-played.json';
import absExecuteFixture from '../../../../e2e/fixtures/media/abs-execute.json';

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
  http.get('/api/media/music-assistant/playlists', () =>
    HttpResponse.json(maPlaylistsFixture)
  ),
  http.get('/api/media/music-assistant/recent', () =>
    HttpResponse.json(maRecentFixture)
  ),
  http.get('/api/media/music-assistant/search', () =>
    HttpResponse.json(maSearchFixture)
  ),
  http.get('/api/media/audiobookshelf/libraries', () =>
    HttpResponse.json(absLibrariesFixture)
  ),
  http.get('/api/media/audiobookshelf/library/:libraryId', () =>
    HttpResponse.json(absLibraryFixture)
  ),
  http.get('/api/media/audiobookshelf/search', () =>
    HttpResponse.json(absSearchFixture)
  ),
  http.get('/api/media/audiobookshelf/last-played', () =>
    HttpResponse.json(absLastPlayedFixture)
  ),
  http.post('/execute/audiobookshelf', () => HttpResponse.json(absExecuteFixture)),
];
