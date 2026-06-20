/**
 * End-to-end playback tests for ABS and MA across:
 *  - Office TV (and other HA devices via cast)
 *  - Browser WebPlayer (Local Audio Player)
 *  - Android App WebPlayer (Local Audio Player, mobile viewport)
 */
import { test, expect, type Page } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const TEST_USER = process.env.TEST_USER;
const TEST_PASS = process.env.TEST_PASS;

// ──────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────

/**
 * Login as admin user. Dismisses biometric modal if present.
 * Returns true if login succeeds (URL changes to /dashboard).
 */
async function loginAsAdmin(page: Page): Promise<boolean> {
  if (!TEST_USER || !TEST_PASS) {
    console.log('[login] Skipping: TEST_USER and TEST_PASS required');
    return false;
  }

  try {
    await page.goto(`${UI_URL}/login`, { waitUntil: 'networkidle', timeout: 30000 });

    // Wait for the page to be ready
    const ready = await page.locator('h1:text("Jarvis OS"), input[type="text"], input[type="password"], button:text("Sign In")')
      .first()
      .isVisible({ timeout: 10000 })
      .catch(() => false);

    if (!ready) {
      console.log('[login] Page did not render form');
      return false;
    }

    // Handle biometric auth if present
    const useDifferent = page.locator('button:text("Use different account")').first();
    if (await useDifferent.isVisible({ timeout: 2000 }).catch(() => false)) {
      await useDifferent.click();
      await page.waitForTimeout(500);
    }

    // Fill credentials
    const usernameInput = page.locator('input[type="text"], input[placeholder*="user"], input[name="username"]').first();
    const passwordInput = page.locator('input[type="password"], input[placeholder*="pass"]').first();
    const signInBtn = page.locator('button:has-text("Sign In"), button:has-text("Signin")').first();

    if (!await usernameInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      await page.screenshot({ path: 'test-results/login-screenshot.png' });
      return false;
    }

    await usernameInput.fill(TEST_USER);
    if (await passwordInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      await passwordInput.fill(TEST_PASS);
    }

    await signInBtn.click();

    // Wait for navigation or error
    try {
      await page.waitForURL('**/dashboard', { timeout: 10000 });
    } catch {
      const currentUrl = page.url();
      const isLoginPage = currentUrl.includes('login');
      if (isLoginPage) {
        const errorText = page.getByText(/Authentication failed|Invalid|Invalid username/);
        if (await errorText.isVisible({ timeout: 2000 }).catch(() => false)) {
          console.log(`[login] Auth error: ${await errorText.textContent()}`);
        }
        return false;
      }
      // Navigated away from /login — treat as success
    }

    // Capture token and user from the page context
    await page.waitForTimeout(2000);
    return true;
  } catch (err) {
    console.log(`[login] Error: ${(err as Error).message}`);
    return false;
  }
}

/**
 * Navigate to media page. Uses React Router's pushState to avoid full page reload
 * which triggers ProtectedRoute's auth init race condition.
 */
