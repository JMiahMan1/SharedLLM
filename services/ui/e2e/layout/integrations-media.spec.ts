import { test, expect as baseExpect, type Page, type TestInfo } from '@playwright/test';
import { setupPlayerMocks } from '../fixtures/mediaPlayback';
import { INTEGRATIONS_STATUS_PATH, integrationsStatus } from '../fixtures/serverCalls';
import { expectClearOfBottomBar, expectNoHorizontalOverflow } from '../fixtures/layoutChecks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';

/**
 * The Media page and the home integrations, on every form factor. Music
 * Assistant, Audiobookshelf and this device's own player work without a home
 * hub, so the page stays (in the nav and at its address); only the hub's
 * speakers and TVs, and the calls that read them, leave with the hub. With a
 * hub connected they are back. Hermetic: the media mocks answer every call;
 * this spec changes the integrations status and counts the hub calls.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** A hub speaker that is playing, so "Playing in your home" has something to show. */
const HUB_STATUS = {
  status: 'SUCCESS',
  message: 'ok',
  service: 'media',
  detail: {
    active: null,
    available: [],
    all_players: [
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'playing',
        media_title: 'Morning Jazz', media_artist: 'Trio', volume_level: 0.3 },
    ],
  },
};

/** A speaker only Music Assistant knows: it needs no hub. */
const MA_ONLY = [{ player_id: 'den', name: 'Den Speaker', state: 'idle', available: true, volume_level: 20 }];

async function open(page: Page, hub: boolean, setup?: (page: Page) => Promise<void>) {
  await quietHotReload(page);
  const harness = await setupPlayerMocks(page, { mediaStatus: HUB_STATUS, remotePlayers: MA_ONLY });
  const hubCalls: string[] = [];
  // Registered after the shared mocks, so these answer first.
  await page.route((url) => url.pathname === INTEGRATIONS_STATUS_PATH,
    (route) => route.fulfill({ json: integrationsStatus(hub, false) }));
  await page.route('**/execute/media/status', (route) => {
    hubCalls.push('/execute/media/status');
    return route.fulfill({ json: HUB_STATUS });
  });
  await page.route('**/api/entities', (route) => {
    hubCalls.push('/api/entities');
    return route.fulfill({ json: { entities: [
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'playing', domain: 'media_player' },
      { entity_id: 'media_player.living_room', friendly_name: 'Living Room TV', state: 'idle', domain: 'media_player' },
    ] } });
  });
  if (setup) await setup(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/media');
  await expect(page.getByRole('heading', { name: 'Listen Now', level: 1 })).toBeVisible();
  return { ...harness, hubCalls, errors };
}

async function audit(page: Page, info: TestInfo, name: string) {
  await settle(page);
  const found = await screenFindings(page, page.locator('main'), name);
  await expectNoHorizontalOverflow(page);
  await expectClearOfBottomBar(page);
  await shot(page, info, name);
  return found;
}

/** Open "Playing on ..." and return its list of outputs. */
async function openOutputs(page: Page) {
  await page.getByRole('button', { name: /Choose where to play$/ }).click();
  const list = page.getByRole('list', { name: 'Outputs' });
  await expect(list).toBeVisible();
  return list;
}

test.describe('Media and the home integrations', () => {
  test.describe.configure({ timeout: 120_000 });

  test('no hub: music, books and Music Assistant speakers work; the hub players are gone', async ({ page }, info) => {
    const run = await open(page, false);
    // The page is offered, and its shelves (Music Assistant, Audiobookshelf) are all there.
    await expect(page.locator('a[href="/media"]').first()).toBeAttached();
    for (const shelf of ['Continue listening', 'Recently played', 'Your playlists', 'Favorites', 'Radio']) {
      await expect(page.getByRole('heading', { name: shelf, level: 2 })).toBeVisible();
    }
    // No hub speaker is playing anywhere, so there is no "Playing in your home" row (and no gap for one).
    await expect(page.getByRole('heading', { name: 'Playing in your home' })).toHaveCount(0);
    await expect(page.getByRole('alert')).toHaveCount(0);
    const found = await audit(page, info, 'integrations-off-media');

    const outputs = await openOutputs(page);
    await expect(outputs.getByRole('button', { name: /This device/ })).toBeVisible();
    await expect(outputs.getByRole('button', { name: /Den Speaker/ })).toBeVisible();
    await expect(outputs.getByText('Kitchen Speaker')).toHaveCount(0);
    await expect(outputs.getByText('Living Room TV')).toHaveCount(0);
    await settle(page);
    await shot(page, info, 'integrations-off-media-outputs');
    await page.keyboard.press('Escape');

    // The hub's players were never asked for.
    expect(run.hubCalls).toEqual([]);
    expect(found).toEqual([]);
    expect(run.errors).toEqual([]);
    run.api.assertNoUnmocked();
  });

  test('no hub: a hub speaker remembered from before falls back to this device', async ({ page }) => {
    const run = await open(page, false, async (p) => {
      await p.addInitScript(() => {
        localStorage.setItem('jarvis_media', JSON.stringify({
          state: { activeOutputId: 'media_player.kitchen', userPickedOutput: true, recentSearches: [] }, version: 1,
        }));
      });
    });
    await expect(page.getByRole('button', { name: 'Playing on This device. Choose where to play' })).toBeVisible();
    expect(run.hubCalls).toEqual([]);
    expect(run.errors).toEqual([]);
  });

  test('hub on: the hub speakers and what they play come back', async ({ page }, info) => {
    const run = await open(page, true);
    const home = page.getByRole('region', { name: 'Playing in your home' });
    await expect(home.getByText('Morning Jazz')).toBeVisible();
    const found = await audit(page, info, 'integrations-on-media');

    const outputs = await openOutputs(page);
    await expect(outputs.getByRole('button', { name: /Kitchen Speaker/ })).toBeVisible();
    await expect(outputs.getByRole('button', { name: /Living Room TV/ })).toBeVisible();
    await expect(outputs.getByRole('button', { name: /Den Speaker/ })).toBeVisible();
    await settle(page);
    await shot(page, info, 'integrations-on-media-outputs');

    expect(run.hubCalls).toContain('/execute/media/status');
    expect(run.hubCalls).toContain('/api/entities');
    expect(found).toEqual([]);
    expect(run.errors).toEqual([]);
  });
});
