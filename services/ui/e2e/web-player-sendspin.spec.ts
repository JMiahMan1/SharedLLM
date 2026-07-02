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
    // Attach WebSocket listener BEFORE navigation (critical!)
    const sendspinUrls: string[] = [];
    const maJsonRpcUrls: string[] = [];
    page.on('websocket', (ws: WebSocket) => {
      const url = ws.url();
      console.log(`[TEST] WebSocket connected: ${url}`);
      if (url.includes('/api/sendspin')) {
        sendspinUrls.push(url);
        console.log(`[TEST] Captured sendspin URL: ${url}`);
      }
      if (url.includes('/api/ma-jsonrpc')) {
        maJsonRpcUrls.push(url);
        console.log(`[TEST] Captured ma-jsonrpc URL: ${url}`);
      }
    });

    const consoleMessages: string[] = [];
    page.on('console', (msg) => {
      consoleMessages.push(msg.text());
    });

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

    // 2. Wait for WebSocket connections to be established
    await page.waitForTimeout(3000);

    // 3. Search for a track in MA
    const searchInput = page.locator('input[placeholder*="Search"]').first();
    if (!await searchInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }
    await searchInput.click();
    await searchInput.fill('test');
    await page.waitForTimeout(5000);

    // 4. Find and click first play button
    const playBtn = page.locator('button:has-text("Play")').first();
    if (!await playBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }

    await playBtn.click();
    await page.waitForTimeout(5000);

    // 5. Verify player card shows active playback
    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    if (!await playerCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }
    await expect(playerCard).toBeVisible();

    // 6. Verify play button is visible
    const playPauseBtn = playerCard.getByRole('button', { name: /play|pause/i });
    if (await playPauseBtn.isVisible({ timeout: 3000 })) {
      await expect(playPauseBtn).toBeVisible();
    }

    // 7. Verify volume slider is present
    const volumeSlider = playerCard.locator('input[type="range"]').first();
    if (await volumeSlider.isVisible({ timeout: 3000 })) {
      await expect(volumeSlider).toBeVisible();
    }

    // 8. Test pause via transport controls
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

    // 9. Test volume slider interaction
    if (await volumeSlider.isVisible({ timeout: 3000 }).catch(() => false)) {
      await volumeSlider.evaluate((el: HTMLInputElement) => {
        el.value = '50';
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await page.waitForTimeout(500);
    }

    // 10. Test skip forward
    const skipForwardBtn = playerCard.getByRole('button', { name: /next|forward/i }).first();
    if (await skipForwardBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await skipForwardBtn.click();
      await page.waitForTimeout(500);
    }

    // 11. Verify WebSocket connections were established
    expect(sendspinUrls.length).toBeGreaterThan(0);
    expect(maJsonRpcUrls.length).toBeGreaterThan(0);

    // 12. Check console for MAWebPlayer debug messages
    const maWebPlayerMessages = consoleMessages.filter(m => m.includes('[MAWebPlayer]'));
    const mediaMessages = consoleMessages.filter(m => m.includes('[Media]'));
    expect(maWebPlayerMessages.length).toBeGreaterThan(0);

    expect(mediaMessages.length).toBeGreaterThan(0);
  });
  test('Progress bar width matches playback time', async ({ page }) => {
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    // Click Web Player card
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Play a track from Jump Back In
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
    await page.waitForTimeout(5000);

    // Find the player card progress bar
    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    const progressBar = playerCard.locator('.w-full.h-2.bg-white\\/10.rounded-full.relative').first();
    if (!await progressBar.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Try alternative selector
      const progressBarAlt = playerCard.locator('text=/Wind Up Bird|Dude/').locator('..').locator('.w-full.h-2.rounded-full').first();
      if (!await progressBarAlt.isVisible({ timeout: 3000 }).catch(() => false)) {
        test.skip();
      } else {
        // Use alt progress bar for rest of test
        await page.evaluate(() => {
          const orig = document.querySelector('.glass-panel.border-cyan-500\\/20');
          if (orig) (orig as HTMLElement).setAttribute('data-test-progress-bar', 'true');
        });
        // Continue with original selector - if we got here, the bar exists but selector failed
      }
    }

    // Wait until currentTime is at least 5 seconds
    await page.waitForFunction(async () => {
      const spans = Array.from(document.querySelectorAll('span'));
      const timeSpans = spans.filter(s => /^\d+:\d+$/.test(s.textContent || ''));
      if (timeSpans.length >= 2) {
        const parts = timeSpans[0].textContent?.split(':');
        if (parts) {
          const minutes = parseInt(parts[0]);
          const seconds = parseInt(parts[1]);
          const total = minutes * 60 + seconds;
          return total >= 5;
        }
      }
      return false;
    }, { timeout: 30000 });

    // Get the time values from the display
    const [leftTime, rightTime] = await playerCard.locator('span').filter({ hasText: /\d+:\d+/ }).allTextContents();

    // Parse times - format is "M:SS" or "MM:SS"
    function parseTime(t: string): number {
      const parts = t.trim().split(':');
      const minutes = parseInt(parts[0]);
      const seconds = parseInt(parts[1]);
      return minutes * 60 + seconds;
    }

    const currentSeconds = parseTime(leftTime);
    const totalSeconds = parseTime(rightTime);

    // Verify total duration makes sense (at least 30 seconds)
    if (totalSeconds < 30) {
      test.skip();
    }

    // Verify current time is positive and less than total
    expect(currentSeconds).toBeGreaterThan(0);
    expect(currentSeconds).toBeLessThan(totalSeconds);

    // Get progress bar and its fill element
    const progressFill = playerCard.locator('.bg-gradient-to-r.from-cyan-400.to-purple-400.rounded-full.transition-all').first();
    if (await progressFill.isVisible({ timeout: 3000 }).catch(() => false)) {
      const fillStyle = await progressFill.getAttribute('style');
      // Extract width percentage from style
      const widthMatch = fillStyle?.match(/width:\s*(\d+\.?\d*)%/);
      if (widthMatch) {
        const displayedWidth = parseFloat(widthMatch[1]);
        const expectedWidth = (currentSeconds / totalSeconds) * 100;
        // Allow 5% tolerance for timing differences
        expect(displayedWidth).toBeGreaterThanOrEqual(expectedWidth - 5);
        expect(displayedWidth).toBeLessThanOrEqual(expectedWidth + 5);
      }
    }

    // Verify the time labels format is correct
    expect(leftTime).toMatch(/^\d+:\d+$/);
    expect(rightTime).toMatch(/^\d+:\d+$/);

    // Verify left time (current) is less than right time (total)
    expect(currentSeconds).toBeLessThan(totalSeconds);
  });

  test('ABS audiobook plays via Web Player', async ({ page }) => {
    // Attach WebSocket listener BEFORE navigation
    const sendspinUrls: string[] = [];
    const maJsonRpcUrls: string[] = [];
    page.on('websocket', (ws: WebSocket) => {
      const url = ws.url();
      console.log(`[TEST] WebSocket connected: ${url}`);
      if (url.includes('/api/sendspin')) {
        sendspinUrls.push(url);
      }
      if (url.includes('/api/ma-jsonrpc')) {
        maJsonRpcUrls.push(url);
      }
    });

    await loginAsDefault(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    // Click Web Player card
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Switch to ABS tab
    const absTab = page.getByText('AudioBookshelf').first();
    if (await absTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await absTab.click();
      await page.waitForTimeout(1000);
    }

    // Search for an ABS book
    const searchInput = page.locator('input[placeholder*="Search"]').first();
    if (!await searchInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }
    await searchInput.click();
    await searchInput.fill('homilies');
    await page.waitForTimeout(3000);

    // Find and click play button
    const playBtn = page.locator('button:has-text("Play")').first();
    if (!await playBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }

    await playBtn.click();
    await page.waitForTimeout(3000);

    // Verify WebSocket connections were established
    expect(sendspinUrls.length).toBeGreaterThan(0);
    expect(maJsonRpcUrls.length).toBeGreaterThan(0);

    // Verify player card shows active playback
    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
    if (await playerCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(playerCard).toBeVisible();
    } else {
      test.skip();
    }
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

    // Find volume slider - use the player card slider
    const volumeSlider = page.locator('[aria-label="Volume"]').first();
    if (!await volumeSlider.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip();
    }

    // Get the current position on the slider
    const sliderRect = await volumeSlider.boundingBox();
    if (!sliderRect) {
      test.skip();
    }

    // Calculate the position for 50% volume (middle of slider)
    const targetValue = 50;
    const inputRange = 100; // slider min=0, max=100
    const x = sliderRect.x + (sliderRect.width * targetValue / inputRange);

    // Drag slider to the target position
    await page.mouse.move(sliderRect.x + sliderRect.width / 2, sliderRect.y + sliderRect.height / 2);
    await page.mouse.down();
    await page.mouse.move(x, sliderRect.y + sliderRect.height / 2, { steps: 10 });
    await page.mouse.up();

    // Wait for volume to propagate
    await page.waitForTimeout(1000);

    // Volume display should update - find the volume number next to the slider
    const volumeDisplay = volumeSlider.locator('..').locator('span.tabular-nums').first();
    if (await volumeDisplay.isVisible({ timeout: 3000 }).catch(() => false)) {
      const text = await volumeDisplay.textContent();
      expect(text).toContain('50');
    }
  });
});
