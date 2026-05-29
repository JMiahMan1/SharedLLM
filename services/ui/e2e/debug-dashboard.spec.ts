import { test } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_USER = 'default';
const ADMIN_PASS = 'admin';

test('debug dashboard content', async ({ page }) => {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(3000);
  
  const bodyText = await page.locator('body').innerText();
  console.log('Dashboard text:', bodyText.substring(0, 1000));
  
  const allText = await page.locator('*').allTextContents();
  const uniqueTexts = [...new Set(allText)].filter(t => t.trim().length > 0).slice(0, 50);
  console.log('Unique texts:', uniqueTexts);
});
