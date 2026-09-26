import { test } from '@playwright/test';

test.use({ channel: 'chrome' });

const STATUS_PLAYING = {
  status: 'SUCCESS',
  detail: {
    active: {
      entity_id: 'media_player.office',
      name: 'Office Speaker',
      state: 'playing',
      media_title: 'Test Song',
      media_artist: 'Test Artist',
      media_album_name: 'Test Album',
      volume_level: 0.42,
      is_volume_muted: false,
      position: 65,
      duration: 214,
      media_content_id: 'ma:track:abc123',
      media_type: 'music',
      device_name: 'Office Speaker',
    },
    available: [
      { entity_id: 'media_player.office', name: 'Office Speaker', state: 'playing' },
      { entity_id: 'media_player.living', name: 'Living Room TV', state: 'idle' },
    ],
    all_players: [
      { entity_id: 'media_player.office', name: 'Office Speaker', state: 'playing' },
      { entity_id: 'media_player.living', name: 'Living Room TV', state: 'idle' },
    ],
  },
};

async function boot(page: import('@playwright/test').Page, tag: string) {
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push('CONSOLE: ' + m.text());
  });
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));

  await page.addInitScript(() => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify({ username: 'jeremiah', is_admin: true }));
  });

  const json = (route: import('@playwright/test').Route, body: unknown) => route.fulfill({ json: body });
  await page.route('**/api/users/me/theme', (r) => json(r, { status: 'SUCCESS', theme_id: 'aurora', packs: [] }));
  await page.route('**/api/users/me', (r) =>
    json(r, { id: 1, username: 'jeremiah', display_name: 'Jeremiah', is_admin: true, role: 'admin' })
  );
  await page.route('**/execute/media/status', (r) => json(r, STATUS_PLAYING));
  await page.route('**/execute/media/transport', (r) => json(r, { status: 'SUCCESS', message: 'ok' }));
  await page.route('**/execute/media/play', (r) => json(r, { status: 'SUCCESS', message: 'ok' }));
  await page.route('**/execute/media/state/sync', (r) => json(r, { status: 'SUCCESS', message: 'ok' }));
  await page.route('**/execute/ha_service**', (r) => json(r, { status: 'SUCCESS', message: 'ok' }));
  await page.route('**/api/media/music-assistant/playlists', (r) =>
    json(r, { status: 'SUCCESS', playlists: [{ name: 'Rock Classics', items: 25, uri: 'ma://playlist/rock' }] })
  );
  await page.route('**/api/media/music-assistant/recent', (r) =>
    json(r, { status: 'SUCCESS', recent: [{ name: 'Recent Rock Song', artist: 'Recent Rock Artist', uri: 'ma://track/recent1' }] })
  );
  await page.route('**/api/media/audiobookshelf/**', (r) => json(r, { status: 'SUCCESS', libraries: [], books: [] }));
  await page.route('**/api/entities**', (r) =>
    json(r, [
      { entity_id: 'media_player.office', friendly_name: 'Office Speaker', state: 'playing', domain: 'media_player' },
      { entity_id: 'media_player.living', friendly_name: 'Living Room TV', state: 'idle', domain: 'media_player' },
    ])
  );
  await page.route('**/api/media/detail**', (r) => json(r, { status: 'SUCCESS' }));
  await page.route('**/api/media/favorite', (r) => json(r, { status: 'SUCCESS', favorite: true }));

  await page.goto('http://localhost:5173/media', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  console.log(`ERRORS[${tag}]:`, JSON.stringify(errors, null, 2));
  await page.screenshot({
    path: `/Users/jeremiahsummers/Code/SharedLLM/.tmp/media-${tag}.png`,
    fullPage: true,
  });
}

test('media desktop', async ({ page }) => {
  await boot(page, 'desktop');
});

test('media mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await boot(page, 'mobile');
});
