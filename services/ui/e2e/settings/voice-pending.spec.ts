import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, PROFILES } from '../fixtures/homeMocks';
import { controlsOffScreen, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

/**
 * Sentences taught at a voice satellite for someone with no voice profile are
 * held until that person approves them from their own signed-in app. Hermetic,
 * and run in every project: desktop, phones, small phone and tablets. The
 * Vocal Signature panel lists them with "It was me" and "Not me, discard";
 * nothing joins the profile before a tap, and the buttons fit and are
 * thumb-sized on every screen.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const HELD = {
  id: 'session-test-1',
  satellite: 'assist_satellite.office_assist_satellite',
  samples: 4,
  created_at: '2026-11-20T18:00:00Z',
};

function voice(samples: number, pending: typeof HELD[]) {
  return { enrolled: samples >= 3, samples, min_samples: 3, max_samples: 6,
    consistency: samples >= 2 ? 0.93 : null, model: 'test', updated_at: samples ? '2026-11-20T18:05:00Z' : null, pending };
}

async function setUp(page: Page) {
  await mockHome(page, 'member');
  const calls: string[] = [];
  let state = voice(0, [HELD]);
  await page.route('**/api/spotify/status', (route) => route.fulfill({ json: { configured: false, missing: ['SPOTIFY_CLIENT_ID'], linked: false } }));
  await page.route('**/api/user-panel/devices', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/users/me/keys', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/users/me', (route) => route.fulfill({ json: PROFILES.member }));
  await page.route('**/api/users/me/voice', (route) => route.fulfill({ json: state }));
  await page.route('**/api/users/me/voice/pending/*', async (route) => {
    const method = route.request().method();
    calls.push(`${method} ${new URL(route.request().url()).pathname}`);
    if (method === 'POST') {
      state = voice(HELD.samples, []);
      return route.fulfill({ json: { status: 'SUCCESS', added: HELD.samples, ...state } });
    }
    state = voice(0, []);
    return route.fulfill({ json: { status: 'SUCCESS', ...state } });
  });
  return calls;
}

async function openPanel(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/identity');
  const panel = page.getByTestId('voice-panel');
  await panel.scrollIntoViewIfNeeded();
  await expect(panel).toBeVisible();
  return { panel, errors };
}

test.describe('voice taught at a satellite waits for approval', () => {
  test.describe.configure({ timeout: 90_000 });

  test('lists the held sentences, fits the screen, and adds them only on "It was me"', async ({ page }, info) => {
    const calls = await setUp(page);
    const { panel, errors } = await openPanel(page);
    const pending = panel.getByTestId('voice-pending');
    await expect(pending).toContainText('4 sentences were taught as your voice at the office satellite');
    await expect(pending).toContainText('Approve only if it was you');
    await expect(panel.getByTestId('voice-status')).toHaveText('Not set up');

    await pending.scrollIntoViewIfNeeded();
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(pending);
    expect(await controlsOffScreen(page)).toEqual([]);
    await snap(page, info, 'voice-pending');

    expect(calls).toEqual([]);
    await pending.getByRole('button', { name: /It was me/ }).click();
    await expect(panel.getByTestId('voice-status')).toHaveText('Enrolled · 4 recordings');
    await expect(panel.getByTestId('voice-pending')).toHaveCount(0);
    expect(calls).toEqual([`POST /api/users/me/voice/pending/${HELD.id}`]);
    expect(errors).toEqual([]);
  });

  test('"Not me, discard" throws them away without touching the profile', async ({ page }) => {
    const calls = await setUp(page);
    const { panel, errors } = await openPanel(page);
    await panel.getByRole('button', { name: /Not me, discard/ }).click();
    await expect(panel.getByTestId('voice-pending')).toHaveCount(0);
    await expect(panel.getByTestId('voice-status')).toHaveText('Not set up');
    expect(calls).toEqual([`DELETE /api/users/me/voice/pending/${HELD.id}`]);
    expect(errors).toEqual([]);
  });
});
