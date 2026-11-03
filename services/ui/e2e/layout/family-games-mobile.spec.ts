import { test, expect, type Page } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';
import { isServerCall } from '../fixtures/serverCalls';

// Family Games on a phone: the shelf, the vote stars, the family scoreboard
// and the admin Test & publish controls all fit, nothing runs off the right
// edge, and every button is a real thumb-sized (44px) target.

function card(slug: string, title: string, emoji: string, state: string, extra: Record<string, unknown> = {}) {
  return {
    slug,
    title,
    collection: 'family',
    family_state: state,
    source: 'built-in',
    emoji,
    tagline: 'A cheerful little game for the whole family',
    art: { from: '#7c5cff', to: '#ff7ab6' },
    genre: 'puzzle',
    ages: '6+',
    rating: { count: 4, average: 4.5 },
    plays: 12,
    top_score: { initials: 'KAT', score: 12345 },
    on_shelf: state === 'published',
    shelf_reason: state === 'published' ? '' : 'In testing',
    ...extra,
  };
}

const FAMILY = {
  success: true,
  arcade_available: true,
  play_base: 'https://games.example.test',
  is_admin: true,
  admin_token_configured: true,
  min_rating: 4,
  min_votes: 2,
  games: [card('family-cloud-hop', 'Cloud Hop', '☁️', 'published'), card('family-petal-swap', 'Petal Swap', '🌸', 'published')],
  review: [
    card('family-orbit-guard', 'Orbit Guard', '🪐', 'testing'),
    card('qwen3-35b_mobile-bubble-pop', 'Mobile Bubble Pop With A Rather Long Benchmark Title', '🫧', 'published', {
      source: 'benchmark',
      model: 'qwen3.6-35b-a3b:q4_k_m',
      on_shelf: false,
      shelf_reason: 'Rated 3.2★, below the 4★ bar',
      rating: { count: 5, average: 3.2 },
    }),
  ],
  unpublished: [card('family-garden-pipes', 'Garden Pipes', '🌻', 'unpublished', { shelf_reason: 'Unpublished' })],
};

const PLAYERS = ['Jeremiah', 'Michele', 'Kate', 'Samuel'].map((name, i) => ({
  username: name.toLowerCase(),
  display_name: name,
  initials: name.slice(0, 3).toUpperCase(),
  player_url: `https://games.example.test/player/${name.slice(0, 3).toUpperCase()}`,
  you: i === 0,
  best_score: 9000 - i * 1500,
  total_games: 2,
  games_scored: 2,
  submits: 5,
  podiums: 1,
  crowns: i === 0 ? 1 : 0,
}));

async function mockApi(page: Page) {
  await page.route(isServerCall, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/users/me') return route.fallback();
    if (url.pathname === '/api/arcade/family') return route.fulfill({ json: FAMILY });
    if (url.pathname === '/api/arcade/scoreboard') return route.fulfill({ json: { success: true, arcade_available: true, players: PLAYERS } });
    if (url.pathname === '/api/arcade/highscores') {
      return route.fulfill({
        json: {
          success: true,
          arcade_available: true,
          players: [],
          games: FAMILY.games.map((g) => ({ slug: g.slug, title: g.title, scores: [{ initials: 'JER', score: 4200 }, { initials: 'KAT', score: 3100 }] })),
        },
      });
    }
    if (url.pathname === '/api/arcade/games') {
      return route.fulfill({ json: { success: true, arcade_available: true, play_base: FAMILY.play_base, featured_source: 'none', featured: [], games: [], count: 0 } });
    }
    return route.fulfill({ json: {} });
  });
  // The embedded arcade player.
  await page.route('https://games.example.test/**', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<body style="margin:0;background:#3b2a8a;color:#fff;font:20px sans-serif">game</body>' }),
  );
}

async function problems(page: Page, rootSelector: string) {
  return page.evaluate((selector) => {
    const limit = window.innerWidth;
    const root = document.querySelector(selector);
    if (!root) return ['root missing'];
    const out: string[] = [];
    for (const el of root.querySelectorAll<HTMLElement>('*')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > limit + 0.5) {
        out.push(`overflow ${el.tagName.toLowerCase()}[${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30)}] right=${Math.round(r.right)} > ${limit}`);
      }
    }
    for (const el of root.querySelectorAll<HTMLElement>('button, a[href]')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0) continue;
      if (r.height < 43.5) out.push(`small target ${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30)} h=${Math.round(r.height)}`);
    }
    return out;
  }, rootSelector);
}

for (const width of [412, 375, 360]) {
  test(`Family Games fit a ${width}px phone`, async ({ page }, info) => {
    test.skip((info.project.use.viewport?.width ?? 1000) > 500, 'phone layout only');
    await page.setViewportSize({ width, height: 860 });
    await mockApi(page);
    await seedAuth(page);
    await page.goto('/family?tab=games');
    await expect(page.getByTestId('family-shelf')).toBeVisible();
    await expect(page.getByTestId('family-admin')).toBeVisible();
    await expect(page.getByTestId('family-scoreboard')).toBeVisible();
    await page.waitForTimeout(600);
    expect(await problems(page, '[data-testid="family-games"]')).toEqual([]);
    await page.screenshot({ path: `../../.tmp/family-games-${width}.png`, fullPage: true });
    await page.getByTestId('family-scoreboard').screenshot({ path: `../../.tmp/family-games-${width}-board.png` });
    await page.getByTestId('family-admin').screenshot({ path: `../../.tmp/family-games-${width}-admin.png` });

    // The player fills the phone with the game.
    await page.getByRole('button', { name: 'Play Cloud Hop' }).click();
    const frame = page.getByTestId('family-player-frame');
    await expect(frame).toBeVisible();
    const box = await frame.boundingBox();
    expect(box && box.width).toBeGreaterThanOrEqual(width - 2);
    expect(box && box.height).toBeGreaterThan(860 * 0.7);
    await page.screenshot({ path: `../../.tmp/family-games-${width}-player.png` });
  });
}
