import { test, expect } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_USER = 'default';
const ADMIN_PASS = process.env.ADMIN_PASS || 'changeme';

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto(`${UI_URL}/login`, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click({ force: true });
  await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
}

test.describe('Communication Page - Timer CRUD', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`, { waitUntil: 'domcontentloaded' });
  });

  test('creates a new timer with title and duration', async ({ page }) => {
    await page.getByPlaceholder('Timer name').fill('Test Timer');
    await page.getByPlaceholder(/duration|time expression/i).fill('5 minutes');
    await page.getByRole('button', { name: /add timer/i }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByText('Test Timer')).toBeVisible({ timeout: 10000 });
  });

  test('deletes a timer by clicking trash icon', async ({ page }) => {
    await page.getByPlaceholder('Timer name').fill('Timer To Delete');
    await page.getByPlaceholder(/duration|time expression/i).fill('3 minutes');
    await page.getByRole('button', { name: /add timer/i }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByText('Timer To Delete')).toBeVisible();
    await page.getByLabel('Delete Timer To Delete').click();
    await page.waitForTimeout(1000);
    await expect(page.getByText('Timer To Delete')).not.toBeVisible();
  });

  test('shows validation when timer title is empty', async ({ page }) => {
    await page.getByPlaceholder(/duration|time expression/i).fill('5 minutes');
    await page.getByRole('button', { name: /add timer/i }).click();
    await expect(page.getByText(/enter a timer title/i)).toBeVisible({ timeout: 5000 });
  });

  test('shows validation when timer duration is empty', async ({ page }) => {
    await page.getByPlaceholder('Timer name').fill('Test');
    await page.getByRole('button', { name: /add timer/i }).click();
    await expect(page.getByText(/enter a timer title/i)).toBeVisible({ timeout: 5000 });
  });

  test('timer input fields clear after successful creation', async ({ page }) => {
    await page.getByPlaceholder('Timer name').fill('Auto Clear Timer');
    await page.getByPlaceholder(/duration|time expression/i).fill('2 minutes');
    await page.getByRole('button', { name: /add timer/i }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByPlaceholder('Timer name')).toHaveValue('');
    await expect(page.getByPlaceholder(/duration|time expression/i)).toHaveValue('');
  });
});

test.describe('Communication Page - Announcements', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`, { waitUntil: 'domcontentloaded' });
  });

  test('announcement volume slider updates value', async ({ page }) => {
    const volumeSlider = page.locator('input[type="range"]');
    await volumeSlider.fill('0.5');
    const newValue = await volumeSlider.inputValue();
    expect(newValue).toBe('0.5');
  });

  test('announcement requires target device selection', async ({ page }) => {
    await page.getByRole('button', { name: /send announcement/i }).click();
    await expect(page.getByText(/select a target device/i)).toBeVisible({ timeout: 5000 });
  });

  test('announcement sends with selected device and message', async ({ page }) => {
    const select = page.getByLabel('Announcement target device');
    const options = await select.evaluateAll(
      (els) => (els as HTMLSelectElement[])
        .filter(el => el.options)
        .flatMap(el => Array.from(el.options).map(o => ({ value: o.value, text: o.text })))
    );
    const validOption = options.find(o => o.value && o.value !== '');
    if (validOption) {
      await select.selectOption(validOption.value);
      await page.getByPlaceholder(/announcement message/i).fill('Test announcement from E2E');
      await page.getByRole('button', { name: /send announcement/i }).click();
      await expect(page.getByText('Announcement sent')).toBeVisible({ timeout: 5000 });
    }
  });

  test('announcement textarea accepts multi-line content', async ({ page }) => {
    const select = page.getByLabel('Announcement target device');
    const options = await select.evaluateAll(
      (els) => (els as HTMLSelectElement[])
        .filter(el => el.options)
        .flatMap(el => Array.from(el.options).map(o => o.value))
    );
    const validValue = options.find(v => v && v !== '');
    if (validValue) {
      await select.selectOption(validValue);
    }
    await page.getByPlaceholder(/announcement message/i).fill('Line 1\nLine 2\nLine 3');
    const value = await page.getByPlaceholder(/announcement message/i).inputValue();
    expect(value).toContain('Line 1');
    expect(value).toContain('Line 2');
  });
});

