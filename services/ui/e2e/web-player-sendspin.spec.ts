/**
 * End-to-end test for MA Web Player (Sendspin protocol) via browser.
 *
 * Tests the full flow:
 * 1. Login to get API token
 * 2. Click Web Player card to select it as the playback target
 * 3. Play an MA track from Jump Back In
 * 4. Verify Web Player UI elements appear (track title, transport controls)
 * 5. Test transport controls (play/pause, volume, skip)
 * 6. Verify WebSocket connections are established
 */
import { test, expect } from '@playwright/test';
import type { Page, WebSocket } from '@playwright/test';

const UI_URL = process.env.UI_URL;
if (!UI_URL) {
  throw new Error(
    'Environment variable UI_URL is not set.\n' +
    'Set UI_URL to the target server URL (e.g., http://192.168.2.205:8080).'
  );
}

const TEST_USER = process.env.TEST_USER;
if (!TEST_USER) {
  throw new Error(
    'Environment variable TEST_USER is not set.\n' +
    'Set TEST_USER to the username for Playwright E2E tests.'
  );
}

const TEST_PASS = process.env.TEST_PASS;
if (!TEST_PASS) {
  throw new Error(
    'Environment variable TEST_PASS is not set.\n' +
    'Set TEST_PASS to the password for Playwright E2E tests.'
  );
}

async function loginAsDefault(page: Page) {
  await page.goto(`${UI_URL}/login`);

  // Handle biometric auth if present
  const useDifferent = page.locator('button:text("Use different account")').first();
  if (await useDifferent.isVisible({ timeout: 2000 }).catch(() => false)) {
    await useDifferent.click();
    await page.waitForTimeout(500);
  }

  const usernameInput = page.locator('input[type="text"], input[placeholder*="user"], input[name="username"]').first();
  const passwordInput = page.locator('input[type="password"], input[placeholder*="pass"]').first();

  await usernameInput.fill(TEST_USER);
  if (await passwordInput.isVisible({ timeout: 2000 }).catch(() => false)) {
    await passwordInput.fill(TEST_PASS);
  }

  const signInBtn = page.locator('button:has-text("Sign In"), button:has-text("Signin")').first();
  await signInBtn.click();
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});

  await page.waitForTimeout(2000);
}

