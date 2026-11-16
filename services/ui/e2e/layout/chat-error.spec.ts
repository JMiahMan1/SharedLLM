import { test, expect as baseExpect } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { settle, shot } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * The Family chat list when the chat service cannot be reached (no Nextcloud
 * account yet): one clear card with Try again, and nothing drawn over it.
 * On a phone the "Chats" heading was drawn on top of the card's title.
 */
test('a chat list that cannot load shows one card, with nothing over it', async ({ page }, info) => {
  const mocks = await mockHome(page, 'admin');
  await mockFamilyPages(page);
  await page.route(/\/api\/communication\/talk\/conversations(\?|$)/, (route) =>
    // What the server really answers (HTTP 200, a FAILURE in the body).
    route.fulfill({ json: { status: 'FAILURE', service: 'talk', detail: null, events: null,
      message: 'No Nextcloud account is set up for this person. Add one under Settings > Integrations, or ask an admin to share the household account with you.' } }));
  await page.goto('/family');
  const card = page.getByTestId('conversations-error');
  await expect(card).toBeVisible();
  await settle(page);
  await shot(page, info, 'chat-error');
  // The card's own title is what is on top at its position: no heading drawn over it.
  const title = card.getByText("Couldn't load conversations");
  const box = (await title.boundingBox())!;
  const top = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.textContent ?? '', [box.x + 8, box.y + box.height / 2]);
  expect(top).toContain("Couldn't load conversations");
  await expect(page.getByRole('heading', { name: 'Chats' })).toHaveCount(0);
  expect(mocks.unmocked).toEqual([]);
});
