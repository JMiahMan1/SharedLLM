import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { expectNoHorizontalOverflow, MIN_TARGET } from '../fixtures/layoutChecks';
import { quietHotReload, settle, shot } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Household rule: every password field lets the person see what they typed
 * before sending it. A representative set of the screens that gained the shared
 * PasswordField eye (Admin's new user and set password dialogs, the router
 * login, the account export sheet), on every form factor: the eye is visible,
 * sits inside its input, is a 44px target, shows and hides what was typed, and
 * the page does not scroll sideways. Hermetic: the shared mocks answer every
 * call (the catch-all stays empty) and no page error is thrown.
 */

interface Run { home: HomeMocks; errors: string[] }

async function open(page: Page, path: string): Promise<Run> {
  await quietHotReload(page);
  const home = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockSettingsPages(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(path);
  return { home, errors };
}

/** The eye that belongs to `input` (PasswordField renders it right after the input). */
function eyeOf(input: Locator): Locator {
  return input.locator('xpath=following-sibling::button[1]');
}

async function checkEye(page: Page, info: TestInfo, input: Locator, where: string): Promise<void> {
  await input.scrollIntoViewIfNeeded();
  const eye = eyeOf(input);
  await expect(input).toHaveAttribute('type', 'password');
  await expect(eye).toBeVisible();
  await expect(eye).toHaveAttribute('aria-label', 'Show password');
  await expect(eye).toHaveAttribute('aria-pressed', 'false');

  const e = (await eye.boundingBox())!;
  const i = (await input.boundingBox())!;
  expect(e.width, `${where}: the eye is a 44px target`).toBeGreaterThanOrEqual(MIN_TARGET - 0.5);
  expect(e.height, `${where}: the eye is a 44px target`).toBeGreaterThanOrEqual(MIN_TARGET - 0.5);
  expect(e.x, `${where}: the eye sits inside its input`).toBeGreaterThanOrEqual(i.x - 1);
  expect(e.x + e.width, `${where}: the eye sits inside its input`).toBeLessThanOrEqual(i.x + i.width + 1);
  expect(e.y, `${where}: the eye sits inside its input`).toBeGreaterThanOrEqual(i.y - 1);
  expect(e.y + e.height, `${where}: the eye sits inside its input`).toBeLessThanOrEqual(i.y + i.height + 1);

  await input.fill('made-up-pass');
  await eye.click();
  await expect(input).toHaveAttribute('type', 'text');
  await expect(input).toHaveValue('made-up-pass');
  await expect(eye).toHaveAttribute('aria-label', 'Hide password');
  await expect(eye).toHaveAttribute('aria-pressed', 'true');
  await eye.focus();
  await page.keyboard.press('Enter');
  await expect(input).toHaveAttribute('type', 'password');

  await settle(page);
  await expectNoHorizontalOverflow(page);
  await shot(page, info, `password-${where.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`);
}

function finish(run: Run): void {
  expect([...new Set(run.home.unmocked)], 'unmocked calls').toEqual([]);
  expect(run.errors, 'page errors').toEqual([]);
}

test.describe('password fields show what was typed, on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('admin: new user and set password', async ({ page }, info) => {
    const run = await open(page, '/admin/users');
    const main = page.locator('main');
    await expect(main.getByText('@robin').first()).toBeVisible({ timeout: 60_000 });

    await page.getByRole('button', { name: 'Create User' }).click();
    const create = page.getByRole('dialog', { name: 'Create User' });
    await expect(create).toBeVisible();
    await checkEye(page, info, create.getByLabel('Password', { exact: true }), 'admin create user');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);

    await main.getByRole('button', { name: 'Change password for robin' }).click();
    const set = page.getByRole('dialog', { name: 'Set Password for @robin' });
    await expect(set).toBeVisible();
    await checkEye(page, info, set.getByLabel('New Password', { exact: true }), 'admin set password');
    finish(run);
  });

  test('admin: router login', async ({ page }, info) => {
    const run = await open(page, '/admin/network');
    const main = page.locator('main');
    const toggle = main.getByRole('button', { name: /^(Router login|Set up)$/ });
    await expect(toggle).toBeVisible({ timeout: 60_000 });
    await toggle.click();
    await checkEye(page, info, main.getByLabel('Router password', { exact: true }), 'admin router login');
    finish(run);
  });

  test('settings: account export', async ({ page }, info) => {
    const run = await open(page, '/settings');
    await expect(page.getByTestId('settings-profile-name')).toBeVisible({ timeout: 60_000 });
    const opener = page.getByTestId('account-export-open');
    await opener.scrollIntoViewIfNeeded();
    await opener.click();
    const password = page.getByTestId('account-export-password');
    await expect(password).toBeVisible();
    await expect(page.getByLabel('Password for the file', { exact: true })).toBeVisible();
    await checkEye(page, info, password, 'account export password');
    await checkEye(page, info, page.getByTestId('account-export-confirm'), 'account export confirm');
    finish(run);
  });
});
