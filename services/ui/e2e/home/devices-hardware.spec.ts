import { test, expect as baseExpect, type Locator, type Page } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockDeviceStates, type DeviceStateMocks } from '../fixtures/hardwareMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > Your devices (the tile grid and a device's detail), moved devices and adding by hand,
 * in every project (desktop, Pixel 7, a small phone, a tablet both ways round). Hermetic:
 * fixtures/homeMocks.ts + hardwareMocks.ts, whose devices are made up.
 */

type Run = { mocks: HomeMocks; devices: DeviceStateMocks; errors: string[] };

async function open(page: Page): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  // The voice choices the page PATCHes, so the detail read after a change shows them.
  const voices = () => new Map(mocks.posts
    .filter((p) => p.path.startsWith('/api/homehub/entities/') && typeof (p.body as { voice?: unknown })?.voice === 'boolean')
    .map((p) => [decodeURIComponent(p.path.split('/').pop() || ''), (p.body as { voice: boolean }).voice] as const));
  const devices = await mockDeviceStates(page, voices);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/devices');
  await expect(page.getByTestId('page-header')).toBeVisible();
  await expect(page.getByTestId('device-tile').first()).toBeVisible();
  return { mocks, devices, errors };
}

/** A sheet slides in as it opens; measure it once it has stopped moving. */
async function settled(page: Page, dialog: Locator) {
  const h = page.viewportSize()?.height ?? 0;
  const w = page.viewportSize()?.width ?? 0;
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.y + b.height : h + 99; })
    .toBeLessThanOrEqual(h + 1);
  await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.x + b.width : w + 99; })
    .toBeLessThanOrEqual(w + 1);
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

const tile = (page: Page, name: string | RegExp) => page.getByTestId('device-tile').filter({ hasText: name });

async function openDevice(page: Page, name: string | RegExp): Promise<{ dialog: Locator; detail: Locator }> {
  await tile(page, name).getByTestId('hardware-device').click();
  const dialog = page.getByRole('dialog');
  const detail = dialog.getByTestId('hardware-detail');
  await expect(detail).toBeVisible();
  await settled(page, dialog);
  return { dialog, detail };
}

