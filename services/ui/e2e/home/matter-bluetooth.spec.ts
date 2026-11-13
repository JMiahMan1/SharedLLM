import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > New devices: adding a new Wi-Fi Matter device over Bluetooth, and the household Wi-Fi
 * for new devices, in every project (desktop, Pixel 7, a small phone, a tablet both ways round).
 * Hermetic: fixtures/homeMocks.ts, plus the hub's Matter routes answered here. Made-up network.
 */

interface MatterHub {
  bluetooth: boolean | null;
  wifi: { configured: boolean; ssid: string | null; updated_at: string | null };
  /** What the next commission POSTs answer, in order; the last one repeats. */
  answers: Array<{ status: number; body: Record<string, unknown> }>;
  commissions: Array<Record<string, unknown>>;
  wifiPuts: Array<Record<string, unknown>>;
}

async function open(page: Page, hub: Partial<MatterHub> = {}): Promise<{ mocks: HomeMocks; errors: string[]; hub: MatterHub }> {
  const state: MatterHub = {
    bluetooth: true, wifi: { configured: false, ssid: null, updated_at: null }, answers: [], commissions: [], wifiPuts: [],
    ...hub,
  };
  const mocks = await mockHome(page, 'admin');
  // Registered after mockHome, so these win for the Matter routes.
  await page.route((url) => url.pathname.startsWith('/api/homehub/matter/'), async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === '/api/homehub/matter/settings') {
      return json({ bluetooth: { enabled: state.bluetooth, setting: 'MATTER_BLUETOOTH_ADAPTER' }, wifi: state.wifi });
    }
    if (path === '/api/homehub/matter/wifi' && req.method() === 'PUT') {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.wifiPuts.push(body);
      state.wifi = { configured: true, ssid: String(body.ssid), updated_at: new Date().toISOString() };
      return json(state.wifi);
    }
    if (path === '/api/homehub/matter/commission') {
      state.commissions.push(req.postDataJSON() as Record<string, unknown>);
      const next = state.answers.length > 1 ? state.answers.shift()! : state.answers[0];
      return json(next.body, next.status);
    }
    return json({ detail: 'not mocked' }, 404);
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/devices#devices-new');  // an old link: opens the Add & fix tab
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors, hub: state };
}

/** A sheet slides up as it opens; measure it once it has stopped moving. */
async function settled(page: Page, dialog: ReturnType<Page['getByRole']>) {
  const h = page.viewportSize()?.height ?? 0;
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.y + b.height : h + 99; })
    .toBeLessThanOrEqual(h + 1);
}

function clean({ mocks, errors }: { mocks: HomeMocks; errors: string[] }) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('Devices: Matter over Bluetooth', () => {
  test.describe.configure({ timeout: 90_000 });

  test('an admin sets the household Wi-Fi for new devices; only its name comes back', async ({ page }, info) => {
    const run = await open(page);
    const panel = page.getByTestId('new-devices');
    await panel.scrollIntoViewIfNeeded();
    await panel.getByRole('button', { name: /Wi-Fi for new devices/ }).click();
    const wifi = panel.getByTestId('matter-wifi');
    await expect(wifi.getByTestId('matter-bluetooth')).toContainText('ready');
    await expect(wifi.getByText('No network is set yet.')).toBeVisible();
    await wifi.getByLabel('Network name').fill('ExampleNet');
    await wifi.getByLabel('Password').fill('short');
    await wifi.getByRole('button', { name: /Save Wi-Fi/ }).click();
    await expect(wifi.getByRole('alert')).toContainText('8 to 63');
    expect(run.hub.wifiPuts).toEqual([]);
    await expectTapTargets(wifi);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'matter-wifi-form');

    await wifi.getByLabel('Password').fill('example-passphrase');
    await wifi.getByRole('button', { name: /Save Wi-Fi/ }).click();
    await expect(wifi.getByTestId('matter-wifi-current')).toContainText('ExampleNet');
    await expect(wifi.getByTestId('matter-wifi-current')).toContainText('Password saved');
    expect(run.hub.wifiPuts).toEqual([{ ssid: 'ExampleNet', password: 'example-passphrase' }]);
    await expect(wifi.getByLabel('Password')).toHaveCount(0);
    await expectTapTargets(wifi);
    await expectNoHorizontalOverflow(page);
    clean(run);
  });

  test('a new device is added over Bluetooth with its progress shown', async ({ page }, info) => {
    const run = await open(page, {
      wifi: { configured: true, ssid: 'ExampleNet', updated_at: '2030-01-01T00:00:00+00:00' },
      answers: [
        { status: 200, body: { status: 'pending', session: 'b1', step: 'matter_ble_commission', poll_ms: 1500,
          message: 'Finding the device over Bluetooth, giving it the Wi-Fi details and waiting for it to join. Keep it powered and close to the hub; this can take a few minutes (0:04 so far).' } },
        { status: 200, body: { status: 'pending', session: 'b1', step: 'matter_ble_register', poll_ms: 200,
          message: 'The device joined the network; adding its controls.' } },
        { status: 200, body: { status: 'adopted', entry_id: 'm1', name: 'Smart Bulb', entities: ['light.smart_bulb'] } },
      ],
    });
    await page.getByRole('button', { name: /Add Matter device/ }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('radio', { name: /over Bluetooth/ }).click();
    await expect(dialog.getByTestId('matter-ble-network')).toContainText('ExampleNet');
    await dialog.getByLabel(/Setup code/).fill('3497-011-2332');
    await expectTapTargets(dialog.getByRole('radiogroup'));
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'matter-ble-start');

    await dialog.getByRole('button', { name: 'Add over Bluetooth' }).click();
    const waiting = dialog.getByTestId('adopt-waiting');
    await expect(waiting).toContainText('Adding over Bluetooth');
    await expect(waiting).toContainText('0:04 so far');
    await expectFits(page, dialog);
    await snap(page, info, 'matter-ble-progress');
    await expect(dialog).toHaveCount(0);
    await expect(page.getByText(/Smart Bulb was added/)).toBeVisible();

    expect(run.hub.commissions[0]).toEqual({ code: '3497-011-2332', method: 'ble' });
    for (const poll of run.hub.commissions.slice(1)) {
      expect(poll).toEqual({ code: '3497-011-2332', method: 'ble', session: 'b1' });
    }
    expect(run.hub.commissions.length).toBeGreaterThanOrEqual(3);
    clean(run);
  });

  test('Bluetooth off and a device that is not found are said plainly', async ({ page }, info) => {
    const run = await open(page, {
      bluetooth: false,
      answers: [{ status: 404, body: { status: 'error', detail: 'The device was not found over Bluetooth. Check that it is powered, new or freshly reset (in pairing mode), and within a few metres of the hub, then try again.' } }],
    });
    await page.getByRole('button', { name: /Add Matter device/ }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('radio', { name: /over Bluetooth/ }).click();
    await expect(dialog.getByTestId('matter-bluetooth')).toContainText('MATTER_BLUETOOTH_ADAPTER');
    await expect(dialog.getByTestId('matter-wifi-form')).toBeVisible();
    await dialog.getByLabel(/Setup code/).fill('34970112332');
    await dialog.getByRole('button', { name: 'Add over Bluetooth' }).click();
    await expect(dialog.getByRole('alert')).toContainText('not found over Bluetooth');
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'matter-ble-error');
    clean(run);
  });
});