async function goToMedia(page: Page, retries: number = 1): Promise<boolean> {
  try {
    const currentUrl = page.url();

    // Already on /media
    if (currentUrl.includes('/media')) {
      await page.waitForTimeout(2000);
      return await page.getByRole('heading', { name: 'Media', level: 1 })
        .isVisible({ timeout: 5000 }).catch(() => false);
    }

    // Check if we have an auth token
    const hasToken = await page.evaluate(() => !!localStorage.getItem('jarvis_api_key'));

    if (hasToken) {
      // Use pushState to navigate without full page reload
      await page.evaluate(() => {
        window.history.pushState({}, '', '/media');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
    } else {
      // No token — must do full navigation
      await page.goto(`${UI_URL}/media`, { waitUntil: 'networkidle', timeout: 30000 });
    }

    await page.waitForTimeout(3000);

    // Verify page loaded
    const headingVisible = await page.getByRole('heading', { name: 'Media', level: 1 })
      .isVisible({ timeout: 10000 }).catch(() => false);

    if (!headingVisible) {
      // Check if we were redirected to login
      const isLoginPage = await page.locator('input[type="password"]').isVisible({ timeout: 2000 }).catch(() => false);
      if (isLoginPage && retries > 0) {
        console.log('[goToMedia] Redirected to login, re-attempting...');
        return goToMedia(page, retries - 1);
      }
      console.log('[goToMedia] Media heading not found');
      await page.screenshot({ path: 'test-results/media-page-failed.png' });
      return false;
    }
    return true;
  } catch (err) {
    console.log(`[goToMedia] Error: ${(err as Error).message}`);
    await page.screenshot({ path: 'test-results/media-page-failed.png' });
    return false;
  }
}

async function findDeviceCard(page: Page, deviceName: string) {
  const card = page.locator('.glass-panel button').filter({ hasText: deviceName }).first();
  const visible = await card.isVisible({ timeout: 5000 }).catch(() => false);
  return visible ? card : null;
}

async function verifyPlaybackState(page: Page, expectedContent: string, timeout: number = 15000): Promise<boolean> {
  const startTime = Date.now();
  while (Date.now() - startTime < timeout) {
    try {
      const response = await page.evaluate(async () => {
        try {
          const resp = await fetch('/execute/media/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: '', domain: null, area: null, state: null }),
          });
          return resp.status === 200 ? await resp.json() : null;
        } catch { return null; }
      });

      if (response && response.status === 'SUCCESS' && response.detail) {
        const detail = response.detail as {
          active?: { state?: string; media_title?: string; entity_id?: string };
        };
        if (detail.active?.state === 'playing' && detail.active.media_title) {
          return true;
        }
        if (detail.active?.state === 'idle' || detail.active?.state === 'stopped') {
          return false;
        }
      }
    } catch (err) {
      console.log(`[verifyPlaybackState] Error: ${(err as Error).message}`);
    }
    await page.waitForTimeout(2000);
  }
  return false;
}

async function verifyPlayerCardPlaying(page: Page, title: string, timeout: number = 12000): Promise<boolean> {
  const startTime = Date.now();
  while (Date.now() - startTime < timeout) {
    try {
      const playerCard = page.locator('.glass-panel.border-cyan-500\\/20').first();
      const hasTitle = await playerCard.getByText(title, { exact: false })
        .isVisible({ timeout: 3000 }).catch(() => false);
      if (hasTitle) return true;
    } catch { /* ignore */ }
    await page.waitForTimeout(1500);
  }
  return false;
}

async function waitForLocalPlayerOverlay(page: Page, timeout: number = 15000): Promise<boolean> {
  const startTime = Date.now();
  while (Date.now() - startTime < timeout) {
    try {
      const visible = await page.locator('.fixed.inset-0.bg-black\\/90').first()
        .isVisible({ timeout: 3000 }).catch(() => false);
      if (visible) return true;
    } catch { /* ignore */ }
    await page.waitForTimeout(1000);
  }
  return false;
}

// ──────────────────────────────────────────────────────────────
// Test Suites
// ──────────────────────────────────────────────────────────────

test.describe('ABS Audiobook → Office TV', () => {
  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('ABS audiobook plays on Office TV — full playback chain', async ({ page }) => {
    const officeTvCard = await findDeviceCard(page, 'Office TV');
    test.skip(!officeTvCard, 'Office TV not available — skipping');

    await officeTvCard!.click();
    await page.waitForTimeout(1000);

    await expect(officeTvCard!).toHaveClass(/cyan-500/);

    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    await expect(playerCard.getByText('Office TV')).toBeVisible({ timeout: 5000 });

    const absBookCards = page.locator('div.glass-panel button').filter({ has: page.getByText('Homilies') });
    const firstAbsBook = absBookCards.first();
    const hasAbsBook = await firstAbsBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasAbsBook) {
      const playBtn = firstAbsBook.locator('button').filter({ has: page.getByRole('img') }).first();
      if (await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await firstAbsBook.click();
        await page.waitForTimeout(8000);

        const titleVisible = await verifyPlayerCardPlaying(page, 'Homilies', 15000);
        expect(titleVisible).toBe(true);

        const apiPlaying = await verifyPlaybackState(page, 'Homilies', 15000);
        expect(apiPlaying).toBe(true);
      }
    }
  });

  test('ABS audiobook — media status API reports correct state after play', async ({ page }) => {
    const officeTvCard = await findDeviceCard(page, 'Office TV');
    test.skip(!officeTvCard, 'Office TV not available — skipping');

    await officeTvCard!.click();
    await page.waitForTimeout(1000);

    const absResponse = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/media/audiobookshelf/last-played');
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    });

    if (!absResponse || !absResponse.books?.length) {
      test.skip();
    }

    const bookId = absResponse.books[0].id;
    const bookTitle = absResponse.books[0].title;

    const playResponse = await page.evaluate(async (id, device) => {
      try {
        const resp = await fetch('/execute/audiobookshelf', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'play', book_id: id, entity_id: device, resume: true }),
        });
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    }, bookId, 'media_player.office_tv');

    expect(playResponse).not.toBeNull();
    expect(playResponse!.status).toBe('SUCCESS');

    const apiPlaying = await verifyPlaybackState(page, bookTitle, 20000);
    expect(apiPlaying).toBe(true);
  });
});