test.describe('Devices: the tile grid', () => {
  test.describe.configure({ timeout: 90_000 });

  test('rooms of tiles with plain state lines; a tile toggles, and a refused command says why', async ({ page }, info) => {
    const run = await open(page);
    const panel = page.getByTestId('hardware');
    await expect(page.getByTestId('device-tile')).toHaveCount(13);
    await expect(panel.getByTestId('hardware-group').locator('h3'))
      .toHaveText([/^Den/, /^Front porch/, /^Garage/, /^Kitchen/, /^Living room/, /^Office/, /^No room/]);
    await expect(tile(page, 'Hall thermostat').getByTestId('tile-state')).toHaveText('Heat and cool, 68 to 76°F');
    await expect(tile(page, 'Garage door').getByTestId('tile-state')).toHaveText('Closed');
    await expect(tile(page, 'Front door lock').getByTestId('tile-state')).toHaveText('Locked');
    await expect(tile(page, 'Robot vacuum').getByTestId('tile-state')).toHaveText('Docked');
    await expect(tile(page, 'Reading lamp').getByTestId('tile-state')).toHaveText('On, 71%');
    await expect(tile(page, 'Desk bulb').getByTestId('tile-state')).toHaveText('On, 50%');
    await expect(tile(page, 'Kitchen speaker').getByTestId('tile-state')).toHaveText('Playing Morning Songs');
    await expect(tile(page, 'Den TV').getByTestId('tile-state')).toHaveText('On, Video app');
    await expect(tile(page, 'Den TV').getByRole('switch', { name: 'Den TV power' })).toHaveAttribute('aria-checked', 'true');
    await expect(tile(page, 'Hall climate sensor').getByTestId('tile-state')).toHaveText('21.5 °C');
    await expect(tile(page, 'Kettle plug').getByTestId('tile-state')).toHaveText('Off');
    // Offline: dimmed, badged, how long, and nothing to tap but the detail.
    const tv = tile(page, 'Living TV');
    await expect(tv).toHaveAttribute('data-state', 'offline');
    await expect(tv.getByTestId('tile-state')).toHaveText('Offline for 3 h');
    await expect(tv.getByText('Offline', { exact: true })).toBeVisible();
    await expect(tv.getByTestId('tile-action')).toHaveCount(0);
    // A lit light wears its own colour.
    expect(await tile(page, 'Reading lamp').getByTestId('tile-action').evaluate((el) => getComputedStyle(el).backgroundColor))
      .toBe('rgb(255, 150, 70)');
    // No capability words and no entity ids on a tile.
    await expect(panel.getByText('Color temp')).toHaveCount(0);
    await expect(panel.getByText('light.reading_lamp')).toHaveCount(0);
    await expectTapTargets(page.getByTestId('hardware-filters'));
    await expectTapTargets(panel.getByTestId('hardware-group').first());
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-grid');

    // One tap: the light goes off at once, and the hub's answer keeps it off.
    const lamp = tile(page, 'Reading lamp').getByRole('switch');
    await expect(lamp).toHaveAttribute('aria-checked', 'true');
    await lamp.click();
    await expect(lamp).toHaveAttribute('aria-checked', 'false');
    await expect(tile(page, 'Reading lamp').getByTestId('tile-state')).toHaveText('Off');
    await expect.poll(() => run.devices.commands.find((c) => c.entity_id === 'light.reading_lamp')?.service).toBe('turn_off');

    // The plug refuses: the tile goes back to Off and the hub's own words are shown.
    const plug = tile(page, 'Kettle plug').getByRole('switch', { name: 'Kettle plug power' });
    await plug.click();
    await expect(page.getByText('Kettle plug did not answer. Check that it is plugged in.')).toBeVisible();
    await expect(plug).toHaveAttribute('aria-checked', 'false');
    await expect(tile(page, 'Kettle plug').getByTestId('tile-state')).toHaveText('Off');
    await snap(page, info, 'devices-grid-refused');
    clean(run);
  });

  test('search, type chips, needs attention and grouping by type', async ({ page }, info) => {
    const run = await open(page);
    const filters = page.getByTestId('hardware-filters');
    await page.getByLabel('Search devices').fill('kettle');
    await expect(page.getByTestId('device-tile')).toHaveCount(1);
    await page.getByLabel('Search devices').fill('');
    await filters.getByRole('button', { name: /^Lights/ }).click();
    await expect(page.getByTestId('device-tile')).toHaveCount(2);
    await filters.getByRole('button', { name: /^All/ }).click();
    await filters.getByRole('button', { name: 'Needs attention (2)' }).click();
    await expect(page.getByTestId('device-tile')).toHaveCount(2);
    const noRoom = page.getByTestId('no-room-list');
    await expect(noRoom).toContainText('Hall climate sensor');
    await noRoom.getByLabel('Room for Hall climate sensor').selectOption('office');
    await expect.poll(() => run.mocks.posts.find((p) => p.path === '/api/homehub/devices/hw-sensor')?.body)
      .toEqual({ area_id: 'office' });
    await expectTapTargets(noRoom);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-needs-attention');
    await filters.getByRole('button', { name: /^All/ }).click();
    await page.getByRole('radio', { name: 'Types' }).click();
    await expect(page.getByTestId('hardware-group').locator('h3'))
      .toHaveText([/^Lights/, /^Plugs & switches/, /^TVs & speakers/, /^Climate/, /^Doors & locks/, /^Cameras/, /^Sensors/,
        /^Vacuums/]);
    await page.getByTestId('add-device').click();
    await expect(page.getByRole('tab', { name: 'Add devices' })).toHaveAttribute('aria-selected', 'true');
    clean(run);
  });

  test('an RGB and white bulb: power, brightness, white and colour first; its settings after', async ({ page }, info) => {
    const run = await open(page);
    const { dialog, detail } = await openDevice(page, 'Desk bulb');
    await expect(dialog.getByTestId('detail-status')).toHaveText('Office · On, 50%');
    const main = detail.getByTestId('main-control');
    await expect(main.getByRole('switch', { name: 'Desk bulb power' })).toHaveAttribute('aria-checked', 'true');
    const brightness = main.getByRole('slider', { name: 'Desk bulb brightness' });
    await expect(brightness).toHaveAttribute('aria-valuenow', '50');
    const white = main.getByRole('slider', { name: 'Desk bulb colour temperature' });
    await expect(white).toHaveAttribute('aria-valuenow', '2700');
    await expect(main.getByRole('group', { name: 'Desk bulb colour' }).getByRole('button')).toHaveCount(9);
    // The main control comes before everything else.
    const [mainBox, settingsBox] = await Promise.all([main.boundingBox(), detail.getByTestId('section-settings').boundingBox()]);
    expect(mainBox!.y).toBeLessThan(settingsBox!.y);
    // Configuration, in plain names with proper inputs; ids and addresses are folded away.
    const configs = detail.getByTestId('section-config');
    await expect(configs.getByText('Default fade', { exact: true })).toBeVisible();
    await expect(configs.getByRole('combobox', { name: 'Effect' })).toBeVisible();
    await expect(configs.getByRole('combobox', { name: 'Power-on state' })).toBeVisible();
    await expect(detail.getByText('light.desk_bulb')).toBeHidden();
    await expect(detail.getByText('192.0.2.41')).toBeHidden();
    await expectFits(page, dialog);
    await expectTapTargets(main);
    await snap(page, info, 'devices-detail-rgb-bulb');

    await brightness.focus();
    await page.keyboard.press('End');
    await expect.poll(() => run.devices.commands.find((c) => c.service_data?.brightness_pct != null)?.service_data)
      .toEqual({ brightness_pct: 100 });
    await white.focus();
    await page.keyboard.press('Home');
    await expect.poll(() => run.devices.commands.find((c) => c.service_data?.color_temp_kelvin != null)?.service_data)
      .toEqual({ color_temp_kelvin: 2000 });
    await main.getByRole('button', { name: 'Blue' }).click();
    await expect.poll(() => run.devices.commands.find((c) => c.service_data?.rgb_color != null)?.service_data)
      .toEqual({ rgb_color: [10, 132, 255] });
    await expect(main.getByRole('button', { name: 'Blue' })).toHaveAttribute('aria-pressed', 'true');
    await configs.getByRole('combobox', { name: 'Effect' }).selectOption('rainbow');
    await expect.poll(() => run.devices.commands.find((c) => c.service === 'select_option'))
      .toMatchObject({ entity_id: 'select.desk_bulb_effect', service_data: { option: 'rainbow' } });

    // Device info opens to the facts, ids included, for the admin who needs them.
    await detail.getByTestId('device-info').locator('summary').click();
    await expect(detail.getByText('192.0.2.41')).toBeVisible();
    await expect(detail.getByText('light.desk_bulb', { exact: true })).toBeVisible();
    await expect(detail.getByTestId('section-diagnostic')).toContainText('-61 dBm');
    await expectFits(page, dialog);
    await snap(page, info, 'devices-detail-info');
    clean(run);
  });

  test('a plug, a speaker, a sensor with its last day, and an offline TV', async ({ page }, info) => {
    const run = await open(page);
    let { dialog, detail } = await openDevice(page, 'Kettle plug');
    await expect(detail.getByTestId('main-control').getByRole('switch', { name: 'Kettle plug power' })).toBeVisible();
    const usage = detail.getByTestId('power-usage');
    await expect(usage).toContainText('0 W');
    await expect(usage).toContainText('12.4 kWh');
    await expect(usage.getByTestId('sensor-history')).toBeVisible();
    await expectFits(page, dialog);
    await snap(page, info, 'devices-detail-plug');
    await detail.getByTestId('main-control').getByRole('switch').click();
    await expect(page.getByText('Kettle plug did not answer. Check that it is plugged in.')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);

    ({ dialog, detail } = await openDevice(page, 'Kitchen speaker'));
    const media = detail.getByTestId('media-control');
    await expect(media).toContainText('Morning Songs');
    await expect(media.getByRole('slider', { name: 'Kitchen speaker volume' })).toHaveAttribute('aria-valuenow', '35');
    await expect(media.getByRole('combobox', { name: 'Source' })).toHaveValue('Playlist');
    await expectTapTargets(media);
    await expectFits(page, dialog);
    await snap(page, info, 'devices-detail-speaker');
    await media.getByRole('button', { name: 'Pause Kitchen speaker' }).click();
    await expect.poll(() => run.devices.commands.find((c) => c.entity_id === 'media_player.kitchen_speaker')?.service)
      .toBe('media_pause');
    await expect(media.getByRole('button', { name: 'Play Kitchen speaker' })).toBeVisible();
    await dialog.getByRole('button', { name: 'Close' }).click();

    ({ dialog, detail } = await openDevice(page, 'Hall climate sensor'));
    const reading = detail.getByTestId('sensor-reading');
    await expect(reading).toContainText('21.5 °C');
    await expect(reading.getByTestId('sensor-history')).toBeVisible();
    await expect(detail.getByTestId('section-sensors')).toContainText('Humidity');
    await expect(detail.getByTestId('section-sensors')).not.toContainText('Hall climate sensor Humidity');
    await expectFits(page, dialog);
    await snap(page, info, 'devices-detail-sensor');
    await dialog.getByRole('button', { name: 'Close' }).click();

    ({ dialog, detail } = await openDevice(page, 'Living TV'));
    await expect(detail.getByTestId('offline-note')).toContainText('Not answering');
    await expect(dialog.getByTestId('detail-status')).toHaveText('Living room · Offline for 3 h');
    await expect(detail.getByTestId('main-control').getByRole('button', { name: 'OK', exact: true })).toBeDisabled();
    await expect(detail.getByTestId('main-control').getByTestId('remote-missing')).toBeVisible();
    await expectFits(page, dialog);
    await snap(page, info, 'devices-detail-offline');
    clean(run);
  });

  test('settings: renaming, voice and chat access, and an entity renamed under Device info', async ({ page }, info) => {
    const run = await open(page);
    const { dialog, detail } = await openDevice(page, 'Reading lamp');
    await expect(detail.getByRole('slider', { name: /Reading lamp with .* brightness/ })).toBeVisible();
    const voice = detail.getByRole('switch', { name: 'Voice can control Reading lamp', exact: true });
    await expect(voice).toHaveAttribute('aria-checked', 'true');
    await voice.scrollIntoViewIfNeeded();
    expect((await voice.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await voice.click();
    await expect.poll(() => run.mocks.posts.find(
      (p) => decodeURIComponent(p.path) === '/api/homehub/entities/light.reading_lamp')?.body).toEqual({ voice: false });
    await expect(voice).toHaveAttribute('aria-checked', 'false');
    // A diagnostic reading voice does not control by default can be allowed too.
    const power = detail.getByRole('switch', { name: 'Voice can control Reading lamp Power', exact: true });
    await expect(power).toHaveAttribute('aria-checked', 'false');
    await power.click();
    await expect.poll(() => run.mocks.posts.find(
      (p) => decodeURIComponent(p.path) === '/api/homehub/entities/sensor.reading_lamp_power')?.body).toEqual({ voice: true });
    await expectFits(page, dialog);
    await snap(page, info, 'devices-detail-settings');

    await detail.getByLabel('Name', { exact: true }).fill('Chair lamp');
    await detail.getByTestId('section-settings').getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => run.mocks.posts.find((p) => p.path === '/api/homehub/devices/hw-lamp')?.body)
      .toEqual({ name_by_user: 'Chair lamp' });

    await detail.getByTestId('device-info').locator('summary').click();
    await detail.getByRole('button', { name: 'Rename Reading lamp', exact: true }).click();
    await detail.getByLabel('New name for Reading lamp').fill('Chair light');
    await detail.getByTestId('device-info').getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => run.mocks.posts.find(
      (p) => decodeURIComponent(p.path) === '/api/homehub/entities/light.reading_lamp' && (p.body as { name?: string }).name)?.body)
      .toEqual({ name: 'Chair light' });

    // Removing asks first.
    await detail.getByRole('button', { name: 'Remove device' }).click();
    const confirm = page.getByRole('dialog', { name: /Remove Reading lamp/ });
    await expect(confirm).toBeVisible();
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(run.mocks.posts.some((p) => p.path === '/api/homehub/devices/hw-lamp' && p.body === null)).toBe(false);
    await expectNoHorizontalOverflow(page);
    clean(run);
  });
});

