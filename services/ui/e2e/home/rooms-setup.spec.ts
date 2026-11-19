import { test, expect as baseExpect, type Locator, type Page } from '@playwright/test';
import { mockHome, type HomeMocks, type HomeOptions, type Role } from '../fixtures/homeMocks';
import { expectFits, expectLayoutSound, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The Rooms page as the place a parent sets up rooms: Add room in the header and in the empty state,
 * and rename and remove on each room's card. A member never sees any of it. Every project runs it
 * (desktop, Pixel 7, a small phone, tablets, iPhone). Hermetic: fixtures/homeMocks.ts, and the Android
 * app's 3-button nav bar is drawn as the bottom inset Capacitor sets. Every name is made up.
 */

const NAV_BAR = 48;
const NO_ROOMS: NonNullable<HomeOptions['rooms']> = { floors: [], areas: [], unassigned: [] };

async function open(page: Page, role: Role, options: HomeOptions = {}): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, role, options);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/rooms');
  await expect(page.getByTestId('rooms-page')).toBeVisible();
  // The Android app's edge-to-edge insets, as Capacitor's SystemBars injects them (env() reads 0 there).
  await page.evaluate((px) => document.documentElement.style.setProperty('--safe-area-inset-bottom', `${px}px`), NAV_BAR);
  return { mocks, errors };
}

function clean({ mocks, errors }: { mocks: HomeMocks; errors: string[] }) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

function lastSent(mocks: HomeMocks, path: string): unknown {
  return mocks.posts.filter((p) => decodeURIComponent(p.path) === path).map((p) => p.body).at(-1) ?? `${path} not sent`;
}

/** The dialog sits on the screen, its buttons are thumb-sized, and its last button clears the nav bar. */
async function expectDialogSound(page: Page, dialog: Locator): Promise<void> {
  // The sheet slides up as it opens; measure where it comes to rest.
  await expect(async () => expectFits(page, dialog)).toPass();
  // Every control in it but the shared dialog close (its own size rule lives in Modal.tsx) is thumb-sized.
  const small = await dialog.locator('button:not([aria-label="Close dialog"]), input, select')
    .evaluateAll((els) => els.filter((el) => el.getBoundingClientRect().height + 0.5 < 44)
      .map((el) => el.getAttribute('aria-label') || el.textContent || el.tagName));
  expect(small, `controls shorter than 44px: ${small.join('; ')}`).toEqual([]);
  const bottom = await dialog.getByRole('button').last().evaluate((el) => el.getBoundingClientRect().bottom);
  const height = page.viewportSize()?.height ?? 0;
  expect(bottom, 'a dialog button sits under the nav bar').toBeLessThanOrEqual(height - NAV_BAR + 1);
}

function roomCard(page: Page, name: string): Locator {
  return page.getByTestId('room-card').filter({ has: page.getByRole('heading', { name, exact: true }) });
}

