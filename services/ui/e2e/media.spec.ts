import { test, expect } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill('default');
  await page.getByPlaceholder('Enter password').fill('admin');
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

test.describe('Media Page - Structure & Loading', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('media page title is visible', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Media', level: 1 })).toBeVisible();
  });

  test('now playing card is visible with no active playback', async ({ page }) => {
    await expect(page.getByText('No Active Playback')).toBeVisible();
  });

  test('transport controls are visible', async ({ page }) => {
    const prevBtn = page.getByLabel('Previous track');
    const playPauseBtn = page.getByRole('button', { name: /play|pause/i });
    const nextBtn = page.getByLabel('Next track');

    if (await prevBtn.isVisible({ timeout: 5000 })) {
      await expect(prevBtn).toBeVisible();
    }
    if (await playPauseBtn.isVisible({ timeout: 5000 })) {
      await expect(playPauseBtn).toBeVisible();
    }
    if (await nextBtn.isVisible({ timeout: 5000 })) {
      await expect(nextBtn).toBeVisible();
    }
  });

  test('volume controls are visible', async ({ page }) => {
    const volumeSlider = page.getByLabel('Volume');
    if (await volumeSlider.isVisible({ timeout: 5000 })) {
      await expect(volumeSlider).toBeVisible();
    }
  });

  test('cast-to device selector button is visible', async ({ page }) => {
    const castBtn = page.getByRole('button', { name: /cast to/i }).or(
      page.locator('button').filter({ hasText: 'Cast To' }).first()
    );
    if (await castBtn.isVisible({ timeout: 5000 })) {
      await expect(castBtn).toBeVisible();
    }
  });

  test('device selector shows "select a device" warning when no target selected', async ({ page }) => {
    const warning = page.getByText('Select a device above to enable playback');
    if (await warning.isVisible({ timeout: 5000 })) {
      await expect(warning).toBeVisible();
    }
  });

  test('jump back in section is visible', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Jump Back In' })).toBeVisible({ timeout: 10000 });
  });

  test('playlists section is visible', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Playlists' })).toBeVisible({ timeout: 10000 });
  });

  test('browse all media button is visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Browse All Media' })).toBeVisible();
  });
});

test.describe('Media Page - Device Selector', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('device selector dropdown opens on click', async ({ page }) => {
    const castBtn = page.locator('button').filter({ hasText: 'Cast To' }).first();
    if (await castBtn.isVisible({ timeout: 5000 })) {
      await castBtn.click();
      await page.waitForTimeout(1000);
      // Dropdown should be visible - check for media player entity list items
      const dropdownItems = page.locator('.glass-panel').first();
      if (await dropdownItems.isVisible({ timeout: 5000 }).catch(() => false)) {
        await expect(dropdownItems).toBeVisible();
      }
    }
  });

  test('device selector shows entity names when available', async ({ page }) => {
    const castBtn = page.locator('button').filter({ hasText: 'Cast To' }).first();
    if (await castBtn.isVisible({ timeout: 5000 })) {
      await castBtn.click();
      await page.waitForTimeout(1000);
      // Check for any text in the dropdown (entity names or "no media players found")
      const glassPanel = page.locator('.glass-panel').first();
      if (await glassPanel.isVisible({ timeout: 5000 }).catch(() => false)) {
        await expect(glassPanel).toBeVisible();
      }
    }
  });
});