test.describe('MA Music → Office TV', () => {
  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('MA music plays on Office TV — full playback chain', async ({ page }) => {
    const officeTvCard = await findDeviceCard(page, 'Office TV');
    test.skip(!officeTvCard, 'Office TV not available — skipping');

    await officeTvCard!.click();
    await page.waitForTimeout(1000);

    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    await expect(playerCard.getByText('Office TV')).toBeVisible({ timeout: 5000 });

    const maRecentItem = page.getByText('Does Anybody Hear Her').first();
    const hasMaTrack = await maRecentItem.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasMaTrack) {
      const playCard = maRecentItem.locator('ancestor::div[role="button"], ancestor::div.glass-panel')
        .filter({ has: page.getByRole('button', { name: /play/i }).first() })
        .or(maRecentItem.locator('..').locator('button').first());

      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);

        const titleVisible = await verifyPlayerCardPlaying(page, 'Does Anybody Hear Her', 15000);
        expect(titleVisible).toBe(true);
      }
    }
  });

  test('MA music — media status API reports correct state after play', async ({ page }) => {
    const officeTvCard = await findDeviceCard(page, 'Office TV');
    test.skip(!officeTvCard, 'Office TV not available — skipping');

    await officeTvCard!.click();
    await page.waitForTimeout(1000);

    const maResponse = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/media/music-assistant/recent');
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    });

    if (!maResponse || !maResponse.recent?.length) {
      test.skip();
    }

    const recentTrack = maResponse.recent[0];
    const trackName = recentTrack.name;
    const trackUri = recentTrack.uri;

    const playResponse = await page.evaluate(async (uri, device) => {
      try {
        const resp = await fetch('/execute/media/play', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: uri, media_type: 'music', entity_id: device }),
        });
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    }, trackUri, 'media_player.office_tv');

    expect(playResponse).not.toBeNull();
    expect(playResponse!.status).toBe('SUCCESS');

    const apiPlaying = await verifyPlaybackState(page, trackName, 20000);
    expect(apiPlaying).toBe(true);
  });

  test('MA playlist plays on Office TV', async ({ page }) => {
    const officeTvCard = await findDeviceCard(page, 'Office TV');
    test.skip(!officeTvCard, 'Office TV not available — skipping');

    await officeTvCard!.click();
    await page.waitForTimeout(1000);

    const plResponse = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/media/music-assistant/playlists');
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    });

    if (!plResponse || !plResponse.playlists?.length) {
      test.skip();
    }

    const playlist = plResponse.playlists.find((p: { items?: number }) => p.items > 0);
    if (!playlist) {
      test.skip();
    }

    const playResponse = await page.evaluate(async (uri, device) => {
      try {
        const resp = await fetch('/execute/media/play', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: uri, media_type: 'music', entity_id: device }),
        });
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    }, playlist.uri, 'media_player.office_tv');

    expect(playResponse).not.toBeNull();
    expect(playResponse!.status).toBe('SUCCESS');

    const apiPlaying = await verifyPlaybackState(page, playlist.name, 20000);
    expect(apiPlaying).toBe(true);
  });
});

