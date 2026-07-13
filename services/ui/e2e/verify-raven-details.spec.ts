import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const TEST_USER = process.env.TEST_USER || 'default';
const TEST_PASS = process.env.TEST_PASS || 'changeme';

async function loginAsDefault(page: Page) {
  await page.goto(`${UI_URL}/login`);
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

test('Jarvis Lab - Inspect & Refine button opens modal with timeline and chat dialog', async ({ page }) => {
  test.setTimeout(45000);
  await loginAsDefault(page);

  // Navigate to Jarvis Lab page
  await page.goto(`${UI_URL}/lab`);
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2000);

  // Click on the Missions tab if not selected (though we defaulted it to first)
  const missionsTab = page.locator('button:text("Missions")').first();
  if (await missionsTab.isVisible()) {
    await missionsTab.click();
  }
  await page.waitForTimeout(1000);

  // Find the first "Inspect" or "Inspect & Refine" button
  const inspectBtn = page.getByRole('button', { name: /Inspect/i }).first();
  await expect(inspectBtn).toBeVisible({ timeout: 10000 });

  // Click the inspect button
  await inspectBtn.click();
  await page.waitForTimeout(1500);

  // Verify modal is open and shows correct titles
  const modalHeader = page.getByText(/Details & Refinement/i).first();
  await expect(modalHeader).toBeVisible({ timeout: 5000 });

  // Verify chat refinement input is visible
  const chatLabel = page.getByText(/Tweak or Fix Results/i).first();
  await expect(chatLabel).toBeVisible({ timeout: 5000 });

  // Verify timeline logs label is visible
  const timelineLabel = page.getByText(/Audit Execution Timeline/i).first();
  await expect(timelineLabel).toBeVisible({ timeout: 5000 });

  // Close the modal
  const closeBtn = page.locator('button:text("✖️")').first();
  if (await closeBtn.isVisible()) {
    await closeBtn.click();
  }
  await page.waitForTimeout(1000);

  // Assert modal is gone
  await expect(modalHeader).not.toBeVisible({ timeout: 3000 });
});
