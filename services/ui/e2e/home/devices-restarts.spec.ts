import { test, expect as baseExpect, type Locator, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockDeviceStates } from '../fixtures/hardwareMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > an ESPHome device > Restarts (services/homehub/esphome_restarts.py): its restarts and dropped
 * connections with the likely cause, the log lines around each, the log level the hub keeps, and the YAML to add
 * when the device cannot report why it restarted. Every project (desktop, phones, tablets). Hermetic: the devices
 * are fixtures/hardwareMocks.ts's made-up ones, and every log line here is invented.
 */

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const LEVELS = ['off', 'error', 'warning', 'info', 'debug'];

const RESTART = {
  id: 'r1', at: ago(12), reconnected_at: ago(12), kind: 'restart', summary: 'Restarted (Exception panic), 5 s offline',
  evidence: 'its uptime went back from 3.2 h to 4 s', cause: 'Reset reason: Exception panic', hint: null,
  downtime_s: 5, expected: false, error: 'SocketClosedAPIError: Connection lost', silent_s: 0.3,
  reset_reason: 'Exception panic', last_reset_reason: null, last_crash: null,
  memory: { heap_free: { before: 14_336, lowest: 9_216, after: 201_728 }, psram_free: { before: 310_000, lowest: 300_000, after: 1_900_000 } },
  context_text: ['A voice run ended 26 s before (ok)', 'media_player.desk_bulb_speaker was playing for 20 s'],
  log_level: 'warning', dropped_lines: 0,
  lines_before: [
    [ago(13), 'W', '[W][component:214]: Component speaker took a long time for an operation (152 ms).'],
    [ago(12), 'E', '[E][ring_buffer:62]: Failed to allocate 32768 bytes for a ring buffer with a rather long description'],
  ],
  lines_after: [[ago(12), 'W', '[W][wifi:1]: Signal is weak']],
};
const DROP = {
  ...RESTART, id: 'r2', at: ago(200), kind: 'drop', summary: 'Connection dropped, the device kept running, 2 s offline',
  evidence: 'its uptime kept counting (2.9 h, then 2.9 h)', cause: null, reset_reason: null, last_reset_reason: 'Power on',
  memory: {}, context_text: [], lines_before: [], lines_after: [],
};

const WITH_DEBUG = {
  supported: true, device_id: 'hw-bulb', name: 'desk-bulb', title: 'desk-bulb', connected: true, log_level: 'warning',
  log_levels: LEVELS, subscribed: true, subscribe_error: null, sensors: ['heap_free', 'psram_free', 'reset_reason', 'uptime'],
  has_debug: true, guidance: null, current: { uptime: 720 }, buffer: { lines: 3, max: 200, dropped: 0 }, offline: null,
  counts_1h: { restart: 1, drop: 0, unknown: 0 }, counts_24h: { restart: 1, drop: 1, unknown: 0 },
  events: [RESTART, DROP], recent_lines: [[ago(1), 'W', '[W][wifi:1]: Signal is weak']],
};

const YAML = 'debug:\n  update_interval: 30s\n\ntext_sensor:\n  - platform: debug\n    reset_reason:\n      name: "Reset reason"\n\n'
  + 'sensor:\n  - platform: debug\n    free:\n      name: "Heap free"\n    psram:            # only on an ESP32 with PSRAM\n'
  + '      name: "PSRAM free"\n  - platform: uptime\n    name: "Uptime"\n';

const WITHOUT_DEBUG = {
  ...WITH_DEBUG, device_id: 'hw-fan', name: 'study-fan', title: 'study-fan', sensors: [], has_debug: false,
  guidance: { missing: ['reset reason (debug component)', 'free heap (debug component)', 'uptime sensor'], yaml: YAML,
    text: 'Without a reset reason the hub cannot say why the device restarted, and without an uptime sensor it cannot '
      + "tell a restart from a dropped connection. Add this to the device's ESPHome configuration and install it." },
  counts_24h: { restart: 0, drop: 0, unknown: 2 },
  events: [{ ...RESTART, id: 'u1', kind: 'unknown', summary: 'Disconnected; restart or dropped connection not known, 2 s offline',
    evidence: 'it has no uptime sensor, so a restart cannot be told from a dropped connection', cause: null, reset_reason: null,
    memory: {}, hint: 'The node closed the connection or no longer knew it, which happens when it restarts (and also when its Wi-Fi reconnects).' }],
};

type Run = { mocks: HomeMocks; errors: string[]; patches: unknown[] };

