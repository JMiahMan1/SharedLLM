/**
 * Export my data and Open an export file (docs/ACCOUNT_EXPORT.md), hermetic:
 * the gateway is mocked in the browser and nothing reaches a server. The real
 * device crypto runs: the export is encrypted in the page, downloaded, opened
 * again with its password (and refused with a wrong one), read, and restored.
 * Runs in every hermetic project (desktop, phones, tablets).
 */
import { readFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { expectNoHorizontalOverflow, expectTapTargets } from '../fixtures/layoutChecks';

const PASSWORD = 'four calm otters swim north';

/** A made-up person's export, as the gateway streams it. */
function exportStream(): string {
  const lines: Record<string, unknown>[] = [
    { kind: 'manifest', format: 'jarvis-account-export', version: 1, exported_at: '2026-03-04T05:06:07Z',
      subject: { username: 'parent' }, exported_by: { username: 'parent' }, services: ['identity', 'health', 'bible', 'geo'],
      excluded: [{ what: 'Passwords, API keys and stored service logins', why: 'never exported' }] },
    { kind: 'section', service: 'identity', collections: {
      profile: [{ username: 'parent', display_name: 'Pat Example', family_role: 'father' }],
      theme: [{ username: 'parent', data: '{"theme_id":"forest"}' }],
      dashboard_widgets: [{ id: 1, widget_key: 'weather', order_index: 1, updated_at: 1000 }],
    } },
    { kind: 'section', service: 'health', collections: {
      metrics: Array.from({ length: 40 }, (_, i) => ({ id: i + 1, metric: 'weight', value: 70 + i / 10, unit: 'kg', recorded_at: `2026-01-${String((i % 28) + 1).padStart(2, '0')}` })),
      checkins: [{ day: '2026-01-02', note: `A very long note ${'Supercalifragilisticexpialidociousnessreallylongunbrokenword'.repeat(2)}` }],
    } },
    { kind: 'section', service: 'bible', collections: { marks: [{ osis: 'John', chapter: 3, verse_start: 16, kind: 'highlight', color: 'gold' }] } },
    { kind: 'section', service: 'geo', collections: { trips: [{ id: 't1', distance_miles: 3.2 }], daily_steps: [{ day: '2026-01-01', steps: '8000' }] } },
    { kind: 'section', service: 'geo', collections: { location_history: [{ t: 1, lat: 1, lon: 2 }] } },
    { kind: 'end', sections: 4, failed: [] },
  ];
  return lines.map((l) => JSON.stringify(l)).join('\n') + '\n';
}

async function mockGateway(page: Page) {
  const imports: Array<Record<string, unknown>> = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  // The made-up household's Settings page (signed in as its admin, 'parent').
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockSettingsPages(page);
  await page.route('**/api/account/export', (route) =>
    route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: exportStream() }));
  await page.route('**/api/account/import', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    imports.push(body);
    const collections = body.collections as Record<string, unknown[]>;
    const counts = Object.fromEntries(Object.entries(collections).map(([name, rows]) => [name, {
      add: body.service === 'health' ? rows.length : 0,
      update: body.service === 'bible' ? rows.length : 0,
      conflict: body.service === 'identity' && name === 'theme' && !body.replace_conflicts ? 1 : 0,
      unchanged: body.service === 'geo' ? rows.length : 0,
      skipped: 0,
      errors: [],
    }]));
    return route.fulfill({ json: { service: body.service, dry_run: body.dry_run, collections: counts } });
  });
  return { imports, errors };
}

/** The control is on screen and nothing (the bottom bar, the page) sits on top of it. */
async function expectReachable(locator: import('@playwright/test').Locator) {
  await locator.scrollIntoViewIfNeeded();
  const hit = await locator.evaluate((el) => {
    const b = el.getBoundingClientRect();
    const top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return { covered: !(top && (top === el || el.contains(top))), bottom: b.bottom, inner: window.innerHeight };
  });
  expect(hit.covered, 'something covers this control').toBe(false);
  expect(hit.bottom).toBeLessThanOrEqual(hit.inner);
}

async function openSettingsAccount(page: Page) {
  await page.goto('/settings');
  const row = page.getByTestId('account-export-open');
  await expect(row).toBeVisible();
  await row.scrollIntoViewIfNeeded();
  return row;
}

async function makeExport(page: Page): Promise<string> {
  await (await openSettingsAccount(page)).click();
  const sheet = page.getByTestId('account-export-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByTestId('account-export-submit')).toBeDisabled();
  await sheet.getByTestId('account-export-password').fill('short');
  await expect(sheet.getByTestId('account-export-strength')).toContainText('Too short');
  await sheet.getByTestId('account-export-password').fill(PASSWORD);
  await expect(sheet.getByTestId('account-export-strength')).toContainText('Strong');
  await sheet.getByTestId('account-export-confirm').fill(PASSWORD);
  await expect(sheet.getByTestId('account-export-submit')).toBeDisabled();
  await sheet.getByTestId('account-export-understood').check();
  await expectTapTargets(sheet);
  await expectNoHorizontalOverflow(page);
  await expectReachable(sheet.getByTestId('account-export-submit'));
  const download = page.waitForEvent('download');
  await sheet.getByTestId('account-export-submit').click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^jarvis-export-parent-\d{4}-\d{2}-\d{2}\.jarvis-export$/);
  await expect(page.getByTestId('account-export-done')).toBeVisible();
  const path = await file.path();
  const bytes = await readFile(path);
  expect(bytes.subarray(0, 10).toString()).toBe('JARVISACCT');
  expect(bytes.includes(Buffer.from('Pat Example'))).toBe(false);
  return path;
}

