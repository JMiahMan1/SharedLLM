import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { expectFits, expectLayoutSound, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > Rooms & places (rooms, floors, labels, zones), Setup > People, and labels on a device's sheet,
 * in every project (desktop, Pixel 7, a small phone, a tablet both ways round). Hermetic:
 * fixtures/homeMocks.ts with fixtures/registryMocks.ts; every name and coordinate is made up.
 */

async function open(page: Page, hash = ''): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`/devices${hash}`);
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors };
}

function clean({ mocks, errors }: { mocks: HomeMocks; errors: string[] }) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

function sent(mocks: HomeMocks, method: string, path: string) {
  return mocks.posts.filter((p) => decodeURIComponent(p.path) === path).map((p) => p.body).at(-1) ?? `${method} ${path} not sent`;
}

test.describe('Devices: rooms, floors, labels, zones and people', () => {
  test.describe.configure({ timeout: 90_000 });

  test('rooms sit on floors; a floor and a room are added, moved and renamed', async ({ page }, info) => {
    const run = await open(page);
    await page.getByRole('tab', { name: 'Rooms & places' }).click();
    const panel = page.getByTestId('areas');
    await expect(panel.getByTestId('area')).toHaveCount(2);
    await expect(panel.getByTestId('area-group').first()).toContainText('Upstairs');
    await expect(panel.getByTestId('area-group').first()).toContainText('Bedroom');
    await expectLayoutSound(page, info, 'devices-rooms-places');
    await expectTapTargets(panel);

    await panel.getByLabel('New floor name').fill('Basement');
    await panel.getByLabel('New floor level').fill('-1');
    await panel.getByRole('button', { name: 'Add floor' }).click();
    await expect(panel.getByTestId('floor')).toHaveCount(2);
    await expect.poll(() => sent(run.mocks, 'POST', '/api/homehub/floors')).toEqual({ name: 'Basement', level: -1 });

    await panel.getByLabel('New room name').fill('Workshop');
    await panel.getByLabel('Floor of the new room').selectOption('basement');
    await panel.getByRole('button', { name: 'Add room' }).click();
    await expect(panel.getByTestId('area')).toHaveCount(3);
    await expect.poll(() => sent(run.mocks, 'POST', '/api/homehub/areas')).toEqual({ name: 'Workshop', floor_id: 'basement' });

    await panel.getByLabel('Floor of Living room').selectOption('upstairs');
    await expect.poll(() => sent(run.mocks, 'PATCH', '/api/homehub/areas/living_room')).toEqual({ floor_id: 'upstairs' });
    await panel.getByRole('button', { name: 'Rename Bedroom' }).click();
    await panel.getByLabel('New name for Bedroom').fill('Guest room');
    await panel.getByRole('button', { name: 'Save' }).click();
    await expect(panel.getByText('Guest room')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-rooms-edited');

    await panel.getByRole('button', { name: 'Remove Guest room' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Remove' }).click();
    await expect(panel.getByTestId('area')).toHaveCount(2);
    clean(run);
  });

  test('labels are made, and worn by a device and an entity', async ({ page }, info) => {
    const run = await open(page, '#devices-labels');
    const labels = page.getByTestId('labels');
    await labels.getByLabel('New label name').fill('Kids');
    await labels.getByLabel('Colour of the new label').selectOption('teal');
    await labels.getByRole('button', { name: 'Add label' }).click();
    await expect(labels.getByTestId('label')).toHaveCount(2);
    await expect.poll(() => sent(run.mocks, 'POST', '/api/homehub/labels')).toEqual({ name: 'Kids', color: 'teal' });
    await expectTapTargets(labels);

    await page.getByRole('tab', { name: 'Your devices' }).click();
    await page.getByTestId('hardware').getByTestId('hardware-device').filter({ hasText: 'Reading lamp' }).click();
    const dialog = page.getByRole('dialog');
    const chips = dialog.getByRole('group', { name: /^Labels of Reading lamp with/ });
    await chips.getByRole('button', { name: 'Kids' }).click();
    await expect.poll(() => sent(run.mocks, 'PATCH', '/api/homehub/devices/hw-lamp')).toEqual({ labels: ['kids'] });
    await expectTapTargets(chips);
    await dialog.getByRole('button', { name: 'Labels of Reading lamp', exact: true }).click();
    await dialog.getByRole('group', { name: 'Labels of Reading lamp', exact: true }).getByRole('button', { name: 'Night lights' }).click();
    await expect.poll(() => sent(run.mocks, 'PATCH', '/api/homehub/entities/light.reading_lamp')).toEqual({ labels: ['night_lights'] });
    await expectFits(page, dialog);
    await snap(page, info, 'devices-labels-on-a-device');
    clean(run);
  });

  test('zones: a saved place is shown read-only; a zone is drawn, checked and added', async ({ page }, info) => {
    const run = await open(page, '#devices-zones');
    const zones = page.getByTestId('zones');
    await expect(zones.getByTestId('zone')).toHaveCount(2);
    const place = zones.getByTestId('zone').filter({ hasText: 'Practice field' });
    await expect(place).toContainText('Saved place in Jarvis');
    await expect(place.getByRole('button', { name: /Edit/ })).toHaveCount(0);

    await zones.getByRole('button', { name: 'New zone' }).click();
    const editor = zones.getByTestId('zone-editor');
    await editor.getByLabel('Name').fill('Ball park');
    await editor.getByLabel('Latitude').fill('12.5, -45.25');
    await expect(editor.getByLabel('Longitude')).toHaveValue('-45.25');
    await editor.getByLabel('Radius (metres)').fill('0');
    await editor.getByRole('button', { name: 'Add zone' }).click();
    await expect(editor.getByRole('alert')).toHaveText('The radius is 1 to 100000 metres.');
    await editor.getByLabel('Radius (metres)').fill('250');
    await expectTapTargets(editor);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-zone-editor');
    await editor.getByRole('button', { name: 'Add zone' }).click();
    await expect(zones.getByTestId('zone')).toHaveCount(3);
    await expect.poll(() => sent(run.mocks, 'POST', '/api/homehub/zones')).toEqual(
      { name: 'Ball park', latitude: 12.5, longitude: -45.25, radius: 250, passive: false });

    await zones.getByRole('button', { name: 'Saved places' }).click();
    await expect.poll(() => run.mocks.posts.some((p) => p.path === '/api/homehub/zones/sync')).toBe(true);
    await expectTapTargets(zones);
    clean(run);
  });

  test('people: a person is linked to a Jarvis user and to trackers no one else has', async ({ page }, info) => {
    const run = await open(page, '#devices-persons');
    const people = page.getByTestId('persons');
    await expect(people.getByTestId('person')).toHaveCount(1);
    await expect(people.getByTestId('person')).toContainText("Robin's Phone");
    await people.getByRole('button', { name: 'New person' }).click();
    const editor = people.getByTestId('person-editor');
    await editor.getByLabel('Name').fill('Sky');
    await editor.getByLabel('Jarvis user').fill('Sky');
    await expect(editor.getByRole('checkbox', { name: "Robin's Phone" })).toBeDisabled();
    await editor.getByRole('checkbox', { name: 'Sky tablet' }).check();
    await expectTapTargets(editor);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-people');
    await editor.getByRole('button', { name: 'Add person' }).click();
    await expect(people.getByTestId('person')).toHaveCount(2);
    await expect.poll(() => sent(run.mocks, 'POST', '/api/homehub/persons')).toEqual(
      { name: 'Sky', jarvis_user: 'sky', device_trackers: ['device_tracker.sky_tablet'] });

    await people.getByRole('button', { name: 'Edit Robin' }).click();
    await people.getByTestId('person-editor').getByRole('checkbox', { name: "Robin's Phone" }).uncheck();
    await people.getByTestId('person-editor').getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => sent(run.mocks, 'PATCH', '/api/homehub/persons/p1')).toEqual(
      { name: 'Robin', jarvis_user: 'robin', device_trackers: [] });
    clean(run);
  });
});