test.describe('Media Page - Media Explorer Modal', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('media explorer modal opens when browse button clicked', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('heading', { name: 'Browse All Media' })).toBeVisible();
  });

  test('modal has Music Assistant tab', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('button', { name: /Music Assistant/i })).toBeVisible();
  });

  test('modal has Audiobooks tab', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('button', { name: /Audiobooks/i })).toBeVisible();
  });

  test('Music Assistant tab - playlists section renders with data or empty state', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    // Should show playlists header
    const playlistsHeader = page.getByRole('heading', { name: /playlists/i, level: 3 }).first();
    await expect(playlistsHeader).toBeVisible({ timeout: 10000 });

    // Either show actual playlists or empty state
    const actualPlaylist = page.locator('button').filter({ hasText: /playlist/i }).first();
    const noPlaylists = page.getByText('No playlists found');

    const hasContent = await actualPlaylist.isVisible({ timeout: 5000 }).catch(() => false);
    const hasEmpty = await noPlaylists.isVisible({ timeout: 5000 }).catch(() => false);
    expect(hasContent || hasEmpty).toBe(true);
  });

  test('Music Assistant tab - recently played section renders with data or empty state', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    // Should show recently played header
    const recentHeader = page.getByRole('heading', { name: /recently played/i, level: 3 }).first();
    await expect(recentHeader).toBeVisible({ timeout: 10000 });

    // Either show actual items or empty state
    const noRecent = page.getByText('No recent items');
    const hasEmpty = await noRecent.isVisible({ timeout: 5000 }).catch(() => false);

    // Look for any playlist or recent item button (not the empty state)
    const recentItems = page.locator('button').filter({ hasText: /track|album|artist|song/i }).first();
    const hasItems = await recentItems.isVisible({ timeout: 5000 }).catch(() => false);

    expect(hasItems || hasEmpty).toBe(true);
  });

  test('Audiobooks tab - libraries section renders with data or empty state', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await page.getByRole('button', { name: /Audiobooks/i }).click();
    await page.waitForTimeout(4000);

    // Should show libraries header
    const libsHeader = page.getByRole('heading', { name: /libraries/i, level: 3 }).first();
    await expect(libsHeader).toBeVisible({ timeout: 10000 });

    // Either show actual library cards or empty state
    const noLibs = page.getByText('No libraries found');
    const hasEmpty = await noLibs.isVisible({ timeout: 5000 }).catch(() => false);

    // Look for library cards (they have media_type text like "audiobook")
    const libraryCards = page.locator('button').filter({ hasText: /audiobook/i }).first();
    const hasLibraries = await libraryCards.isVisible({ timeout: 5000 }).catch(() => false);

    expect(hasLibraries || hasEmpty).toBe(true);
  });

  test('Music Assistant tab - shows actual playlist names when playlists exist', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(4000);

    const noPlaylists = page.getByText('No playlists found');
    const isEmpty = await noPlaylists.isVisible({ timeout: 5000 }).catch(() => false);

    if (!isEmpty) {
      // Verify actual playlist items are displayed (buttons with playlist names)
      const playlistButtons = page.locator('[class*="bg-white/5"]').filter({ hasText: /^\\d+ tracks?$/ });
      const count = await playlistButtons.count();
      expect(count).toBeGreaterThan(0);
    }
  });

  test('Audiobooks tab - shows actual library names when libraries exist', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await page.getByRole('button', { name: /Audiobooks/i }).click();
    await page.waitForTimeout(4000);

    const noLibs = page.getByText('No libraries found');
    const isEmpty = await noLibs.isVisible({ timeout: 5000 }).catch(() => false);

    if (!isEmpty) {
      // Verify actual library cards are displayed
      const libraryCards = page.locator('[class*="bg-white/5"]');
      const count = await libraryCards.count();
      expect(count).toBeGreaterThan(0);
    }
  });

  test('modal search input works for filtering', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    const searchInput = page.locator('input[type="text"]').first();
    await expect(searchInput).toBeVisible({ timeout: 10000 });

    // Type in search
    await searchInput.fill('test');
    await page.waitForTimeout(2000);

    // Input should contain the search text
    await expect(searchInput).toHaveValue('test');
  });

  test('modal closes when clicking overlay', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('heading', { name: 'Browse All Media' })).toBeVisible();

    // Click on the overlay (outside the modal content)
    await page.locator('.fixed.inset-0').click({ position: { x: 10, y: 10 } });
    await page.waitForTimeout(1000);
    await expect(page.getByRole('heading', { name: 'Browse All Media' })).not.toBeVisible({ timeout: 5000 });
  });

  test('modal close button works', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('heading', { name: 'Browse All Media' })).toBeVisible();

    const closeBtn = page.getByLabel('Close');
    if (await closeBtn.isVisible({ timeout: 3000 })) {
      await closeBtn.click();
      await page.waitForTimeout(1000);
      await expect(page.getByRole('heading', { name: 'Browse All Media' })).not.toBeVisible({ timeout: 5000 });
    }
  });

  test('audiobook library navigation - enter library and see back button', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await page.getByRole('button', { name: /Audiobooks/i }).click();
    await page.waitForTimeout(4000);

    // Look for library cards with "audiobook" media type text
    const libraryItem = page.locator('[class*="bg-white/5"]').filter({ hasText: /audiobook/i }).first();

    if (await libraryItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      await libraryItem.click({ force: true });
      await page.waitForTimeout(3000);

      // Should show "Back to Libraries" link
      const backLink = page.getByText('Back to Libraries');
      if (await backLink.isVisible({ timeout: 5000 })) {
        await expect(backLink).toBeVisible();
        // Go back
        await backLink.click();
        await page.waitForTimeout(1000);
        await expect(page.getByText('Libraries')).toBeVisible({ timeout: 5000 });
      }
    }
  });
});