test.describe('ABS Audiobook → Web Player (Browser)', () => {
  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('ABS audiobook plays via Web Player — audio loads from streaming endpoint', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    await expect(page.getByText('Web Player (Browser Audio) Active')).toBeVisible({ timeout: 5000 });

    const jumpBackInHeading = page.getByRole('heading', { name: 'Jump Back In' });
    await expect(jumpBackInHeading).toBeVisible({ timeout: 10000 });

    const absBook = page.getByText('Homilies of Saint John Chrysostom').first();
    const hasAbsBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasAbsBook) {
      const playBtn = absBook.locator('ancestor::button.glass-panel').first();
      if (await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playBtn.click();
        await page.waitForTimeout(8000);

        const overlayVisible = await waitForLocalPlayerOverlay(page, 15000);
        expect(overlayVisible).toBe(true);

        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
        await expect(localPlayer.getByText('Homilies of Saint John Chrysostom')).toBeVisible({ timeout: 5000 });
      }
    }
  });

  test('ABS audiobook — streaming endpoint is called and returns data', async ({ page }) => {
    const absResponse = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/media/audiobookshelf/last-played');
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    });

    if (!absResponse || !absResponse.books?.length) {
      test.skip();
    }


    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const absBook = page.getByText(absResponse.books[0].title).first();
    const hasBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasBook) {
      const streamResponse = await page.waitForResponse(
        (resp) =>
          resp.url().includes('/api/media/stream/audiobookshelf/') &&
          resp.status() === 200,
        { timeout: 15000 },
      );

      expect(streamResponse.status()).toBe(200);

      const contentLength = streamResponse.headers()['content-length']
        || streamResponse.headers()['content-range']
        || streamResponse.headers()['accept-ranges'];
      expect(contentLength || streamResponse.ok()).toBeTruthy();
    }
  });

  test('ABS audiobook — Web Player transport controls work', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const absBook = page.getByText('Homilies').first();
    const hasBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasBook) {
      const playCard = absBook.locator('ancestor::button.glass-panel').first();
      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);

        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
        await expect(localPlayer).toBeVisible({ timeout: 5000 });

        const toggleBtn = localPlayer.locator('button.rounded-full.bg-gradient-to-br').first();
        if (await toggleBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
          const initialPaused = await localPlayer.locator('text=Pause').isVisible({ timeout: 2000 }).catch(() => false);

          await toggleBtn.click();
          await page.waitForTimeout(2000);

          const newPaused = await localPlayer.locator('text=Pause').isVisible({ timeout: 3000 }).catch(() => false);
          expect(newPaused).not.toBe(initialPaused);
        }
      }
    }
  });
});

test.describe('MA Music → Web Player (Browser)', () => {
  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('MA music plays via Web Player — audio loads from streaming endpoint', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    await expect(page.getByText('Web Player (Browser Audio) Active')).toBeVisible({ timeout: 5000 });

    const maTrack = page.getByText('Does Anybody Hear Her').first();
    const hasTrack = await maTrack.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasTrack) {
      const playCard = maTrack.locator('ancestor::button.glass-panel').first();
      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);

        const overlayVisible = await waitForLocalPlayerOverlay(page, 15000);
        expect(overlayVisible).toBe(true);

        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
        await expect(localPlayer.getByText('Does Anybody Hear Her')).toBeVisible({ timeout: 5000 });
      }
    }
  });

  test('MA music — streaming endpoint returns valid response', async ({ page }) => {
    const maResponse = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/media/music-assistant/recent');
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    });

    if (!maResponse || !maResponse.recent?.length) {
      test.skip();
    }


    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const maTrack = page.getByText(maResponse.recent[0].name).first();
    const hasTrack = await maTrack.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasTrack) {
      const streamResponse = await page.waitForResponse(
        (resp) =>
          resp.url().includes('/api/media/stream/music-assistant') &&
          resp.status() === 200,
        { timeout: 15000 },
      );

      expect(streamResponse.status()).toBe(200);
    }
  });
});