test.describe('account export', () => {
  // Each open derives the key with 600,000 PBKDF2 rounds in the page, several times per test.
  test.slow();
  test('exports an encrypted file, refuses a wrong password, reads it and restores it', async ({ page }, testInfo) => {
    const { imports, errors } = await mockGateway(page);
    const path = await makeExport(page);
    await page.screenshot({ path: testInfo.outputPath('account-export-done.png') });
    await page.getByRole('button', { name: 'Done' }).click();

    await page.getByTestId('account-export-open-file').click();
    const sheet = page.getByTestId('account-open-sheet');
    await sheet.getByTestId('account-open-file').setInputFiles(path);
    await sheet.getByTestId('account-open-password').fill('not the right password');
    await sheet.getByTestId('account-open-submit').click();
    await expect(sheet.getByTestId('account-open-error')).toHaveText('That password does not open this file.');
    await page.screenshot({ path: testInfo.outputPath('account-open-wrong-password.png') });

    await sheet.getByTestId('account-open-password').fill(PASSWORD);
    await sheet.getByTestId('account-open-submit').click();
    const viewer = page.getByTestId('account-viewer');
    await expect(viewer).toBeVisible();
    await expect(viewer.getByTestId('account-viewer-health')).toContainText('41 items');
    await viewer.getByTestId('account-viewer-health').getByRole('button', { name: /Health/ }).click();
    await viewer.getByRole('button', { name: /Check-ins/ }).click();
    await expect(viewer).toContainText('A very long note');
    await viewer.getByRole('button', { name: /Measurements/ }).click();
    await expect(viewer.getByRole('button', { name: /Show more \(15 left\)/ })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(sheet);
    await page.screenshot({ path: testInfo.outputPath('account-viewer.png') });

    await expectReachable(viewer.getByTestId('account-save-page'));
    await expectReachable(viewer.getByTestId('account-restore-preview'));
    const pageDownload = page.waitForEvent('download');
    await viewer.getByTestId('account-save-page').click();
    const html = await readFile(await (await pageDownload).path(), 'utf8');
    expect(html).toContain('jarvis-export-data');
    expect(html).not.toContain('Pat Example');
    expect(html).not.toContain('A very long note');

    await viewer.getByTestId('account-restore-preview').click();
    const summary = page.getByTestId('account-restore-summary');
    await expect(summary).toBeVisible();
    await expect(summary.getByTestId('account-counts-health')).toContainText('41 new');
    await expect(summary.getByTestId('account-counts-identity')).toContainText('1 newer here');
    await expect(summary.getByTestId('account-restore-replace')).not.toBeChecked();
    expect(imports.every((b) => b.dry_run === true)).toBe(true);
    // The location trail is never sent back.
    expect(JSON.stringify(imports)).not.toContain('location_history');
    await expectTapTargets(sheet);
    await page.screenshot({ path: testInfo.outputPath('account-restore-summary.png') });

    await expectReachable(summary.getByTestId('account-restore-apply'));
    await summary.getByTestId('account-restore-apply').click();
    const result = page.getByTestId('account-restore-result');
    await expect(result).toBeVisible();
    await expect(result.getByTestId('account-counts-bible')).toContainText('1 updated');
    const applied = imports.filter((b) => b.dry_run === false);
    expect(applied.map((b) => b.service)).toEqual(['identity', 'health', 'bible', 'geo']);
    expect(applied.every((b) => b.replace_conflicts === false && b.user === undefined)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath('account-restore-result.png') });
    expect(errors).toEqual([]);
  });

  test('a damaged file is refused and nothing is shown', async ({ page }) => {
    await mockGateway(page);
    const path = await makeExport(page);
    await page.getByRole('button', { name: 'Done' }).click();
    const bytes = await readFile(path);
    bytes[bytes.length - 3] ^= 0xff;
    await page.getByTestId('account-export-open-file').click();
    const sheet = page.getByTestId('account-open-sheet');
    await sheet.getByTestId('account-open-file').setInputFiles({ name: 'damaged.jarvis-export', mimeType: 'application/octet-stream', buffer: bytes });
    await sheet.getByTestId('account-open-password').fill(PASSWORD);
    await sheet.getByTestId('account-open-submit').click();
    await expect(sheet.getByTestId('account-open-error')).toContainText('changed or damaged');
    await expect(page.getByTestId('account-viewer')).toHaveCount(0);
  });

  test('an unfinished export is not saved', async ({ page }) => {
    await mockGateway(page);
    await page.route('**/api/account/export', (route) =>
      route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: exportStream().split('\n').slice(0, 3).join('\n') }));
    await (await openSettingsAccount(page)).click();
    const sheet = page.getByTestId('account-export-sheet');
    await sheet.getByTestId('account-export-password').fill(PASSWORD);
    await sheet.getByTestId('account-export-confirm').fill(PASSWORD);
    await sheet.getByTestId('account-export-understood').check();
    let downloaded = false;
    page.on('download', () => { downloaded = true; });
    await sheet.getByTestId('account-export-submit').click();
    await expect(sheet.getByTestId('account-export-error')).toContainText('stopped before it finished');
    expect(downloaded).toBe(false);
  });
});
