import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * "Show earlier messages" in a family chat whose newest page was thinned by
 * reactions. Talk counts a reaction as a message, and execution leaves those
 * out of the feed, so the page the app draws is shorter than the page asked
 * for; the server's `has_more` (counted before the reactions were taken out)
 * is what says there is more. A long poll's refresh of the same page must not
 * show "Loading" on the button either. Hermetic: the server is mocked.
 */

const TOKEN = 'room-trip';
const SEC = Math.floor(Date.now() / 1000);

function history(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    id: 900 - i, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin',
    timestamp: SEC - 60 * (i + 1), message_type: 'comment', message: `Trip note ${900 - i}`,
  }));
}

interface Feed {
  /** Pages asked for, by limit; and whether the long poll has news to give once. */
  limits: number[];
  news: boolean;
  /** Hold the next first-page fetch this long (a slow refresh). */
  slowMs: number;
  /** The newest id the room has (a reaction moves it on without a message to draw). */
  lastId: number;
}

async function serve(page: Page, feed: Feed): Promise<void> {
  // Registered after the shared mocks, so these answer first.
  await page.route(/\/api\/communication\/talk\/messages(\/wait)?(\?|$)/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/messages/wait')) {
      if (feed.news) {
        feed.news = false;
        feed.lastId += 1;
        return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [], last_id: feed.lastId, updated: [900] } } }).catch(() => {});
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
      return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [], last_id: feed.lastId } } }).catch(() => {});
    }
    const limit = Number(url.searchParams.get('limit')) || 50;
    feed.limits.push(limit);
    if (limit === 50 && feed.slowMs) {
      const wait = feed.slowMs;
      feed.slowMs = 0;
      await new Promise((resolve) => setTimeout(resolve, wait));
    }
    // 50 asked for, three of them reactions: 47 drawn, and the server says there is more.
    const detail = limit === 50
      ? { messages: history(47), last_id: feed.lastId, has_more: true }
      : { messages: history(80), last_id: feed.lastId, has_more: false };
    return route.fulfill({ json: { status: 'SUCCESS', detail } }).catch(() => {});
  });
}

async function open(page: Page, feed: Feed): Promise<string[]> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await serve(page, feed);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/family');
  await page.getByRole('list', { name: 'Conversations' }).getByText('Camping trip planning').first().click();
  await expect(page.getByTestId('chat-feed')).toContainText('Trip note 900');
  return errors;
}

test.describe('chat history after reactions', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a page thinned by reactions still offers earlier messages, and a refresh is not "Loading"', async ({ page }, info) => {
    const feed: Feed = { limits: [], news: false, slowMs: 0, lastId: 950 };
    const errors = await open(page, feed);
    const feedEl = page.getByTestId('chat-feed');
    const earlier = feedEl.getByRole('button', { name: 'Show earlier messages' });
    await expect(earlier).toBeVisible();

    // A reaction from someone else wakes the long poll; its refresh of the same page is slow.
    feed.slowMs = 2500;
    feed.news = true;
    const asked = feed.limits.length;
    await expect.poll(() => feed.limits.length, { timeout: 10_000 }).toBeGreaterThan(asked);
    // While that refresh is out, the button is still the offer, never "Loading".
    await expect(feedEl.getByRole('button', { name: /Loading/ })).toHaveCount(0);
    await expect(earlier).toBeVisible();
    await settle(page);
    const found = await screenFindings(page, feedEl, 'chat history');
    await shot(page, info, 'chat-history-earlier-offer');

    // Asking for earlier messages loads them, and then there is nothing more to offer.
    await earlier.click();
    await expect(feedEl).toContainText('Trip note 821');
    expect(feed.limits).toContain(100);
    await expect(feedEl.getByRole('button', { name: /Show earlier messages|Loading/ })).toHaveCount(0);

    expect(found, found.join('\n')).toEqual([]);
    expect(errors, errors.join(' | ')).toEqual([]);
  });
});
