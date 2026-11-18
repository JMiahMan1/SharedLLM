import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { HARDWARE, mockDeviceStates } from '../fixtures/hardwareMocks';
import { expectClearOfBottomBar, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > Your devices: duplicates (the same bulb seen through Matter and WiZ), hiding one
 * copy per device or every copy of one integration, the folded "Hidden" list with Show, and
 * combining two copies into one device with a main and a fallback (swap, uncombine, the rule).
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

/** Combined pairs: main id -> fallback id. */
type Combos = Map<string, string>;

function duplicates(hidden: Set<string>, combos: Combos = new Map(), rule: string | null = null) {
  const row = (c: Copy) => ({
    id: c.id, name: c.name, manufacturer: 'Example Lighting', model: 'A19', domain: c.domain, label: c.label,
    entry_id: `e-${c.domain}`, entry_title: c.label, area_id: 'kitchen', area: c.area, entity_ids: c.entities,
    hidden: hidden.has(c.id), hidden_by: hidden.has(c.id) ? 'user' : null,
    last_updated: new Date(Date.now() - 4 * 60_000).toISOString(),
  });
  const label = (id: string) => COPIES.find((c) => c.id === id)!;
  const groups = GROUPS.map((ids) => {
    const main = ids.find((id) => combos.has(id));
    const fallback = main ? combos.get(main)! : null;
    const devices = ids.map((id) => {
      const r = row(label(id));
      if (!main) return { ...r, role: null };
      const isFallback = id === fallback;
      return { ...r, role: isFallback ? 'fallback' : 'main', hidden: isFallback, hidden_by: isFallback ? 'combined' : null };
    });
    const combined = main && fallback ? {
      main, fallback, main_domain: label(main).domain, main_label: label(main).label,
      fallback_domain: label(fallback).domain, fallback_label: label(fallback).label,
      entities: [{ main: label(main).entities[0], fallback: label(fallback).entities[0] }],
      main_available: main !== 'mt-2', fallback_available: true, using: main === 'mt-2' ? 'fallback' : 'main',
    } : null;
    return { id: ids[0], match: 'name', name: devices[0].name, area: 'Kitchen',
      domains: ['matter', 'wiz'], devices, combined, can_combine: !combined, suggested_main: rule };
  });
  // Like the hub: a combined group is left out (the combination decides which copy shows).
  const open = GROUPS.filter((g) => !g.some((id) => combos.has(id)));
  const integrations = open.length === 0 ? [] : [['matter', 'Matter'], ['wiz', 'WiZ']].map(([domain, lbl]) => {
    const mine = COPIES.filter((c) => c.domain === domain && open.some((g) => g.includes(c.id)));
    return { domain, label: lbl, groups: open.length, devices: mine.length, hidden: mine.filter((c) => hidden.has(c.id)).length };
  });
  const rules = rule ? [{ domains: ['matter', 'wiz'], main_domain: rule, main_label: rule === 'matter' ? 'Matter' : 'WiZ',
    fallback_domain: rule === 'matter' ? 'wiz' : 'matter', fallback_label: rule === 'matter' ? 'WiZ' : 'Matter' }] : [];
  return { groups, integrations, rules };
}

function hardware(hidden: Set<string>, combos: Combos = new Map()) {
  const fallbacks = new Set(combos.values());
  const base = HARDWARE.devices.find((d) => d.id === 'hw-lamp')!;
  const extra = COPIES.map((c) => ({
    ...base, id: c.id, name: c.name, original_name: c.name, domain: c.domain, label: c.label, area_id: 'kitchen',
    area: c.area, entry_id: `e-${c.domain}`, entry_title: c.label, kind: 'light',
    connections: c.mac ? [{ type: 'mac', value: c.mac }] : [],
    group_id: GROUPS.find((g) => g.includes(c.id))![1], group_primary: c.domain === 'wiz',
    hidden: hidden.has(c.id) || fallbacks.has(c.id),
    hidden_by: fallbacks.has(c.id) ? 'combined' : hidden.has(c.id) ? 'user' : null,
  }));
  return { ...HARDWARE, devices: [...HARDWARE.devices, ...extra] };
}

async function open(page: Page): Promise<Run & { combos: Combos }> {
  const mocks = await mockHome(page, 'admin');
  await mockDeviceStates(page);
  const hidden = new Set<string>();
  const combos: Combos = new Map();
  let rule: string | null = null;
  const calls: Run['calls'] = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const body = (route: Route) => { try { return route.request().postDataJSON(); } catch { return null; } };
  // Registered after mockHome, so these win.
  await page.route('**/api/homehub/hardware', (route) => route.fulfill({ json: hardware(hidden, combos) }));
  await page.route('**/api/homehub/devices/duplicates', (route) => route.fulfill({ json: duplicates(hidden, combos, rule) }));
  await page.route(/\/api\/homehub\/devices\/duplicates\/(combine|uncombine|swap|rule)$/, (route) => {
    const path = new URL(route.request().url()).pathname;
    const b = (body(route) ?? {}) as Record<string, unknown>;
    calls.push({ method: 'POST', path, body: b });
    const action = path.split('/').pop();
    const pairOf = (id: string) => GROUPS.find((g) => g.includes(id))!;
    const mainOf = (id: string) => pairOf(id).find((x) => combos.has(x))!;
    if (action === 'combine' && b.main) {
      combos.set(String(b.main), String(b.fallback));
    } else if (action === 'combine') {
      rule = String(b.main_domain);
      for (const g of GROUPS) {
        if (g.some((x) => combos.has(x))) continue;
        const main = g.find((x) => COPIES.find((c) => c.id === x)!.domain === b.main_domain)!;
        combos.set(main, g.find((x) => x !== main)!);
      }
    } else if (action === 'uncombine') {
      combos.delete(mainOf(String(b.device)));
    } else if (action === 'swap') {
      const main = mainOf(String(b.device));
      const fallback = combos.get(main)!;
      combos.delete(main);
      combos.set(fallback, main);
    } else if (action === 'rule') {
      rule = null;
    }
    return route.fulfill({ json: { combined: [...combos.keys()], skipped: [], ...duplicates(hidden, combos, rule) } });
  });
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
  return { mocks, hidden, calls, errors, combos };
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

  test('combine a pair with Matter as the main, swap it, uncombine it, then combine all like this', async ({ page }, info) => {
    const run = await open(page);
    const panel = page.getByTestId('duplicates');
    const first = panel.getByTestId('duplicate-group').first();
    const choice = first.getByTestId('combine-choice');
    // Nothing is hidden, so the first copy is offered as the main; the person picks it explicitly.
    await choice.getByRole('radio', { name: 'Matter as the main for Pantry Lamp 1' }).click();
    await expect(choice.getByTestId('combine-preview')).toHaveText('Matter first, WiZ as the fallback.');
    await page.getByTestId('duplicates').scrollIntoViewIfNeeded();
    await layout(page);
    await snap(page, info, 'devices-duplicates-combine-choice');

    await choice.getByRole('button', { name: 'Combine Pantry Lamp 1' }).click();
    const combined = first.getByTestId('combined');
    await expect(combined).toContainText('Main: Matter, fallback: WiZ');
    await expect(combined.getByTestId('combined-status')).toHaveText('Using Matter');
    await expect(first.getByTestId('duplicate-copy')).toHaveText([/Matter.*Main/, /WiZ.*Fallback/]);
    await expect(first.getByRole('button', { name: /Hide the/ })).toHaveCount(0);
    expect(run.calls).toEqual([{ method: 'POST', path: '/api/homehub/devices/duplicates/combine',
      body: { main: 'mt-1', fallback: 'wz-1' } }]);
    // The fallback is not in the folded Hidden list (Uncombine brings it back), and the lamp is one tile.
    await expect(page.getByTestId('hardware-hidden')).toHaveCount(0);
    await expect(page.getByTestId('device-tile').filter({ hasText: 'Pantry Lamp 1' })).toHaveCount(1);
    await first.scrollIntoViewIfNeeded();
    await layout(page);
    await snap(page, info, 'devices-duplicates-combined');

    await combined.getByRole('button', { name: 'Make WiZ the main for Pantry Lamp 1' }).click();
    await expect(combined).toContainText('Main: WiZ, fallback: Matter');
    expect(run.calls.at(-1)).toEqual({ method: 'POST', path: '/api/homehub/devices/duplicates/swap', body: { device: 'mt-1' } });
    await combined.getByRole('button', { name: 'Uncombine Pantry Lamp 1' }).click();
    await expect(first.getByTestId('combine-choice')).toBeVisible();
    expect(run.calls.at(-1)).toEqual({ method: 'POST', path: '/api/homehub/devices/duplicates/uncombine', body: { device: 'wz-1' } });

    // Combine all like this: every pair, Matter first, and the household rule says so.
    const second = panel.getByTestId('duplicate-group').nth(1);
    await second.getByRole('radio', { name: /^Matter as the main/ }).click();
    await second.getByRole('button', { name: 'Combine all Matter and WiZ copies with Matter first' }).click();
    await expect(panel.locator('[data-testid="duplicate-group"][data-combined="true"]')).toHaveCount(2);
    expect(run.calls.at(-1)).toEqual({ method: 'POST', path: '/api/homehub/devices/duplicates/combine',
      body: { main_domain: 'matter', fallback_domain: 'wiz' } });
    await expect(panel.getByTestId('duplicates-rule')).toContainText('Household rule: prefer Matter, fall back to WiZ.');
    // The second lamp's Matter copy is not answering: the panel says WiZ is carrying it.
    await expect(second.getByTestId('combined-status')).toHaveText('Matter is not answering, so WiZ is used');
    await expect(panel.getByTestId('duplicates-bulk').getByRole('button')).toHaveCount(0);
    await page.getByTestId('duplicates').scrollIntoViewIfNeeded();
    await layout(page);
    await snap(page, info, 'devices-duplicates-all-combined');
    clean(run);
  });
});
