/**
 * Export my data and Open an export file (docs/ACCOUNT_EXPORT.md), hermetic:
 * the gateway is mocked in the browser and nothing reaches a server. The real
 * device crypto runs: the export is encrypted in the page, downloaded, opened
 * again with its password (and refused with a wrong one), read, and restored.
 * Runs in every hermetic project (desktop, phones, tablets).
 */
import { readFile, writeFile } from 'node:fs/promises';
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
      profile: [{ id: 1, username: 'parent', display_name: 'Pat Example', family_role: 'father' }],
      theme: [{ username: 'parent', data: '{"theme_id":"forest"}' }],
      dashboard_widgets: [{ id: 1, username: 'parent', widget_key: 'weather', visibility: 'visible', size: 'medium', order_index: 1, updated_at: 1000 }],
      assistant_conversations: [{ id: 7, username: 'parent', at: '2026-02-03T19:42:00', features: '["lights"]', turns: JSON.stringify([
        { role: 'user', text: 'Is the porch light on?' },
        { role: 'assistant', text: 'It is off. Do you want me to turn it on?' },
        { role: 'user', text: 'Yes please, and dim it to half.' },
        { role: 'assistant', text: 'Done: the porch light is on at 50%.' },
      ]) }],
    } },
    { kind: 'section', service: 'health', collections: {
      profile: [{ user_id: 'parent', display_name: 'Pat', birth_year: 1984, height_cm: 178, units: 'metric', activity_level: 'moderate', goal: 'maintain', kid_mode: false }],
      metrics: Array.from({ length: 40 }, (_, i) => ({ id: i + 1, user_id: 'parent', metric: 'weight', value: 70 + i / 10, unit: 'kg', source: 'manual',
        recorded_at: `2026-0${i < 20 ? 1 : 2}-${String((i % 20) + 1).padStart(2, '0')}T07:30:00` })),
      sleep: [1, 2, 3].map((d) => ({ id: d, user_id: 'parent', night: `2026-02-0${d}`, bedtime: `2026-02-0${d}T22:40:00`, wake_time: `2026-02-0${d + 1}T06:25:00`, duration_minutes: 445, efficiency: 0.9, quality: 'good' })),
      checkins: [{ id: 1, user_id: 'parent', day: '2026-01-02', mood: 4, energy: 3, note: `A very long note ${'Supercalifragilisticexpialidociousnessreallylongunbrokenword'.repeat(2)}` }],
    } },
    { kind: 'section', service: 'bible', collections: {
      marks: [
        { id: 1, username: 'parent', osis: 'John', chapter: 3, verse_start: 16, verse_end: 17, kind: 'highlight', color: 'yellow', version_code: 'kjv', note_preview: 'For God so loved the world', created_at: '2026-02-01T06:10:00', updated_at: '2026-02-01T06:10:00' },
        { id: 2, username: 'parent', osis: 'Ps', chapter: 23, verse_start: 1, kind: 'bookmark', version_code: 'kjv', created_at: '2026-01-20T06:10:00', updated_at: '2026-01-20T06:10:00' },
        { id: 3, username: 'parent', osis: 'Rom', chapter: 8, verse_start: 28, kind: 'highlight', color: 'blue', version_code: 'esv', created_at: '2026-01-12T06:10:00', updated_at: '2026-01-12T06:10:00' },
      ],
      memory_verses: [{ id: 1, username: 'parent', ref: 'Philippians 4:13', level: 3, due_on: '2026-02-10', created_at: '2026-01-01T00:00:00' }],
    } },
    { kind: 'section', service: 'geo', collections: {
      trips: [
        { id: 't1', user_id: 'parent', start_time: 1770130800, end_time: 1770132600, distance_miles: 12.4, duration_seconds: 1800, activity_type: 'driving',
          start_location: { name: 'Home' }, end_location: { name: 'Corner Cafe' }, vehicle_name: 'Family van' },
        { id: 't2', user_id: 'parent', start_time: 1770220800, end_time: 1770221700, distance_miles: 3.1, duration_seconds: 900, activity_type: 'driving',
          start_location: { name: 'Corner Cafe' }, end_location: { name: 'Home' } },
      ],
      daily_steps: [{ day: '2026-02-01', steps: '8000' }],
    } },
    { kind: 'section', service: 'geo', collections: { location_history: [{ t: 1770130800, lat: 1, lon: 2 }] } },
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
    await expect(viewer.getByTestId('account-viewer-health')).toContainText('45 items');
    const shot = async (name: string, at: import('@playwright/test').Locator) => {
      await at.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`${name}.png`) });
    };

    // Health: measurements by month, a night's sleep, a check-in.
    await viewer.getByTestId('account-viewer-health').getByRole('button', { name: /Health/ }).click();
    const health = viewer.getByTestId('account-viewer-health');
    await health.getByRole('button', { name: /Measurements/ }).click();
    await expect(health.getByTestId('account-kind-metrics')).toContainText('February 2026 · 20');
    await expect(health.getByTestId('account-kind-metrics')).toContainText('Weight 73.9 kg');
    await expect(health.getByRole('button', { name: /Show more \(15 left\)/ })).toBeVisible();
    await health.getByRole('button', { name: /Sleep/ }).click();
    await expect(health.getByTestId('account-kind-sleep')).toContainText('7 h 25 min, night of');
    await health.getByRole('button', { name: /Check-ins/ }).click();
    await expect(health).toContainText('A very long note');
    await shot('account-viewer-health', health.getByTestId('account-kind-metrics'));
    await shot('account-viewer-health-sleep', health.getByTestId('account-kind-sleep'));

    // Bible: highlights with their verse and colour, a bookmark.
    await viewer.getByTestId('account-viewer-bible').getByRole('button', { name: /Bible/ }).click();
    const bible = viewer.getByTestId('account-viewer-bible');
    await bible.getByRole('button', { name: /Highlights and bookmarks/ }).click();
    await expect(bible).toContainText('John 3:16-17');
    await expect(bible).toContainText('Yellow highlight · KJV');
    await expect(bible).toContainText('Psalms 23:1');
    await bible.getByRole('button', { name: /Memory verses/ }).click();
    await shot('account-viewer-bible', bible.getByTestId('account-kind-marks'));

    // Trips: from and to, the day and times, the distance.
    await viewer.getByTestId('account-viewer-geo').getByRole('button', { name: /Places, trips and steps/ }).click();
    const geo = viewer.getByTestId('account-viewer-geo');
    await geo.getByRole('button', { name: /^Trips/ }).click();
    await expect(geo).toContainText('Home to Corner Cafe');
    await expect(geo).toContainText('20 km'); // the health profile chose metric
    await shot('account-viewer-trips', geo.getByTestId('account-kind-trips'));

    // A conversation with Jarvis, as bubbles.
    await viewer.getByTestId('account-viewer-identity').getByRole('button', { name: /Profile and settings/ }).click();
    const identity = viewer.getByTestId('account-viewer-identity');
    await identity.getByRole('button', { name: /Conversations with Jarvis/ }).click();
    const transcript = identity.getByTestId('account-transcript');
    await expect(transcript).toContainText('Is the porch light on?');
    await expect(transcript).toContainText('Done: the porch light is on at 50%.');
    await shot('account-viewer-conversation', transcript);

    // Nothing internal shows: no field names, ids, raw flags or JSON.
    const shownText = await viewer.innerText();
    expect(shownText).not.toMatch(/\b[a-z]+_[a-z_]+\b|\btrue\b|\bfalse\b|\{"|recorded at|user id/);
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(sheet);

    await expectReachable(viewer.getByTestId('account-save-page'));
    await expectReachable(viewer.getByTestId('account-restore-preview'));
    const pageDownload = page.waitForEvent('download');
    await viewer.getByTestId('account-save-page').click();
    const pagePath = await (await pageDownload).path();
    const html = await readFile(pagePath, 'utf8');
    expect(html).toContain('jarvis-export-data');
    expect(html).not.toContain('Pat Example');
    expect(html).not.toContain('A very long note');

    // The saved page opens on its own, offline, and reads like the app.
    const htmlFile = testInfo.outputPath('saved-export.html');
    await writeFile(htmlFile, html);
    const standalone = await page.context().newPage();
    await standalone.goto(`file://${htmlFile}`);
    await standalone.locator('#pw').fill('wrong password entirely');
    await standalone.getByRole('button', { name: 'Open' }).click();
    await expect(standalone.locator('#msg')).toHaveText('That password does not open this file.');
    await standalone.locator('#pw').fill(PASSWORD);
    await standalone.getByRole('button', { name: 'Open' }).click();
    await expect(standalone.locator('#out')).toContainText('Health · 45');
    await expect(standalone.locator('#unlock')).toBeHidden();
    await standalone.getByText('Highlights and bookmarks').click();
    await expect(standalone.locator('#out')).toContainText('Yellow highlight · KJV');
    await standalone.getByText('Conversations with Jarvis').click();
    await expect(standalone.locator('.bubble').first()).toHaveText('Is the porch light on?');
    expect(await standalone.locator('#out').innerText()).not.toMatch(/\b[a-z]+_[a-z_]+\b|\btrue\b|\bfalse\b/);
    await standalone.screenshot({ path: testInfo.outputPath('account-encrypted-page.png'), fullPage: true });
    await standalone.close();

    await viewer.getByTestId('account-restore-preview').click();
    const summary = page.getByTestId('account-restore-summary');
    await expect(summary).toBeVisible();
    await expect(summary.getByTestId('account-counts-health')).toContainText('45 new');
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
    await expect(result.getByTestId('account-counts-bible')).toContainText('4 updated');
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
