import { test, expect as baseExpect } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { quietHotReload } from '../fixtures/pageAudit';
import { expectNoHorizontalOverflow, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The Calls & messages banner when a child's phone stopped collecting: the
 * server's staleness check and the phone's missing permissions in plain words,
 * on every form factor (desktop, phones, tablets), inside the screen. Hermetic:
 * fixtures/familyPagesMocks.ts answers /api/guardian/collector-health/jordan
 * with texts and calls stale for 30 hours and two permissions off.
 */

test('a parent sees why a child\'s calls and texts are missing, on every screen', async ({ page }, info) => {
  test.setTimeout(90_000);
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));

  await page.goto('/family/member/jordan');
  await expect(page.getByTestId('member-header')).toContainText('Jordan');
  const mobile = (page.viewportSize()?.width ?? 1280) < 1024; // the rail shows from lg up
  await page.getByTestId(`member-${mobile ? 'tab' : 'rail'}-comms`).click();
  await expect(page.getByTestId('member-panel-comms')).toBeVisible();

  const banner = page.getByTestId('member-collection-banner');
  // "phone", "tablet" or "device": the word comes from the child's devices.
  await expect(banner).toContainText(/Jordan's (phone|tablet|device) has not sent texts or calls since [A-Z][a-z]{2} \d{1,2}\./);
  await expect(banner).toContainText(/Open Jarvis on the (phone|tablet|device) to restart collection\./);
  await expect(banner).toContainText('missing permission for Text messages and Notification access (app messages)');
  await expect(banner.getByTestId('member-collection-notice')).toHaveCount(2);

  await banner.scrollIntoViewIfNeeded();
  const width = page.viewportSize()?.width ?? 0;
  const box = await banner.boundingBox();
  expect(box, 'the banner is drawn').not.toBeNull();
  expect(box?.x ?? -1, 'the banner starts on screen').toBeGreaterThanOrEqual(0);
  expect((box?.x ?? 0) + (box?.width ?? 0), 'the banner stays on screen').toBeLessThanOrEqual(width + 1);
  // Every line wraps inside the banner instead of running past it.
  const overflowing = await banner.evaluate((el) => {
    const outer = el.getBoundingClientRect();
    return Array.from(el.querySelectorAll('li')).filter((li) => li.getBoundingClientRect().right > outer.right + 1).length;
  });
  expect(overflowing, 'a notice runs past the banner').toBe(0);
  // It sits above the Calls & messages totals, where a parent looks first.
  const totals = await page.getByTestId('member-comms-summary').boundingBox();
  expect((box?.y ?? 0)).toBeLessThan(totals?.y ?? 0);
  await expectNoHorizontalOverflow(page);
  await snap(page, info, 'collector-health-banner');

  expect(errors).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
});