/** The device's detail, its control, and the commands that reached the hub. */
async function kind(page: Page, name: string) {
  const { dialog, detail } = await openDevice(page, name);
  const control = detail.getByTestId('main-control');
  await expect(control.getByTestId('device-control')).toBeVisible();
  return { dialog, detail, control };
}

async function confirm(page: Page, button: string) {
  const ask = page.getByRole('dialog').filter({ has: page.getByRole('button', { name: 'Cancel' }) });
  await ask.getByRole('button', { name: button, exact: true }).click();
}

const sent = (run: Run, entity: string) => run.devices.commands.filter((c) => c.entity_id === entity);

test.describe('Devices: every kind gets its control', () => {
  test.describe.configure({ timeout: 120_000 });

  test('a TV has a remote, apps and volume', async ({ page }, info) => {
    const run = await open(page);
    const { dialog, control } = await kind(page, 'Den TV');
    const remote = control.getByTestId('tv-remote');
    await expect(remote.getByRole('button', { name: 'OK', exact: true })).toBeEnabled();
    await remote.getByRole('button', { name: 'Up', exact: true }).click();
    await expect.poll(() => sent(run, 'remote.den_tv')[0]).toMatchObject({ service: 'send_command', service_data: { command: 'DPAD_UP' } });
    await remote.getByRole('button', { name: 'Home', exact: true }).click();
    await expect.poll(() => sent(run, 'remote.den_tv').map((c) => c.service_data?.command)).toContain('HOME');
    await remote.getByRole('button', { name: 'Volume up' }).click();
    await expect.poll(() => sent(run, 'media_player.den_tv').map((c) => c.service)).toContain('volume_up');
    const apps = control.getByTestId('tv-apps');
    await expect(apps.getByRole('button', { name: 'Video app' })).toHaveAttribute('aria-pressed', 'true');
    await apps.getByRole('button', { name: 'Music app' }).click();
    await expect.poll(() => sent(run, 'remote.den_tv').find((c) => c.service === 'turn_on')?.service_data).toEqual({ activity: 'Music app' });
    await expectTapTargets(control);
    await expectFits(page, dialog);
    await snap(page, info, 'controls-tv-remote');
    clean(run);
  });

  test('a robot vacuum has its controls, suction, map, rooms and parts', async ({ page }, info) => {
    const run = await open(page);
    const { dialog, control } = await kind(page, 'Robot vacuum');
    await expect(control).toContainText('87%');
    await expect(control.getByTestId('vacuum-map').getByTestId('image-tile')).toBeVisible();
    await expect(control.getByTestId('vacuum-wear')).toContainText('Filter time left');
    await expect(control.getByTestId('vacuum-wear')).toContainText('42 h');
    await expect(control.getByTestId('vacuum-error')).toHaveCount(0);
    await control.getByRole('button', { name: 'Turbo' }).click();
    await expect.poll(() => sent(run, 'vacuum.robot_vacuum').find((c) => c.service === 'set_fan_speed')?.service_data)
      .toEqual({ fan_speed: 'turbo' });
    await control.getByRole('button', { name: 'Start Robot vacuum' }).click();
    await expect.poll(() => sent(run, 'vacuum.robot_vacuum').map((c) => c.service)).toContain('start');
    for (const b of ['Stop Robot vacuum', 'Send Robot vacuum to its dock', 'Find Robot vacuum']) {
      await expect(control.getByRole('button', { name: b })).toBeVisible();
    }
    await expect(control.getByTestId('vacuum-rooms')).toBeVisible();
    await expectTapTargets(control);
    await expectFits(page, dialog);
    await snap(page, info, 'controls-vacuum');
    clean(run);
  });

  test('a camera with its siren and floodlight; a thermostat; a fan', async ({ page }, info) => {
    const run = await open(page);
    let { dialog, control } = await kind(page, 'Porch camera');
    await control.getByRole('button', { name: 'Sound Siren' }).click();
    await confirm(page, 'Sound it');
    await expect.poll(() => sent(run, 'siren.porch_camera_siren')[0]?.service).toBe('turn_on');
    await control.getByRole('switch', { name: 'Floodlight on Porch camera' }).click();
    await expect.poll(() => sent(run, 'light.porch_camera_floodlight')[0]?.service).toBe('turn_on');
    await expectFits(page, dialog);
    await snap(page, info, 'controls-camera');
    await dialog.getByRole('button', { name: 'Close' }).click();

    ({ dialog, control } = await kind(page, 'Hall thermostat'));
    await expect(control).toContainText('71°');
    await expect(control).toContainText('41%');
    await expect(control.getByRole('button', { name: 'Heat and cool' })).toHaveAttribute('aria-pressed', 'true');
    await control.getByRole('button', { name: 'Raise cool to' }).click();
    await expect.poll(() => sent(run, 'climate.hall_thermostat').find((c) => c.service === 'set_temperature')?.service_data)
      .toEqual({ target_temp_low: 68, target_temp_high: 77 });
    await control.getByRole('group', { name: 'Fan' }).getByRole('button', { name: 'On' }).click();
    await expect.poll(() => sent(run, 'climate.hall_thermostat').some((c) => c.service === 'set_fan_mode')).toBe(true);
    await expectTapTargets(control);
    await expectFits(page, dialog);
    await snap(page, info, 'controls-thermostat');
    await dialog.getByRole('button', { name: 'Close' }).click();

    ({ dialog, control } = await kind(page, 'Office fan'));
    await expect(control.getByRole('slider', { name: 'Office fan speed' })).toHaveAttribute('aria-valuenow', '40');
    await control.getByRole('button', { name: /Swing/ }).click();
    await expect.poll(() => sent(run, 'fan.office_fan').find((c) => c.service === 'oscillate')?.service_data).toEqual({ oscillating: true });
    await control.getByRole('button', { name: 'Sleep' }).click();
    await expect.poll(() => sent(run, 'fan.office_fan').some((c) => c.service === 'set_preset_mode')).toBe(true);
    await expectFits(page, dialog);
    await snap(page, info, 'controls-fan');
    clean(run);
  });

  test('a garage door, a lock and a firmware update ask before they act', async ({ page }, info) => {
    const run = await open(page);
    let { dialog, control } = await kind(page, 'Garage door');
    await control.getByRole('button', { name: 'Open Garage door' }).click();
    await confirm(page, 'Open');
    await expect.poll(() => sent(run, 'cover.garage_door')[0]?.service).toBe('open_cover');
    await snap(page, info, 'controls-garage');
    await dialog.getByRole('button', { name: 'Close' }).click();

    ({ dialog, control } = await kind(page, 'Front door lock'));
    await control.getByRole('button', { name: 'Unlock Front door lock' }).click();
    await page.getByRole('dialog').filter({ has: page.getByRole('button', { name: 'Cancel' }) }).getByRole('button', { name: 'Cancel' }).click();
    expect(sent(run, 'lock.front_door')).toEqual([]);
    await control.getByRole('button', { name: 'Unlock Front door lock' }).click();
    await confirm(page, 'Unlock');
    await expect.poll(() => sent(run, 'lock.front_door')[0]?.service).toBe('unlock');
    await snap(page, info, 'controls-lock');
    await dialog.getByRole('button', { name: 'Close' }).click();

    let detail: Locator;
    ({ dialog, detail } = await kind(page, 'Desk bulb'));
    const fw = detail.getByTestId('firmware-control');
    await expect(fw).toContainText('2026.11.0 available (2026.10.1 installed)');
    await fw.getByRole('button', { name: /^Install 2026.11.0/ }).click();
    await confirm(page, 'Install');
    await expect.poll(() => sent(run, 'update.desk_bulb_firmware')[0]?.service).toBe('install');
    await fw.scrollIntoViewIfNeeded();
    await snap(page, info, 'controls-firmware');
    clean(run);
  });

  test('big sliders keep their labels clear of the fill and the marker at 0, 20, 50 and 100%', async ({ page }, info) => {
    const run = await open(page);
    const shot = async (loc: Locator, name: string) => {
      const dir = process.env.SNAP_DIR;
      await loc.screenshot({ path: dir ? `${dir}/${name}-${info.project.name}.png` : info.outputPath(`${name}.png`) });
    };
    /** The label row sits wholly above the bar, so nothing on the bar can cover it. */
    const clear = async (slider: Locator) => {
      const bar = await slider.boundingBox();
      const row = await slider.locator('xpath=preceding-sibling::div[1]').boundingBox();
      expect(row && bar && row.y + row.height <= bar.y + 0.5, 'label row above the bar').toBe(true);
    };
    const setTo = async (slider: Locator, keys: string[], want: string) => {
      await slider.focus();
      for (const k of keys) await page.keyboard.press(k);
      await expect(slider).toHaveAttribute('aria-valuenow', want);
      // Let the change be sent (the slider waits for a pause in key presses) and the answer drawn.
      await page.waitForTimeout(900);
      await expect(slider).toHaveAttribute('aria-valuenow', want);
    };
    let { dialog, control } = await kind(page, 'Desk bulb');
    const brightness = control.getByRole('slider', { name: 'Desk bulb brightness' });
    const white = control.getByRole('slider', { name: 'Desk bulb colour temperature' });
    for (const [keys, want, label] of [[['Home'], '1', '0'], [Array(19).fill('ArrowRight'), '20', '20'],
      [Array(3).fill('PageUp'), '50', '50'], [['End'], '100', '100']] as const) {
      await setTo(brightness, [...keys], want);
      await clear(brightness);
      await shot(brightness.locator('..'), `slider-brightness-${label}`);
    }
    for (const [key, label] of [['Home', 'warmest'], ['End', 'coolest']] as const) {
      await setTo(white, [key], key === 'Home' ? '2000' : '6535');
      await clear(white);
      await shot(white.locator('..'), `slider-white-${label}`);
    }
    await expect.poll(() => run.devices.commands.length).toBeGreaterThan(0);
    await dialog.getByRole('button', { name: 'Close' }).click();
    ({ dialog, control } = await kind(page, 'Den TV'));
    const volume = control.getByRole('slider', { name: 'Den TV volume' });
    for (const [keys, want] of [[['Home'], '0'], [Array(2).fill('PageUp'), '20'], [Array(3).fill('PageUp'), '50'], [['End'], '100']] as const) {
      await setTo(volume, [...keys], want);
      await clear(volume);
      await shot(volume.locator('..'), `slider-volume-${want}`);
    }
    clean(run);
  });

  test('the Rooms page opens the same controls from a name', async ({ page }, info) => {
    const run = await open(page);
    await page.goto('/rooms');
    await page.getByTestId('more-controls').filter({ hasText: 'Den TV' }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('tv-remote')).toBeVisible();
    await settled(page, dialog);
    await expectFits(page, dialog);
    await snap(page, info, 'controls-from-rooms');
    clean(run);
  });
});

