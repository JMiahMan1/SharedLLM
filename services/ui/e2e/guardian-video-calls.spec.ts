import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from './fixtures/homeMocks';
import { mockHealth } from './fixtures/healthMocks';
import { mockBible } from './fixtures/bibleMocks';
import { memberStatus, mockFamilyPages } from './fixtures/familyPagesMocks';
import { openAllDetails, screenFindings, settle, shot, quietHotReload } from './fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Video call checks on every form factor: a flagged video-call screenshot in
 * Concerns (the call, "Unsure, not sent", both checks, review, export and the
 * parent's one-tap block), and the Video calls card (checks, interval, the
 * corroboration count, the block switch, the lists, and a phone that cannot
 * take screenshots). Nothing may run past its card, nothing is cut off or off
 * the screen, and on phones every control is at least 44px. Hermetic.
 */

interface Run { mocks: HomeMocks; errors: string[]; found: string[]; saved: unknown[] }

const VIDEO_CALLS = {
  scan: true, interval_s: 45, max_frames: 20, corroborate_frames: 2, block: false,
  apps: ['com.whatsapp', 'us.zoom.videomeetings', 'com.example.a.very.long.package.name.for.small.phones'],
  sites: ['facetime.apple.com', 'meet.google.com', 'a-very-long-subdomain-name-for-small-phones.example.net'],
  custom_apps: false, custom_sites: false,
  labels: { 'com.whatsapp': 'WhatsApp', 'us.zoom.videomeetings': 'Zoom' },
};

const FRAME = {
  id: 'vc1', category: 'sexual', label: 'Sexual content', level: 'severe', excerpt: 'Possible nudity in the call',
  feature: 'video_call', action: 'video_call_frame', item: 'Chrome (facetime.apple.com)', has_image: true,
  at: new Date().toISOString(),
  video_call: { app: 'Chrome', package: 'com.android.chrome', site: 'facetime.apple.com', label: 'Chrome (facetime.apple.com)',
    call_started_at: new Date().toISOString(), frame_index: 0, call_id: 'call-a' },
  alert: { state: 'unsure', confirmed_frames: 1, needed: 2 },
  review: { by: 'vision', verdict: 'unsure', reason: 'skin visible', second_check: { confirmed: false, reason: 'ordinary clothing' } },
};

async function open(page: Page, path: string, ready: string): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const saved: unknown[] = [];
  // Registered last, so these answer before the shared family mocks.
  await page.route('**/api/screen-time/jordan/status*', (route) =>
    route.fulfill({ json: { ...memberStatus('jordan'), video_calls: VIDEO_CALLS } }));
  await page.route('**/api/guardian/concerns/jordan*', (route) =>
    route.fulfill({ json: { status: 'SUCCESS', concerns: [FRAME] } }));
  await page.route('**/api/screen-time/jordan/rules', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    saved.push(route.request().postDataJSON());
    return route.fulfill({ json: { username: 'jordan', rules: {} } });
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) errors.push(m.text().slice(0, 400));
  });
  await page.goto(path);
  await expect(page.getByTestId(ready)).toBeVisible().catch((e: Error) => {
    throw new Error(`${e.message}\npage errors: ${errors.join(' | ')}\nunmocked: ${mocks.unmocked.join(', ')}`);
  });
  return { mocks, errors, found: [], saved };
}

async function check(page: Page, info: TestInfo, run: Run, where: string, scope: Locator) {
  await settle(page);
  run.found.push(...await screenFindings(page, scope, where));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

function done(run: Run) {
  expect(run.found, run.found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('video call checks', () => {
  test.describe.configure({ timeout: 120_000 });

  test('a flagged video-call screenshot waits in Concerns, unsent, with the block one tap away', async ({ page }, info) => {
    const run = await open(page, '/family/member/jordan', 'member-concerns');
    const card = page.getByTestId('member-concerns');
    await card.scrollIntoViewIfNeeded();
    await expect(card.getByTestId('concern-video-call')).toContainText('Video call · Chrome (facetime.apple.com)');
    await expect(card.getByTestId('concern-alert-state')).toHaveText('Unsure, not sent');
    await expect(card.getByTestId('concern-second-check')).toContainText('did not agree');
    await expect(card.getByTestId('concern-review')).toBeVisible();
    await expect(card.getByTestId('concern-image-export')).toBeVisible();
    const block = card.getByTestId('concern-block-video-calls');
    await expect(block).toBeVisible();
    await check(page, info, run, 'video call concern', card);

    await block.click();
    await page.getByRole('button', { name: 'Block video calls', exact: true }).click();
    await expect.poll(() => run.saved.length).toBe(1);
    expect((run.saved[0] as { video_calls?: unknown }).video_calls).toEqual({ block: true });
    done(run);
  });

  test('the Video calls card: checks, how often, when to notify, the block and the lists', async ({ page }, info) => {
    const run = await open(page, '/family/member/jordan?section=apps', 'guardian-video-calls-card');
    const card = page.getByTestId('guardian-video-calls-card');
    await card.scrollIntoViewIfNeeded();
    await expect(card).toContainText('two separate checks agree on at least 2 screenshots');
    await expect(card.getByTestId('toggle-video-call-scan')).toHaveAttribute('aria-checked', 'true');
    await expect(card.getByTestId('toggle-video-call-block')).toHaveAttribute('aria-checked', 'false');
    await openAllDetails(card);
    await expect(card.getByTestId('video-call-sites')).toHaveValue(/facetime\.apple\.com/);
    await check(page, info, run, 'video calls card', card);

    await card.getByTestId('video-call-corroborate').selectOption('3');
    await expect.poll(() => run.saved.length).toBe(1);
    expect((run.saved[0] as { video_calls?: unknown }).video_calls).toEqual({ corroborate_frames: 3 });
    done(run);
  });
});