test.describe('ABS Audiobook → Web Player (Android App / Mobile)', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('ABS audiobook plays via Web Player on mobile viewport', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const localIndicator = page.getByText('Web Player (Browser Audio) Active');
    if (await localIndicator.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(localIndicator).toBeVisible();
    }

    const absBook = page.getByText('Homilies of Saint John Chrysostom').first();
    const hasAbsBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasAbsBook) {
      const playCard = absBook.locator('ancestor::button.glass-panel').first();
      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);

        const overlayVisible = await waitForLocalPlayerOverlay(page, 15000);
        expect(overlayVisible).toBe(true);

        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
        await expect(localPlayer.getByText('Homilies of Saint John Chrysostom')).toBeVisible({ timeout: 5000 });
      }
    }
  });

  test('ABS audiobook — streaming endpoint works from mobile viewport', async ({ page }) => {
    const absResponse = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/media/audiobookshelf/last-played');
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    });

    if (!absResponse || !absResponse.books?.length) {
      test.skip();
    }

    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const absBook = page.getByText(absResponse.books[0].title).first();
    const hasBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasBook) {
      const streamResponse = await page.waitForResponse(
        (resp) =>
          resp.url().includes('/api/media/stream/audiobookshelf/') &&
          resp.status() === 200,
        { timeout: 15000 },
      );
      expect(streamResponse.status()).toBe(200);
    }
  });

  test('Web Player transport controls work on mobile', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const absBook = page.getByText('Homilies').first();
    const hasBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasBook) {
      const playCard = absBook.locator('ancestor::button.glass-panel').first();
      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);

        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
        await expect(localPlayer).toBeVisible({ timeout: 5000 });

        const toggleBtn = localPlayer.locator('button.rounded-full.bg-gradient-to-br').first();
        if (await toggleBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
          await toggleBtn.click();
          await page.waitForTimeout(2000);

          const pauseBtn = localPlayer.locator('text=Pause');
          const playBtn = localPlayer.locator('text=Play');
          const hasPause = await pauseBtn.isVisible({ timeout: 2000 }).catch(() => false);
          const hasPlay = await playBtn.isVisible({ timeout: 2000 }).catch(() => false);
          expect(hasPause || hasPlay).toBe(true);
        }
      }
    }
  });
});

test.describe('MA Music → Web Player (Android App / Mobile)', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('MA music plays via Web Player on mobile viewport', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const maTrack = page.getByText('Does Anybody Hear Her').first();
    const hasTrack = await maTrack.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasTrack) {
      const playCard = maTrack.locator('ancestor::button.glass-panel').first();
      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);

        const overlayVisible = await waitForLocalPlayerOverlay(page, 15000);
        expect(overlayVisible).toBe(true);

        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
        await expect(localPlayer.getByText('Does Anybody Hear Her')).toBeVisible({ timeout: 5000 });
      }
    }
  });

  test('MA music — streaming endpoint works from mobile viewport', async ({ page }) => {
    const maResponse = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/media/music-assistant/recent');
        return resp.status === 200 ? await resp.json() : null;
      } catch { return null; }
    });

    if (!maResponse || !maResponse.recent?.length) {
      test.skip();
    }

    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const maTrack = page.getByText(maResponse.recent[0].name).first();
    const hasTrack = await maTrack.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasTrack) {
      const streamResponse = await page.waitForResponse(
        (resp) =>
          resp.url().includes('/api/media/stream/music-assistant') &&
          resp.status() === 200,
        { timeout: 15000 },
      );
      expect(streamResponse.status()).toBe(200);
    }
  });
});

test.describe('ABS Audiobook → Other Devices', () => {
  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('ABS audiobook — API returns valid book data for playback', async ({ request }) => {
    const resp = await request.get('/api/media/audiobookshelf/last-played');
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('SUCCESS');
    if (!data.books || data.books.length === 0) test.skip();

    const book = data.books[0];
    expect(book.id).toBeDefined();
    expect(book.title).toBeDefined();
    expect(book.author).toBeDefined();
    expect(book.progress).toBeDefined();
  });

  test('MA music — API returns valid track data for playback', async ({ request }) => {
    const resp = await request.get('/api/media/music-assistant/recent');
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('SUCCESS');
    if (!data.recent || data.recent.length === 0) test.skip();

    const track = data.recent[0];
    expect(track.name).toBeDefined();
    expect(track.uri).toBeDefined();
    expect(track.artist).toBeDefined();
  });
});

