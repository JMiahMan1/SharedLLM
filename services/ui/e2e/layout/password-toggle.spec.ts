import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { expectFits, expectNoHorizontalOverflow, MIN_TARGET } from '../fixtures/layoutChecks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The show/hide eye on a password field a device flow asks for (a locked
 * Tasmota's web password), on every form factor: visible, inside its input and
 * the dialog, a 44px target, nothing wider than the screen, and it shows and
 * hides what was typed. Hermetic: fixtures/homeMocks.ts answers every call; this
 * spec only adds one made-up locked device to the discovered list.
 */

const LOCKED = {
  id: 'tasmota:test-lamp', integration: 'tasmota', label: 'Tasmota', name: 'Test lamp with a long name for small phones',
  model: null, manufacturer: null, host: '192.0.2.77', mac: null, source: 'dhcp', first_seen: new Date().toISOString(),
  last_seen: new Date().toISOString(), known: false, entry_id: null, entry_title: null, ignored: false, new: true,
  adoptable: true, unsupported: null, props: { locked: true },
  needs: [{ key: 'password', label: 'Web password', type: 'password',
    help: 'The password of the device\'s own web page (on the device: Configuration, Other, Web Admin Password).' }],
};

async function openAddDialog(page: Page) {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  // Registered last, so it answers before the shared mocks.
  await page.route((url) => url.pathname === '/api/homehub/discovered',
    (route) => route.fulfill({ json: { devices: [LOCKED], last_scan: { at: new Date().toISOString() } } }));
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/devices');
  await page.getByTestId('devices-sections').getByRole('tab', { name: 'Add devices' }).click();
  await page.getByRole('button', { name: `Add ${LOCKED.name}` }).click();
  const dialog = page.getByTestId('adopt-dialog');
  await expect(dialog).toBeVisible();
  return { mocks, errors, dialog };
}

test('a device flow\'s password field has a show/hide eye that fits every screen', async ({ page }, info) => {
  const { mocks, errors, dialog } = await openAddDialog(page);
  const input = dialog.getByLabel('Web password');
  const eye = dialog.getByRole('button', { name: 'Show password' });
  await expect(input).toHaveAttribute('type', 'password');
  await expect(eye).toBeVisible();
  await expect(eye).toHaveAttribute('aria-pressed', 'false');

  const e = (await eye.boundingBox())!;
  const i = (await input.boundingBox())!;
  expect(e.width, 'the eye is a 44px target').toBeGreaterThanOrEqual(MIN_TARGET - 0.5);
  expect(e.height, 'the eye is a 44px target').toBeGreaterThanOrEqual(MIN_TARGET - 0.5);
  expect(e.x, 'the eye sits inside its input').toBeGreaterThanOrEqual(i.x - 1);
  expect(e.x + e.width, 'the eye sits inside its input').toBeLessThanOrEqual(i.x + i.width + 1);
  expect(e.y).toBeGreaterThanOrEqual(i.y - 1);
  expect(e.y + e.height).toBeLessThanOrEqual(i.y + i.height + 1);

  await input.fill('made-up-pass');
  await eye.click();
  await expect(input).toHaveAttribute('type', 'text');
  await expect(input).toHaveValue('made-up-pass');
  const hide = dialog.getByRole('button', { name: 'Hide password' });
  await expect(hide).toHaveAttribute('aria-pressed', 'true');
  await hide.focus();
  await page.keyboard.press('Enter');
  await expect(input).toHaveAttribute('type', 'password');

  await settle(page);
  await expectNoHorizontalOverflow(page);
  await expectFits(page, page.getByRole('dialog').last());
  const found = await screenFindings(page, page.getByRole('dialog').last(), 'adopt password', []);
  await shot(page, info, 'adopt-password-toggle');
  expect(found, found.join('\n')).toEqual([]);
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
});
