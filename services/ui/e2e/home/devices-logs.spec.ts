import { test, expect as baseExpect, type Locator, type Page, type Route } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockDeviceStates } from '../fixtures/hardwareMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > an ESPHome device > Logs (services/homehub/esphome_logs.py): the device's live log with level filter,
 * search, pause, a config dump, a download, and the restart monitor's offline/reconnected/restarted markers in
 * line. Every project (desktop, phones, tablets). Hermetic: the device is fixtures/hardwareMocks.ts's made-up one,
 * and every log line here is invented.
 */

const at = (s: number) => new Date(Date.now() - (60 - s) * 1000).toISOString();
const RANK: Record<string, number> = { error: 1, warning: 2, info: 3, config: 4, debug: 5, verbose: 6 };
const VIEW: Record<string, number> = { error: 1, warning: 2, info: 4, debug: 5, verbose: 6 };

const LINES = [
  { type: 'line', seq: 1, at: at(1), level: 'info', tag: 'app', message: 'Running through setup()' },
  { type: 'line', seq: 2, at: at(2), level: 'debug', tag: 'light', message: "'Desk bulb' Setting state ON" },
  { type: 'line', seq: 3, at: at(3), level: 'warning', tag: 'wifi', message: 'Signal is weak' },
  { type: 'marker', seq: 4, at: at(4), event: 'offline', message: 'Device offline (SocketClosedAPIError: Connection lost)' },
  { type: 'marker', seq: 5, at: at(10), event: 'online', message: 'Reconnected' },
  { type: 'marker', seq: 6, at: at(12), event: 'restart', message: 'Restarted (Power on), 2 s offline' },
  { type: 'line', seq: 7, at: at(13), level: 'error', tag: 'i2c', message: 'Bus fault on a rather long line that keeps going: '
    + 'abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz' },
  { type: 'line', seq: 8, at: at(14), level: 'verbose', tag: 'sensor', message: 'raw reading 1234' },
];
const DUMP = [
  { type: 'marker', seq: 9, at: at(20), event: 'dump_config', message: 'Config dump requested' },
  { type: 'line', seq: 10, at: at(21), level: 'config', tag: 'wifi', message: 'WiFi:\n  Local MAC: 02:00:00:00:00:41' },
];

type Run = { mocks: HomeMocks; errors: string[]; streams: URL[]; dumps: number };

async function open(page: Page): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  await mockDeviceStates(page);
  const run: Run = { mocks, errors: [], streams: [], dumps: 0 };
  page.on('pageerror', (e) => run.errors.push(e.message));
  await page.route(/\/api\/homehub\/devices\/[^/]+\/restarts$/, (route: Route) =>
    route.fulfill({ json: { supported: false, device_id: 'x' } }));
  await page.route(/\/api\/homehub\/devices\/hw-bulb\/logs\/dump_config$/, (route: Route) => {
    run.dumps += 1;
    return route.fulfill({ json: { ok: true, device_id: 'hw-bulb' } });
  });
  await page.route(/\/api\/homehub\/devices\/hw-bulb\/logs\/stream\?/, async (route: Route) => {
    const url = new URL(route.request().url());
    run.streams.push(url);
    const level = url.searchParams.get('level') ?? 'debug';
    const since = Number(url.searchParams.get('since') ?? 0);
    if (since) {
      // A reopened stream: nothing new until the view closes it.
      await new Promise((r) => setTimeout(r, 20_000));
      return route.fulfill({ body: '', contentType: 'application/x-ndjson' }).catch(() => undefined);
    }
    const items = [...LINES, ...(run.dumps ? DUMP : [])]
      .filter((i) => i.type === 'marker' || RANK[i.level as string] <= VIEW[level]);
    const body = [{ type: 'hello', seq: 10, level, device_id: 'hw-bulb', connected: true }, ...items]
      .map((i) => JSON.stringify(i)).join('\n') + '\n';
    return route.fulfill({ body, contentType: 'application/x-ndjson' }).catch(() => undefined);
  });
  await page.goto('/devices');
  await expect(page.getByTestId('device-tile').first()).toBeVisible();
  return run;
}

async function settled(page: Page, dialog: Locator) {
  const h = page.viewportSize()?.height ?? 0;
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.y + b.height : h + 99; })
    .toBeLessThanOrEqual(h + 1);
}

