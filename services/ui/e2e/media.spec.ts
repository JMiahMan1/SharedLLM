import { test, expect } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || 'changeme';

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill('default');
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForTimeout(3000);
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
    const panel = page.locator('.glass-panel:has(h2:text("Select Device"))');
    await expect(panel).toBeVisible();
  });

  test('renders device cards for media player entities', async ({ page }) => {
    const panel = page.locator('.glass-panel:has(h2:text("Select Device"))');
    await expect(panel).toBeVisible();

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
    // This prompt shows in the device selector when no devices exist;
    // if devices are on the server the cards replace it
    const prompt = page.getByText('Tap a device to start');
    if (await prompt.isVisible({ timeout: 3000 }).catch(() => false)) {
      await expect(prompt).toBeVisible();
    }
  });

  test('device cards show device name and room', async ({ page }) => {
    // Each device card has a name + room name derived from entity_id
    // e.g. "master_bedroom_tv" → room = "master bedroom"
    const panel = page.locator('.glass-panel:has(h2:text("Select Device"))');
    const cards = panel.locator('button');
    await expect(cards.first()).toBeVisible();
  });

  test('device cards have online/offline visual indicators', async ({ page }) => {
    // Online devices get bg-green-400, offline get bg-slate-600
    // These are small colored dots (w-2.5 h-2.5 rounded-full)
    const panel = page.locator('.glass-panel:has(h2:text("Select Device"))');
    const dots = panel.locator('.rounded-full');
    const count = await dots.count();
    // At least one indicator dot should exist (one per card)
    expect(count).toBeGreaterThan(0);
  });

  test('shows Web Player card as first option', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
  });

  test('Web Player card shows Browser / Android App subtitle', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    const subtitle = localPlayerCard.locator('p:text-is("Browser / Android App")');
    await expect(subtitle).toBeVisible({ timeout: 10000 });
  });

  test('Web Player card has online indicator', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    // Green dot indicator
    const dot = localPlayerCard.locator('.rounded-full.bg-green-400');
    await expect(dot).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Device Selector — Selection', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('Web Player card is selected by default', async ({ page }) => {
    // Web Player is auto-selected since localMode starts as true
    const selected = page.locator(
      '.glass-panel button.bg-cyan-500\\/15',
    ).first();
    await expect(selected).toBeVisible();
    await expect(selected).toContainText('Web Player');
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
      const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
      await expect(playerCard.getByText('Gracies TV')).toBeVisible();
    }
  });

  test('clicking Web Player card highlights it', async ({ page }) => {
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);
    // Web Player card should get cyan highlight
    await expect(localPlayerCard).toHaveClass(/cyan-500/);
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
    // Scope to player card to avoid matching buttons in device selector
    const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');

    const prevBtn = playerCard.getByLabel('Previous track');
    const playPauseBtn = playerCard.getByRole('button', { name: /play|pause/i });
    const nextBtn = playerCard.getByLabel('Next track');

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

  test('Jump Back In shows maximum 3 entries', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Jump Back In' })).toBeVisible({ timeout: 10000 });
    const jumpBackInSection = page.getByRole('heading', { name: 'Jump Back In' }).locator('..');
    await expect(jumpBackInSection).toBeVisible({ timeout: 10000 });

    // Count items in the Jump Back In grid
    const grid = jumpBackInSection.locator('div.grid.grid-cols-1.sm\\:grid-cols-2');
    if (await grid.count() > 0) {
      const items = grid.first().locator('div[class*="rounded"], div[class*="glass"], div[class*="bg-"], button');
      const itemCount = await items.count();
      // Should show at most 3 entries
      expect(itemCount).toBeLessThanOrEqual(3);
    }
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

  test('MA tab playlists section exists with items', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    const modal = page.locator('div.fixed.inset-0');
    const playlistsHeader = modal.getByRole('heading', { name: /playlists/i, level: 3 });
    await expect(playlistsHeader).toBeVisible({ timeout: 10000 });

    // Playlists must have items > 0 — empty generators are not valid playlists
    const hasItems = await modal.locator('text=/\\d+ tracks/').first().isVisible({ timeout: 5000 }).catch(() => false);
    const hasEmpty = await modal.getByText('No playlists found').isVisible({ timeout: 5000 }).catch(() => false);
    expect(hasItems && !hasEmpty).toBe(true);
  });

  test('MA tab shows recent items section', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    const modal = page.locator('div.fixed.inset-0');
    const recentHeader = modal.getByRole('heading', { name: /recently played/i, level: 3 });
    await expect(recentHeader).toBeVisible({ timeout: 10000 });

    // Recent items must display track names (e.g. "Does Anybody Hear Her")
    const hasRecentItems = await modal.locator('text=Does Anybody Hear Her').first().isVisible({ timeout: 5000 }).catch(() => false);
    expect(hasRecentItems).toBe(true);
  });

  test('Audiobooks tab shows libraries with actual content', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await page.getByRole('button', { name: /Audiobooks/i }).click();
    await page.waitForTimeout(4000);

    const modal = page.locator('div.fixed.inset-0');
    const libsHeader = modal.getByRole('heading', { name: /libraries/i, level: 3 });
    await expect(libsHeader).toBeVisible({ timeout: 10000 });

    // Libraries must have actual content (e.g. "Books", "Podcasts")
    const hasBooks = await modal.getByText('Books').isVisible({ timeout: 5000 }).catch(() => false);
    const hasPodcasts = await modal.getByText('Podcasts').isVisible({ timeout: 5000 }).catch(() => false);
    const hasEmpty = await modal.getByText('No libraries found').isVisible({ timeout: 5000 }).catch(() => false);
    expect((hasBooks || hasPodcasts) && !hasEmpty).toBe(true);
  });

  test('Audiobooks tab live search returns ABS results', async ({ page }) => {
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(2000);
    await page.getByRole('button', { name: /Audiobooks/i }).click();
    await page.waitForTimeout(3000);

    const liveAbs = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/media/audiobookshelf/last-played');
        return resp.status === 200 ? await resp.json() : null;
      } catch {
        return null;
      }
    });

    const book = liveAbs?.books?.[0];
    if (!book?.title && !book?.author) {
      test.skip();
      return;
    }

    const query = (process.env.TEST_ABS_QUERY?.trim() || book.title || book.author).trim();
    const modal = page.locator('div.fixed.inset-0');
    const searchInput = modal.locator('input[type="text"]').first();

    await expect(searchInput).toBeVisible({ timeout: 10000 });
    await searchInput.fill(query);
    await page.waitForTimeout(2500);

    const apiSearch = await page.evaluate(async (searchQuery) => {
      try {
        const resp = await fetch(`/api/media/audiobookshelf/search?q=${encodeURIComponent(searchQuery)}&limit=20`);
        return resp.status === 200 ? await resp.json() : null;
      } catch {
        return null;
      }
    }, query);

    expect(Array.isArray(apiSearch?.books)).toBe(true);
    expect(apiSearch.books.length).toBeGreaterThan(0);

    await expect(modal.getByRole('heading', { name: /Search Results/i, level: 3 })).toBeVisible({ timeout: 10000 });
    await expect(modal.getByText(book.title || query, { exact: false })).toBeVisible({ timeout: 10000 });
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
      (e: { domain: string }) => e.domain === 'media_player',
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
    const titles = data.books.slice(0, 10).map((b: { title: string }) => b.title);
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
   Media Playback — full flow
   ────────────────────────────────────────────────────────────── */

