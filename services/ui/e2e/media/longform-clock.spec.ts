import { test, expect, type Page } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { seedAuth, mockMediaApi } from '../fixtures/mediaMocks';

const SHOTS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../.tmp/shots');
fs.mkdirSync(SHOTS_DIR, { recursive: true });

/** 1h 06m 40s long, 1h 02m 05s in — an audiobook, which is what ABS sends. */
const LONGFORM_STATUS = {
  status: 'SUCCESS',
  message: 'ok',
  service: 'media',
  detail: {
    active: {
      entity_id: 'media_player.kitchen',
      friendly_name: 'Kitchen Speaker',
      state: 'playing',
      media_title: 'The Way of Kings',
      media_artist: 'Brandon Sanderson',
      media_album: 'Stormlight Archive',
      volume_level: 0.3,
      is_volume_muted: false,
      position: 3725,
      duration: 4000,
    },
    available: [],
    all_players: [
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'playing' },
    ],
  },
};

async function open(page: Page) {
  await mockMediaApi(page, 'happy');
  // Registered after the fixture route, so it takes precedence: this spec is
  // about the clock, and the shared fixture deliberately has nothing playing.
  await page.route('**/execute/media/status', (route) =>
    route.fulfill({ json: LONGFORM_STATUS }),
  );
  // The shell polls /api/logs; the shared mock aborts it, and the resulting
  // reconnect toast sits on top of the page in screenshots.
  await page.route('**/api/logs**', (route) => route.fulfill({ json: [] }));
  await seedAuth(page);
  await page.goto('/media', { waitUntil: 'domcontentloaded' });
  await expect(page.getByText('The Way of Kings').first()).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(1000);
}

test.describe('media: long-form track clock', () => {
  test('renders an hours field instead of counting minutes past 60', async ({ page }) => {
    await open(page);
    // The total is fixed by the payload. Elapsed time keeps ticking locally, so
    // match it by shape rather than by an exact second.
    await expect(page.getByText('1:06:40')).toBeVisible();
    await expect(page.getByText(/^1:0[0-9]:\d\d$/).first()).toBeVisible();
    // The old formatter's output, which must not come back.
    await expect(page.getByText('66:40')).toHaveCount(0);
    await page.screenshot({ path: path.join(SHOTS_DIR, 'media-longform-clock.png') });
  });

  test('the wider clock still fits the viewport', async ({ page }) => {
    await open(page);
    const m = await page.evaluate(() => ({
      docScrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
    }));
    expect(
      m.docScrollW,
      'the 8-character clock pushed the page wider than the viewport',
    ).toBeLessThanOrEqual(m.clientW + 1);

    // Name the widest offender so a failure says what is too wide. Elements
    // inside a deliberate horizontal scroller (the media chip strip) are skipped:
    // their chips are `shrink-0` by design and live past the fold on purpose.
    const culprits = await page.evaluate(() => {
      const limit = document.documentElement.clientWidth;
      const inScroller = (el: Element) => {
        for (let n = el.parentElement; n; n = n.parentElement) {
          const ov = getComputedStyle(n).overflowX;
          if (ov === 'auto' || ov === 'scroll') return true;
        }
        return false;
      };
      return Array.from(document.querySelectorAll<HTMLElement>('body *'))
        .filter((n) => n.getBoundingClientRect().right > limit + 1 && !inScroller(n))
        .slice(0, 5)
        .map((n) => ({
          tag: n.tagName.toLowerCase(),
          cls: (n.className || '').toString().slice(0, 70),
          testid: n.getAttribute('data-testid'),
          text: (n.textContent || '').trim().slice(0, 30),
          right: Math.round(n.getBoundingClientRect().right),
        }));
    });
    expect(culprits, `elements past the viewport: ${JSON.stringify(culprits)}`).toEqual([]);

    // And the clock row itself must not be squeezed by its own text.
    const row = await page.getByText('1:06:40').locator('xpath=..').evaluate((el) => ({
      scrollW: el.scrollWidth,
      clientW: el.clientWidth,
    }));
    expect(row.scrollW).toBeLessThanOrEqual(row.clientW + 1);
  });
});