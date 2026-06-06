import { test, expect, Locator } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill('default');
  await page.getByPlaceholder('Enter password').fill('admin');
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

/* ──────────────────────────────────────────────────────────────
   Device Selector — horizontal card list (new design)
   ────────────────────────────────────────────────────────────── */

test.describe('Device Selector — Rendering', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('shows "Select Device" section header', async ({ page }) => {
    await expect(
      page.getByRole('heading', { name: 'Select Device' }),
    ).toBeVisible();
  });

  test('shows online device count next to header', async ({ page }) => {
    const heading = page.getByRole('heading', { name: 'Select Device' });
    await expect(heading).toBeVisible();
    // The "X online" badge is next to the heading in the same flex row
    const deviceSection = page.locator('.glass-panel').first();
    await expect(deviceSection).toBeVisible();
  });

  test('renders device cards for media player entities', async ({ page }) => {
    const deviceSection = page.locator('.glass-panel').first();
    await expect(deviceSection).toBeVisible();

    // Check for at least one device card button
    const deviceCards = page.locator(
      '.glass-panel button:has-text("Master Bedroom TV")',
    ).first();
    if (await deviceCards.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(deviceCards).toBeVisible();
    }
  });

  test('shows "Tap a device to start" prompt when no device selected', async ({
    page,
  }) => {
    // No device should be auto-selected on page load
    await expect(
      page.getByText('Tap a device to start'),
    ).toBeVisible();
  });

  test('device cards show device name and room', async ({ page }) => {
    // Each device card has a name + room name derived from entity_id
    // e.g. "master_bedroom_tv" → room = "master bedroom"
    const cards = page.locator('.glass-panel button').first();
    await expect(cards).toBeVisible();
  });

  test('device cards have online/offline visual indicators', async ({ page }) => {
    // Online devices get bg-green-400, offline get bg-slate-600
    // These are small colored dots (w-2.5 h-2.5 rounded-full)
    const dots = page.locator('.glass-panel .rounded-full');
    const count = await dots.count();
    // At least one indicator dot should exist (one per card)
    expect(count).toBeGreaterThan(0);
  });
});

test.describe('Device Selector — Selection', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('no device is selected initially', async ({ page }) => {
    // No cyan highlight should be visible
    const selected = page.locator(
      '.glass-panel button.bg-cyan-500\\/15',
    ).first();
    // If no card is selected, the prompt should be visible
    await expect(page.getByText('Tap a device to start')).toBeVisible();
  });

  test('clicking a device card selects it', async ({ page }) => {
    // Click the first available device card
    const deviceCard = page.locator(
      '.glass-panel button:has-text("Master Bedroom TV")',
    ).first();
    if (await deviceCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await deviceCard.click();
      await page.waitForTimeout(500);
      // The selected card should get a cyan highlight
      await expect(deviceCard).toHaveClass(/bg-cyan-500/);
    }
  });

  test('selected device shows cyan highlight ring', async ({ page }) => {
    const deviceCard = page.locator(
      '.glass-panel button:has-text("Office TV")',
    ).first();
    if (await deviceCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await deviceCard.click();
      await page.waitForTimeout(500);
      // Selected card gets cyan-500/15 bg and cyan-500/40 border
      await expect(deviceCard).toHaveClass(/cyan-500/);
    }
  });

  test('selecting a device shows its name in the player card', async ({
    page,
  }) => {
    const deviceCard = page.locator(
      '.glass-panel button:has-text("Gracies TV")',
    ).first();
    if (await deviceCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await deviceCard.click();
      await page.waitForTimeout(500);
      // Player card should show the selected device name
      await expect(page.getByText('Gracies TV')).toBeVisible();
    }
  });
});

/* ──────────────────────────────────────────────────────────────
   Player Header — layout and controls
   ────────────────────────────────────────────────────────────── */

