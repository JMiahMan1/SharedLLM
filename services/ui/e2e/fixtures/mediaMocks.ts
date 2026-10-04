import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import type { Page, Route } from '@playwright/test';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export type MediaScenario =
  | 'happy'
  | 'abs-down'
  | 'ma-down'
  | 'empty-library'
  | 'slow'
  | 'offline';

const FIXTURES_DIR = path.join(__dirname, 'media');

const ADMIN_PROFILE = {
  id: 1,
  username: 'admin',
  display_name: 'Test Admin',
  full_name: 'Test Admin',
  role: 'admin',
  is_admin: true,
  is_system_default: true,
};

function fixture(name: string): unknown {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES_DIR, `${name}.json`), 'utf-8'));
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isEmptyLibrary(scenario: MediaScenario): boolean {
  return scenario === 'empty-library';
}

function isUpstreamDown(scenario: MediaScenario, upstream: 'ma' | 'abs'): boolean {
  if (scenario === 'offline') return true;
  if (scenario === 'ma-down' && upstream === 'ma') return true;
  if (scenario === 'abs-down' && upstream === 'abs') return true;
  return false;
}

function emptyVariant(name: string): unknown {
  switch (name) {
    case 'ma-recent': return { status: 'ok', recent: [] };
    case 'ma-playlists': return { status: 'ok', playlists: [] };
    case 'ma-search': return { status: 'ok', tracks: [] };
    case 'abs-libraries': return { status: 'ok', libraries: [] };
    case 'abs-library': return { status: 'ok', books: [] };
    case 'abs-search': return { status: 'ok', books: [] };
    case 'abs-last-played': return { status: 'ok', books: [] };
    case 'media-home': return { recent: [], continue: [], playlists: [], favorites: [], radio: [], errors: {} };
    case 'media-search': return { top: null, tracks: [], artists: [], albums: [], playlists: [], audiobooks: [], podcasts: [], authors: [] };
    case 'media-library': return { items: [], total: 0 };
    case 'media-favorites': return { favorites: [] };
    case 'entities': return { entities: [] };
    case 'media-status': return { status: 'SUCCESS', message: 'ok', service: 'media', detail: { active: null, available: [], all_players: [] } };
    default: return fixture(name);
  }
}

async function respond(
  route: Route,
  fixtureName: string,
  scenario: MediaScenario,
  upstream?: 'ma' | 'abs',
): Promise<void> {
  if (scenario === 'slow') await delay(2000);
  if (scenario === 'offline') {
    await route.abort();
    return;
  }
  if (upstream && isUpstreamDown(scenario, upstream)) {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'upstream_unavailable' }),
    });
    return;
  }
  const body = isEmptyLibrary(scenario) ? emptyVariant(fixtureName) : fixture(fixtureName);
  await route.fulfill({ json: body });
}

function silentWav(frames = 4000): Buffer {
  const dataSize = frames;
  const buffer = Buffer.alloc(44 + dataSize);
  buffer.write('RIFF', 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write('WAVE', 8);
  buffer.write('fmt ', 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(8000, 24);
  buffer.writeUInt32LE(8000, 28);
  buffer.writeUInt16LE(1, 32);
  buffer.writeUInt16LE(8, 34);
  buffer.write('data', 36);
  buffer.writeUInt32LE(dataSize, 40);
  buffer.fill(0x80, 44);
  return buffer;
}

const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);

export interface MediaMocks {
  unmockedCalls: string[];
  assertNoUnmocked: () => void;
}

export async function seedAuth(page: Page): Promise<void> {
  await page.addInitScript((profile) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(profile));
  }, ADMIN_PROFILE);
  await page.route('**/api/users/me', (route) =>
    route.fulfill({ json: fixture('users-me') }),
  );
}

