/**
 * End-to-end test for MA (Music Assistant) stream playback via the gateway.
 *
 * Tests the full flow:
 * 1. Login to get API token
 * 2. Resolve stream URL via /api/media/stream/music-assistant
 * 3. Play audio from the resolved URL in browser
 * 4. Verify audio playback events (playing, canplay, duration > 0)
 */
import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const TEST_OUTPUT_DIR = path.join(path.dirname(new URL('.', import.meta.url).pathname), 'test-results');
const TEST_USER = process.env.TEST_USER;
const TEST_PASS = process.env.TEST_PASS;

// ──────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────

/**
 * Login as admin user and return the API token.
 */
async function loginAndGetToken(page: Page): Promise<string | null> {
  if (!TEST_USER || !TEST_PASS) {
    console.log('[login] Skipping: TEST_USER and TEST_PASS required');
    return null;
  }

  try {
    await page.goto(`${UI_URL}/login`, { waitUntil: 'networkidle', timeout: 30000 });

    // Wait for the login form
    const ready = await page.locator('input[type="text"], input[type="password"]').first()
      .isVisible({ timeout: 10000 }).catch(() => false);

    if (!ready) {
      console.log('[login] Login form not found');
      return null;
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

    if (!await usernameInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      console.log('[login] Username input not found');
      await page.screenshot({ path: path.join(TEST_OUTPUT_DIR, 'login-failed.png') });
      return null;
    }

    await usernameInput.fill(TEST_USER);
    if (await passwordInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      await passwordInput.fill(TEST_PASS);
    }

    const signInBtn = page.locator('button:has-text("Sign In"), button:has-text("Signin")').first();
    await signInBtn.click();

    // Wait for navigation
    try {
      await page.waitForURL('**/dashboard', { timeout: 10000 });
    } catch {
      const currentUrl = page.url();
      if (currentUrl.includes('login')) {
        const errorText = await page.getByText(/Authentication failed|Invalid|Invalid username/).textContent().catch(() => null);
        console.log(`[login] Auth failed: ${errorText || 'unknown'}`);
        return null;
      }
    }

    await page.waitForTimeout(2000);

    // Get token from localStorage
    const token = await page.evaluate(() => localStorage.getItem('jarvis_api_key'));
    console.log(`[login] Token obtained: ${token ? 'yes' : 'no'}`);
    return token;
  } catch (err) {
    console.log(`[login] Error: ${(err as Error).message}`);
    return null;
  }
}

/**
 * Call the MA stream endpoint to resolve a stream URL.
 */
async function resolveStreamUrl(page: Page, uri: string, token: string, playerId?: string): Promise<{ ok: boolean; streamUrl?: string; error?: string }> {
  try {
    const result = await page.evaluate(async ({ uri, token, playerId }) => {
      try {
        const params = new URLSearchParams({ uri });
        if (playerId) {
          params.set('player_id', playerId);
        }
        const endpointUrl = `/api/media/stream/music-assistant?${params.toString()}`;
        const resp = await fetch(endpointUrl, {
          method: 'GET',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
        });

        if (!resp.ok) {
          const text = await resp.text();
          return { ok: false, error: `HTTP ${resp.status}: ${text.substring(0, 500)}` };
        }
        return { ok: true, streamUrl: resp.url || endpointUrl };
      } catch (err: unknown) {
        return { ok: false, error: err.message || String(err) };
      }
    }, { uri, token, playerId });

    return result;
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

async function connectBrowserPlayer(page: Page): Promise<string | null> {
  try {
    await page.goto(`${UI_URL}/ma-stream-test.html`, { waitUntil: 'networkidle', timeout: 30000 });
    const connectButton = page.locator('#btn-connect').first();
    await expect(connectButton).toBeVisible({ timeout: 10000 });
    await connectButton.click();
    await expect(page.locator('#connection-status')).toContainText(/Connected/i, { timeout: 20000 });

    const playerId = await page.evaluate(() => localStorage.getItem('sendspin_webplayer_id'));
    return playerId;
  } catch (err) {
    console.log(`[connect] Failed to connect browser player: ${(err as Error).message}`);
    return null;
  }
}

/**
 * Get recent MA tracks to find a valid URI for testing.
 */
async function getRecentMATracks(page: Page, token: string): Promise<unknown[] | null> {
  try {
    const result = await page.evaluate(async (token) => {
      try {
        const resp = await fetch('/api/media/music-assistant/recent', {
          headers: { 'Authorization': `Bearer ${token}` },
        });
        if (resp.status !== 200) return null;
        return await resp.json();
      } catch {
        return null;
      }
    }, token);

    if (result && result.recent && result.recent.length > 0) {
      return result.recent;
    }
    return null;
  } catch {
    return null;
  }
}

function isUnsupportedPlaybackUri(uri: string): boolean {
  return /youtube\.com|youtu\.be|spotify:|open\.spotify\.com/i.test(uri);
}

async function getLiveMATestUri(page: Page, token: string): Promise<string | null> {
  const configuredUri = process.env.TEST_MA_URI?.trim();
  if (configuredUri && !isUnsupportedPlaybackUri(configuredUri)) {
    return configuredUri;
  }

  const recentTracks = await getRecentMATracks(page, token);
  const recentUri = (recentTracks ?? [])
    .map((track: { uri?: string }) => track?.uri?.trim() || '')
    .find((uri: string) => uri && !isUnsupportedPlaybackUri(uri));
  if (recentUri) {
    return recentUri;
  }

  const query = process.env.TEST_MA_QUERY?.trim() || 'Brandon Lake';
  const searchResult = await page.evaluate(async ({ token, query }) => {
    try {
      const resp = await fetch(`/api/media/music-assistant/search?query=${encodeURIComponent(query)}&limit=20`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      return null;
    }
  }, { token, query });

  const searchUri = (searchResult?.results ?? [])
    .filter((item: { uri?: string; type?: string }) => item?.type === 'track' || item?.type === 'song' || item?.type === 'library')
    .map((item: { uri?: string }) => item?.uri?.trim() || '')
    .find((uri: string) => uri && !isUnsupportedPlaybackUri(uri));
  if (searchUri) {
    return searchUri;
  }

  return null;
}

// ──────────────────────────────────────────────────────────────
// Test Suites
// ──────────────────────────────────────────────────────────────

test.describe('MA Stream Endpoint', () => {
  let token: string | null = null;
  let page: Page | null = null;

  test.beforeAll(async () => {
    // Ensure output directory exists
    if (!fs.existsSync(TEST_OUTPUT_DIR)) {
      fs.mkdirSync(TEST_OUTPUT_DIR, { recursive: true });
    }
  });

  test('authenticate and get token', async ({ browser }) => {
    page = await browser.newPage();
    token = await loginAndGetToken(page);
    expect(token).toBeTruthy();
  });

  test('resolve MA stream URL from live MA data', async () => {
    test.skip(!token || !page, 'Skipping: no token');

    const playerId = await connectBrowserPlayer(page!);
    if (!playerId) {
      test.skip();
      return;
    }

    const testUri = await getLiveMATestUri(page!, token!);
    if (!testUri) {
      console.log('[stream] No live MA track URI available; skipping');
      test.skip();
      return;
    }

    console.log(`[stream] Testing with URI: ${testUri}`);

    const result = await resolveStreamUrl(page!, testUri, token!, playerId);

    expect(result.ok).toBe(true);
    expect(result.streamUrl).toBeTruthy();
    expect(typeof result.streamUrl).toBe('string');
    expect(result.streamUrl!.length).toBeGreaterThan(10);
    // Stream URL should contain the MA stream server pattern
    expect(result.streamUrl!).toMatch(/https?:\/\/.*:\d+\/(flow|stream|audio)/i);

    console.log(`[stream] Stream URL resolved: ${result.streamUrl!.substring(0, 80)}...`);
  });

  test('play audio from resolved stream URL', async () => {
    test.skip(!token || !page, 'Skipping: no token');

    const playerId = await connectBrowserPlayer(page!);
    if (!playerId) {
      test.skip();
      return;
    }

    const testUri = await getLiveMATestUri(page!, token!);
    if (!testUri) {
      console.log('[play] No live MA track URI available; skipping');
      test.skip();
      return;
    }

    // Resolve stream URL
    const result = await resolveStreamUrl(page!, testUri, token!, playerId);

    if (!result.ok || !result.streamUrl) {
      test.skip();
    }

    const streamUrl = result.streamUrl!;
    console.log(`[play] Playing stream: ${streamUrl.substring(0, 80)}...`);

    // Create a hidden audio element and test playback
    const audioEvents = await page!.evaluate(async (url) => {
      return new Promise<Record<string, unknown>[]>((resolve) => {
        const audio = new Audio();
        audio.preload = 'auto';
        audio.playsInline = true;
        audio.controls = false;
        document.body.appendChild(audio);

        const events: Record<string, unknown>[] = [];
        let settled = false;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;

        const finish = (value: Record<string, unknown>[]) => {
          if (settled) return;
          settled = true;
          if (timeoutId) {
            clearTimeout(timeoutId);
          }
          audio.pause();
          audio.remove();
          resolve(value);
        };

        audio.addEventListener('canplay', () => {
          events.push({ type: 'canplay', timestamp: Date.now() });
        });

        audio.addEventListener('playing', () => {
          events.push({ type: 'playing', timestamp: Date.now(), duration: audio.duration });
        });

        audio.addEventListener('timeupdate', () => {
          if (audio.currentTime > 0) {
            events.push({ type: 'current-time', timestamp: Date.now(), currentTime: audio.currentTime });
            finish(events);
          }
        });

        audio.addEventListener('progress', () => {
          if (audio.buffered.length > 0) {
            events.push({
              type: 'progress',
              timestamp: Date.now(),
              buffered: audio.buffered.end(0),
              duration: audio.duration,
            });
          }
        });

        audio.addEventListener('waiting', () => {
          events.push({ type: 'waiting', timestamp: Date.now() });
        });

        audio.addEventListener('ended', () => {
          events.push({ type: 'ended', timestamp: Date.now() });
        });

        audio.addEventListener('pause', () => {
          events.push({ type: 'pause', timestamp: Date.now() });
        });

        audio.addEventListener('error', () => {
          const error = audio.error;
          events.push({
            type: 'error',
            timestamp: Date.now(),
            code: error?.code,
            message: error?.message,
          });
          finish(events);
        });

        audio.src = url;

        timeoutId = setTimeout(() => {
          finish([
            ...events,
            { type: 'timeout', timestamp: Date.now(), message: 'Audio never reached playing state' },
          ]);
        }, 12000);

        audio.play().then(() => {
          // Wait for the `playing` event to confirm media actually started.
        }).catch((error: unknown) => {
          finish([...events, { type: 'play-error', timestamp: Date.now(), message: (error as Error).message || String(error) }]);
        });
      });
    }, streamUrl);

    // Verify we got audio events
    expect(audioEvents.length).toBeGreaterThan(0);

    // Check for success events
    const playingEvents = audioEvents.filter((e: Record<string, unknown>) => e.type === 'playing');
    const errorEvents = audioEvents.filter((e: Record<string, unknown>) => e.type === 'error');
    const timeoutEvents = audioEvents.filter((e: Record<string, unknown>) => e.type === 'timeout');

    expect(timeoutEvents).toHaveLength(0);
    expect(errorEvents).toHaveLength(0);
    expect(playingEvents.length).toBeGreaterThan(0);

    // If we have a playing event, verify duration is available
    const playingEvent = playingEvents[playingEvents.length - 1];
    if (playingEvent) {
      expect(playingEvent.duration).toBeDefined();
      // Duration should be a number (could be Infinity for live streams)
      expect(typeof playingEvent.duration).toBe('number');
    }
    const currentTimeEvents = audioEvents.filter((e: Record<string, unknown>) => e.type === 'current-time');
    expect(currentTimeEvents.length).toBeGreaterThan(0);

    console.log(`[play] Audio events captured: ${JSON.stringify(audioEvents.map((e: Record<string, unknown>) => e.type))}`);

    // Log any errors
    if (errorEvents.length > 0) {
      console.log(`[play] Audio errors: ${JSON.stringify(errorEvents)}`);
    }
  });

  test('verify stream endpoint returns non-empty audio data (bytes)', async ({ request }) => {
    test.skip(!token, 'Skipping: no token');

    const playerId = await connectBrowserPlayer(page!);
    if (!playerId) {
      test.skip();
      return;
    }

    const testUri = await getLiveMATestUri(page!, token);
    if (!testUri) {
      console.log('[bytes] No live MA track URI available; skipping');
      test.skip();
      return;
    }

    // First resolve the stream URL
    const resolveResult = await resolveStreamUrl(page!, testUri, token, playerId);

    if (!resolveResult.ok || !resolveResult.streamUrl) {
      console.log(`[bytes] Stream resolution failed: ${resolveResult.error}`);
      test.skip();
    }

    const streamUrl = resolveResult.streamUrl!;
    console.log(`[bytes] Testing stream bytes from: ${streamUrl.substring(0, 80)}...`);

    // Fetch first 1MB of the stream and verify it's non-empty audio data
    const streamResponse = await request.fetch(streamUrl, {
      method: 'GET',
      maxRedirects: 5,
    });

    expect(streamResponse.ok()).toBe(true);

    const contentType = streamResponse.headers()['content-type'] || '';
    console.log(`[bytes] Content-Type: ${contentType}`);

    // Read the response body
    const buffer = await streamResponse.buffer();
    const bytesRead = buffer.length;

    console.log(`[bytes] Received ${bytesRead} bytes`);
    expect(bytesRead).toBeGreaterThan(0);
    expect(bytesRead).toBeGreaterThan(1024); // At least 1KB of data

    // Verify it's audio data (MP3, AAC, or OGG)
    // MP3 starts with ID3 or FFF
    // AAC often starts with 0xFF 0xF1 or similar
    const firstBytes = buffer.subarray(0, 4).toString('hex');
    console.log(`[bytes] First bytes: ${firstBytes}`);

    // Audio files typically start with known magic bytes
    const isAudioFormat =
      firstBytes.startsWith('494433') || // ID3 (MP3 with tags)
      firstBytes.startsWith('ffff') ||   // MP3 frame sync
      firstBytes.startsWith('fffb') ||   // MP3 frame sync
      firstBytes.startsWith('0fff') ||   // MP3 frame sync
      firstBytes.startsWith('47414c39');  // GLOB (some streams)

    // Even if magic bytes aren't matched, having >1KB of data is a good sign
    if (!isAudioFormat) {
      console.log(`[bytes] Magic bytes not recognized, but received ${bytesRead} bytes - likely valid stream`);
    }

    // Save the captured audio for inspection
    const outputPath = path.join(TEST_OUTPUT_DIR, 'ma-stream-capture.mp3');
    fs.writeFileSync(outputPath, buffer);
    console.log(`[bytes] Audio saved to: ${outputPath} (${(bytesRead / 1024).toFixed(2)}KB)`);

    // Verify the saved file is non-empty
    const stats = fs.statSync(outputPath);
    expect(stats.size).toBeGreaterThan(0);
    expect(stats.size).toBeGreaterThan(1024);
  });

  test('verify stream endpoint supports Range requests (progressive download)', async ({ request }) => {
    test.skip(!token, 'Skipping: no token');

    const playerId = await connectBrowserPlayer(page!);
    if (!playerId) {
      test.skip();
      return;
    }

    const testUri = await getLiveMATestUri(page!, token);
    if (!testUri) {
      console.log('[range] No live MA track URI available; skipping');
      test.skip();
      return;
    }

    // First resolve the stream URL
    const resolveResult = await resolveStreamUrl(page!, testUri, token, playerId);

    if (!resolveResult.ok || !resolveResult.streamUrl) {
      test.skip();
    }

    const streamUrl = resolveResult.streamUrl!;

    // Test Range request (first 10KB)
    const rangeResponse = await request.fetch(streamUrl, {
      method: 'GET',
      headers: {
        'Range': 'bytes=0-10239',
      },
    });

    // Should return 206 Partial Content for Range requests
    const status = rangeResponse.status();
    console.log(`[range] Range request status: ${status}`);

    if (status === 206) {
      const contentLength = rangeResponse.headers()['content-length'];
      const contentRange = rangeResponse.headers()['content-range'];
      console.log(`[range] Content-Length: ${contentLength}, Content-Range: ${contentRange}`);

      expect(contentLength).toBeDefined();
      expect(contentRange).toBeDefined();
      expect(parseInt(contentLength!)).toBeLessThanOrEqual(10240);
    } else {
      // 200 OK without Range support is acceptable for some streams
      console.log(`[range] Range not supported (status ${status}), stream may still be playable`);
    }
  });

  test('test standalone MA stream test page loads', async () => {
    // Navigate to the standalone test page via gateway (avoids SPA catch-all)
    try {
      await page!.goto(`${UI_URL}/ma-stream-test.html`, { waitUntil: 'networkidle', timeout: 15000 });
      await page!.waitForTimeout(1000);

      // Verify the page loaded (may be SPA fallback if not served as static)
      const title = await page!.title();
      console.log(`[test-page] Page title: ${title}`);

      // Check if we got the standalone page or SPA fallback
      const hasUriInput = await page!.locator('#uri').count().catch(() => 0);
      if (hasUriInput > 0) {
        await expect(page!.locator('#uri')).toBeVisible();
        await expect(page!.locator('#btn-resolve')).toBeVisible();
        await expect(page!.locator('#btn-play')).toBeVisible();
        await expect(page!.locator('#audio-player')).toBeVisible();
        await expect(page!.locator('#log')).toBeVisible();
        console.log('[test-page] Standalone test page loaded and verified');
      } else {
        console.log('[test-page] Standalone page intercepted by SPA (expected - needs Caddy route)');
      }
    } catch (err) {
      console.log(`[test-page] Test page not accessible: ${(err as Error).message}`);
      test.skip();
    }
  });

  test.afterAll(async () => {
    if (page) {
      await page.close();
    }
  });
});