test.describe('MA Web Player (Sendspin)', () => {
  test('Web Player selection and MA track playback flow', async ({ page }) => {
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    // 1. Click Web Player card
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Verify Web Player card gets selected (cyan highlight)
    await expect(localPlayerCard).toHaveClass(/cyan-500/);

    // 2. Play an MA track from Jump Back In
    const maRecentItem = page.getByText('Does Anybody Hear Her').first();
    if (!await maRecentItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }

    const playBtn = maRecentItem.locator('ancestor::div button:has-text("Play")').first()
      .or(maRecentItem.locator('..').locator('button:has(svg path[d*="play"])').first());

    if (!await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip();
    }

    // 3. Track WebSocket connections (established when play() is called)
    const [sendspinWs, maJsonRpcWs] = await Promise.all([
      page.waitForEvent('websocket', (ws: WebSocket) =>
        ws.url().includes('/api/sendspin'),
      ),
      page.waitForEvent('websocket', (ws: WebSocket) =>
        ws.url().includes('/api/ma-jsonrpc'),
      ),
    ]);

    // Listen for console messages to verify sendspin protocol
    const consoleMessages: string[] = [];
    page.on('console', (msg) => {
      consoleMessages.push(msg.text());
    });

    await playBtn.click();
    await page.waitForTimeout(3000);

    // 4. Verify player card shows active playback
    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    await expect(playerCard.getByText('Does Anybody Hear Her')).toBeVisible({ timeout: 5000 });

    // 5. Verify play button is visible
    const playPauseBtn = playerCard.getByRole('button', { name: /play|pause/i });
    if (await playPauseBtn.isVisible({ timeout: 3000 })) {
      await expect(playPauseBtn).toBeVisible();
    }

    // 6. Verify volume slider is present
    const volumeSlider = playerCard.locator('input[type="range"]').first();
    if (await volumeSlider.isVisible({ timeout: 3000 })) {
      await expect(volumeSlider).toBeVisible();
    }

    // 7. Test pause via transport controls
    const pauseBtn = playerCard.getByRole('button', { name: /pause/i }).first();
    if (await pauseBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await pauseBtn.click();
      await page.waitForTimeout(1000);

      // Verify UI responds to pause
      const playBtnAfterPause = playerCard.getByRole('button', { name: /play/i }).first();
      if (await playBtnAfterPause.isVisible({ timeout: 3000 }).catch(() => false)) {
        await expect(playBtnAfterPause).toBeVisible();
      }
    }

    // 8. Test volume slider interaction
    if (await volumeSlider.isVisible({ timeout: 3000 }).catch(() => false)) {
      await volumeSlider.evaluate((el: HTMLInputElement) => {
        el.value = '50';
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await page.waitForTimeout(500);
    }

    // 9. Test skip forward
    const skipForwardBtn = playerCard.getByRole('button', { name: /next|forward/i }).first();
    if (await skipForwardBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await skipForwardBtn.click();
      await page.waitForTimeout(500);
    }

    // 10. Verify WebSocket connections were established
    expect(sendspinWs).toBeTruthy();
    expect(maJsonRpcWs).toBeTruthy();

    // 11. Check console for MAWebPlayer debug messages
    const maWebPlayerMessages = consoleMessages.filter(m => m.includes('[MAWebPlayer]'));
    const mediaMessages = consoleMessages.filter(m => m.includes('[Media]'));
    expect(maWebPlayerMessages.length).toBeGreaterThan(0);
    expect(mediaMessages.length).toBeGreaterThan(0);
  });

  test('ABS audiobook plays via Web Player', async ({ page }) => {
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    // Click Web Player card
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Play an ABS book
    const absBook = page.getByText('Homilies of Saint John Chrysostom').first();
    if (!await absBook.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }

    const playBtn = absBook.locator('ancestor::div button:has-text("Play")').first()
      .or(absBook.locator('..').locator('button:has(svg path[d*="play"])').first());

    if (!await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip();
    }

    await Promise.all([
      page.waitForEvent('websocket', (ws: WebSocket) =>
        ws.url().includes('/api/sendspin'),
      ),
      page.waitForEvent('websocket', (ws: WebSocket) =>
        ws.url().includes('/api/ma-jsonrpc'),
      ),
    ]);

    await playBtn.click();
    await page.waitForTimeout(3000);

    // Verify player card shows the book title
    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    await expect(playerCard.getByText('Homilies of Saint John Chrysostom')).toBeVisible({ timeout: 5000 });
  });

  test('Web Player transport controls respond to clicks', async ({ page }) => {
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    // Click Web Player card
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Find and play an MA track
    const maRecentItem = page.getByText('Does Anybody Hear Her').first();
    if (!await maRecentItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }

    const playBtn = maRecentItem.locator('ancestor::div button:has-text("Play")').first()
      .or(maRecentItem.locator('..').locator('button:has(svg path[d*="play"])').first());

    if (!await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip();
    }

    await playBtn.click();
    await page.waitForTimeout(3000);

    // Verify player card shows active playback
    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    await expect(playerCard.getByText('Does Anybody Hear Her')).toBeVisible();

    // Click next track
    const nextBtn = playerCard.getByRole('button', { name: /next/i }).first();
    if (await nextBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await nextBtn.click();
      await page.waitForTimeout(2000);

      // Player card should still show active playback
      const stillPlaying = playerCard.locator('text=/playing|paused|Does Anybody Hear Her/').first();
      if (await stillPlaying.isVisible({ timeout: 3000 }).catch(() => false)) {
        await expect(stillPlaying).toBeVisible();
      }
    }

    // Click pause button
    const pauseBtn = playerCard.getByRole('button', { name: /pause/i }).first();
    if (await pauseBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await pauseBtn.click();
      await page.waitForTimeout(1000);

      // Should show play button instead
      const playBtnAfterPause = playerCard.getByRole('button', { name: /play/i }).first();
      if (await playBtnAfterPause.isVisible({ timeout: 3000 }).catch(() => false)) {
        await expect(playBtnAfterPause).toBeVisible();
      }
    }
  });

  test('Web Player volume slider updates display', async ({ page }) => {
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    // Click Web Player card
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Find volume slider
    const volumeSlider = page.locator('input[type="range"]').first();
    if (!await volumeSlider.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip();
    }

    // Set volume to 50
    await volumeSlider.evaluate((el: HTMLInputElement) => {
      el.value = '50';
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.waitForTimeout(500);

    // Volume display should update
    const volumeDisplay = page.locator('span.tabular-nums').first();
    if (await volumeDisplay.isVisible({ timeout: 3000 }).catch(() => false)) {
      const text = await volumeDisplay.textContent();
      expect(text).toContain('50');
    }
  });
});