export async function mockMediaApi(page: Page, scenario: MediaScenario = 'happy'): Promise<MediaMocks> {
  const unmockedCalls: string[] = [];

  const catchAll = (route: Route): Promise<void> => {
    const url = route.request().url();
    if (url.includes('/api/users/me')) return route.fallback();
    unmockedCalls.push(`${route.request().method()} ${url}`);
    return route.abort();
  };
  await page.route('**/api/**', catchAll);
  await page.route('**/execute/**', catchAll);

  const json = (fixtureName: string, upstream?: 'ma' | 'abs') =>
    async (route: Route): Promise<void> => respond(route, fixtureName, scenario, upstream);

  await page.route('**/execute/media/status', json('media-status'));
  await page.route('**/execute/media/play', json('play'));
  await page.route('**/execute/media/transport', json('transport'));
  await page.route('**/execute/media/state/sync', json('state-sync'));
  await page.route('**/execute/audiobookshelf', json('abs-execute', 'abs'));

  await page.route('**/api/media/music-assistant/recent', json('ma-recent', 'ma'));
  await page.route('**/api/media/music-assistant/playlists', json('ma-playlists', 'ma'));
  await page.route('**/api/media/music-assistant/search**', json('ma-search', 'ma'));
  await page.route('**/api/media/audiobookshelf/libraries', json('abs-libraries', 'abs'));
  await page.route('**/api/media/audiobookshelf/library/**', json('abs-library', 'abs'));
  await page.route('**/api/media/audiobookshelf/search**', json('abs-search', 'abs'));
  await page.route('**/api/media/audiobookshelf/last-played', json('abs-last-played', 'abs'));
  await page.route('**/api/media/detail**', json('media-detail'));
  await page.route('**/api/media/favorite', json('favorite'));
  await page.route('**/api/groups/media', json('groups-media'));
  await page.route('**/api/groups/media/**', json('groups-media'));
  await page.route('**/api/entities', json('entities'));
  // The shell header polls this for its notification bell on every page; without
  // a mock the catch-all aborts it and the spec fails on an unmocked call.
  await page.route('**/api/logs**', (route) => route.fulfill({ json: [] }));

  await page.route('**/api/raven/missions', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/admin/services/updates', (route) =>
    route.fulfill({ json: { checked: 0, updates_available: 0, services: [] } }),
  );

  await page.route('**/api/media/home', json('media-home'));
  await page.route('**/api/media/search**', json('media-search'));
  await page.route('**/api/media/item**', json('media-item'));
  await page.route('**/api/media/library/**', json('media-library'));
  await page.route('**/api/media/favorites', json('media-favorites'));
  await page.route('**/api/media/abs/progress', json('abs-progress'));
  await page.route('**/api/media/token', json('media-token'));

  await page.route('**/api/media/stream/music-assistant**', async (route) => {
    if (scenario === 'slow') await delay(2000);
    if (scenario === 'offline') {
      await route.abort();
      return;
    }
    if (isUpstreamDown(scenario, 'ma')) {
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'upstream_unavailable' }) });
      return;
    }
    await route.fulfill({ contentType: 'audio/wav', body: silentWav() });
  });
  // ABS audio is served from the session HLS route (BUG-33); this mock only
  // shapes the network (latency/offline/upstream-down), not the HLS bytes.
  await page.route('**/api/media/stream/abs-session/**', async (route) => {
    if (scenario === 'slow') await delay(2000);
    if (scenario === 'offline') {
      await route.abort();
      return;
    }
    if (isUpstreamDown(scenario, 'abs')) {
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'upstream_unavailable' }) });
      return;
    }
    await route.fulfill({ contentType: 'audio/wav', body: silentWav() });
  });
  await page.route('**/api/media/imageproxy**', async (route) => {
    if (scenario === 'slow') await delay(2000);
    if (scenario === 'offline') {
      await route.abort();
      return;
    }
    await route.fulfill({ contentType: 'image/png', body: PNG_1X1 });
  });

  return {
    unmockedCalls,
    assertNoUnmocked: () => {
      if (unmockedCalls.length > 0) {
        throw new Error(
          `Unmocked API calls hit the catch-all (nothing may reach a real server):\n  ${unmockedCalls.join('\n  ')}`,
        );
      }
    },
  };
}

const MA_ALLOWLIST = new Set([
  'players/all', 'players/get', 'players/cmd/play', 'players/cmd/pause',
  'players/cmd/play_pause', 'players/cmd/stop', 'players/cmd/seek',
  'players/cmd/volume_set', 'players/cmd/volume_mute', 'players/cmd/group',
  'players/cmd/group_many', 'players/cmd/ungroup', 'player_queues/all',
  'player_queues/get', 'player_queues/items', 'player_queues/play_media',
  'player_queues/next', 'player_queues/previous', 'player_queues/play_index',
  'player_queues/move_item', 'player_queues/delete_item', 'player_queues/clear',
  'player_queues/shuffle', 'player_queues/repeat', 'player_queues/transfer',
  'player_queues/seek', 'player_queues/skip', 'music/search', 'music/item_by_uri',
  'music/recently_played_items', 'music/in_progress_items', 'music/recommendations',
  'music/favorites/add_item', 'music/favorites/remove_item',
  'music/albums/library_items', 'music/albums/album_tracks',
  'music/artists/library_items', 'music/artists/artist_albums',
  'music/artists/artist_tracks', 'music/playlists/library_items',
  'music/playlists/playlist_tracks', 'music/playlists/add_playlist_tracks',
  'music/playlists/remove_playlist_tracks', 'music/tracks/library_items',
  'music/radios/library_items', 'music/podcasts/library_items',
  'music/podcasts/podcast_episodes',
]);

export interface MaJsonRpcMock {
  sentCommands: Array<{ command: string; args?: Record<string, unknown> }>;
  pushEvent: (event: string, data: Record<string, unknown>) => void;
}

export async function mockMaJsonRpc(page: Page): Promise<MaJsonRpcMock> {
  const sentCommands: Array<{ command: string; args?: Record<string, unknown> }> = [];
  let pushEvent: (event: string, data: Record<string, unknown>) => void = () => {};

  await page.routeWebSocket('**/api/ma-jsonrpc**', (ws) => {
    pushEvent = (event, data) => ws.send(JSON.stringify({ event, ...data }));
    ws.onMessage((message) => {
      let req: { message_id?: string | number; command?: string; args?: Record<string, unknown> };
      try {
        req = JSON.parse(String(message));
      } catch {
        return;
      }
      if (req.command) sentCommands.push({ command: req.command, args: req.args });
      if (req.message_id === undefined) return;
      if (req.command && !MA_ALLOWLIST.has(req.command)) {
        ws.send(JSON.stringify({
          message_id: req.message_id,
          error_code: 'forbidden',
          details: `Command not allowlisted: ${req.command}`,
        }));
        return;
      }
      ws.send(JSON.stringify({ message_id: req.message_id, result: {} }));
    });
  });

  return { sentCommands, pushEvent };
}

export async function mockSendspin(page: Page): Promise<void> {
  await page.routeWebSocket('**/api/sendspin**', (ws) => {
    ws.onMessage(() => {});
  });
}

export async function mockEvents(page: Page): Promise<void> {
  await page.route('**/api/media/events**', async (route) => {
    const body = [
      'event: snapshot',
      'data: {"players":[],"active":null}',
      '',
      'event: heartbeat',
      'data: {}',
      '',
    ].join('\n');
    await route.fulfill({ contentType: 'text/event-stream', body });
  });
}
