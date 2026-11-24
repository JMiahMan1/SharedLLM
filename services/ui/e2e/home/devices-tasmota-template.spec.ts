import { test, expect as baseExpect, type Locator, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { HARDWARE, detailFor, initialStates, mockDeviceStates } from '../fixtures/hardwareMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > a Tasmota device > Device template (services/homehub/tasmota_templates.py): the template now, the hub's
 * suggestions with their reasons, the catalog and a paste box, and loading one after a confirm dialog that names the
 * device and the template. Every project (desktop, phones, tablets). Hermetic: the device is made up.
 */

const KAUF = '{"NAME":"Kauf Bulb","GPIO":[0,0,0,0,416,419,0,0,417,420,418,0,0,0],"FLAG":0,"BASE":18}';
const GENERIC = '{"NAME":"Generic","GPIO":[1,1,1,1,1,1,0,0,1,1,1,1,1,0],"FLAG":0,"BASE":18}';

const TAS = {
  ...HARDWARE.devices[0], id: 'hw-tas', name: 'Porch bulb', manufacturer: 'Tasmota', model: 'ESP8266EX',
  sw_version: '15.6.0(release-tasmota)', domain: 'tasmota', label: 'Tasmota', entry_id: 'e-tas', entry_title: 'Porch bulb',
  kind: 'other', abilities: [], entities: 1, area_id: 'porch', area: 'Front porch',
  connections: [{ type: 'ip', value: '192.0.2.51' }, { type: 'mac', value: '02:00:00:00:0a:01' }],
};

const kauf = (model: string, id: string) => ({
  id, manufacturer: 'KAUF', model, kind: 'bulb', chip: 'ESP8266', template: KAUF, commands: [], name: 'Kauf Bulb',
  channels: 5, source_url: `https://templates.blakadder.com/kauf_${id.split('-')[1].toUpperCase()}.html`,
  notes: 'E26. Ships with ESPHome; converts to Tasmota from its web page.',
});
const CATALOG = [
  kauf('BLF10 10W RGBCCT bulb', 'kauf-blf10'), kauf('BLF15 A15 5W RGBCCT bulb', 'kauf-blf15'),
  kauf('BLF19 A19 7W RGBCCT bulb', 'kauf-blf19'),
  { id: 'example-plug', manufacturer: 'Example', model: 'P2 power monitoring plug with a long model name', kind: 'plug',
    chip: 'ESP8266', template: '{"NAME":"P2","GPIO":[320,0,576,0,224,2720,0,0,2624,32,2656,0,0,0],"FLAG":0,"BASE":45}',
    commands: [], name: 'P2', channels: 0, source_url: 'https://templates.blakadder.com/example.html', notes: '' },
];
const REASONS = ['Its name mentions KAUF', 'Its name says it is a bulb',
  'Named like a light, but it drives no light channels yet: a bulb template fixes that', 'Same chip family (ESP8266)'];

const CURRENT = {
  device_name: 'Kauf Bulb', friendly_name: ['Kauf Bulb'], hostname: 'kauf-bulb-0a01', chip: 'ESP8266EX',
  firmware: '15.6.0(release-tasmota)', module: 18, module_name: 'Generic', template_name: 'Generic', template_json: GENERIC,
  active: false, light: false, light_channels: 0, relays: 0, energy: false, catalog_matches: [],
};
const STATE = {
  entry_id: 'e-tas', title: 'Porch bulb', current: CURRENT, up_to_date: false, message: null, previous_template: null,
  suggestions: CATALOG.slice(0, 3).map((e, i) => ({ ...e, score: 9 - i, reasons: REASONS })),
};

type Run = { mocks: HomeMocks; errors: string[]; posts: { path: string; body: unknown }[] };

async function open(page: Page): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  await mockDeviceStates(page);
  const errors: string[] = [];
  const posts: Run['posts'] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route(/\/api\/homehub\/hardware$/, (route: Route) =>
    route.fulfill({ json: { ...HARDWARE, devices: [...HARDWARE.devices, TAS] } }));
  await page.route(/\/api\/homehub\/hardware\/hw-tas$/, (route: Route) =>
    route.fulfill({ json: { ...detailFor('hw-lamp', initialStates()), ...TAS, children: [], entry: null, entity_list: [] } }));
  await page.route(/\/api\/homehub\/tasmota\/e-tas\/pinfinder$/, (route: Route) =>
    route.fulfill({ json: { active: false, status: 'idle', question: null, result: null, message: null, interrupted: false,
      kinds: ['bulb', 'strip', 'dimmer'] } }));
  await page.route(/\/api\/homehub\/tasmota\/templates/, (route: Route) =>
    route.fulfill({ json: { kinds: ['bulb', 'plug', 'switch', 'dimmer', 'strip', 'relay'], templates: CATALOG } }));
  let state: Record<string, unknown> = structuredClone(STATE);
  await page.route(/\/api\/homehub\/tasmota\/e-tas\/template(\/restore)?$/, async (route: Route) => {
    const req = route.request();
    if (req.method() === 'GET') return route.fulfill({ json: state });
    const path = new URL(req.url()).pathname;
    posts.push({ path, body: req.postDataJSON() });
    const restore = path.endsWith('/restore');
    state = { ...state, previous_template: restore ? KAUF : GENERIC,
      current: { ...CURRENT, ...(restore ? {} : { module: 0, template_name: 'Kauf Bulb', template_json: KAUF, active: true,
        light: true, light_channels: 5 }) } };
    return route.fulfill({ json: { status: 'applied', verified: true, restarted: true, template: restore ? GENERIC : KAUF,
      sent: `Backlog Template ${restore ? GENERIC : KAUF}; Module ${restore ? 18 : 0}`,
      previous_template: state.previous_template, current: state.current, entities_added: [], entities_stale: [],
      message: 'Loaded and verified: the device restarted with the new template. Added 1 entity.' } });
  });
  await page.goto('/devices');
  await expect(page.getByTestId('device-tile').first()).toBeVisible();
  return { mocks, errors, posts };
}

