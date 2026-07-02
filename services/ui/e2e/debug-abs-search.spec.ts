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

test('debug abs search', async ({ page }) => {
  await login(page);
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
    await page.waitForTimeout(2000);
  }

  // Search for an ABS book
  const searchInput = page.locator('input[placeholder*="Search"]').first();
  if (await searchInput.isVisible({ timeout: 5000 }).catch(() => false)) {
    await searchInput.click();
    await searchInput.fill('homilies');
    await page.waitForTimeout(5000);
    
    // Check for search results
    const resultsText = page.locator('text=Search Results').first();
    const hasResults = await resultsText.isVisible({ timeout: 5000 }).catch(() => false);
    console.log('Has search results:', hasResults);
    
    // Find play button
    const playBtn = page.locator('button:has-text("Play")').first();
    const hasPlayBtn = await playBtn.isVisible({ timeout: 3000 }).catch(() => false);
    console.log('Has play button:', hasPlayBtn);
    
    if (hasPlayBtn) {
      await playBtn.click();
      await page.waitForTimeout(3000);
      console.log('Clicked play button');
    }
  }
  
  await page.pause();
});
