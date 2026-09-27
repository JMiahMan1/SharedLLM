import { test } from '@playwright/test';

test.use({ channel: 'chrome' });

async function boot(page: import('@playwright/test').Page, path: string, tag: string) {
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push('CONSOLE: ' + m.text());
  });
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));

  await page.addInitScript(() => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify({ username: 'jeremiah', is_admin: true }));
  });

  const json = (route: import('@playwright/test').Route, body: unknown) => route.fulfill({ json: body });
  await page.route('**/api/users/me/theme', (r) => json(r, { status: 'SUCCESS', theme_id: 'aurora', packs: [] }));
  await page.route('**/api/users/me', (r) =>
    json(r, { id: 1, username: 'jeremiah', display_name: 'Jeremiah', is_admin: true, role: 'admin' })
  );

  const week: Record<string, number> = {
    '2026-09-20': 8123, '2026-09-21': 10421, '2026-09-22': 6550, '2026-09-23': 12040,
    '2026-09-24': 9877, '2026-09-25': 11002, '2026-09-26': 7420,
  };
  await page.route('**/api/geo/steps**', (r) =>
    json(r, { user_id: 'jeremiah', days: 7, daily_steps: week, today: 7420, goal: 10000 })
  );
  await page.route('**/api/geo/workouts**', (r) =>
    json(r, {
      workouts: [
        {
          id: 'w1', activity_type: 'walking', start_time: Math.floor(Date.now() / 1000) - 86400 * 2,
          end_time: Math.floor(Date.now() / 1000) - 86400 * 2 + 2700, status: 'completed',
          distance_miles: 1.8, duration_seconds: 2700, avg_speed_mph: 2.4, steps: 3900,
          steps_source: 'pedometer', notes: 'Evening walk by the lake',
        },
      ],
      total: 1,
    })
  );
  await page.route('**/api/geo/workouts/w1/route', (r) =>
    json(r, { points: [ { lat: 33.16, lon: -111.56 }, { lat: 33.17, lon: -111.57 } ] })
  );
  await page.route('**/api/geo/achievements**', (r) =>
    json(r, {
      points: 12,
      earned: [ { id: 'first_steps', name: 'First Steps', earned_at: '2026-09-20' } ],
      next_up: [ { id: 'ten_thousand', name: 'Ten Thousand', progress: 0.87, percent: 87 } ],
      goals: { daily_steps: 10000 },
    })
  );
  await page.route('**/api/geo/goals**', (r) =>
    json(r, { daily_steps: 10000, weekly_steps: 70000, workouts_per_week: 4, weekly_distance_miles: 15 })
  );

  await page.goto(`http://localhost:5173${path}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4500);
  console.log(`ERRORS[${tag}]:`, JSON.stringify(errors, null, 2));
  await page.screenshot({ path: `/Users/jeremiahsummers/Code/SharedLLM/.tmp/${tag}.png`, fullPage: false });

  if (tag === 'health-mobile') {
    await page.getByRole('button', { name: /more pages/i }).click();
    await page.waitForTimeout(600);
    await page.screenshot({ path: '/Users/jeremiahsummers/Code/SharedLLM/.tmp/more-sheet.png' });
  }
}

test('health mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await boot(page, '/health', 'health-mobile');
});

test('health desktop', async ({ page }) => {
  await boot(page, '/health', 'health-desktop');
});
