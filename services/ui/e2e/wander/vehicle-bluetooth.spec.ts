import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Wander's vehicles with their linked Bluetooth devices, and a trip that says
 * how its vehicle was decided, on every form factor (phone, small phone,
 * tablet both ways, desktop). The linking itself needs the Android app's
 * Bluetooth; the browser shows where to do it instead.
 */

async function open(page: Page): Promise<string[]> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/wander');
  await expect(page.getByTestId('trip-card').first()).toBeVisible();
  return errors;
}

test('the vehicles panel lists each car with its linked devices and fits the screen', async ({ page }, info) => {
  const errors = await open(page);
  const panel = page.getByTestId('vehicle-bt-panel');
  await panel.scrollIntoViewIfNeeded();
  await expect(panel).toBeVisible();
  const van = panel.getByTestId('vehicle-bt-v1');
  await expect(van.getByText('Uconnect Infotainment Head Unit With A Long Name')).toBeVisible();
  await expect(van.getByText('...DA:71:13')).toBeVisible();
  // A link with no name shows its address instead.
  await expect(van.getByText('Device ...12:34:56')).toBeVisible();
  await expect(panel.getByTestId('vehicle-bt-v2')).toContainText('No Bluetooth linked yet');
  // The browser cannot read the phone's Bluetooth: it says where linking happens.
  await expect(panel).toContainText('from the Jarvis app on a phone');
  await expect(panel.getByRole('button', { name: /Link a device/ })).toHaveCount(0);
  // A parent may remove any link.
  await expect(van.getByRole('button', { name: /Unlink/ })).toHaveCount(2);

  await settle(page);
  expect(await screenFindings(page, panel, 'vehicles panel')).toEqual([]);
  await shot(page, info, 'wander-vehicles');
  expect(errors).toEqual([]);
});

test('a trip says Bluetooth chose its car, and a hand-picked one can go back to it', async ({ page }, info) => {
  const errors = await open(page);
  const patches: unknown[] = [];
  await page.route('**/api/geo/trips/t1', async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback();
    patches.push(route.request().postDataJSON());
    return route.fulfill({ json: { id: 't1', user_id: 'parent', vehicle_id: 'v2', vehicle_name: 'Hatchback', vehicle_source: 'bluetooth', bt_vehicle_id: 'v2' } });
  });

  const first = page.getByTestId('trip-card').first();
  await expect(first.getByTestId('trip-vehicle-source')).toHaveText('Chosen by hand');
  await expect(page.getByTestId('trip-vehicle-source').filter({ hasText: 'Detected by Bluetooth' }).first()).toBeVisible();

  await first.getByRole('button', { name: /edit/i }).first().click();
  const dialog = page.getByRole('dialog').last();
  const detected = dialog.getByTestId('trip-vehicle-detected');
  await expect(detected).toContainText('Bluetooth saw Hatchback');
  await settle(page);
  expect(await screenFindings(page, dialog, 'edit trip')).toEqual([]);
  await shot(page, info, 'wander-trip-detected');

  await detected.getByRole('button', { name: 'Use the detected car' }).click();
  await expect.poll(() => patches).toEqual([{ vehicle_auto: true }]);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('a trip taken together names the person and the car, and a nearby member says how near', async ({ page }, info) => {
  const errors = await open(page);
  const together = page.getByTestId('trip-together').first();
  await together.scrollIntoViewIfNeeded();
  await expect(together).toHaveText(/^with Parent in the Family minivan with the roof box$/);
  await expect(page.getByTestId('member-near').first()).toHaveText('In the same room');
  // No metres anywhere: bands only.
  await expect(page.locator('main')).not.toContainText(/\d+(\.\d+)? ?m away/);
  await settle(page);
  // The map credit Leaflet draws in its corner is an attribution, not a control (as in the family audit).
  const leaflet = [{ where: /wander/, finding: /under 44px: (Leaflet|OpenStreetMap)\b/, why: 'map attribution' }];
  expect(await screenFindings(page, page.locator('main'), 'wander together', leaflet)).toEqual([]);
  await shot(page, info, 'wander-together');
  expect(errors).toEqual([]);
});
