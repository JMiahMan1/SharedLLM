import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { HARDWARE, mockDeviceStates } from '../fixtures/hardwareMocks';
import { expectClearOfBottomBar, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > Your devices: duplicates (the same bulb seen through Matter and WiZ), hiding one
 * copy per device or every copy of one integration, and the folded "Hidden" list with Show.
 * Every project (desktop, phones, tablets). Hermetic: the hub is a small stateful mock below;
 * every name and address is made up.
 */

type Copy = { id: string; name: string; domain: string; label: string; area: string; mac?: string; entities: string[] };

const COPIES: Copy[] = [
  { id: 'mt-1', name: 'Pantry Lamp 1', domain: 'matter', label: 'Matter', area: 'Kitchen', entities: ['light.pantry_lamp_1'] },
  { id: 'wz-1', name: 'Pantry Lamp 1', domain: 'wiz', label: 'WiZ', area: 'Kitchen', mac: '02:00:00:00:00:61',
    entities: ['light.pantry_lamp_1_wiz'] },
  { id: 'mt-2', name: 'Pantry Lamp 2 with a long name for small phones', domain: 'matter', label: 'Matter',
    area: 'Kitchen', entities: ['light.pantry_lamp_2'] },
  { id: 'wz-2', name: 'Pantry Lamp 2 with a long name for small phones', domain: 'wiz', label: 'WiZ', area: 'Kitchen',
    mac: '02:00:00:00:00:62', entities: ['light.pantry_lamp_2_wiz'] },
];
const GROUPS = [['mt-1', 'wz-1'], ['mt-2', 'wz-2']];

type Run = { mocks: HomeMocks; hidden: Set<string>; calls: { method: string; path: string; body: unknown }[]; errors: string[] };

function duplicates(hidden: Set<string>) {
  const row = (c: Copy) => ({
    id: c.id, name: c.name, manufacturer: 'Example Lighting', model: 'A19', domain: c.domain, label: c.label,
    entry_id: `e-${c.domain}`, entry_title: c.label, area_id: 'kitchen', area: c.area, entity_ids: c.entities,
    hidden: hidden.has(c.id), hidden_by: hidden.has(c.id) ? 'user' : null,
    last_updated: new Date(Date.now() - 4 * 60_000).toISOString(),
  });
  const groups = GROUPS.map((ids) => {
    const devices = ids.map((id) => row(COPIES.find((c) => c.id === id)!));
    return { id: ids[0], match: 'name', name: devices[0].name, area: 'Kitchen', domains: ['matter', 'wiz'], devices };
  });
  const integrations = [['matter', 'Matter'], ['wiz', 'WiZ']].map(([domain, label]) => {
    const mine = COPIES.filter((c) => c.domain === domain);
    return { domain, label, groups: 2, devices: mine.length, hidden: mine.filter((c) => hidden.has(c.id)).length };
  });
  return { groups, integrations };
}

function hardware(hidden: Set<string>) {
  const base = HARDWARE.devices.find((d) => d.id === 'hw-lamp')!;
  const extra = COPIES.map((c) => ({
    ...base, id: c.id, name: c.name, original_name: c.name, domain: c.domain, label: c.label, area_id: 'kitchen',
    area: c.area, entry_id: `e-${c.domain}`, entry_title: c.label, kind: 'light',
    connections: c.mac ? [{ type: 'mac', value: c.mac }] : [],
    group_id: GROUPS.find((g) => g.includes(c.id))![1], group_primary: c.domain === 'wiz',
    hidden: hidden.has(c.id), hidden_by: hidden.has(c.id) ? 'user' : null,
  }));
  return { ...HARDWARE, devices: [...HARDWARE.devices, ...extra] };
}

async function open(page: Page): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  await mockDeviceStates(page);
  const hidden = new Set<string>();
  const calls: Run['calls'] = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const body = (route: Route) => { try { return route.request().postDataJSON(); } catch { return null; } };
  // Registered after mockHome, so these win.
  await page.route('**/api/homehub/hardware', (route) => route.fulfill({ json: hardware(hidden) }));
  await page.route('**/api/homehub/devices/duplicates', (route) => route.fulfill({ json: duplicates(hidden) }));
  await page.route(/\/api\/homehub\/devices\/duplicates\/(hide|unhide)$/, (route) => {
    const path = new URL(route.request().url()).pathname;
    const b = body(route) as { hide_domain?: string; domain?: string };
    calls.push({ method: 'POST', path, body: b });
    const domain = path.endsWith('/hide') ? b.hide_domain : b.domain;
    const changed = COPIES.filter((c) => c.domain === domain && hidden.has(c.id) !== path.endsWith('/hide'));
    for (const c of changed) {
      if (path.endsWith('/hide')) hidden.add(c.id); else hidden.delete(c.id);
    }
    return route.fulfill({ json: { changed: changed.map((c) => c.id), ...duplicates(hidden) } });
  });
  await page.route(/\/api\/homehub\/devices\/(mt|wz)-\d$/, (route) => {
    const path = new URL(route.request().url()).pathname;
    const id = path.split('/').pop()!;
    const b = body(route) as { hidden: boolean };
    calls.push({ method: route.request().method(), path, body: b });
    if (b.hidden) hidden.add(id); else hidden.delete(id);
    return route.fulfill({ json: { id, name: id, hidden: b.hidden, hidden_by: b.hidden ? 'user' : null } });
  });
  await page.goto('/devices');
  await expect(page.getByTestId('page-header')).toBeVisible();
  await expect(page.getByTestId('duplicates')).toBeVisible();
  return { mocks, hidden, calls, errors };
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

async function layout(page: Page) {
  await expectNoHorizontalOverflow(page);
  await expectTapTargets(page.getByTestId('duplicates'));
  await expectClearOfBottomBar(page);
}

test.describe('Devices: duplicates', () => {
  test.describe.configure({ timeout: 90_000 });

  test('each group says where each copy comes from, and one copy hides and shows again', async ({ page }, info) => {
    const run = await open(page);
    const panel = page.getByTestId('duplicates');
    await expect(panel.getByTestId('duplicate-group')).toHaveCount(2);
    const first = panel.getByTestId('duplicate-group').first();
    await expect(first.getByRole('heading', { name: 'Pantry Lamp 1' })).toBeVisible();
    await expect(first).toContainText('Same name in Kitchen');
    await expect(first.getByTestId('duplicate-copy')).toHaveText([/Matter.*1 entity/, /WiZ.*1 entity/]);
    // The two copies of a lamp are one tile, as before.
    await expect(page.getByTestId('device-tile').filter({ hasText: 'Pantry Lamp 1' })).toHaveCount(1);
    await page.getByTestId('duplicates').scrollIntoViewIfNeeded();
    await layout(page);
    await snap(page, info, 'devices-duplicates');

    await first.getByRole('button', { name: 'Hide the Matter copy of Pantry Lamp 1' }).click();
    await expect(first.getByTestId('duplicate-copy').first()).toHaveAttribute('data-hidden', 'true');
    await expect(first.getByTestId('duplicate-copy').first()).toContainText('Hidden');
    expect(run.calls).toEqual([{ method: 'PATCH', path: '/api/homehub/devices/mt-1', body: { hidden: true } }]);
    // Still one tile (the WiZ copy shows), and the hidden copy waits, folded, at the end of the list.
    await expect(page.getByTestId('device-tile').filter({ hasText: 'Pantry Lamp 1' })).toHaveCount(1);
    const folded = page.getByTestId('hardware-hidden');
    await expect(folded).toContainText('Hidden (1)');
    await folded.locator('summary').click();
    await expect(folded.getByTestId('hidden-device')).toHaveText([/Pantry Lamp 1.*Matter/]);
    await expectTapTargets(folded);
    await folded.scrollIntoViewIfNeeded();
    await snap(page, info, 'devices-hidden-list');
    await expectNoHorizontalOverflow(page);
    await expectClearOfBottomBar(page);

    await folded.getByRole('button', { name: 'Show Pantry Lamp 1 (Matter)' }).click();
    await expect(folded).toHaveCount(0);
    await expect(first.getByTestId('duplicate-copy').first()).toHaveAttribute('data-hidden', 'false');
    expect(run.calls.at(-1)).toEqual({ method: 'PATCH', path: '/api/homehub/devices/mt-1', body: { hidden: false } });
    clean(run);
  });

  test('hide every copy from one integration, then show them all again', async ({ page }, info) => {
    const run = await open(page);
    const panel = page.getByTestId('duplicates');
    const bulk = panel.getByTestId('duplicates-bulk');
    await expect(bulk.getByRole('button')).toHaveText(['Hide all Matter copies', 'Hide all WiZ copies']);
    await bulk.getByRole('button', { name: 'Hide all Matter copies' }).click();
    await expect(panel.locator('[data-testid="duplicate-copy"][data-hidden="true"]')).toHaveCount(2);
    expect(run.calls).toEqual([{ method: 'POST', path: '/api/homehub/devices/duplicates/hide', body: { hide_domain: 'matter' } }]);
    await expect(bulk.getByRole('button')).toHaveText(['Show all Matter copies', 'Hide all WiZ copies']);
    await expect(page.getByTestId('hardware-hidden')).toContainText('Hidden (2)');
    await page.getByTestId('duplicates').scrollIntoViewIfNeeded();
    await layout(page);
    await snap(page, info, 'devices-duplicates-hidden');

    await bulk.getByRole('button', { name: 'Show all Matter copies' }).click();
    await expect(panel.locator('[data-testid="duplicate-copy"][data-hidden="true"]')).toHaveCount(0);
    expect(run.calls.at(-1)).toEqual({ method: 'POST', path: '/api/homehub/devices/duplicates/unhide', body: { domain: 'matter' } });
    await expect(page.getByTestId('hardware-hidden')).toHaveCount(0);
    clean(run);
  });
});
