import { test, expect as baseExpect, type Locator, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { detailFor, initialStates, mockDeviceStates } from '../fixtures/hardwareMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Tasmota devices with a web password: one found on the network is added with its password, and one
 * already set up whose password the hub does not have is repaired, from Integrations and from the
 * device's own settings. Every project (desktop, phones, tablets). Hermetic: the names, addresses and
 * MACs are made up, and the password is a placeholder.
 */

const PASSWORD = 'made-up-pw';
const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

const LAMP = {
  id: 'tasmota:020000000a01', integration: 'tasmota', label: 'Tasmota', name: 'Shelf-Bulb', model: 'ESP8266EX',
  manufacturer: 'Tasmota', host: '192.0.2.51', port: 80, mac: '020000000a01', source: 'http', first_seen: ago(3),
  last_seen: ago(1), known: false, entry_id: null, entry_title: null, ignored: false, new: true, adoptable: true,
  unsupported: null, moved: null, note: 'Needs its web password',
  needs: [{ key: 'password', label: 'Web password', type: 'password',
    help: "The password of the device's own web page (on the device: Configuration, Other, Web Admin Password)." }],
  details: { locked: true, version: '15.6.0', hostname: 'Shelf-Bulb' },
};

const PLUG_ENTRY = {
  entry_id: 'ts1', domain: 'tasmota', label: 'Tasmota', title: 'Tasmota', state: 'setup_retry',
  reason: 'AuthRequired: Tasmota 192.0.2.174 needs its web password', needs: 'password', source: 'mqtt',
  disabled_by: null, offline_since: ago(60 * 30), entities: 3, unpairs: false, shared: false, syncs: false,
  reconfigure: [
    { key: 'host', label: 'Address', type: 'text', value: '192.0.2.174',
      help: "The device's IP address. The hub follows it by its MAC when the router changes it." },
    { key: 'password', label: 'Web password', type: 'password', set: false,
      help: "The password of the device's own web page. Leave empty to keep the stored one." },
  ],
  devices: [{ id: 'hw-plug', name: 'Kettle plug', original_name: 'Kettle plug', name_by_user: null, area_id: 'kitchen',
    manufacturer: 'Tasmota', model: 'Four outlet strip', via_device_id: null, entities: ['switch.kettle_plug'] }],
};

type Run = { mocks: HomeMocks; errors: string[]; sent: { method: string; path: string; body: unknown }[] };

async function start(page: Page): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  return { mocks, errors, sent: [] };
}

/** The repair's entry, fixed once the right password is PATCHed. */
async function mockPlugEntry(page: Page, run: Run) {
  let entry: Record<string, unknown> = structuredClone(PLUG_ENTRY);
  await page.route('**/api/homehub/entries', (route) => route.fulfill({ json: { entries: [entry], areas: [
    { area_id: 'kitchen', name: 'Kitchen' }] } }));
  await page.route('**/api/homehub/entries/ts1', async (route: Route) => {
    const body = route.request().postDataJSON() as { data?: Record<string, string> };
    run.sent.push({ method: route.request().method(), path: '/api/homehub/entries/ts1', body });
    if (body.data?.password && body.data.password !== PASSWORD) {
      return route.fulfill({ status: 400, json: { status: 'error', message: 'The password was not accepted.' } });
    }
    entry = { ...entry, state: 'loaded', reason: null, needs: null, offline_since: null,
      reconfigure: PLUG_ENTRY.reconfigure.map((f) => (f.key === 'password' ? { ...f, set: true } : f)) };
    return route.fulfill({ json: entry });
  });
}