async function open(page: Page, answers: Record<string, Record<string, unknown>>): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  await mockDeviceStates(page);
  const errors: string[] = [];
  const patches: unknown[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route(/\/api\/homehub\/devices\/[^/]+\/restarts$/, async (route: Route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[4]);
    const current = answers[id] ?? { supported: false, device_id: id };
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON() as { log_level: string };
      patches.push(body);
      answers[id] = { ...current, log_level: body.log_level };
      return route.fulfill({ json: answers[id] });
    }
    return route.fulfill({ json: current });
  });
  await page.goto('/devices');
  await expect(page.getByTestId('device-tile').first()).toBeVisible();
  return { mocks, errors, patches };
}

async function settled(page: Page, dialog: Locator) {
  const h = page.viewportSize()?.height ?? 0;
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.y + b.height : h + 99; })
    .toBeLessThanOrEqual(h + 1);
}

async function openDevice(page: Page, name: string): Promise<{ dialog: Locator; section: Locator }> {
  await page.getByTestId('device-tile').filter({ hasText: name }).getByTestId('hardware-device').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByTestId('hardware-detail')).toBeVisible();
  await settled(page, dialog);
  const section = dialog.getByTestId('section-restarts');
  await section.scrollIntoViewIfNeeded();
  await expect(section).toBeVisible();
  return { dialog, section };
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('Devices: ESPHome restarts', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a restart shows its cause, what was happening and the lines before; the log level changes', async ({ page }, info) => {
    const run = await open(page, { 'hw-bulb': structuredClone(WITH_DEBUG) });
    const { dialog, section } = await openDevice(page, 'Desk bulb');
    await expect(section.getByTestId('restart-summary')).toHaveText('Last 24 hours: 1 restart, 1 dropped connection.');
    await expect(section.getByTestId('restart-guidance')).toHaveCount(0);
    const rows = section.getByTestId('restart-event');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText('Restarted (Exception panic), 5 s offline');
    await expect(rows.nth(0)).toContainText('Reset reason: Exception panic');
    await expect(rows.nth(1)).toContainText('Connection dropped');
    await expectTapTargets(section);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-restarts-list');

    await rows.nth(0).getByText('Restarted (Exception panic), 5 s offline').click();
    const detail = rows.nth(0).getByTestId('restart-event-detail');
    await expect(detail).toContainText('its uptime went back from 3.2 h to 4 s');
    await expect(detail).toContainText('A voice run ended 26 s before (ok)');
    await expect(detail).toContainText('Free heap: 14 kB before, lowest 9 kB, 197 kB after');
    await expect(detail.getByTestId('restart-lines').first()).toContainText('Failed to allocate 32768 bytes');
    await detail.scrollIntoViewIfNeeded();
    await expectFits(page, dialog);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-restarts-event');

    const select = section.getByLabel('Log lines kept from the device');
    await expect(select).toHaveValue('warning');
    await select.selectOption('debug');
    await expect(select).toHaveValue('debug');
    expect(run.patches).toEqual([{ log_level: 'debug' }]);
    clean(run);
  });

  test('a device without the debug component says so and shows the YAML to add', async ({ page }, info) => {
    const run = await open(page, { 'hw-fan': structuredClone(WITHOUT_DEBUG) });
    const { section } = await openDevice(page, 'Study fan');
    await expect(section.getByTestId('restart-summary')).toHaveText('Last 24 hours: 2 disconnects of unknown cause.');
    const guide = section.getByTestId('restart-guidance');
    await expect(guide).toContainText('The cause of a restart cannot be read from this device');
    await expect(guide).toContainText('Missing: reset reason (debug component), free heap (debug component), uptime sensor.');
    const yaml = guide.getByTestId('restart-yaml');
    await expect(yaml).toContainText('- platform: debug');
    await expect(yaml).toContainText('reset_reason:');
    await expect(section.getByTestId('restart-event')).toContainText('Restart or drop not known');
    await yaml.scrollIntoViewIfNeeded();
    await expectTapTargets(section);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-restarts-guidance');
    clean(run);
  });

  test('a device that is not an ESPHome node has no Restarts section', async ({ page }) => {
    const run = await open(page, {});
    await page.getByTestId('device-tile').filter({ hasText: 'Hall climate sensor' }).getByTestId('hardware-device').click();
    await expect(page.getByRole('dialog').getByTestId('hardware-detail')).toBeVisible();
    await expect(page.getByTestId('section-restarts')).toHaveCount(0);
    clean(run);
  });
});