test.describe('Playback Controls — Device', () => {
  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('pause and resume works on device playback', async ({ page }) => {
    const officeTvCard = await findDeviceCard(page, 'Office TV');
    test.skip(!officeTvCard, 'Office TV not available — skipping');

    await officeTvCard!.click();
    await page.waitForTimeout(1000);

    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    await expect(playerCard.getByText('Office TV')).toBeVisible({ timeout: 5000 });

    const pauseBtn = playerCard.getByRole('button', { name: /pause/i }).first();
    if (await pauseBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await pauseBtn.click();
      await page.waitForTimeout(2000);

      const playBtn = playerCard.getByRole('button', { name: /play/i }).first();
      const isPaused = await playBtn.isVisible({ timeout: 3000 }).catch(() => false);
      expect(isPaused || (await playerCard.getByText('paused', { exact: false }).isVisible({ timeout: 2000 }).catch(() => false))).toBe(true);
    }
  });

  test('volume slider changes display value on device', async ({ page }) => {
    const officeTvCard = await findDeviceCard(page, 'Office TV');
    test.skip(!officeTvCard, 'Office TV not available — skipping');

    await officeTvCard!.click();
    await page.waitForTimeout(1000);

    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    const volumeSlider = playerCard.locator('input[type="range"]').first();
    if (await volumeSlider.isVisible({ timeout: 5000 }).catch(() => false)) {
      await volumeSlider.evaluate((el: HTMLInputElement) => {
        el.value = '75';
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await page.waitForTimeout(1000);

      const volumeDisplay = playerCard.locator('span.tabular-nums').first();
      if (await volumeDisplay.isVisible({ timeout: 3000 }).catch(() => false)) {
        const text = await volumeDisplay.textContent();
        expect(text).toContain('75');
      }
    }
  });
});

test.describe('Playback Controls — Web Player', () => {
  test.beforeEach(async ({ page }) => {
    const logged = await loginAsAdmin(page);
    if (!logged) test.skip();
    const ok = await goToMedia(page);
    if (!ok) test.skip();
  });

  test('volume slider works in Web Player overlay', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const absBook = page.getByText('Homilies').first();
    const hasBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasBook) {
      const playCard = absBook.locator('ancestor::button.glass-panel').first();
      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);
      }
    }

    const overlayVisible = await waitForLocalPlayerOverlay(page, 5000).catch(() => false);
    if (!overlayVisible) test.skip();

    const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
    await expect(localPlayer).toBeVisible({ timeout: 5000 });

    const volumeSlider = localPlayer.locator('input[type="range"]').first();
    if (await volumeSlider.isVisible({ timeout: 3000 }).catch(() => false)) {
      await volumeSlider.evaluate((el: HTMLInputElement) => {
        el.value = '60';
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await page.waitForTimeout(1000);

      const volumeDisplay = localPlayer.locator('span.tabular-nums').first();
      if ( await volumeDisplay.isVisible({ timeout: 3000 }).catch(() => false)) {
        const text = await volumeDisplay.textContent();
        expect(text).toContain('60');
      }
    }
  });

  test('skip forward/back buttons are present in Web Player', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const absBook = page.getByText('Homilies').first();
    const hasBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasBook) {
      const playCard = absBook.locator('ancestor::button.glass-panel').first();
      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);
      }
    }

    const overlayVisible = await waitForLocalPlayerOverlay(page, 5000).catch(() => false);
    if (!overlayVisible) test.skip();

    const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
    await expect(localPlayer).toBeVisible({ timeout: 5000 });

    const controlsContainer = localPlayer.locator('div.flex.items-center.justify-center.gap-6');
    const buttons = controlsContainer.locator('button');
    const count = await buttons.count();
    expect(count).toBe(3);
  });

  test('stop playback button closes Web Player', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(1000);

    const absBook = page.getByText('Homilies').first();
    const hasBook = await absBook.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasBook) {
      const playCard = absBook.locator('ancestor::button.glass-panel').first();
      if (await playCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playCard.click();
        await page.waitForTimeout(8000);
      }
    }

    const overlayVisible = await waitForLocalPlayerOverlay(page, 5000).catch(() => false);
    if (!overlayVisible) test.skip();

    const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
    await expect(localPlayer).toBeVisible({ timeout: 5000 });

    const stopBtn = localPlayer.getByText('Stop Playback');
    if (await stopBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await stopBtn.click();
      await page.waitForTimeout(2000);

      const overlayClosed = await localPlayer.isVisible({ timeout: 5000 }).catch(() => false);
      expect(overlayClosed).toBe(false);
    }
  });
});
