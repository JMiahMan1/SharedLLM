import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > Setup > Archived devices (services/homehub/device_archive.py and services/identity/device_archive.py):
 * devices that went without contact, with when each was last seen, archived and will be deleted; Restore, Delete
 * now (after confirming) and the two thresholds. Every project (desktop, phones, tablets). Hermetic: every device,
 * name and owner here is made up.
 */

const DAY = 86_400_000;
const iso = (days: number) => new Date(Date.now() + days * DAY).toISOString();
const SETTINGS = { enabled: true, archive_after_days: 90, delete_after_days: 365 };

const HUB_ITEMS = [
  { kind: 'entry', id: 'e-plug', name: 'Garage plug', label: 'Tasmota', domain: 'tasmota', area: 'Garage',
    last_seen: iso(-130), archived_at: iso(-40), delete_on: iso(325), entities: 2 },
  { kind: 'device', id: 'd-node', name: 'A Matter night light with a rather long name for small phones',
    label: 'Matter', domain: 'matter', area: 'Hallway', last_seen: iso(-95), archived_at: iso(-5),
    delete_on: iso(360), entities: 1 },
];
const IDENTITY_ITEMS = [
  { kind: 'identity', id: 'tab-1', name: 'Old tablet', device_kind: 'tablet', owner: 'sky',
    last_seen: iso(-300), archived_at: iso(-210), delete_on: iso(155) },
];

type Run = { mocks: HomeMocks; errors: string[]; calls: Array<{ method: string; path: string; body: unknown }> };

async function open(page: Page): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  const calls: Run['calls'] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  let hub = structuredClone(HUB_ITEMS);
  let identity = structuredClone(IDENTITY_ITEMS);
  let settings = { ...SETTINGS };
  const note = (route: Route) => {
    const r = route.request();
    calls.push({ method: r.method(), path: new URL(r.url()).pathname, body: r.postData() ? r.postDataJSON() : null });
  };
  await page.route(/\/api\/homehub\/archive(\/.*)?$/, async (route) => {
    note(route);
    const r = route.request();
    const path = new URL(r.url()).pathname;
    if (path === '/api/homehub/archive') return route.fulfill({ json: { settings, items: hub, audit: [] } });
    if (path === '/api/homehub/archive/settings') {
      settings = { ...settings, ...(r.postDataJSON() as object) };
      return route.fulfill({ json: { settings } });
    }
    const { id } = r.postDataJSON() as { id: string };
    hub = hub.filter((i) => i.id !== id);
    return route.fulfill({ json: { status: path.endsWith('restore') ? 'restored' : 'deleted', id } });
  });
  await page.route(/\/api\/user-panel\/devices\/archive(\/.*)?$/, async (route) => {
    note(route);
    const r = route.request();
    const path = new URL(r.url()).pathname;
    if (path === '/api/user-panel/devices/archive') return route.fulfill({ json: { settings, items: identity } });
    if (path.endsWith('/settings')) return route.fulfill({ json: { settings } });
    const id = decodeURIComponent(path.split('/')[5]);
    identity = identity.filter((i) => i.id !== id);
    return route.fulfill({ json: { status: r.method() === 'DELETE' ? 'deleted' : 'restored', id } });
  });
  await page.goto('/devices#devices-archived');
  await expect(page.getByRole('tab', { name: 'Setup' })).toHaveAttribute('aria-selected', 'true');
  return { mocks, errors, calls };
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('Devices: archived devices', () => {
  test.describe.configure({ timeout: 90_000 });

  test('lists both sources with last seen, archived since and deletion; restores and deletes', async ({ page }, info) => {
    const run = await open(page);
    const panel = page.getByTestId('archived-devices');
    await panel.scrollIntoViewIfNeeded();
    const rows = panel.getByTestId('archived-device');
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText('A Matter night light');
    await expect(rows.nth(1)).toContainText('Garage plug');
    await expect(rows.nth(1)).toContainText('Tasmota · Garage');
    await expect(rows.nth(1).getByTestId('archived-last-seen')).toContainText('130 d ago');
    await expect(rows.nth(1).getByTestId('archived-deletion')).toContainText('unless it comes back');
    await expect(rows.nth(2)).toContainText("Tablet, sky's");
    await expect(panel).toContainText('3 archived');
    await expectTapTargets(panel);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-archived-list');

    await panel.getByRole('button', { name: 'Restore Garage plug' }).click();
    await expect(rows).toHaveCount(2);
    expect(run.calls.find((c) => c.path === '/api/homehub/archive/restore')?.body).toEqual({ kind: 'entry', id: 'e-plug' });

    await panel.getByRole('button', { name: 'Delete Old tablet now' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Delete Old tablet?');
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-archived-delete');
    await dialog.getByRole('button', { name: 'Delete' }).click();
    await expect(rows).toHaveCount(1);
    expect(run.calls.some((c) => c.method === 'DELETE' && c.path === '/api/user-panel/devices/archive/tab-1')).toBe(true);
    clean(run);
  });

  test('the thresholds are saved for both sources and checked first', async ({ page }, info) => {
    const run = await open(page);
    const form = page.getByTestId('archive-settings');
    await form.scrollIntoViewIfNeeded();
    const archiveAfter = form.getByLabel('Archive after days');
    await archiveAfter.fill('3');
    await expect(form.getByRole('alert')).toContainText('7 to 3650');
    await expect(form.getByRole('button', { name: 'Save' })).toBeDisabled();
    await archiveAfter.fill('120');
    await form.getByLabel('Delete after days').fill('400');
    await expectTapTargets(form);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-archived-settings');
    await form.getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => run.calls.filter((c) => c.method === 'PUT').map((c) => c.path).sort()).toEqual(
      ['/api/homehub/archive/settings', '/api/user-panel/devices/archive/settings']);
    expect(run.calls.find((c) => c.method === 'PUT')?.body).toEqual(
      { archive_after_days: 120, delete_after_days: 400, enabled: true });
    clean(run);
  });
});