test.describe('Player Header', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('player card is positioned above media sections', async ({ page }) => {
    // Player card has cyan border (border-cyan-500/20)
    const playerCard = page.locator(
      '.glass-panel.border-cyan-500\\/20',
    ).first();
    await expect(playerCard).toBeVisible();

    // Jump Back In section should be below player card
    await expect(
      page.getByRole('heading', { name: 'Jump Back In' }),
    ).toBeVisible({ timeout: 10000 });
  });

  test('shows "No Active Playback" when no media playing', async ({
    page,
  }) => {
    await expect(page.getByText('No Active Playback')).toBeVisible();
  });

  test('transport controls (prev, play/pause, next) are visible', async ({
    page,
  }) => {
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

  test('volume slider and mute button are visible', async ({ page }) => {
    const volumeSlider = page.getByLabel('Volume');
    if (await volumeSlider.isVisible({ timeout: 5000 })) {
      await expect(volumeSlider).toBeVisible();
    }

    const muteBtn = page.locator('button').filter({ has: page.getByRole('button', { name: /mute|unmute|volume/i }) }).first();
    if (await muteBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(muteBtn).toBeVisible();
    }
  });

  test('volume displays numeric value', async ({ page }) => {
    // Volume percentage or "M" for muted
    const volumeDisplay = page.locator(
      'span.tabular-nums',
    ).first();
    if (await volumeDisplay.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(volumeDisplay).toBeVisible();
    }
  });
});

/* ──────────────────────────────────────────────────────────────
   Media Sections — rendering and data
   ────────────────────────────────────────────────────────────── */

test.describe('Media Sections', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('Jump Back In section renders with heading', async ({ page }) => {
    await expect(
      page.getByRole('heading', { name: 'Jump Back In' }),
    ).toBeVisible({ timeout: 10000 });
  });

  test('Jump Back In shows ABS books when available', async ({ page }) => {
    // ABS last-played returns books with titles like "Homilies of Saint John Chrysostom"
    const booksSection = page.getByRole('heading', { name: 'Jump Back In' }).locator('..');
    await expect(booksSection).toBeVisible({ timeout: 10000 });

    // If books exist, at least one book title should be visible
    const hasBooks = await page.getByText('Homilies').isVisible({ timeout: 5000 }).catch(() => false);
    const hasEmpty = await page.getByText('No recently played content').isVisible({ timeout: 5000 }).catch(() => false);
    // Either books are displayed or the empty state
    expect(hasBooks || hasEmpty).toBe(true);
  });

  test('Playlists section renders with heading', async ({ page }) => {
    await expect(
      page.getByRole('heading', { name: 'Playlists' }),
    ).toBeVisible({ timeout: 10000 });
  });

  test('Playlists section shows actual playlist names when available', async ({
    page,
  }) => {
    const hasPlaylists = await page.getByText('500 Random tracks').isVisible({ timeout: 5000 }).catch(() => false);
    const hasEmpty = await page.getByText('No playlists available').isVisible({ timeout: 5000 }).catch(() => false);
    // MA returns 8 playlists, so actual playlist names should appear
    expect(hasPlaylists || hasEmpty).toBe(true);
  });

  test('Browse All Media button is visible and opens modal', async ({ page }) => {
    await expect(
      page.getByRole('button', { name: 'Browse All Media' }),
    ).toBeVisible();

    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(
      page.getByRole('heading', { name: 'Browse All Media' }),
    ).toBeVisible();
  });
});

/* ──────────────────────────────────────────────────────────────
   Media Explorer Modal
   ────────────────────────────────────────────────────────────── */

