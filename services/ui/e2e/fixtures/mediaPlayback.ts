import { expect, type Page } from '@playwright/test';
import {
  mockEvents,
  mockMaCommandApi,
  mockMaJsonRpc,
  mockMediaApi,
  mockSendspin,
  pushNowPlaying,
  seedAuth,
  seedPlayerId,
  WEB_PLAYER_ID,
  type MaCommandMock,
  type MaJsonRpcMock,
  type MaJsonRpcOptions,
  type MediaMocks,
  type SendspinMock,
} from './mediaMocks';

/** The track every player spec starts from. */
export const FIXTURE_TRACK = {
  title: 'Midnight Drive',
  artist: 'The Synthwave Collective',
  uri: 'library://track/1',
  duration: 210,
  position: 12,
  volume: 70,
};

export interface PlayerHarness {
  api: MediaMocks;
  ma: MaJsonRpcMock;
  remote: MaCommandMock;
  sendspin: SendspinMock;
}

export interface PlayerHarnessOptions {
  ma?: MaJsonRpcOptions;
  /** Speaker list the REST `players/all` fallback should return. */
  remotePlayers?: unknown[];
  /** Override `/execute/media/status`, for output-picker dedupe/grouping. */
  mediaStatus?: unknown;
}

/**
 * Install every mock a spec needs to start the browser player and keep it
 * alive: auth, a pinned player id, API fixtures, and both WebSockets.
 */
export async function setupPlayerMocks(
  page: Page,
  options: PlayerHarnessOptions = {},
): Promise<PlayerHarness> {
  await seedAuth(page);
  await seedPlayerId(page);
  const api = await mockMediaApi(page, 'happy');
  if (options.mediaStatus) {
    await page.route('**/execute/media/status', (route) =>
      route.fulfill({ json: options.mediaStatus }),
    );
  }
  const ma = await mockMaJsonRpc(page, options.ma);
  const remote = await mockMaCommandApi(page, { players: options.remotePlayers });
  const sendspin = await mockSendspin(page);
  await mockEvents(page);
  return { api, ma, remote, sendspin };
}

/** Open /media and wait for its shelves. */
export async function openMedia(page: Page): Promise<void> {
  await page.goto('/media');
  await expect(page.getByRole('heading', { name: 'Media', level: 1 })).toBeVisible();
  await expect(page.getByRole('button', { name: /Midnight Drive/ })).toBeVisible();
}

/**
 * Play the fixture track on the browser player and push the MA events a real
 * server would send after accepting `player_queues/play_media`.
 */
export async function startTrack(
  page: Page,
  ma: MaJsonRpcMock,
  overrides: Partial<typeof FIXTURE_TRACK> = {},
): Promise<void> {
  const track = { ...FIXTURE_TRACK, ...overrides };
  await openMedia(page);
  await page.getByRole('button', { name: new RegExp(track.title) }).click();
  await expect.poll(() => ma.sentCommands.some(
    (entry) => entry.command === 'player_queues/play_media'
      && entry.args?.queue_id === WEB_PLAYER_ID
      && entry.args?.media === track.uri,
  )).toBe(true);
  pushNowPlaying(ma, WEB_PLAYER_ID, track);
  await expect(page.getByText('Playing on this device')).toBeVisible();
}

/** Push only the queue state, e.g. after a pause/play command in a spec. */
export function pushPlaybackState(
  ma: MaJsonRpcMock,
  state: 'playing' | 'paused',
  track: typeof FIXTURE_TRACK = FIXTURE_TRACK,
): void {
  ma.pushEvent('queue_updated', {
    queue_id: WEB_PLAYER_ID,
    state,
    current_item: {
      name: track.title,
      artist: track.artist,
      uri: track.uri,
      duration: track.duration,
    },
  });
}

/** Set the paused playback clock, which the app only accepts while paused. */
export function pushPosition(
  ma: MaJsonRpcMock,
  position: number,
  track: typeof FIXTURE_TRACK = FIXTURE_TRACK,
): void {
  ma.pushEvent('player_updated', {
    player_id: WEB_PLAYER_ID,
    volume_level: track.volume / 100,
    is_volume_muted: false,
    current_media: {
      title: track.title,
      artist: track.artist,
      uri: track.uri,
      duration: track.duration,
      elapsed_time: position,
    },
  });
}

/** Move between pages the way a person would, not by reloading the app. */
export async function goToPage(
  page: Page,
  destination: 'home' | 'calendar' | 'media',
): Promise<void> {
  const mobile = (page.viewportSize()?.width ?? 1440) < 768;
  if (destination === 'calendar' && mobile) {
    await page.getByRole('button', { name: 'More pages' }).click();
    await page.getByTestId('more-sheet').getByRole('link', { name: 'Calendar' }).click();
    return;
  }
  const name = destination === 'home'
    ? (mobile ? 'Home' : 'Dashboard')
    : destination === 'calendar'
      ? 'Calendar'
      : 'Media';
  await page.getByRole('link', { name, exact: true }).first().click();
}

/** Remove focus from a control so Space reaches the shortcut handler. */
export async function clearFocus(page: Page): Promise<void> {
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
}

export function findCommand(
  ma: MaJsonRpcMock,
  command: string,
  from = 0,
): { command: string; args?: Record<string, unknown> } | undefined {
  return ma.sentCommands.slice(from).find((entry) => entry.command === command);
}