test.describe('Rooms: adding, renaming and removing rooms', () => {
  test.describe.configure({ timeout: 90_000 });

  test('an admin adds a room from the header; it shows at once, then is renamed and removed', async ({ page }, info) => {
    const run = await open(page, 'admin');
    await expect(page.getByTestId('room-card')).toHaveCount(3);
    const add = page.getByTestId('page-header').getByRole('button', { name: 'Add room' });
    await expect(add).toBeVisible();
    await expect(page.getByTestId('room-admin-actions')).toHaveCount(3);
    // The new controls; the room's own device controls are checked by home-pages.spec.ts.
    await expectTapTargets(page.getByTestId('room-admin-actions').first());
    await expectLayoutSound(page, info, 'rooms-admin');

    await add.click();
    const dialog = page.getByRole('dialog', { name: 'Add a room' });
    await expect(dialog).toBeVisible();
    await expectDialogSound(page, dialog);
    await snap(page, info, 'rooms-add-dialog');
    await dialog.getByLabel('New room name').fill('  Playroom ');
    await dialog.getByLabel('Floor of the new room').selectOption('ground');
    await dialog.getByRole('button', { name: 'Add room' }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(() => lastSent(run.mocks, '/api/homehub/areas')).toEqual({ name: 'Playroom', floor_id: 'ground' });
    const playroom = roomCard(page, 'Playroom');
    await expect(playroom).toBeVisible();
    await expect(playroom.getByTestId('room-empty')).toContainText('Nothing in this room yet');
    await expect(page.getByTestId('room-card')).toHaveCount(4);
    await expectNoHorizontalOverflow(page);

    await page.getByRole('button', { name: 'Rename Bedroom' }).click();
    const rename = page.getByRole('dialog', { name: 'Rename Bedroom' });
    await expectDialogSound(page, rename);
    await rename.getByLabel('New name for Bedroom').fill('Guest room');
    await rename.getByRole('button', { name: 'Save' }).click();
    await expect(rename).toHaveCount(0);
    await expect.poll(() => lastSent(run.mocks, '/api/homehub/areas/bedroom')).toEqual({ name: 'Guest room' });
    await expect(roomCard(page, 'Guest room')).toBeVisible();

    await page.getByRole('button', { name: 'Remove Playroom' }).click();
    const confirm = page.getByRole('dialog', { name: 'Remove Playroom?' });
    await expect(confirm).toContainText('Its devices keep working, with no room.');
    await expectDialogSound(page, confirm);
    await confirm.getByRole('button', { name: 'Remove' }).click();
    await expect(roomCard(page, 'Playroom')).toHaveCount(0);
    await expect(page.getByTestId('room-card')).toHaveCount(3);
    clean(run);
  });

  test('a member sees the rooms but no add, rename or remove', async ({ page }, info) => {
    const run = await open(page, 'member');
    await expect(page.getByTestId('room-card')).toHaveCount(3);
    await expect(page.getByRole('button', { name: 'Add room' })).toHaveCount(0);
    await expect(page.getByTestId('add-room')).toHaveCount(0);
    await expect(page.getByTestId('room-admin-actions')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^(Rename|Remove) / })).toHaveCount(0);
    await expect(page.getByLabel('New room name')).toHaveCount(0);
    await expectLayoutSound(page, info, 'rooms-member-no-edit');
    expect(run.mocks.posts.filter((p) => p.path.startsWith('/api/homehub/areas'))).toEqual([]);
    clean(run);
  });

  test('an admin with no rooms is offered Add room in the empty state', async ({ page }, info) => {
    const run = await open(page, 'admin', { rooms: structuredClone(NO_ROOMS) });
    const empty = page.getByText('No rooms yet');
    await expect(empty).toBeVisible();
    await expect(page.getByText('Add the rooms in your home')).toBeVisible();
    const cta = page.getByTestId('rooms-page').getByRole('button', { name: 'Add room' }).last();
    await expectTapTargets(page.getByTestId('rooms-page'));
    await expectLayoutSound(page, info, 'rooms-empty-admin');

    await cta.click();
    const dialog = page.getByRole('dialog', { name: 'Add a room' });
    await dialog.getByLabel('New room name').fill('Kitchen');
    await dialog.getByLabel('New room name').press('Enter');
    await expect(dialog).toHaveCount(0);
    await expect(roomCard(page, 'Kitchen')).toBeVisible();
    await expect(empty).toHaveCount(0);
    await expect.poll(() => lastSent(run.mocks, '/api/homehub/areas')).toEqual({ name: 'Kitchen', floor_id: null });
    clean(run);
  });

  test('a member with no rooms is told a parent sets them up, with nothing to add', async ({ page }, info) => {
    const run = await open(page, 'member', { rooms: structuredClone(NO_ROOMS) });
    await expect(page.getByText('No rooms yet')).toBeVisible();
    await expect(page.getByText('once a parent puts them in a room')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add room' })).toHaveCount(0);
    await expect(page.getByTestId('room-admin-actions')).toHaveCount(0);
    await expectLayoutSound(page, info, 'rooms-empty-member');
    clean(run);
  });
});
