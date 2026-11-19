import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks, type Role } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { isServerCall, INTEGRATIONS_STATUS_PATH, integrationsStatus } from '../fixtures/serverCalls';
import { expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';
import { quietHotReload } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Who may change the family's places, as Wander > Places shows it, in every project (desktop, Pixel 7, a
 * small phone, a tablet both ways round). Every place lives on the home hub: an admin sees the controls for
 * the household's places and a link to Devices > Rooms & places (where Home moves); a member sees none of
 * them, only edit and remove on their own saved place; with no home hub there are no places at all.
 * Hermetic: fixtures/familyPagesMocks.ts, every name and coordinate made up.
 */

const KID_PLACES = [
  { id: 'k1', entity_id: 'zone.wander_skate_park', name: 'Skate park', lat: 39.73, lon: -104.96, radius: 120,
    scope: 'family', owner: 'kid', important: false, notify: [] },
  { id: 'p1', entity_id: 'zone.wander_grandmas', name: 'Farmhouse', lat: 39.8, lon: -105.1, radius: 150,
    scope: 'family', owner: 'parent', important: true, notify: [] },
];

async function open(page: Page, role: Role, { hub = true }: { hub?: boolean } = {}): Promise<{ mocks: HomeMocks; errors: string[] }> {
  await quietHotReload(page);
  const mocks = await mockHome(page, role);
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await page.route(isServerCall, async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === INTEGRATIONS_STATUS_PATH) return route.fulfill({ json: integrationsStatus(hub, false) });
    if (p === '/api/geo/saved-places' && role === 'member') return route.fulfill({ json: { places: KID_PLACES } });
    if (p === '/api/geo/saved-places' && !hub) {
      return route.fulfill({ status: 503, json: { detail: 'HomeHub is not set up.' } });
    }
    return route.fallback();
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/wander');
  await expect(page.getByTestId('places-panel')).toBeVisible();
  return { mocks, errors };
}

test.describe('Places: who may add, change and remove them', () => {
  test.describe.configure({ timeout: 90_000 });

  test('an admin has the controls and a link to where Home and the household zones are drawn', async ({ page }, info) => {
    const run = await open(page, 'admin');
    const panel = page.getByTestId('places-panel');
    await panel.scrollIntoViewIfNeeded();
    const help = panel.getByTestId('places-help');
    await expect(help).not.toContainText('Home Assistant');
    const link = help.getByRole('link', { name: /Devices > Rooms & places/ });
    await expect(link).toHaveAttribute('href', '/devices#devices-zones');
    const school = panel.getByTestId('place-zone.school');
    await expect(school.getByRole('switch')).toBeVisible();
    await expect(panel.getByRole('button', { name: /^Edit Grandma/ })).toBeVisible();
    await expect(panel.getByRole('button', { name: /^Remove Grandma/ })).toBeVisible();
    await expectTapTargets(panel);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'wander-places-admin');

    // The link opens Devices on Rooms & places, where Home is moved.
    await link.click();
    await expect(page).toHaveURL(/\/devices#devices-zones$/);
    const home = page.getByTestId('zones').getByTestId('zone-home');
    await expect(home.getByRole('button', { name: 'Edit Home' })).toBeVisible();
    await expect(home.getByRole('button', { name: 'Remove Home' })).toHaveCount(0);
    await snap(page, info, 'wander-places-admin-to-zones');
    expect(run.errors, run.errors.join(' | ')).toEqual([]);
  });

  test('a member sees no controls for the household places, only for their own saved place', async ({ page }, info) => {
    const run = await open(page, 'member');
    const panel = page.getByTestId('places-panel');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByTestId('places-help').getByRole('link')).toHaveCount(0);
    const school = panel.getByTestId('place-zone.school');
    await expect(school).toBeVisible();
    await expect(school.getByRole('switch')).toHaveCount(0);
    await expect(school.getByRole('combobox')).toHaveCount(0);
    // Their own place: edit and remove. Someone else's: neither (hidden, not disabled).
    await expect(panel.getByRole('button', { name: 'Edit Skate park' })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Remove Skate park' })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Edit Farmhouse' })).toHaveCount(0);
    await expect(panel.getByRole('button', { name: 'Remove Farmhouse' })).toHaveCount(0);
    await expect(panel.getByTestId('add-place')).toBeVisible();
    await expectTapTargets(panel);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'wander-places-member');
    expect(run.errors, run.errors.join(' | ')).toEqual([]);
  });

  test('with no home hub there are no places, and an admin is told where to connect one', async ({ page }, info) => {
    const run = await open(page, 'admin', { hub: false });
    const notice = page.getByTestId('places-need-hub');
    await notice.scrollIntoViewIfNeeded();
    await expect(notice).toContainText('Places live on the home hub');
    await expect(notice.getByRole('link')).toHaveAttribute('href', '/identity');
    await expect(page.getByTestId('add-place')).toHaveCount(0);
    await expect(page.getByTestId('places-panel').getByRole('switch')).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'wander-places-no-hub');
    expect(run.errors, run.errors.join(' | ')).toEqual([]);
  });
});