test.describe('Devices: adding', () => {
  test.describe.configure({ timeout: 90_000 });

  test('an old #add-fix link opens Add devices', async ({ page }) => {
    await mockHome(page, 'admin');
    await page.goto('/devices#add-fix');
    await expect(page.getByRole('tab', { name: 'Add devices' })).toHaveAttribute('aria-selected', 'true');
  });

  test('a device at a new address is offered as an update; adding by hand is grouped', async ({ page }, info) => {
    const run = await open(page);
    await page.getByRole('tab', { name: 'Add devices' }).click();
    const moved = page.getByTestId('moved-device');
    await moved.scrollIntoViewIfNeeded();
    await expect(moved).toContainText('192.0.2.66 → 192.0.2.96');
    await expectTapTargets(page.getByTestId('moved-devices'));
    await moved.getByRole('button', { name: 'Use the new address for Living TV' }).click();
    await expect.poll(() => run.mocks.posts.some(
      (p) => decodeURIComponent(p.path) === '/api/homehub/discovered/roku:x00000000001/update-address'))
      .toBe(true);

    await page.getByRole('button', { name: /Add by address/ }).click();
    const picker = page.getByTestId('add-by-address');
    await expect(picker.getByText('Cloud accounts')).toBeVisible();
    await expect(picker.getByText('Helpers')).toBeVisible();
    await picker.getByRole('button', { name: 'Switch as another device' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel('Switch')).toBeVisible();
    await dialog.getByLabel('Switch').selectOption('switch.kettle');
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-add-helper');
    clean(run);
  });

  test('Common Sense Media is a cloud account; the abuse.ch key is not a hub integration', async ({ page }, info) => {
    const run = await open(page);
    await page.getByRole('tab', { name: 'Add devices' }).click();
    await page.getByRole('button', { name: /Add by address/ }).click();
    const picker = page.getByTestId('add-by-address');
    const cloud = picker.locator('div', { has: page.getByText('Cloud accounts', { exact: true }) }).last();
    const csm = cloud.getByRole('button', { name: 'Common Sense Media (app age ratings)' });
    await expect(csm).toBeVisible();
    await expect(picker.getByRole('button', { name: /MalwareBazaar|abuse\.ch/ })).toHaveCount(0);
    await expectTapTargets(picker);
    await expectNoHorizontalOverflow(page);
    await csm.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('An online account');
    await expect(dialog.getByLabel('Room (optional)')).toHaveCount(0); // an account has no room
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectTapTargets(dialog.getByTestId('adopt-dialog'));
    // The dialog's close button is 44px under a finger (pointer-coarse) and 36px under a mouse.
    if (info.project.use.hasTouch) await expectTapTargets(dialog);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-add-common-sense');
    clean(run);
  });
});