async function openLogs(page: Page): Promise<{ dialog: Locator; section: Locator; log: Locator }> {
  await page.getByTestId('device-tile').filter({ hasText: 'Desk bulb' }).getByTestId('hardware-device').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByTestId('hardware-detail')).toBeVisible();
  await settled(page, dialog);
  const section = dialog.getByTestId('section-logs');
  await section.scrollIntoViewIfNeeded();
  await section.getByRole('button', { name: 'Show live logs' }).click();
  const log = section.getByTestId('device-log');
  await expect(log.getByTestId('log-line').first()).toBeVisible();
  return { dialog, section, log };
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('Devices: ESPHome logs', () => {
  test.describe.configure({ timeout: 90_000 });

  test('the live log shows lines by level with the restart markers in line', async ({ page }, info) => {
    const run = await open(page);
    const { dialog, section, log } = await openLogs(page);
    expect(run.streams[0].searchParams.get('level')).toBe('debug');
    await expect(section.getByTestId('log-status')).toHaveText('Live');
    await expect(log.getByTestId('log-line')).toHaveCount(4); // the verbose line is not in a debug view
    await expect(log.getByTestId('log-marker')).toHaveCount(3);
    await expect(log.getByTestId('log-marker').nth(0)).toContainText('Device offline');
    await expect(log.getByTestId('log-marker').nth(1)).toContainText('Reconnected');
    await expect(log.getByTestId('log-marker').nth(2)).toContainText('Restarted (Power on), 2 s offline');
    await expect(log.locator('[data-testid="log-line"][data-level="error"]')).toContainText('[E][i2c] Bus fault');
    await section.scrollIntoViewIfNeeded();
    await expectTapTargets(section);
    await expectNoHorizontalOverflow(page);
    await expectFits(page, dialog);
    await snap(page, info, 'devices-logs-live');

    await section.getByLabel('Search the log').fill('wifi');
    await expect(log.getByTestId('log-line')).toHaveCount(1);
    await expect(log.getByTestId('log-line')).toContainText('Signal is weak');
    await section.getByLabel('Search the log').fill('');

    await section.getByLabel('Log level').selectOption('warning');
    await expect.poll(() => run.streams.at(-1)?.searchParams.get('level')).toBe('warning');
    await expect(log.getByTestId('log-line')).toHaveCount(2);
    await expect(log.getByTestId('log-marker')).toHaveCount(3);
    clean(run);
  });

  test('pause holds the view; the config dump and the download work', async ({ page }, info) => {
    const run = await open(page);
    const { section, log } = await openLogs(page);
    await section.getByRole('button', { name: 'Pause' }).click();
    await expect(section.getByTestId('log-status')).toHaveText('Paused');
    await section.getByRole('button', { name: 'Resume' }).click();
    await expect(section.getByTestId('log-status')).toHaveText('Live');

    await section.getByLabel('Log level').selectOption('warning');
    await expect.poll(() => run.streams.at(-1)?.searchParams.get('level')).toBe('warning');
    await section.getByRole('button', { name: 'Show config dump' }).click();
    await expect.poll(() => run.dumps).toBe(1);
    await expect(section.getByLabel('Log level')).toHaveValue('info');
    await expect(log.locator('[data-testid="log-line"][data-level="config"]')).toContainText('Local MAC');
    await expect(log.locator('[data-testid="log-marker"][data-event="dump_config"]')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-logs-dump');

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      section.getByRole('button', { name: 'Download' }).click(),
    ]);
    expect(download.suggestedFilename()).toBe('Desk-bulb-log.txt');
    const text = await readFile(await download.path(), 'utf8');
    expect(text).toContain('[C][wifi] WiFi:');
    expect(text).toContain('---- Restarted (Power on), 2 s offline ----');

    await section.getByRole('button', { name: 'Close' }).click();
    await expect(section.getByRole('button', { name: 'Show live logs' })).toBeVisible();
    clean(run);
  });

  test('a device that is not an ESPHome node has no Logs section', async ({ page }) => {
    const run = await open(page);
    await page.getByTestId('device-tile').filter({ hasText: 'Hall climate sensor' }).getByTestId('hardware-device').click();
    await expect(page.getByRole('dialog').getByTestId('hardware-detail')).toBeVisible();
    await expect(page.getByTestId('section-logs')).toHaveCount(0);
    clean(run);
  });
});
