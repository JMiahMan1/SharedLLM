/**
 * "Sign everyone out and reset keys", hermetic: every API call is mocked and
 * nothing reaches a server. Runs in every hermetic project (desktop, phones,
 * tablets).
 *
 * - A device whose key was reset gets a 401 with code "sign_in_reset" and
 *   lands on /login saying so, with the stored key cleared.
 * - An admin opens the Security panel, reads the preview, types RESET, and
 *   gets the report: counts, the home hub's answer, what to enrol again by
 *   hand and the third-party logins to rotate. "Sign in again" signs out with
 *   the reset reason.
 */
import { expect, test, type Page, type Route } from '@playwright/test';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets } from '../fixtures/layoutChecks';

const ADMIN = {
  id: 1, username: 'admin', display_name: 'Test Admin', full_name: 'Test Admin',
  role: 'admin', is_admin: true, is_system_default: true, is_test_account: false,
};
const TESTER = {
  id: 2, username: 'e2e-tester', display_name: 'E2E Tester', full_name: 'E2E Tester',
  role: 'user', is_admin: false, is_test_account: true,
};

const RESET_MESSAGE = 'Your sign-in was reset, please sign in again';
const RESET_401 = { detail: 'Your sign-in was reset, please sign in again.', code: 'sign_in_reset' };

const RE_ENROL = [
  { kind: 'windows_guard', label: 'Study PC with a rather long name for a small phone', owner: 'e2e-tester', how: 'Run the Windows Guard installer again and sign in.' },
  { kind: 'esphome_satellite', label: 'Kitchen satellite', owner: null, count: 2, how: 'Re-flash it with the new key from Devices.' },
  { kind: 'homehub_long_lived_token', label: 'Garage dashboard', owner: 'admin', how: 'Make a new long-lived token in HomeHub and paste it in.' },
];
const THIRD_PARTY = [{ field: 'github_token', label: 'GitHub token', accounts: 2 }];

/** Sign in once (the first page load), so a later sign-out really empties storage. */
async function seedOnce(page: Page, profile: Record<string, unknown>) {
  await page.addInitScript((p) => {
    if (sessionStorage.getItem('e2e-seeded')) return;
    sessionStorage.setItem('e2e-seeded', '1');
    localStorage.setItem('jarvis_api_key', 'fake-key-123');
    localStorage.setItem('jarvis_user', JSON.stringify(p));
  }, profile);
}

/** Everything else answers empty, so no request can leave the browser. */
async function catchAll(page: Page) {
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/') || u.pathname === '/health',
    async (route: Route) => {
      const p = new URL(route.request().url()).pathname;
      if (route.request().method() !== 'GET') return route.fulfill({ json: { status: 'SUCCESS' } });
      if (/\/api\/(health|info)\b/.test(p)) return route.fulfill({ json: { status: 'ok', version: 'e2e', git_sha: 'e2e' } });
      if (/\/api\/(config|calendar\/settings)\b/.test(p)) return route.fulfill({ json: { status: 'SUCCESS', settings: {}, config: {} } });
      if (/\/api\/telemetry\//.test(p)) return route.fulfill({ json: { notifications: [], jobs: [] } });
      return route.fulfill({ json: [] });
    });
}

