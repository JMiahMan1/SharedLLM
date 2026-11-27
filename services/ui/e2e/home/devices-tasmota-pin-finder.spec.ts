import { test, expect as baseExpect, type Locator, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { HARDWARE, detailFor, initialStates, mockDeviceStates } from '../fixtures/hardwareMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > a Tasmota light that says nothing about itself > Device template (services/homehub/tasmota_templates.py,
 * tasmota_pin_finder.py, tasmota_measured.py): the page says nothing identifies it and offers "Find my pins" before
 * any guess; the pin finder asks one question at a time, shows the template it found and saves it as measured; a
 * measured template is labelled with the device it was measured on. Every project (desktop, phones, tablets).
 * Hermetic: the device, its answers and the household's names are made up.
 */

const GENERIC = '{"NAME":"Generic","GPIO":[1,1,1,1,1,1,0,0,1,1,1,1,1,0],"FLAG":0,"BASE":18}';
const FOUND = '{"NAME":"Example A1","GPIO":[0,0,0,0,0,416,0,0,0,449,0,0,0,0],"FLAG":0,"BASE":18,"CMND":"SO92 1"}';

const TAS = {
  ...HARDWARE.devices[0], id: 'hw-tas', name: 'Den lamp', manufacturer: 'Tasmota', model: 'ESP8266EX',
  sw_version: '15.6.0(release-tasmota)', domain: 'tasmota', label: 'Tasmota', entry_id: 'e-tas', entry_title: 'Den lamp',
  kind: 'other', abilities: [], entities: 1, area_id: 'den', area: 'Den',
  connections: [{ type: 'ip', value: '192.0.2.61' }, { type: 'mac', value: '02:00:00:00:0b:01' }],
};

const KAUF = {
  id: 'kauf-blf10', manufacturer: 'KAUF', model: 'BLF10 10W RGBCCT bulb', kind: 'bulb', chip: 'ESP8266', commands: [],
  template: '{"NAME":"Kauf Bulb","GPIO":[0,0,0,0,416,419,0,0,417,420,418,0,0,0],"FLAG":0,"BASE":18}', name: 'Kauf Bulb',
  channels: 5, source_url: 'https://templates.blakadder.com/kauf_BLF10.html', notes: '', source: 'catalog',
};
const MEASURED = {
  id: 'measured-example-a1-1a2b3c4d', manufacturer: 'Example', model: 'A1', kind: 'bulb', chip: 'ESP8266', template: FOUND,
  commands: ['SO92 1'], source_url: '', notes: '', name: 'Example A1', channels: 2, source: 'measured',
  measured_on: 'Hall lamp', measured_at: '2026-11-20', method: 'pin_finder',
};

const CURRENT = {
  device_name: 'Den lamp', friendly_name: ['Den lamp'], hostname: 'tasmota-0B01', chip: 'ESP8266EX',
  firmware: '15.6.0(release-tasmota)', module: 18, module_name: 'Generic', template_name: 'Generic', template_json: GENERIC,
  active: false, light: false, light_channels: 0, relays: 0, energy: false, catalog_matches: [], measured_matches: [],
};
const UNKNOWN = {
  entry_id: 'e-tas', title: 'Den lamp', current: CURRENT, up_to_date: false, message: null, previous_template: null,
  identified: false, suggestions: [],
  identify_note: 'It reports nothing that identifies it: it runs the Generic module with no pins set, and its names match '
    + 'no maker or model. Find its pins with the pin finder, or search the catalog by the model on its box.',
  guesses: [{ ...MEASURED, score: 5, reasons: ['Its name says it is a bulb'] }, { ...KAUF, score: 5, reasons: ['Its name says it is a bulb'] }],
};
const KNOWN = {
  ...UNKNOWN, identified: true, identify_note: null, guesses: [],
  suggestions: [{ ...MEASURED, score: 25, reasons: ['Its name matches the model measured on Hall lamp', 'Its name says it is a bulb'] },
    { ...KAUF, score: 5, reasons: ['Its name says it is a bulb'] }],
};

