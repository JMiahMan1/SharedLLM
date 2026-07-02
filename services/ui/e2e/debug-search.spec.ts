import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

const UI_URL = process.env.UI_URL;
const TEST_USER = process.env.TEST_USER;
const TEST_PASS = process.env.TEST_PASS;

async function login(page: Page) {
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

test('debug search', async ({ page }) => {
  await login(page);
  await page.goto(`${UI_URL}/media`);
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(3000);

  // Click Web Player card
  const localPlayerCard = page.locator('button:has-text("Web Player")').first();
  await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
  await localPlayerCard.click();
  await page.waitForTimeout(500);

  // Find all search inputs
  const searchInputs = page.locator('input[type="text"]').all();
  console.log('Found', searchInputs.length, 'text inputs');
  
  for (let i = 0; i < searchInputs.length; i++) {
    const placeholder = await searchInputs[i].getAttribute('placeholder');
    const visible = await searchInputs[i].isVisible();
    console.log(`Input ${i}: placeholder="${placeholder}", visible=${visible}`);
  }
  
  // Try to find any input with search placeholder
  const anySearchInput = page.locator('input[placeholder*="Search"]').first();
  const hasSearchInput = await anySearchInput.isVisible({ timeout: 3000 }).catch(() => false);
  console.log('Has search input:', hasSearchInput);
  
  if (hasSearchInput) {
    await anySearchInput.click();
    await anySearchInput.fill('test');
    await page.waitForTimeout(3000);
  }
});