/** A dialog slides in as it opens; measure it once it has stopped moving. */
async function settled(page: Page, dialog: Locator) {
  const h = page.viewportSize()?.height ?? 0;
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.y + b.height : h + 99; })
    .toBeLessThanOrEqual(h + 1);
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('Devices: Tasmota with a web password', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a locked Tasmota on the network says it needs its password and is added with it', async ({ page }, info) => {
    const run = await start(page);
    let adopted = false;
    await page.route('**/api/homehub/discovered', (route) => route.fulfill({ json: {
      devices: adopted ? [{ ...LAMP, known: true, new: false, entry_id: 'e-lamp', entry_title: 'Shelf-Bulb' }] : [LAMP],
      last_scan: { at: ago(2) } } }));
    await page.route('**/api/homehub/discovered/*/adopt', async (route) => {
      const body = route.request().postDataJSON() as { input?: { password?: string } };
      run.sent.push({ method: 'POST', path: decodeURIComponent(new URL(route.request().url()).pathname), body });
      if (body.input?.password !== PASSWORD) {
        return route.fulfill({ json: { status: 'needs_input', message: 'This Tasmota device asks for its web password.',
          error: 'The password was not accepted.', needs: LAMP.needs } });
      }
      adopted = true;
      return route.fulfill({ json: { status: 'adopted', entry_id: 'e-lamp', domain: 'tasmota', title: 'Shelf-Bulb',
        name: 'Shelf-Bulb', device_id: 'd-lamp', entities: ['light.shelf_bulb'] } });
    });
    await page.goto('/devices');
    await page.getByRole('tab', { name: 'Add devices' }).click();
    const row = page.getByTestId('new-device').filter({ hasText: 'Shelf-Bulb' });
    await expect(row).toBeVisible();
    await expect(row.getByTestId('new-device-note')).toHaveText('Needs its web password');
    await expect(row).toContainText('Tasmota · ESP8266EX · 192.0.2.51');
    await expectTapTargets(row);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-found');

    await row.getByRole('button', { name: 'Add Shelf-Bulb' }).click();
    const dialog = page.getByRole('dialog');
    const form = dialog.getByTestId('adopt-dialog');
    await expect(dialog).toContainText('Tasmota at 192.0.2.51. Needs its web password.');
    const add = form.getByRole('button', { name: /^Add/ });
    await expect(add).toBeDisabled(); // the password is required for a locked device
    await form.getByLabel(/Web password/).fill('not-it');
    await add.click();
    await expect(form.getByRole('alert')).toHaveText('The password was not accepted.');
    await form.getByLabel(/Web password/).fill(PASSWORD);
    await form.getByLabel('Room (optional)').selectOption({ label: 'Bedroom' });
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectTapTargets(form);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-add-password');
    await add.click();
    await expect(page.getByTestId('adopt-dialog')).toHaveCount(0);
    await expect(page.getByTestId('new-device').filter({ hasText: 'Shelf-Bulb' })).toHaveCount(0);
    expect(run.sent.map((s) => (s.body as { input?: { password?: string } }).input?.password)).toEqual(['not-it', PASSWORD]);
    expect(run.sent[1]).toMatchObject({ path: '/api/homehub/discovered/tasmota:020000000a01/adopt',
      body: { area_id: 'bedroom', input: { password: PASSWORD } } });
    clean(run);
  });

  test('a set-up Tasmota that needs its password is repaired from Integrations', async ({ page }, info) => {
    const run = await start(page);
    await mockPlugEntry(page, run);
    await page.goto('/devices');
    await page.getByRole('tab', { name: 'Setup' }).click();
    const row = page.getByTestId('offline-entry').filter({ hasText: 'Tasmota' });
    await row.scrollIntoViewIfNeeded();
    await expect(row).toContainText('Tasmota · Needs its web password');
    await expect(page.getByTestId('integration-reason')).toHaveText('Needs its web password');
    await expect(page.getByText(/AuthRequired/)).toHaveCount(0);
    await expectTapTargets(row);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-needs-password');

    await row.getByRole('button', { name: 'Enter the web password of Tasmota' }).click();
    const dialog = page.getByRole('dialog');
    const form = dialog.getByTestId('reconfigure-dialog');
    await expect(form.getByRole('status')).toContainText('asks for its web password');
    await expect(form.getByLabel('Address')).toHaveValue('192.0.2.174');
    await form.getByLabel('Web password').fill('not-it');
    await form.getByRole('button', { name: 'Save' }).click();
    await expect(form.getByRole('alert')).toHaveText('The password was not accepted.');
    await form.getByLabel('Web password').fill(PASSWORD);
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectTapTargets(form);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-repair');
    await form.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('reconfigure-dialog')).toHaveCount(0);
    // Only the password went: the address had not changed.
    expect(run.sent.at(-1)).toEqual({ method: 'PATCH', path: '/api/homehub/entries/ts1', body: { data: { password: PASSWORD } } });
    await expect(page.getByTestId('offline-entry').filter({ hasText: 'Tasmota' })).toHaveCount(0);
    clean(run);
  });

  test("a device's own settings take its address and web password", async ({ page }, info) => {
    const run = await start(page);
    await mockDeviceStates(page);
    await mockPlugEntry(page, run);
    let fixed = false;
    await page.route(/\/api\/homehub\/hardware\/hw-plug(\?.*)?$/, (route) => {
      const base = detailFor('hw-plug', initialStates());
      return route.fulfill({ json: fixed ? { ...base, entry: { ...PLUG_ENTRY, state: 'loaded', reason: null, needs: null,
        reconfigure: PLUG_ENTRY.reconfigure.map((f) => (f.key === 'password' ? { ...f, set: true } : f)) } } : {
        ...base, domain: 'tasmota', label: 'Tasmota', entry_id: 'ts1', entry_title: 'Tasmota', entry_state: 'setup_retry',
        entry_reason: PLUG_ENTRY.reason, status: 'offline', entry: PLUG_ENTRY } });
    });
    await page.route('**/api/homehub/entries/ts1', async (route) => {
      fixed = true;
      return route.fallback();
    });
    // The sheet's Device template section (tasmota_templates_api.py) asks the device itself: a locked device
    // answers 409 until the hub has its password, then the template it runs.
    await page.route(/\/api\/homehub\/tasmota\/ts1\/template$/, (route: Route) => {
      if (!fixed) {
        const message = 'Tasmota 192.0.2.174 needs its web password. Enter it under Connection first.';
        return route.fulfill({ status: 409, json: { status: 'error', message, detail: message } });
      }
      return route.fulfill({ json: { entry_id: 'ts1', title: 'Tasmota', up_to_date: false, message: null,
        identified: true, identify_note: null, suggestions: [], guesses: [], previous_template: null,
        current: { device_name: 'Kettle plug', friendly_name: ['Kettle plug'], hostname: 'kettle-plug-0b02',
          chip: 'ESP8266EX', firmware: '15.6.0(release-tasmota)', module: 0, module_name: 'Generic',
          template_name: 'Four outlet strip', active: true, light: false, light_channels: 0, relays: 4, energy: false,
          template_json: '{"NAME":"Four outlet strip","GPIO":[0,0,0,0,224,225,0,0,226,227,0,0,0,0],"FLAG":0,"BASE":18}',
          catalog_matches: [], measured_matches: [] } } });
    });
    await page.route(/\/api\/homehub\/tasmota\/ts1\/pinfinder$/, (route: Route) =>
      route.fulfill({ json: { active: false, status: 'idle', question: null, result: null, message: null,
        interrupted: false, kinds: ['bulb', 'strip', 'dimmer'] } }));
    await page.route(/\/api\/homehub\/tasmota\/templates/, (route: Route) =>
      route.fulfill({ json: { kinds: ['bulb', 'plug', 'switch', 'dimmer', 'strip', 'relay'], templates: [], measured: [] } }));
    await page.goto('/devices');
    await page.getByTestId('device-tile').filter({ hasText: 'Kettle plug' }).getByTestId('hardware-device').click();
    const dialog = page.getByRole('dialog');
    const detail = dialog.getByTestId('hardware-detail');
    await expect(detail).toBeVisible();
    const note = detail.getByTestId('offline-note');
    await expect(note).toContainText('Needs its web password');
    await expect(note).toContainText('Enter it under Settings, Connection.');
    const connection = detail.getByTestId('connection-settings');
    await connection.scrollIntoViewIfNeeded();
    await expect(connection.getByLabel('Address')).toHaveValue('192.0.2.174');
    await expect(connection.getByLabel('Web password')).toHaveAttribute('placeholder', 'Not set');
    await expect(connection.getByText('The device asks for this password.')).toBeVisible();
    const save = connection.getByRole('button', { name: 'Save connection' });
    await expect(save).toBeDisabled();
    await connection.getByLabel('Address').fill('192.0.2.175');
    await connection.getByLabel('Web password').fill(PASSWORD);
    await expectTapTargets(connection);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-connection');
    await save.click();
    await expect.poll(() => run.sent.at(-1)).toEqual({ method: 'PATCH', path: '/api/homehub/entries/ts1',
      body: { data: { host: '192.0.2.175', password: PASSWORD } } });
    await expect(detail.getByTestId('offline-note')).toHaveCount(0);
    await expect(connection.getByLabel('Web password')).toHaveValue('');
    clean(run);
  });
});