const SINGLES = [4, 5, 12, 13, 14].map((pin, i) => ({
  id: 'single', text: `Only GPIO${pin} is on now. What does Den lamp look like?`, hint: 'Dark is a fine answer: most pins drive nothing.',
  pin, again: true, progress: { done: i, total: 5 },
  answers: [['dark', 'Dark'], ['cool', 'Cool white'], ['warm', 'Warm white'], ['white', 'White'], ['red', 'Red'], ['green', 'Green'],
    ['blue', 'Blue'], ['other', 'Another colour']].map(([value, label]) => ({ value, label })),
}));
const SCRIPT = [
  { id: 'baseline', text: 'Every pin being tested is off. Is Den lamp dark?', hint: 'Look at the light itself, not a power or Wi-Fi LED.',
    pin: null, again: true, progress: { done: 0, total: 5 },
    answers: [{ value: 'dark', label: 'Yes, it is dark' }, { value: 'lit', label: 'No, it is lit' }] },
  ...SINGLES,
  { id: 'pair', text: 'It showed GPIO5 alone for two seconds, then turned GPIO13 on as well. Did the white change when the second pin came on?',
    hint: 'Use Show again to watch it once more.', pin: 13, again: true, progress: { done: 5, total: 5 },
    answers: [{ value: 'cooler', label: 'It got cooler (bluer)' }, { value: 'warmer', label: 'It got warmer (yellower)' },
      { value: 'same', label: 'No change' }, { value: 'dark', label: 'It went dark' }] },
  { id: 'verify', text: 'Is it a cool, bluish white now?', hint: 'Checking the template that was found (1 of 2).', pin: null, again: true,
    progress: { done: 5, total: 5 }, answers: [{ value: 'cool', label: 'Cool white' }, { value: 'warm', label: 'Warm white' }, { value: 'other', label: 'Something else' }] },
  { id: 'verify', text: 'And now, is it a warm, yellowish white?', hint: 'Checking the template that was found (2 of 2).', pin: null, again: true,
    progress: { done: 5, total: 5 }, answers: [{ value: 'cool', label: 'Cool white' }, { value: 'warm', label: 'Warm white' }, { value: 'other', label: 'Something else' }] },
].map((q, i) => ({ ...q, step: i + 1 }));
/** What the person answers to each scripted question. */
const ANSWERS = ['Yes, it is dark', 'Dark', 'White', 'Dark', 'Dark', 'Dark', 'It got cooler (bluer)', 'Cool white', 'Warm white'];

type Run = { mocks: HomeMocks; errors: string[]; posts: { path: string; body: unknown }[] };

