import { test, expect as baseExpect } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { controlsOffScreen, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

/**
 * The family PC installer on Devices, Family: a parent who manages devices sees
 * its version, the SHA-256 and the install steps, and downloads it with their
 * own key; nobody else sees the panel. Every form factor: nothing past the
 * screen's edge, thumb-sized buttons, the long hash wraps. Hermetic.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const INFO = {
  available: true,
  version: '0.3.0',
  sha256: '3e8582b8f5158e281ecd4ed79abdde1237fb962940a92fc4133c4ab77dec2c5b',
  git_sha: '0123abcd',
  built_at: '2026-01-05T10:00:00Z',
  files: [
    { name: 'jarvis-guard-windows.zip', size: 46_000_000 },
    { name: 'jarvis-guard.exe', size: 51_700_000 },
    { name: 'install.ps1', size: 9_000 },
  ],
};

test('a parent finds the Windows installer, its hash and the steps, on every screen', async ({ page }, info) => {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const fetched: string[] = [];
  await page.route('**/api/app-updates/windows/info', (route) => route.fulfill({ json: INFO }));
  await page.route('**/api/app-updates/windows/files/**', (route) => {
    fetched.push(new URL(route.request().url()).pathname);
    return route.fulfill({ contentType: 'application/zip', body: Buffer.from('PK fake zip') });
  });

  await page.goto('/devices#devices-windows');
  const panel = page.getByTestId('windows-guard-panel');
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId('windows-guard-version')).toHaveText('Jarvis Guard 0.3.0');
  await expect(panel.getByTestId('windows-guard-sha256')).toHaveText(INFO.sha256);
  await expect(panel.getByText(/ExecutionPolicy Bypass/)).toBeVisible();
  await panel.scrollIntoViewIfNeeded();

  await expectNoHorizontalOverflow(page);
  await expectTapTargets(panel);
  expect(await controlsOffScreen(page)).toEqual([]);
  const hash = await panel.getByTestId('windows-guard-sha256').boundingBox();
  const width = page.viewportSize()?.width ?? 0;
  expect((hash?.x ?? 0) + (hash?.width ?? 0), 'the hash wraps inside the screen').toBeLessThanOrEqual(width + 1);
  await snap(page, info, 'windows-guard-download');

  await panel.getByRole('button', { name: /Download installer/ }).click();
  await expect.poll(() => fetched).toEqual(['/api/app-updates/windows/files/jarvis-guard-windows.zip']);

  expect(errors).toEqual([]);
  expect(mocks.unmocked).toEqual([]);
});

test('someone who does not manage devices does not see the installer', async ({ page }) => {
  await mockHome(page, 'admin');
  await page.route('**/api/app-updates/windows/info', (route) => route.fulfill({ status: 403, json: { detail: 'no' } }));
  const asked = page.waitForResponse('**/api/app-updates/windows/info');
  await page.goto('/devices#devices-windows');
  await asked;
  await expect(page.getByText('Family devices')).toBeVisible();
  await expect(page.getByTestId('windows-guard-panel')).toHaveCount(0);
});
