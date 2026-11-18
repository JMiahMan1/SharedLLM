import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { mockHome, PROFILES, type HomeMocks } from '../fixtures/homeMocks';
import { mockDeviceStates } from '../fixtures/hardwareMocks';
import { expectClearOfBottomBar, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > Light groups: putting lights together so they switch as one (and so voice can say
 * "turn off the pantry lights"). Make a group from lights picked by room, change it, remove it;
 * a parent without the admin flag gets the same panel. Every project (desktop, phones, tablets).
 * Hermetic: the hub is a small stateful mock below; every name is made up.
 */

type Light = { entity_id: string; name: string; area: string | null; hidden: boolean; available: boolean };
type Group = { entry_id: string; entity_id: string; name: string; area_id: string | null; entities: string[] };

const AREAS = [{ area_id: 'den', name: 'Den' }, { area_id: 'pantry', name: 'Pantry' }];
const LIGHTS: Light[] = [
  ...[1, 2, 3, 4, 5, 6, 7].map((n) => ({
    entity_id: `light.pantry_${n}`, name: `Pantry Light ${n}`, area: 'Pantry', hidden: false, available: n !== 4,
  })),
  { entity_id: 'light.pantry_1_copy', name: 'Pantry Light 1', area: 'Pantry', hidden: true, available: true },
  { entity_id: 'light.den_lamp', name: 'Den Lamp', area: 'Den', hidden: false, available: true },
  { entity_id: 'light.den_reading', name: 'Reading lamp by the window with a long name for small phones', area: 'Den',
    hidden: false, available: true },
  { entity_id: 'light.porch', name: 'Porch Light', area: null, hidden: false, available: true },
];

type Run = { mocks: HomeMocks; groups: Group[]; calls: { method: string; path: string; body: unknown }[]; errors: string[] };

function view(groups: Group[]) {
  return {
    groups: groups.map((g) => ({
      entry_id: g.entry_id, entity_id: g.entity_id, name: g.name, area_id: g.area_id,
      area: AREAS.find((a) => a.area_id === g.area_id)?.name ?? null, state: 'on', missing: 0,
      members: g.entities.map((eid) => {
        const l = LIGHTS.find((x) => x.entity_id === eid)!;
        return { entity_id: eid, name: l.name, state: l.available ? 'on' : 'unavailable', missing: false,
          hidden: l.hidden, area: l.area };
      }),
    })),
    lights: LIGHTS,
    areas: AREAS,
  };
}

async function open(page: Page, as: 'admin' | 'parent' = 'admin'): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  if (as === 'parent') {
    // A mother without the admin flag: no hub tabs, but grouping lights is hers too.
    const mother = { ...PROFILES.admin, username: 'mom', display_name: 'Mom', role: 'user', is_admin: false, family_role: 'mother' };
    await page.addInitScript((p) => localStorage.setItem('jarvis_user', JSON.stringify(p)), mother);
    await page.route('**/api/users/me', (route) => route.fulfill({ json: mother }));
  }
  await mockDeviceStates(page);
  const run: Run = { mocks, groups: [], calls: [], errors: [] };
  page.on('pageerror', (e) => run.errors.push(e.message));
  const body = (route: Route) => { try { return route.request().postDataJSON(); } catch { return null; } };
  await page.route(/\/api\/homehub\/light_groups(\/[^/]+)?$/, (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const method = req.method();
    if (method === 'GET') return route.fulfill({ json: view(run.groups) });
    const b = body(route) as { name?: string; area_id?: string | null; entities?: string[] } | null;
    run.calls.push({ method, path, body: b });
    const id = path.split('/').pop()!;
    if (method === 'POST') {
      const g: Group = { entry_id: `g${run.groups.length + 1}`, entity_id: `light.${(b?.name ?? '').toLowerCase().replace(/\W+/g, '_')}`,
        name: b?.name ?? '', area_id: b?.area_id ?? null, entities: b?.entities ?? [] };
      run.groups.push(g);
      return route.fulfill({ json: view([g]).groups[0] });
    }
    const g = run.groups.find((x) => x.entry_id === id);
    if (!g) return route.fulfill({ status: 404, json: { detail: 'No such light group' } });
    if (method === 'PATCH') {
      Object.assign(g, { name: b?.name ?? g.name, area_id: b && 'area_id' in b ? b.area_id : g.area_id,
        entities: b?.entities ?? g.entities });
      return route.fulfill({ json: view([g]).groups[0] });
    }
    run.groups = run.groups.filter((x) => x !== g);
    return route.fulfill({ json: { status: 'removed', entry_id: id } });
  });
  await page.goto('/devices');
  await expect(page.getByTestId('page-header')).toBeVisible();
  await expect(page.getByTestId('light-groups')).toBeVisible();
  return run;
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked.filter((u) => !u.includes('/api/mdm') && !u.includes('companion')),
    `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

async function layout(page: Page) {
  await expectNoHorizontalOverflow(page);
  await expectTapTargets(page.getByTestId('light-groups'));
  await expectClearOfBottomBar(page);
}

test.describe('Devices: light groups', () => {
  test.describe.configure({ timeout: 90_000 });

  test('make a group from the lights of a room, then see it with its lights', async ({ page }, info) => {
    const run = await open(page);
    const panel = page.getByTestId('light-groups');
    await expect(panel.getByTestId('light-groups-empty')).toBeVisible();
    await panel.scrollIntoViewIfNeeded();
    await panel.getByRole('button', { name: 'New group' }).click();
    const editor = panel.getByTestId('light-group-editor');
    await expect(editor).toBeVisible();
    await editor.getByLabel('Name', { exact: true }).fill('Pantry lights');
    await editor.getByLabel('Room', { exact: true }).selectOption({ label: 'Pantry' });
    // Too few lights: the editor says so and sends nothing.
    await editor.getByRole('button', { name: 'Make group' }).click();
    await expect(editor.getByRole('alert')).toHaveText('Choose at least two lights.');
    // The hidden copy and the light that is not responding say so.
    await expect(editor.getByText('Hidden copy')).toBeVisible();
    await expect(editor.getByText('Not responding')).toBeVisible();
    await editor.getByRole('button', { name: 'Choose every light in Pantry' }).click();
    await editor.getByRole('checkbox', { name: /Pantry Light 1 Hidden copy/ }).uncheck();
    await expect(editor.getByTestId('light-group-count')).toHaveText('7 lights chosen');
    await layout(page);
    await snap(page, info, 'light-groups-editor');
    await editor.getByRole('button', { name: 'Make group' }).click();
    await expect(editor).toHaveCount(0);
    expect(run.calls).toEqual([{ method: 'POST', path: '/api/homehub/light_groups', body: {
      name: 'Pantry lights', area_id: 'pantry', entities: [1, 2, 3, 4, 5, 6, 7].map((n) => `light.pantry_${n}`) } }]);
    const row = panel.getByTestId('light-group');
    await expect(row).toHaveCount(1);
    await expect(row.getByRole('heading', { name: 'Pantry lights' })).toBeVisible();
    await expect(row).toContainText('Pantry · 7 lights · 6 on');
    await expect(row.getByTestId('light-group-member')).toHaveCount(7);
    await layout(page);
    await snap(page, info, 'light-groups-list');
    clean(run);
  });

  test('change a group: rename it, move it and take a light out; then remove it', async ({ page }, info) => {
    const run = await open(page);
    run.groups.push({ entry_id: 'g1', entity_id: 'light.den_lights', name: 'Den lights', area_id: 'den',
      entities: ['light.den_lamp', 'light.den_reading', 'light.porch'] });
    await page.reload();
    const panel = page.getByTestId('light-groups');
    await panel.getByRole('button', { name: 'Change Den lights' }).click();
    const editor = panel.getByTestId('light-group-editor');
    await editor.getByLabel('Name', { exact: true }).fill('Reading corner');
    await editor.getByLabel('Room', { exact: true }).selectOption({ label: 'No room' });
    await editor.getByLabel('Find a light or room').fill('porch');
    await expect(editor.getByTestId('light-group-room')).toHaveCount(1);
    await editor.getByRole('checkbox', { name: 'Porch Light' }).uncheck();
    await layout(page);
    await snap(page, info, 'light-groups-change');
    await editor.getByRole('button', { name: 'Save' }).click();
    await expect(editor).toHaveCount(0);
    expect(run.calls.at(-1)).toEqual({ method: 'PATCH', path: '/api/homehub/light_groups/g1',
      body: { name: 'Reading corner', area_id: null, entities: ['light.den_lamp', 'light.den_reading'] } });
    await expect(panel.getByTestId('light-group')).toContainText('No room · 2 lights');
    await panel.getByRole('button', { name: 'Remove Reading corner' }).click();
    await page.getByRole('button', { name: 'Remove', exact: true }).click();
    await expect(panel.getByTestId('light-groups-empty')).toBeVisible();
    expect(run.calls.at(-1)).toMatchObject({ method: 'DELETE', path: '/api/homehub/light_groups/g1' });
    clean(run);
  });

  test('a parent without the admin flag groups lights too', async ({ page }, info) => {
    const run = await open(page, 'parent');
    await expect(page.getByTestId('devices-sections')).toHaveCount(0); // no hub tabs for her
    const panel = page.getByTestId('light-groups');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByRole('button', { name: 'New group' })).toBeVisible();
    await layout(page);
    await snap(page, info, 'light-groups-parent');
    expect(run.errors).toEqual([]);
  });
});
