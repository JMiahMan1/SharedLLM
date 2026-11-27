import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { INTEGRATIONS_STATUS_PATH, integrationsStatus, isServerCall } from './fixtures/serverCalls';
import { mockBible } from './fixtures/bibleMocks';
import { expectLayoutSound, expectTapTargets, controlsOffScreen } from './fixtures/layoutChecks';
import { expectNoA11yViolations } from './fixtures/a11y';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The profile page (docs/PROFILE_STATS.md) at every size the suite runs
 * (desktop, phone, small phone, tablets): your own with every area and the
 * sharing panel, someone else's with only what they share, every stat a 44px
 * link, and a quiz score that opens the quiz as a challenge. Hermetic: every
 * call is answered here and the catch-all must stay empty.
 */

const ME = { id: 1, username: 'pat', display_name: 'Pat', full_name: 'Pat', role: 'user', is_admin: false, family_role: 'father', is_minor: false };

const DIRECTORY = [
  { username: 'pat', display_name: 'Pat', nextcloud_user: null, avatar: null, family_role: 'father' },
  { username: 'robin', display_name: 'Robin Longname-Example', nextcloud_user: null, avatar: null, family_role: 'teen' },
  { username: 'kit', display_name: 'Kit', nextcloud_user: null, avatar: null, family_role: 'child' },
];

const MINE = {
  username: 'pat', own: true, person: { username: 'pat', display_name: 'Pat' },
  cards: [
    { area: 'bible', state: 'ok', data: { streak_current: 12, streak_longest: 30, days_read: 140, chapters_read: 402, books_read: 9,
      reference: 'Psalm 23', window: Array.from({ length: 20 }, (_, i) => ({ day: `2026-11-${String(i + 1).padStart(2, '0')}`, count: (i % 4) + 1 })),
      achievements: ['First Chapter', 'A week in the Word'], points: 340, quiz_today: { correct: 7, total: 10 } } },
    { area: 'activity', state: 'ok', data: { window: 'week', steps_today: 8123, steps_average: 9450, steps_total: 66150, workout_count: 3,
      workout_distance_miles: 7.4, drive_distance_miles: 112, points: 220, achievements_earned: 6 } },
    { area: 'health', state: 'ok', data: { habits: [
      { key: 'sleep_log', title: 'Log sleep', current: 5, best: 14, today_done: true },
      { key: 'water_goal', title: 'Water goal', current: 2, best: 9, today_done: false },
    ], week: { days_logged: 6, averages: { sleep_minutes: 436 }, changes: [], best_sleep: null } } },
    { area: 'garden', state: 'unavailable', data: null, reason: 'This part of Jarvis took too long to answer.' },
    { area: 'recipes', state: 'ok', data: { times_cooked: 14, recipes_cooked: 5, ratings_given: 4, most_made: [
      { id: 'cookbook/dinner/a-very-long-recipe-name-for-slow-cooker-chili.md', title: 'A Very Long Recipe Name For Slow Cooker Chili', made_by_me: 6, mine: 5, rating: 4.7 },
      { id: 'cookbook/breakfast/pancakes.md', title: 'Pancakes', made_by_me: 4, mine: 4, rating: 4.2 },
    ], favourites: [{ id: 'cookbook/dinner/a-very-long-recipe-name-for-slow-cooker-chili.md', title: 'A Very Long Recipe Name For Slow Cooker Chili', made_by_me: 6, mine: 5, rating: 4.7 }] } },
    { area: 'games', state: 'ok', data: { best_score: 15200, crowns: 2, podiums: 5, total_games: 31, bible_quiz_today: { correct: 7, total: 10 }, games: [
      { slug: 'family-checkers', title: 'Checkers', collection: 'family', best: 900, rank: 1, plays: 12 },
      { slug: 'family-jubilee', title: 'Jubilee!', collection: 'family', best: 4200, rank: 2, plays: 3 },
      { slug: 'space-invaders', title: 'Space Invaders', collection: 'arcade', best: 15200, rank: 4, plays: 9 },
    ] } },
    { area: 'jarvis_use', state: 'ok', data: { days: 30, total: 214, top: [
      { label: 'Music, video and podcasts', count: 90 }, { label: 'Bible', count: 60 }, { label: 'Recipes', count: 20 },
    ] } },
  ],
  sharing: {
    areas: ['recipes'], available: ['garden', 'recipes', 'jarvis_use'],
    activity_sharing: { enabled: true, audience: 'circle', share: ['totals', 'bible'] },
    health_shares: [{ viewer: 'robin', scope: 'sleep' }],
  },
};

const ROBIN = {
  username: 'robin', own: false, person: { username: 'robin', display_name: 'Robin Longname-Example' },
  cards: [
    { area: 'bible', state: 'ok', data: { streak_current: 3, streak_longest: 8, days_read: 20, chapters_read: 41, books_read: 1,
      achievements: [], points: 40, quiz_today: { correct: 8, total: 10 } } },
    { area: 'activity', state: 'ok', data: { window: 'week', steps_today: 11002 } },
    { area: 'health', state: 'hidden', data: null },
    { area: 'garden', state: 'hidden', data: null },
    { area: 'recipes', state: 'hidden', data: null },
    { area: 'games', state: 'ok', data: { best_score: 800, crowns: 0, podiums: 1, total_games: 4, bible_quiz_today: { correct: 8, total: 10 }, games: [
      { slug: 'family-checkers', title: 'Checkers', collection: 'family', best: 800, rank: 2, plays: 4 },
    ] } },
    { area: 'jarvis_use', state: 'hidden', data: null },
  ],
};

