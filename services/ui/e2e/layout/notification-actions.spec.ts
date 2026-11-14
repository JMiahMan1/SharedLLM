import { test, expect as baseExpect } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

/**
 * A Home question in the bell ("Lock the front door?") shows its buttons under the message, on
 * every form factor, and a tap answers it as the person through the gateway's push action, the
 * same call the phone's notification button makes. Hermetic: every call is answered here.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const NOTICE = {
  id: 'n-home-1',
  kind: 'home',
  title: 'Front door',
  body: 'The front door is unlocked. Lock it?',
  created_at: new Date().toISOString(),
  read: false,
  data: {},
  actions: [
    { label: 'Lock it', type: 'callback', callback: 'home_notification_action', params: { action: 'CONFIRM_c1', tag: 'door' } },
    { label: 'Leave it', type: 'callback', callback: 'home_notification_action', params: { action: 'DISMISS_c1', tag: 'door' } },
    { label: 'Say why', type: 'reply', callback: 'home_notification_action', params: { action: 'REPLY', tag: 'door' } },
  ],
};

test('a Home question in the bell is answered from its buttons on every screen', async ({ page }, info) => {
  test.setTimeout(60_000);
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/api/telemetry/notifications*', (route) => route.fulfill({ json: { notifications: [NOTICE] } }));

  await page.goto('/devices');
  await expect(page.getByTestId('page-header')).toBeVisible();
  await page.getByRole('button', { name: /^Notifications/ }).click();
  const card = page.getByTestId('notification-with-actions');
  await expect(card).toContainText('Lock it?');
  const actions = card.getByTestId('notification-actions');
  await expect(actions.getByRole('button', { name: 'Lock it' })).toBeInViewport();
  await expectTapTargets(actions);
  await expectNoHorizontalOverflow(page);
  await snap(page, info, 'notification-actions');

  // A reply asks for its text first, inside the panel.
  await actions.getByRole('button', { name: 'Say why' }).click();
  await actions.getByLabel('Say why').fill('Expecting a delivery');
  await expectNoHorizontalOverflow(page);
  await actions.getByRole('button', { name: 'Send' }).click();
  await expect(card.getByTestId('notification-action-done')).toHaveText('Sent: Say why');

  expect(mocks.posts.filter((p) => p.path === '/api/push/action').map((p) => p.body)).toEqual([
    { callback: 'home_notification_action', params: { action: 'REPLY', tag: 'door' }, reply: 'Expecting a delivery' },
  ]);
  expect(errors).toEqual([]);
  expect(mocks.unmocked).toEqual([]);
});