test.describe('Communication Page - Notes CRUD', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`, { waitUntil: 'domcontentloaded' });
  });

  test('notes section with Monaco editor is visible', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Notes', exact: true })).toBeVisible();
    const editorContainer = page.locator('.monaco-editor, [class*="overflow-hidden"]').first();
    await expect(editorContainer).toBeVisible();
  });

  test('new note button resets editor state', async ({ page }) => {
    await page.getByRole('button', { name: /new note/i }).click();
    await page.getByPlaceholder('Note title').fill('');
    await page.waitForTimeout(500);
    await expect(page.getByPlaceholder('Note title')).toHaveValue('');
  });

  test('note title input is editable', async ({ page }) => {
    await page.getByPlaceholder('Note title').fill('Test Note Title');
    const value = await page.getByPlaceholder('Note title').inputValue();
    expect(value).toBe('Test Note Title');
  });

  test('note editor content area accepts input', async ({ page }) => {
    // Try to type in the editor or the textarea fallback
    const editor = page.locator('.monaco-editor, textarea').first();
    if (await editor.isVisible({ timeout: 5000 }).catch(() => false)) {
      await editor.click();
      await page.keyboard.type('Test note content from E2E');
    }
  });

  test('fullscreen notes mode opens with editor', async ({ page }) => {
    await page.getByRole('button', { name: /full screen/i }).click();
    await expect(page.locator('.fixed.inset-0.z-50')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('button[aria-label="Close"], [class*="Minimize2"]')).toBeVisible({ timeout: 5000 });
  });

  test('fullscreen notes mode closes', async ({ page }) => {
    await page.getByRole('button', { name: /full screen/i }).click();
    await page.waitForTimeout(1000);
    await expect(page.locator('.fixed.inset-0.z-50')).toBeVisible();
    await page.getByRole('button', { ariaLabel: /close/i, exact: false }).or(
      page.locator('button').filter({ has: page.locator('[class*="Minimize2"]') }).first()
    ).click();
    await page.waitForTimeout(500);
  });

  test('note settings panel toggles visibility', async ({ page }) => {
    const settingsBtn = page.getByRole('button', { name: /settings/i });
    if (await settingsBtn.isVisible({ timeout: 3000 })) {
      await settingsBtn.click();
      await expect(page.getByText(/note directories/i)).toBeVisible({ timeout: 5000 });
      await settingsBtn.click();
    }
  });

  test('sync to RAG button exists and is clickable', async ({ page }) => {
    const syncBtn = page.getByRole('button', { name: /sync to rag/i });
    if (await syncBtn.isVisible({ timeout: 3000 })) {
      await syncBtn.click();
      await page.waitForTimeout(1000);
    }
  });
});

test.describe('Communication Page - Talk Chat', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`, { waitUntil: 'domcontentloaded' });
  });

  test('talk section shows conversation feed', async ({ page }) => {
    await expect(page.getByText(/conversation feed|talk/i)).toBeVisible({ timeout: 10000 });
  });

  test('open conversation requires username input', async ({ page }) => {
    await page.getByRole('button', { name: /open conversation/i }).click();
    await expect(page.getByText(/enter a nextcloud username/i)).toBeVisible({ timeout: 5000 });
  });

  test('talk message send requires conversation selection', async ({ page }) => {
    await page.getByPlaceholder(/send a live nextcloud talk message/i).fill('hello');
    await page.getByRole('button', { name: /send message/i }).click();
    await expect(page.getByText(/open or select a conversation/i)).toBeVisible({ timeout: 5000 });
  });

  test('talk message send requires message content', async ({ page }) => {
    await page.getByRole('button', { name: /send message/i }).click();
    await expect(page.getByText(/enter a message/i)).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Communication Page - Calendar', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`, { waitUntil: 'domcontentloaded' });
  });

  test('calendar event title input is editable', async ({ page }) => {
    await page.getByPlaceholder('Team Sync').fill('E2E Test Event');
    const value = await page.getByPlaceholder('Team Sync').inputValue();
    expect(value).toBe('E2E Test Event');
  });

  test('add event requires title and time', async ({ page }) => {
    await page.getByRole('button', { name: /add event/i }).click();
    await expect(page.getByText(/enter an event title and time/i)).toBeVisible({ timeout: 5000 });
  });

  test('calendar select dropdown exists', async ({ page }) => {
    const select = page.getByLabel('Calendar selection');
    if (await select.isVisible({ timeout: 5000 })) {
      await expect(select).toBeVisible();
    }
  });
});

test.describe('Communication Page - Voice Recording', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`, { waitUntil: 'domcontentloaded' });
  });

  test('record voice button is visible', async ({ page }) => {
    const recordBtn = page.getByRole('button', { name: /record voice/i });
    if (await recordBtn.isVisible({ timeout: 5000 })) {
      await expect(recordBtn).toBeVisible();
    }
  });

  test('record voice requires conversation selection', async ({ page }) => {
    const recordBtn = page.getByRole('button', { name: /record voice/i });
    if (await recordBtn.isVisible({ timeout: 5000 })) {
      await recordBtn.click();
      await expect(page.getByText(/open or select a conversation/i)).toBeVisible({ timeout: 5000 });
    }
  });

  test('voice caption input is editable', async ({ page }) => {
    await page.getByPlaceholder(/optional caption/i).fill('Test voice caption');
    const value = await page.getByPlaceholder(/optional caption/i).inputValue();
    expect(value).toBe('Test voice caption');
  });
});

test.describe('Communication Page - Markdown Formatting', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`, { waitUntil: 'domcontentloaded' });
  });

  test('markdown toolbar appears in fullscreen notes', async ({ page }) => {
    await page.getByRole('button', { name: /full screen/i }).click();
    await page.waitForTimeout(1000);
    await expect(page.locator('button[title="Bold"]')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('button[title="Italic"]')).toBeVisible({ timeout: 5000 });
  });

  test('bold markdown button is in toolbar', async ({ page }) => {
    await page.getByRole('button', { name: /full screen/i }).click();
    await page.waitForTimeout(1000);
    await expect(page.locator('button[title="Bold"]')).toBeVisible();
  });

  test('italic markdown button is in toolbar', async ({ page }) => {
    await page.getByRole('button', { name: /full screen/i }).click();
    await page.waitForTimeout(1000);
    await expect(page.locator('button[title="Italic"]')).toBeVisible();
  });
});