async function mockApi(page: Page) {
  const unmocked: string[] = [];
  const puts: unknown[] = [];
  let sharing = structuredClone(MINE.sharing);
  await page.addInitScript((profile) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(profile));
  }, ME);
  await page.route(isServerCall, async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (p === '/api/users/me') return json(ME);
    if (p === '/api/users/me/theme') return json({ theme: 'dark' });
    if (p === '/api/telemetry/notifications') return json({ notifications: [] });
    if (p === '/api/communication/calls') return json({ calls: [] });
    if (p === '/api/media/token') return json({ token: 'test-media-token', expires_at: 4102444800, user: 'pat' });
    if (p === '/api/activity') return json({ status: 'SUCCESS' });
    if (p === INTEGRATIONS_STATUS_PATH) return json(integrationsStatus());
    if (p === '/api/deploy-status') return route.fulfill({ status: 404, body: '' });
    if (p === '/api/status/local-ai') return json({ local_ai: { state: 'up', offline: false, since: null, message: null, still_working: [] }, backends: {}, any_offline: false });
    if (p === '/api/users/family-directory') return json(DIRECTORY);
    if (p === '/api/profile/stats/me') return json({ ...MINE, sharing });
    if (p === '/api/profile/stats/robin') return json(ROBIN);
    if (p === '/api/profile/stats/kit') return json({ ...ROBIN, username: 'kit', person: { username: 'kit', display_name: 'Kit' }, cards: ROBIN.cards.map((c) => ({ ...c, state: 'hidden', data: null })) });
    if (p === '/api/profile/sharing' && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as { areas: string[] };
      puts.push(body);
      sharing = { ...sharing, areas: body.areas };
      return json(sharing);
    }
    unmocked.push(`${route.request().method()} ${p}`);
    return json({});
  });
  // The quiz the challenge opens (registered last, so it answers /api/bible first).
  await mockBible(page);
  return { unmocked, puts };
}

test.describe('profile page', () => {
  test('your own profile: every area, each stat a way in, and what you share', async ({ page }, info) => {
    const { unmocked, puts } = await mockApi(page);
    await page.goto('/profile');
    await expect(page.getByRole('heading', { name: 'My profile' })).toBeVisible();
    await expect(page.getByTestId('profile-card-bible')).toBeVisible();
    for (const area of ['bible', 'activity', 'health', 'garden', 'recipes', 'games', 'jarvis_use']) {
      await expect(page.getByTestId(`profile-card-${area}`)).toBeVisible();
    }
    // One source being slow greys only its own card.
    await expect(page.getByTestId('profile-card-garden-error')).toContainText('took too long');
    await expect(page.getByTestId('profile-bible-streak')).toHaveAttribute('href', '/bible?tab=read&ref=Psalm%2023');
    await expect(page.getByTestId('profile-game-family-checkers')).toHaveAttribute('href', '/family?tab=games&play=family-checkers');

    await expectLayoutSound(page, info, 'profile-own');
    await expectTapTargets(page.getByTestId('profile-cards'));
    await expectTapTargets(page.getByTestId('profile-people'));
    expect(await controlsOffScreen(page)).toEqual([]);

    // The sharing panel: a switch for areas without one, links to the switches that exist.
    const panel = page.getByTestId('profile-sharing');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByTestId('profile-share-recipes')).toHaveAttribute('aria-checked', 'true');
    await expect(panel.getByTestId('profile-share-activity')).toHaveAttribute('href', '/settings#settings-family');
    await expectTapTargets(panel);
    await panel.getByTestId('profile-share-garden').click();
    await expect(panel.getByTestId('profile-share-garden')).toHaveAttribute('aria-checked', 'true');
    expect(puts).toEqual([{ areas: ['garden', 'recipes'] }]);

    await expectNoA11yViolations(page);
    expect(unmocked).toEqual([]);
  });

  test("someone else's profile shows what they share, and a quiz score becomes a challenge", async ({ page }, info) => {
    const { unmocked } = await mockApi(page);
    await page.goto('/profile/robin');
    await expect(page.getByRole('heading', { name: "Robin Longname-Example's profile" })).toBeVisible();
    await expect(page.getByTestId('profile-card-bible')).toBeVisible();
    await expect(page.getByTestId('profile-card-health')).toHaveCount(0);
    await expect(page.getByTestId('profile-private-areas')).toContainText('keeps health, garden, recipes, using jarvis private');
    await expect(page.getByTestId('profile-sharing')).toHaveCount(0);
    await expectLayoutSound(page, info, 'profile-other');
    await expectTapTargets(page.getByTestId('profile-cards'));
    expect(await controlsOffScreen(page)).toEqual([]);

    // Keyboard: the quiz score is a focusable link and Enter takes you to the quiz with the challenge.
    const quiz = page.getByTestId('profile-bible-quiz');
    await expect(quiz).toHaveAttribute('href', '/bible?tab=play&beat=Robin&score=8&of=10');
    await quiz.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/bible\?tab=play&beat=Robin&score=8&of=10/);
    await expect(page.getByTestId('bible-daily-challenge')).toContainText("Robin's 8/10");
    expect(unmocked).toEqual([]);
  });

  test('a person who shares nothing says so plainly', async ({ page }) => {
    const { unmocked } = await mockApi(page);
    await page.goto('/profile/kit');
    await expect(page.getByTestId('profile-nothing-shared')).toContainText('Kit has not shared anything');
    // Switching person from the strip.
    await page.getByTestId('profile-person-robin').click();
    await expect(page).toHaveURL(/\/profile\/robin$/);
    await expect(page.getByTestId('profile-card-bible')).toBeVisible();
    expect(unmocked).toEqual([]);
  });
});