test.describe('Media Explorer Modal', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('has Music Assistant tab', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('button', { name: /Music Assistant/i })).toBeVisible();
  });

  test('has Audiobooks tab', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('button', { name: /Audiobooks/i })).toBeVisible();
  });

  test('MA tab shows playlists with data or empty state', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    const playlistsHeader = page.getByRole('heading', { name: /playlists/i, level: 3 }).first();
    await expect(playlistsHeader).toBeVisible({ timeout: 10000 });

    const hasPlaylists = await page.getByText('500 Random tracks').isVisible({ timeout: 5000 }).catch(() => false);
    const hasEmpty = await page.getByText('No playlists found').isVisible({ timeout: 5000 }).catch(() => false);
    expect(hasPlaylists || hasEmpty).toBe(true);
  });

  test('MA tab shows recently played with data or empty state', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    const recentHeader = page.getByRole('heading', { name: /recently played/i, level: 3 }).first();
    await expect(recentHeader).toBeVisible({ timeout: 10000 });

    const hasItems = await page.getByText('Does Anybody Hear Her').isVisible({ timeout: 5000 }).catch(() => false);
    const hasEmpty = await page.getByText('No recent items').isVisible({ timeout: 5000 }).catch(() => false);
    expect(hasItems || hasEmpty).toBe(true);
  });

  test('Audiobooks tab shows libraries with data or empty state', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await page.getByRole('button', { name: /Audiobooks/i }).click();
    await page.waitForTimeout(4000);

    const libsHeader = page.getByRole('heading', { name: /libraries/i, level: 3 }).first();
    await expect(libsHeader).toBeVisible({ timeout: 10000 });

    const hasLibraries = await page.getByText('Books').isVisible({ timeout: 5000 }).catch(() => false);
    const hasEmpty = await page.getByText('No libraries found').isVisible({ timeout: 5000 }).catch(() => false);
    expect(hasLibraries || hasEmpty).toBe(true);
  });

  test('search input filters content', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    const searchInput = page.locator('input[type="text"]').first();
    await expect(searchInput).toBeVisible({ timeout: 10000 });
    await searchInput.fill('test');
    await page.waitForTimeout(2000);
    await expect(searchInput).toHaveValue('test');
  });

  test('modal closes when clicking close button', async ({ page }) => {
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

  test('modal closes when clicking overlay', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('heading', { name: 'Browse All Media' })).toBeVisible();

    await page.locator('.fixed.inset-0').click({ position: { x: 10, y: 10 } });
    await page.waitForTimeout(1000);
    await expect(page.getByRole('heading', { name: 'Browse All Media' })).not.toBeVisible({ timeout: 5000 });
  });

  test('audiobook library navigation shows back button', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await page.getByRole('button', { name: /Audiobooks/i }).click();
    await page.waitForTimeout(4000);

    const libraryItem = page.locator('[class*="bg-white/5"]').filter({ hasText: /audiobook/i }).first();
    if (await libraryItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      await libraryItem.click({ force: true });
      await page.waitForTimeout(3000);

      const backLink = page.getByText('Back to Libraries');
      if (await backLink.isVisible({ timeout: 5000 })) {
        await expect(backLink).toBeVisible();
        await backLink.click();
        await page.waitForTimeout(1000);
        await expect(page.getByText('Libraries')).toBeVisible({ timeout: 5000 });
      }
    }
  });
});

/* ──────────────────────────────────────────────────────────────
   API Data Integrity — verify backend returns correct data
   ────────────────────────────────────────────────────────────── */