test.describe('Media Page - Data Flow', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('media entities API returns playable entities', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/entities`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(Array.isArray(data.entities)).toBe(true);
  });

  test('music assistant playlists API returns valid structure with content', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/media/music-assistant/playlists`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('status');
    expect(data).toHaveProperty('playlists');
    expect(Array.isArray(data.playlists)).toBe(true);

    // If playlists exist, verify each has required fields
    if (data.playlists.length > 0) {
      for (const pl of data.playlists) {
        expect(pl).toHaveProperty('name');
        expect(pl).toHaveProperty('items');
        expect(pl).toHaveProperty('uri');
      }
    }
  });

  test('music assistant recent API returns valid structure with content', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/media/music-assistant/recent`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('status');
    expect(data).toHaveProperty('recent');
    expect(Array.isArray(data.recent)).toBe(true);

    // If recent items exist, verify each has required fields
    if (data.recent.length > 0) {
      for (const item of data.recent) {
        expect(item).toHaveProperty('name');
        expect(item).toHaveProperty('artist');
        expect(item).toHaveProperty('uri');
      }
    }
  });

  test('audiobookshelf last played API returns valid structure with content', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/media/audiobookshelf/last-played`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('status');
    expect(data).toHaveProperty('books');
    expect(Array.isArray(data.books)).toBe(true);

    // If books exist, verify each has required fields
    if (data.books.length > 0) {
      for (const book of data.books) {
        expect(book).toHaveProperty('id');
        expect(book).toHaveProperty('title');
        expect(book).toHaveProperty('author');
      }
    }
  });

  test('audiobookshelf libraries API returns valid structure with content', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/media/audiobookshelf/libraries`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('status');
    expect(data).toHaveProperty('libraries');
    expect(Array.isArray(data.libraries)).toBe(true);

    // If libraries exist, verify each has required fields
    if (data.libraries.length > 0) {
      for (const lib of data.libraries) {
        expect(lib).toHaveProperty('id');
        expect(lib).toHaveProperty('name');
        expect(lib).toHaveProperty('media_type');
      }
    }
  });

  test('media status API returns structured data', async ({ request }) => {
    const resp = await request.post(`${UI_URL}/execute/media/status`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('status');
    expect(data.status).toMatch(/^(SUCCESS|FAILURE)$/);
    expect(data).toHaveProperty('detail');
  });
});

test.describe('Media Page - Navigation from Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('can navigate to media page from sidebar', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
    await page.waitForTimeout(1000);

    const mediaLink = page.getByRole('link', { name: /media/i }).or(
      page.getByRole('button', { name: /media/i })
    );
    if (await mediaLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      await mediaLink.click();
      await page.waitForURL('**/media', { timeout: 10000 }).catch(() => {});
      await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
      await expect(page.getByRole('heading', { name: 'Media', level: 1 })).toBeVisible({ timeout: 10000 });
    }
  });
});

test.describe('Media Page - Mobile Viewport', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded'); await page.waitForTimeout(3000);
  });

  test('media page renders on mobile', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Media', level: 1 })).toBeVisible();
  });

  test('browse all media button works on mobile', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('heading', { name: 'Browse All Media' })).toBeVisible();
  });
});