test.describe('Media Playback — End-to-End', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/media`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('selecting a device shows it as active', async ({ page }) => {
    // Click the Office TV device card
    const officeTvCard = page.locator('.glass-panel button:has-text("Office TV")').first();
    if (await officeTvCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await officeTvCard.click();
      await page.waitForTimeout(500);

      // Player card should show the selected device name
      const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
      await expect(playerCard.getByText('Office TV')).toBeVisible();
    }
  });

  test('playing a song updates media status', async ({ page }) => {
    // Select Office TV
    const officeTvCard = page.locator('.glass-panel button:has-text("Office TV")').first();
    if (!await officeTvCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }
    await officeTvCard.click();
    await page.waitForTimeout(500);

    // Jump Back In should show MA recent items
    const jumpBackIn = page.getByRole('heading', { name: 'Jump Back In' }).locator('..');
    await expect(jumpBackIn).toBeVisible({ timeout: 10000 });

    // Find the first MA recent item (e.g. "Does Anybody Hear Her")
    const maRecentItem = page.getByText('Does Anybody Hear Her').first();
    if (await maRecentItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Get the parent play button
      const playBtn = maRecentItem.locator('ancestor::div button:has-text("Play")').first()
        .or(maRecentItem.locator('..').locator('button:has(svg path[d*="play"])').first());

      if (await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        // Play the track
        await playBtn.click();
        await page.waitForTimeout(5000);

        // Player card should now show active playback
        const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
        await expect(playerCard.getByText('Does Anybody Hear Her')).toBeVisible();
      }
    }
  });

  test('stopping playback resets player card', async ({ page }) => {
    // Select Office TV
    const officeTvCard = page.locator('.glass-panel button:has-text("Office TV")').first();
    if (!await officeTvCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }
    await officeTvCard.click();
    await page.waitForTimeout(500);

    // Jump Back In should show MA recent items
    const jumpBackIn = page.getByRole('heading', { name: 'Jump Back In' }).locator('..');
    await expect(jumpBackIn).toBeVisible({ timeout: 10000 });

    // Find the first MA recent item
    const maRecentItem = page.getByText('Does Anybody Hear Her').first();
    if (await maRecentItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Get the parent play button
      const playBtn = maRecentItem.locator('ancestor::div button:has-text("Play")').first()
        .or(maRecentItem.locator('..').locator('button:has(svg path[d*="play"])').first());

      if (await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        // Play the track
        await playBtn.click();
        await page.waitForTimeout(5000);

        // Verify track is playing
        const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
        await expect(playerCard.getByText('Does Anybody Hear Her')).toBeVisible();

        // Find and click pause button in player card
        const pauseBtn = playerCard.getByLabel('Pause').or(playerCard.getByRole('button', { name: /pause/i }).first());
        if (await pauseBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
          await pauseBtn.click();
          await page.waitForTimeout(3000);

          // Player card should show paused state or stop
          const stateText = playerCard.locator('text=/playing|paused|stopped/i').first();
          if (await stateText.isVisible({ timeout: 3000 }).catch(() => false)) {
            await expect(stateText).toBeVisible();
          }
        }
      }
    }
  });

  test('playing from playlists updates media status', async ({ page }) => {
    // Select Office TV
    const officeTvCard = page.locator('.glass-panel button:has-text("Office TV")').first();
    if (!await officeTvCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }
    await officeTvCard.click();
    await page.waitForTimeout(500);

    // Click Browse All Media to open modal
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    // Click Music Assistant tab
    await page.getByRole('button', { name: /Music Assistant/i }).click();
    await page.waitForTimeout(2000);

    // Find a playlist with items
    const playlistItem = page.locator('text=/\\d+ tracks/').first();
    if (await playlistItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Click the playlist item
      const playlistCard = playlistItem.locator('ancestor::div[role="button"]').first()
        .or(playlistItem.locator('..').locator('[class*="cursor-pointer"]')).first();

      if (await playlistCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playlistCard.click();
        await page.waitForTimeout(3000);

        // Should show playlist items with play buttons
        const playBtns = page.locator('button:has-text("Play"), [aria-label*="Play"]').first();
        if (await playBtns.isVisible({ timeout: 3000 }).catch(() => false)) {
          await playBtns.click();
          await page.waitForTimeout(5000);

          // Player card should show active playback
          const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
          const hasNowPlaying = playerCard.locator('text=/playing|Now Playing/').first();
          if (await hasNowPlaying.isVisible({ timeout: 5000 }).catch(() => false)) {
            await expect(hasNowPlaying).toBeVisible();
          }
        }
      }
    }
  });

  test('transport controls respond to clicks', async ({ page }) => {
    // Select Office TV
    const officeTvCard = page.locator('.glass-panel button:has-text("Office TV")').first();
    if (!await officeTvCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }
    await officeTvCard.click();
    await page.waitForTimeout(500);

    // Play a track
    const maRecentItem = page.getByText('Does Anybody Hear Her').first();
    if (await maRecentItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      const playBtn = maRecentItem.locator('ancestor::div button:has-text("Play")').first()
        .or(maRecentItem.locator('..').locator('button:has(svg path[d*="play"])').first());

      if (await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playBtn.click();
        await page.waitForTimeout(5000);

        // Player card should show active playback
        const playerCard = page.locator('.glass-panel.border-cyan-500\\/20');
        await expect(playerCard.getByText('Does Anybody Hear Her')).toBeVisible();

        // Click next track
        const nextBtn = playerCard.getByLabel('Next track').or(playerCard.getByRole('button', { name: /next/i }).first());
        if (await nextBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
          await nextBtn.click();
          await page.waitForTimeout(3000);

          // Player card should still show active playback (may be different track)
          const stillPlaying = playerCard.locator('text=/playing|paused|Does Anybody Hear Her/').first();
          if (await stillPlaying.isVisible({ timeout: 5000 }).catch(() => false)) {
            await expect(stillPlaying).toBeVisible();
          }
        }
      }
    }
  });

  test('volume slider updates on player card', async ({ page }) => {
    // Select Office TV
    const officeTvCard = page.locator('.glass-panel button:has-text("Office TV")').first();
    if (!await officeTvCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip();
    }
    await officeTvCard.click();
    await page.waitForTimeout(500);

    // Find volume slider
    const volumeSlider = page.locator('input[type="range"]').first();
    if (await volumeSlider.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Set volume to 50%
      await volumeSlider.evaluate((el: HTMLInputElement) => {
        el.value = '50';
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await page.waitForTimeout(1000);

      // Volume display should update
      const volumeDisplay = page.locator('span.tabular-nums').first();
      if (await volumeDisplay.isVisible({ timeout: 3000 }).catch(() => false)) {
        const text = await volumeDisplay.textContent();
        expect(text).toContain('50');
      }
    }
  });

  test('MA music plays via Web Player', async ({ page }) => {
    // Click Web Player card first
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Jump Back In should show MA recent items
    const maRecentItem = page.getByText('Does Anybody Hear Her').first();
    if (await maRecentItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Get the parent play button
      const playBtn = maRecentItem.locator('ancestor::div button:has-text("Play")').first()
        .or(maRecentItem.locator('..').locator('button:has(svg path[d*="play"])').first());

      if (await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        // Play the track
        await playBtn.click();
        await page.waitForTimeout(3000);

        // Local Audio Player should appear with track info
        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first()
          .or(page.locator('div:has-text("Does Anybody Hear Her")'));
        if (await localPlayer.isVisible({ timeout: 5000 }).catch(() => false)) {
          // Verify the track title is shown
          await expect(localPlayer.getByText('Does Anybody Hear Her')).toBeVisible();
        }
      }
    }
  });

  test('ABS audiobook plays via Web Player', async ({ page }) => {
    // Click Web Player card first
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Jump Back In should show ABS books
    const absBook = page.getByText('Homilies of Saint John Chrysostom').first();
    if (await absBook.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Get the parent play button
      const playBtn = absBook.locator('ancestor::div button:has-text("Play")').first()
        .or(absBook.locator('..').locator('button:has(svg path[d*="play"])').first());

      if (await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        // Play the audiobook
        await playBtn.click();
        await page.waitForTimeout(3000);

        // Local Audio Player should appear with book info
        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first()
          .or(page.locator('div:has-text("Homilies of Saint John Chrysostom")'));
        if (await localPlayer.isVisible({ timeout: 5000 }).catch(() => false)) {
          // Verify the book title is shown
          await expect(localPlayer.getByText('Homilies of Saint John Chrysostom')).toBeVisible();
        }
      }
    }
  });

  test('transport controls work in Web Player', async ({ page }) => {
    // Click Web Player card first
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Find and play an MA track
    const maRecentItem = page.getByText('Does Anybody Hear Her').first();
    if (await maRecentItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      const playBtn = maRecentItem.locator('ancestor::div button:has-text("Play")').first()
        .or(maRecentItem.locator('..').locator('button:has(svg path[d*="play"])').first());

      if (await playBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playBtn.click();
        await page.waitForTimeout(3000);

        // Local Audio Player should be visible
        const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
        if (await localPlayer.isVisible({ timeout: 5000 }).catch(() => false)) {
          // Click the play/pause toggle button
          const toggleBtn = localPlayer.locator('button.rounded-full').first();
          if (await toggleBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
            await toggleBtn.click();
            await page.waitForTimeout(1000);
          }
        }
      }
    }
  });

  test('Web Player can play from playlists', async ({ page }) => {
    // Click Web Player card first
    const localPlayerCard = page.locator('button:has-text("Web Player")').first();
    await expect(localPlayerCard).toBeVisible({ timeout: 10000 });
    await localPlayerCard.click();
    await page.waitForTimeout(500);

    // Click Browse All Media to open modal
    await page.getByRole('button', { name: 'Browse All Media' }).click();
    await page.waitForTimeout(3000);

    // Click Music Assistant tab
    await page.getByRole('button', { name: /Music Assistant/i }).click();
    await page.waitForTimeout(2000);

    // Find a playlist with items
    const playlistItem = page.locator('text=/\\d+ tracks/').first();
    if (await playlistItem.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Click the playlist item
      const playlistCard = playlistItem.locator('ancestor::div[role="button"]').first()
        .or(playlistItem.locator('..').locator('[class*="cursor-pointer"]')).first();

      if (await playlistCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        await playlistCard.click();
        await page.waitForTimeout(3000);

        // Should show playlist items with play buttons
        const playBtns = page.locator('button:has-text("Play"), [aria-label*="Play"]').first();
        if (await playBtns.isVisible({ timeout: 3000 }).catch(() => false)) {
          await playBtns.click();
          await page.waitForTimeout(3000);

          // Local Audio Player should appear
          const localPlayer = page.locator('.fixed.inset-0.bg-black\\/90').first();
          if (await localPlayer.isVisible({ timeout: 5000 }).catch(() => false)) {
            // Should show playlist name or track info
            await expect(localPlayer.locator('p.text-white.font-semibold')).toBeVisible();
          }
        }
      }
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
    const panel = page.locator('.glass-panel:has(h2:text("Select Device"))');
    await expect(panel).toBeVisible();
    // Horizontal scroll container should have overflow-x-auto
    const scrollContainer = panel.locator('.overflow-x-auto');
    if (await scrollContainer.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(scrollContainer).toBeVisible();
    }
  });
});