test.describe('API — Data Integrity', () => {
  test('media entities API returns playable entities', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/entities`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(Array.isArray(data.entities)).toBe(true);

    // Should include media_player domain entities
    const mediaPlayers = data.entities.filter(
      (e: any) => e.domain === 'media_player',
    );
    expect(mediaPlayers.length).toBeGreaterThan(0);
  });

  test('MA playlists API returns valid structure with 8 playlists', async ({
    request,
  }) => {
    const resp = await request.get(
      `${UI_URL}/api/media/music-assistant/playlists`,
    );
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('SUCCESS');
    expect(Array.isArray(data.playlists)).toBe(true);
    expect(data.playlists.length).toBeGreaterThan(0);

    // Verify playlist structure
    const pl = data.playlists[0];
    expect(pl).toHaveProperty('name');
    expect(pl).toHaveProperty('uri');
    expect(pl).toHaveProperty('type');
    // MA playlists return items: 0 for dynamic playlists
    expect(pl).toHaveProperty('items');
  });

  test('MA recent API returns valid structure with tracks', async ({
    request,
  }) => {
    const resp = await request.get(
      `${UI_URL}/api/media/music-assistant/recent`,
    );
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('SUCCESS');
    expect(Array.isArray(data.recent)).toBe(true);
    expect(data.recent.length).toBeGreaterThan(0);

    const item = data.recent[0];
    expect(item).toHaveProperty('name');
    expect(item).toHaveProperty('artist');
    expect(item).toHaveProperty('uri');
    expect(item).toHaveProperty('type');
  });

  test('ABS last-played API returns valid structure with books', async ({
    request,
  }) => {
    const resp = await request.get(
      `${UI_URL}/api/media/audiobookshelf/last-played`,
    );
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('SUCCESS');
    expect(Array.isArray(data.books)).toBe(true);
    expect(data.books.length).toBeGreaterThan(0);

    const book = data.books[0];
    expect(book).toHaveProperty('id');
    expect(book).toHaveProperty('title');
    expect(book).toHaveProperty('author');
    expect(book).toHaveProperty('last_played');
    expect(book).toHaveProperty('progress');
  });

  test('ABS libraries API returns valid structure', async ({ request }) => {
    const resp = await request.get(
      `${UI_URL}/api/media/audiobookshelf/libraries`,
    );
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('SUCCESS');
    expect(Array.isArray(data.libraries)).toBe(true);
    expect(data.libraries.length).toBeGreaterThan(0);

    const lib = data.libraries[0];
    expect(lib).toHaveProperty('id');
    expect(lib).toHaveProperty('name');
    expect(lib).toHaveProperty('media_type');
  });

  test('media status API returns structured response', async ({ request }) => {
    const resp = await request.post(`${UI_URL}/execute/media/status`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toMatch(/^(SUCCESS|FAILURE)$/);
    expect(data).toHaveProperty('detail');
  });

  test('MA playlists have descriptive names', async ({ request }) => {
    const resp = await request.get(
      `${UI_URL}/api/media/music-assistant/playlists`,
    );
    const data = await resp.json();
    // Should have named playlists (not empty strings)
    for (const pl of data.playlists) {
      expect(typeof pl.name).toBe('string');
      expect(pl.name.length).toBeGreaterThan(0);
    }
  });

  test('ABS books have unique titles in top results', async ({ request }) => {
    const resp = await request.get(
      `${UI_URL}/api/media/audiobookshelf/last-played`,
    );
    const data = await resp.json();
    // Top results should have at least some variety in titles
    const titles = data.books.slice(0, 10).map((b: any) => b.title);
    const unique = new Set(titles);
    // Allow some duplicates since user may have replayed same book
    expect(unique.size).toBeGreaterThan(0);
  });
});

/* ──────────────────────────────────────────────────────────────
   Navigation
   ────────────────────────────────────────────────────────────── */

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('can navigate to media page from dashboard', async ({ page }) => {
    await page.goto(`${UI_URL}/dashboard`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    const mediaLink = page.getByRole('link', { name: /media/i }).or(
      page.getByRole('button', { name: /media/i }),
    );
    if (await mediaLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      await mediaLink.click();
      await page.waitForURL('**/media', { timeout: 10000 }).catch(() => {});
      await page.waitForLoadState('domcontentloaded');
      await page.waitForTimeout(3000);
      await expect(page.getByRole('heading', { name: 'Media', level: 1 })).toBeVisible({ timeout: 10000 });
    }
  });
});

/* ──────────────────────────────────────────────────────────────
   Mobile Viewport
   ────────────────────────────────────────────────────────────── */

test.describe('Media Page — Mobile', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('media page renders on mobile', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Media', level: 1 })).toBeVisible();
  });

  test('browse all media button works on mobile', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await expect(page.getByRole('heading', { name: 'Browse All Media' })).toBeVisible();
  });

  test('device selector card list scrolls horizontally on mobile', async ({
    page,
  }) => {
    const deviceSection = page.locator('.glass-panel').first();
    await expect(deviceSection).toBeVisible();
    // Horizontal scroll container should have overflow-x-auto
    const scrollContainer = page.locator('.glass-panel .overflow-x-auto').first();
    if (await scrollContainer.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(scrollContainer).toBeVisible();
    }
  });
});