async function settled(page: Page, dialog: Locator) {
  const h = page.viewportSize()?.height ?? 0;
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.y + b.height : h + 99; })
    .toBeLessThanOrEqual(h + 1);
}

async function openDevice(page: Page): Promise<{ dialog: Locator; section: Locator }> {
  await page.getByTestId('device-tile').filter({ hasText: 'Porch bulb' }).getByTestId('hardware-device').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByTestId('hardware-detail')).toBeVisible();
  await settled(page, dialog);
  const section = dialog.getByTestId('section-tasmota-template');
  await section.scrollIntoViewIfNeeded();
  await expect(section).toBeVisible();
  return { dialog, section };
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('Devices: Tasmota device template', () => {
  test.describe.configure({ timeout: 90_000 });

  test('suggestions with reasons, a confirm that names the device, the result and Restore previous', async ({ page }, info) => {
    const run = await open(page);
    const { dialog, section } = await openDevice(page);
    await expect(section.getByTestId('template-current')).toContainText('Now: Generic (not in use; it runs the Generic module)');
    const rows = section.getByTestId('template-suggestion');
    await expect(rows).toHaveCount(3);
    await expect(rows.first()).toContainText('KAUF BLF10 10W RGBCCT bulb');
    await expect(rows.first()).toContainText('Its name mentions KAUF');
    await expectTapTargets(section);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-template');

    await rows.nth(1).getByRole('button', { name: 'Choose' }).click();
    await expect(section.getByTestId('template-chosen')).toHaveText('Chosen: KAUF BLF15 A15 5W RGBCCT bulb');
    await section.getByRole('button', { name: 'Apply' }).click();
    const confirm = page.getByRole('dialog', { name: 'Load this template onto Porch bulb?' });
    await expect(confirm).toContainText('Porch bulb gets the KAUF BLF15 A15 5W RGBCCT bulb template.');
    await expect(confirm).toContainText('The device restarts');
    await settled(page, confirm);
    await expectFits(page, confirm);
    await snap(page, info, 'devices-tasmota-template-confirm');
    await confirm.getByRole('button', { name: 'Load and restart' }).click();
    await expect(section.getByTestId('template-result')).toContainText('Loaded and verified');
    expect(run.posts).toEqual([{ path: '/api/homehub/tasmota/e-tas/template', body: { catalog_id: 'kauf-blf15', confirm: true } }]);
    await expect(section.getByTestId('template-current')).toContainText('Now: Kauf Bulb');
    await section.getByTestId('template-result').scrollIntoViewIfNeeded();
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-template-result');

    await section.getByRole('button', { name: 'Restore previous' }).click();
    const back = page.getByRole('dialog', { name: 'Restore the previous template on Porch bulb?' });
    await expect(back).toContainText('("Generic")');
    await back.getByRole('button', { name: 'Restore and restart' }).click();
    await expect.poll(() => run.posts.length).toBe(2);
    expect(run.posts[1]).toEqual({ path: '/api/homehub/tasmota/e-tas/template/restore', body: { confirm: true } });
    clean(run);
  });

  test('the catalog is searchable and a pasted template is checked first', async ({ page }, info) => {
    const run = await open(page);
    const { section } = await openDevice(page);
    const catalog = section.getByTestId('template-catalog');
    await catalog.getByText(/Choose from the catalog/).click();
    await expect(catalog.getByTestId('template-option')).toHaveCount(4);
    await catalog.getByLabel('Search templates').fill('plug');
    await expect(catalog.getByTestId('template-option')).toHaveCount(1);
    await catalog.getByLabel('Search templates').fill('');
    await catalog.getByLabel('Kind of device').selectOption('bulb');
    await expect(catalog.getByTestId('template-option')).toHaveCount(3);
    await expectTapTargets(section);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-template-catalog');

    const paste = section.getByTestId('template-paste-box');
    await paste.getByText('Paste a template').click();
    await paste.getByTestId('template-paste').fill('{"NAME":"Porch"');
    await expect(paste.getByRole('alert')).toContainText('not valid JSON');
    await expect(section.getByRole('button', { name: 'Apply' })).toBeDisabled();
    await paste.getByTestId('template-paste').fill(KAUF);
    await expect(section.getByTestId('template-chosen')).toHaveText('Chosen: Kauf Bulb');
    await expect(section.getByRole('button', { name: 'Apply' })).toBeEnabled();
    await section.getByRole('button', { name: 'Apply' }).click();
    const confirm = page.getByRole('dialog', { name: 'Load this template onto Porch bulb?' });
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(confirm).toHaveCount(0);
    expect(run.posts).toEqual([]);
    await expectNoHorizontalOverflow(page);
    clean(run);
  });

  test('a device that is not Tasmota has no Device template section', async ({ page }) => {
    const run = await open(page);
    await page.getByTestId('device-tile').filter({ hasText: 'Desk bulb' }).getByTestId('hardware-device').click();
    await expect(page.getByRole('dialog').getByTestId('hardware-detail')).toBeVisible();
    await expect(page.getByTestId('section-tasmota-template')).toHaveCount(0);
    clean(run);
  });
});
