import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, MESSAGES } from '../fixtures/familyPagesMocks';
import { quietHotReload } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * Typing in the family chat while the chat keeps polling: messages arriving
 * through the long poll must never touch the box being typed in (its text,
 * its focus, so a phone keyboard stays up), and the same goes for the fields
 * of a sheet opened over the chat. Hermetic: the server is mocked, and the
 * long poll delivers a new message every half second.
 */

const SEC = Math.floor(Date.now() / 1000);

interface Feed { arrived: Array<Record<string, unknown>>; sent: string[] }

async function liveRoom(page: Page): Promise<Feed> {
  const feed: Feed = { arrived: [], sent: [] };
  const next = () => {
    const id = 100 + feed.arrived.length;
    feed.arrived.push({ id, token: 'room-trip', actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin',
      message: `Packing list item ${id}`, timestamp: SEC + id, message_type: 'comment' });
  };
  // Registered after the shared mocks, so these answer first.
  await page.route(/\/api\/communication\/talk\/messages(\/wait)?(\?|$)/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname.endsWith('/wait')) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      next();
      return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [feed.arrived[feed.arrived.length - 1]] } } }).catch(() => {});
    }
    if (req.method() === 'POST') {
      feed.sent.push(String((req.postDataJSON() as { message?: string }).message));
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Chat message sent.' } });
    }
    const token = url.searchParams.get('token') ?? '';
    return route.fulfill({ json: { status: 'SUCCESS', detail: {
      messages: [...MESSAGES.map((m) => ({ ...m, token })), ...feed.arrived.filter((m) => m.token === token)] } } });
  });
  return feed;
}

async function open(page: Page, room = true): Promise<{ feed: Feed; errors: string[] }> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const feed = await liveRoom(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/family');
  const list = page.getByRole('list', { name: 'Conversations' });
  if (room) await list.getByText('Camping trip planning').first().click();
  else await expect(list.getByText('Camping trip planning').first()).toBeVisible();
  return { feed, errors };
}

/** Types one key at a time, the way a person does, with polls landing in between. */
async function typeSlowly(page: Page, text: string): Promise<void> {
  for (const ch of text) {
    await page.keyboard.type(ch);
    await page.waitForTimeout(60);
  }
}

test.describe('typing in the chat while it polls', () => {
  test.describe.configure({ timeout: 90_000 });

  test('messages arriving never touch the message being written', async ({ page }) => {
    const { feed, errors } = await open(page);
    const box = page.getByRole('textbox', { name: 'Message' });
    await box.click();
    await typeSlowly(page, 'Bring the big tent and ');
    await expect(page.getByText(/Packing list item 10[2-9]/).first()).toBeVisible();
    await typeSlowly(page, 'two lanterns');
    await expect(box).toBeFocused();
    await expect(box).toHaveValue('Bring the big tent and two lanterns');
    await box.press('Enter');
    await expect.poll(() => feed.sent).toEqual(['Bring the big tent and two lanterns']);
    await expect(box).toHaveValue('');
    expect(errors).toEqual([]);
  });

  test('the new chat sheet keeps the group name being typed', async ({ page }) => {
    // From the list (on a phone, an open conversation covers it). On a wide
    // screen the first conversation is open beside it, long polling.
    const { feed, errors } = await open(page, false);
    await page.getByTestId('chat-new').click();
    const sheet = page.getByTestId('new-chat-sheet');
    const people = sheet.getByRole('list', { name: 'People' });
    await people.getByRole('button').nth(0).click();
    await people.getByRole('button').nth(1).click();
    const name = sheet.getByRole('textbox', { name: 'Group name' });
    await name.click();
    const before = feed.arrived.length;
    await typeSlowly(page, 'Weekend ');
    // Messages land (and re-render the chat behind the sheet) mid-word.
    await expect.poll(() => feed.arrived.length - before, { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
    await typeSlowly(page, 'camping crew');
    await expect(name).toBeFocused();
    await expect(name).toHaveValue('Weekend camping crew');
    expect(errors).toEqual([]);
  });
});