test.describe('sign-in reset', () => {
  test('a reset key lands on the login page saying so', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await seedOnce(page, ADMIN);
    await catchAll(page);
    await page.route('**/api/users/me', (route) => route.fulfill({ status: 401, json: RESET_401 }));

    await page.goto('/');
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByTestId('login-notice')).toHaveText(RESET_MESSAGE);
    expect(await page.evaluate(() => [localStorage.getItem('jarvis_api_key'), localStorage.getItem('jarvis_signout_reason')]))
      .toEqual([null, 'sign_in_reset']);

    await expectNoHorizontalOverflow(page);
    const signIn = await page.getByRole('button', { name: 'Sign In' }).boundingBox();
    expect(signIn?.height ?? 0, 'the Sign In button is a 44px target').toBeGreaterThanOrEqual(44);
    expect(errors).toEqual([]);
  });

  test('any other refused key says the session ended', async ({ page }) => {
    await seedOnce(page, ADMIN);
    await catchAll(page);
    await page.route('**/api/users/me', (route) => route.fulfill({ status: 401, json: { detail: 'Invalid API Key', code: 'api_key_invalid' } }));

    await page.goto('/');
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByTestId('login-notice')).toHaveText('Your session ended. Please sign in again.');
  });

  test('an admin signs everyone out from the Security panel', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const posts: unknown[] = [];
    await seedOnce(page, ADMIN);
    await catchAll(page);
    await page.route('**/api/users/me', (route) => route.fulfill({ json: ADMIN }));
    await page.route('**/api/users?*', (route) => {
      const include = new URL(route.request().url()).searchParams.get('include_test_accounts') === 'true';
      return route.fulfill({ json: include ? [ADMIN, TESTER] : [ADMIN] });
    });
    await page.route('**/api/users/security/sign-in-reset/last', (route) => route.fulfill({ json: null }));
    await page.route('**/api/users/security/sign-in-reset', async (route) => {
      if (route.request().method() === 'POST') {
        posts.push(route.request().postDataJSON());
        return route.fulfill({ json: {
          status: 'SUCCESS', scope: 'everyone', username: null, at: '2026-11-21T10:00:00', by: 'admin',
          users_reset: 4, api_keys_revoked: 9, device_agent_keys_revoked: 3,
          homehub: { status: 'revoked', revoked: 2, message: 'Revoked 2 HomeHub tokens.' },
          re_enrol: RE_ENROL, third_party: THIRD_PARTY, caller_signed_out: true,
        } });
      }
      return route.fulfill({ json: {
        users: 4, api_keys: 9, device_agent_keys: 3, re_enrol: RE_ENROL, third_party: THIRD_PARTY, last_reset: null,
      } });
    });

    await page.goto('/admin/users');
    // The test account is listed, with its badge.
    const testerRow = page.getByRole('button', { name: 'Reset sign-in for e2e-tester' }).first().locator('xpath=ancestor::div[contains(@class,"glass-card")][1]');
    await expect(testerRow.getByTestId('test-account-badge')).toHaveText('Test account');

    const security = page.getByTestId('admin-security');
    await security.scrollIntoViewIfNeeded();
    await expectTapTargets(security);
    await security.getByRole('button', { name: 'Sign everyone out and reset keys' }).click();

    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('sign-in-reset-preview')).toContainText('Windows Guard');
    const confirm = dialog.getByTestId('sign-in-reset-confirm-button');
    await expect(confirm).toBeDisabled();
    await dialog.getByLabel('Type RESET to confirm').fill('RESET');
    await expect(confirm).toBeEnabled();
    await expectTapTargets(dialog.getByTestId('sign-in-reset-dialog-body'));
    await expectNoHorizontalOverflow(page);
    await confirm.click();

    const report = dialog.getByTestId('sign-in-reset-report');
    await expect(report.getByTestId('homehub-status')).toContainText('Revoked 2 HomeHub tokens.');
    await expect(report.getByTestId('reenrol-windows_guard')).toContainText('Run the Windows Guard installer again');
    await expect(report.getByTestId('reenrol-esphome_satellite')).toContainText('Kitchen satellite');
    await expect(report.getByTestId('reenrol-homehub_long_lived_token')).toContainText('Garage dashboard');
    await expect(report.getByTestId('third-party-list')).toContainText('GitHub token');
    await expect(report).toContainText('rotate these at the provider');
    expect(posts).toEqual([{ confirm: 'RESET' }]);

    await expectFits(page, dialog);
    // The dialog's own controls (the shared Modal's close button is checked with the Modal).
    await expectTapTargets(dialog.getByTestId('sign-in-reset-dialog-body'));
    await expectNoHorizontalOverflow(page);

    await dialog.getByTestId('sign-in-again').click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByTestId('login-notice')).toHaveText(RESET_MESSAGE);
    expect(errors).toEqual([]);
  });
});