async function open(page: Page, known = false): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  await mockDeviceStates(page);
  const errors: string[] = [];
  const posts: Run['posts'] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route(/\/api\/homehub\/hardware$/, (route: Route) =>
    route.fulfill({ json: { ...HARDWARE, devices: [...HARDWARE.devices, TAS] } }));
  await page.route(/\/api\/homehub\/hardware\/hw-tas$/, (route: Route) =>
    route.fulfill({ json: { ...detailFor('hw-lamp', initialStates()), ...TAS, children: [], entry: null, entity_list: [] } }));
  await page.route(/\/api\/homehub\/tasmota\/templates/, (route: Route) =>
    route.fulfill({ json: { kinds: ['bulb', 'plug', 'switch', 'dimmer', 'strip', 'relay'], templates: [KAUF], measured: [MEASURED] } }));
  await page.route(/\/api\/homehub\/tasmota\/e-tas\/template$/, (route: Route) => route.fulfill({ json: known ? KNOWN : UNKNOWN }));
  let state: Record<string, unknown> = { active: false, status: 'idle', question: null, result: null, message: null,
    interrupted: false, kinds: ['bulb', 'strip', 'dimmer'] };
  let at = 0;
  await page.route(/\/api\/homehub\/tasmota\/e-tas\/(pinfinder(\/\w+)?|measured)$/, async (route: Route) => {
    const req = route.request();
    if (req.method() === 'GET') return route.fulfill({ json: state });
    const path = new URL(req.url()).pathname;
    const body = req.postDataJSON() as Record<string, unknown>;
    posts.push({ path, body });
    if (path.endsWith('/measured')) {
      return route.fulfill({ json: { status: 'saved', template: { ...MEASURED, measured_on: 'Den lamp' },
        message: 'Saved. Devices like it will be offered this template, marked "Measured on Den lamp".' } });
    }
    if (path.endsWith('/cancel')) {
      state = { ...state, active: false, status: 'cancelled', question: null, restored: true,
        message: 'Cancelled. Put back as it was (Generic).' };
      return route.fulfill({ json: state });
    }
    if (path.endsWith('/start')) at = 0;
    else if (body.answer !== 'again') at += 1;
    state = at < SCRIPT.length
      ? { active: true, status: 'asking', question: SCRIPT[at], result: null, message: null, manufacturer: 'Example', model: 'A1', kind: 'bulb' }
      : { active: false, status: 'done', question: null, manufacturer: 'Example', model: 'A1', kind: 'bulb',
        message: 'Found and checked. The device now runs this template. Added 1 entity.',
        result: { template: FOUND, commands: ['SO92 1'], type: 'ct', pins: { bright: 5, ct: 13 }, invert: true, kind: 'bulb',
          how: 'Found by the pin finder: GPIO5 alone lit it (brightness); adding GPIO13 made it cooler, so GPIO13 is colour temperature (inverted). Checked by eye: CT 153 cool, CT 500 warm.' } };
    return route.fulfill({ json: state });
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
  await page.getByTestId('device-tile').filter({ hasText: 'Den lamp' }).getByTestId('hardware-device').click();
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

test.describe('Devices: Tasmota pin finder and measured templates', () => {
  test.describe.configure({ timeout: 120_000 });

  test('a device that says nothing about itself gets the pin finder first, which finds and saves its template', async ({ page }, info) => {
    const run = await open(page);
    const { dialog, section } = await openDevice(page);
    await expect(section.getByTestId('template-unidentified')).toContainText('nothing that identifies it');
    await expect(section.getByTestId('template-suggestion')).toHaveCount(0);
    const guesses = section.getByTestId('template-guesses');
    await expect(guesses).toContainText('Guesses from its name only (2)');
    await expect(guesses.getByTestId('template-guess').first()).toBeHidden();
    const finder = section.getByTestId('pin-finder');
    await expect(finder.getByTestId('pin-finder-intro')).toBeVisible();
    await expectTapTargets(section);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-pin-finder-intro');

    await finder.getByLabel('Manufacturer').fill('Example');
    await finder.getByLabel('Model').fill('A1');
    await finder.getByRole('button', { name: 'Start the pin finder' }).click();
    const confirm = page.getByRole('dialog', { name: 'Find the pins of Den lamp?' });
    await expect(confirm).toContainText("Den lamp's template is saved first");
    await settled(page, confirm);
    await expectFits(page, confirm);
    await confirm.getByRole('button', { name: 'Save and start' }).click();

    const q = finder.getByTestId('pin-finder-question');
    for (const [i, answer] of ANSWERS.entries()) {
      await expect(q).toContainText(SCRIPT[i].text);
      if (i === 2) {
        await q.scrollIntoViewIfNeeded();
        await expectTapTargets(q);
        await expectNoHorizontalOverflow(page);
        await snap(page, info, 'devices-tasmota-pin-finder-question');
        await q.getByRole('button', { name: 'Show again' }).click();
        await expect.poll(() => run.posts.at(-1)?.body).toEqual({ step: SCRIPT[i].step, answer: 'again' });
      }
      await q.getByRole('button', { name: answer, exact: true }).click();
    }
    const done = finder.getByTestId('pin-finder-done');
    await expect(done).toContainText('Found and checked');
    await expect(done.getByTestId('pin-finder-template')).toHaveText(FOUND);
    await expect(done.getByLabel('Model')).toHaveValue('A1');
    await done.scrollIntoViewIfNeeded();
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-pin-finder-done');
    await done.getByRole('button', { name: 'Save as measured' }).click();
    await expect(finder.getByTestId('pin-finder-saved')).toContainText('Measured on Den lamp');

    const sent = run.posts.map((p) => p.path.replace('/api/homehub/tasmota/e-tas', ''));
    expect(sent[0]).toBe('/pinfinder/start');
    expect(run.posts[0].body).toEqual({ manufacturer: 'Example', model: 'A1', kind: 'bulb', confirm: true });
    expect(sent.at(-1)).toBe('/measured');
    expect(run.posts.at(-1)?.body).toEqual({ manufacturer: 'Example', model: 'A1', kind: 'bulb' });
    clean(run);
  });

  test('cancelling asks first and says the template was put back', async ({ page }, info) => {
    const run = await open(page);
    const { section } = await openDevice(page);
    const finder = section.getByTestId('pin-finder');
    await finder.getByRole('button', { name: 'Start the pin finder' }).click();
    await page.getByRole('dialog', { name: 'Find the pins of Den lamp?' }).getByRole('button', { name: 'Save and start' }).click();
    await expect(finder.getByTestId('pin-finder-question')).toContainText('Is Den lamp dark?');
    await finder.getByRole('button', { name: 'Cancel and put it back' }).click();
    const confirm = page.getByRole('dialog', { name: 'Stop and put Den lamp back?' });
    await settled(page, confirm);
    await expectFits(page, confirm);
    await confirm.getByRole('button', { name: 'Put it back' }).click();
    await expect(finder.getByTestId('pin-finder-ended')).toContainText('Put back as it was (Generic).');
    await expect(finder.getByRole('button', { name: 'Start again' })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-pin-finder-cancelled');
    expect(run.posts.map((p) => p.path.split('/').at(-1))).toEqual(['start', 'cancel']);
    clean(run);
  });

  test('a measured template is labelled with the device it was measured on, and the pin finder waits folded', async ({ page }, info) => {
    const run = await open(page, true);
    const { section } = await openDevice(page);
    const rows = section.getByTestId('template-suggestion');
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText('Example A1');
    await expect(rows.first().getByTestId('measured-label')).toHaveText('Measured on Hall lamp, 2026-11-20');
    await expect(rows.nth(1).getByTestId('measured-label')).toHaveCount(0);
    await expect(section.getByTestId('template-unidentified')).toHaveCount(0);
    const finder = section.getByTestId('pin-finder');
    await expect(finder.getByRole('button', { name: /Find my pins/ })).toHaveAttribute('aria-expanded', 'false');
    await expect(finder.getByTestId('pin-finder-intro')).toHaveCount(0);
    const catalog = section.getByTestId('template-catalog');
    await catalog.getByText(/Choose from the catalog/).click();
    await expect(catalog.locator('[data-source="measured"]')).toHaveCount(1);
    await expect(catalog.getByRole('button', { name: 'Forget Example A1' })).toBeVisible();
    await expectTapTargets(section);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-tasmota-measured');
    clean(run);
  });
});
