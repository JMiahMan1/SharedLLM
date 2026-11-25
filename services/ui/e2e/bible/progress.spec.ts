import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockBible } from '../fixtures/bibleMocks';

/**
 * The Progress tab's number cards show numbers, not markup: "This week" once
 * printed `<span className=...>chapters</span>` as text. Checked on every
 * project (phone, small phone, tablet both ways, desktop): every card shows its
 * value with its unit, the plan card counts real plan days, and no card spills
 * past its box or the screen.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

function dayKey(back: number): string {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - back);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const ENROLLMENT = {
  plan_id: 'made-up-plan', name: 'A Made-up Reading Plan With a Long Name', started_on: '2026-10-01', finished_on: null,
  days: 365, completed: 12, completed_days: [], percent: 3, calendar_day: 13, current_day: 13, next_day: 13, behind: 0,
  finished: false, today: null,
};

async function openProgress(page: Page, mine: unknown[]) {
  await mockHome(page, 'admin');
  await mockBible(page);
  // Registered after mockBible, so these answer first.
  await page.route('**/api/bible/stats**', (route) => route.fulfill({ json: {
    username: 'parent', read_streak_current: 3, read_streak_longest: 9, open_streak_current: 4, open_streak_longest: 12,
    days_read: 20, metrics: { chapters_total: 57, books_read: 2, memory_mastered: 1 }, days_opened: 30, marks: 0,
    highlights: 0, bookmarks: 0, position: null, last_read_at: null,
    window: { [dayKey(0)]: 30, [dayKey(1)]: 30 },
    chapters_window: { [dayKey(0)]: 2, [dayKey(3)]: 1, [dayKey(10)]: 9 },
    last_plan_id: 'made-up-plan',
  } }));
  await page.route('**/api/bible/plans', (route) => route.fulfill({ json: { catalogue: [], mine } }));
  await page.goto('/bible?tab=progress');
  await expect(page.getByTestId('bible-progress')).toBeVisible();
}

async function cardProblems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const problems: string[] = [];
    const width = window.innerWidth;
    if (document.documentElement.scrollWidth > width + 1) problems.push(`page scrolls sideways at ${width}px`);
    const cards = Array.from(document.querySelectorAll<HTMLElement>('[data-testid="bible-progress"] section:nth-of-type(-n+2) > div'));
    if (cards.length !== 8) problems.push(`expected 8 cards, found ${cards.length}`);
    for (const card of cards) {
      const b = card.getBoundingClientRect();
      const name = card.innerText.split('\n')[0];
      if (b.left < -0.5 || b.right > width + 0.5) problems.push(`${name}: past the edge`);
      if (card.scrollWidth > card.clientWidth + 1) problems.push(`${name}: content wider than its card`);
      if (/<\/?span|className=/.test(card.textContent ?? '')) problems.push(`${name}: shows markup as text`);
    }
    return problems;
  });
}

test('the progress cards show their values, not markup', async ({ page }) => {
  await openProgress(page, [ENROLLMENT]);
  const week = page.getByTestId('bible-progress-week');
  await expect(week).toContainText('3 chapters');
  const plan = page.getByTestId('bible-progress-plan');
  await expect(plan).toContainText('12 of 365 days');
  await expect(plan).toContainText('A Made-up Reading Plan');
  await expect(page.getByTestId('bible-progress-memorized')).toContainText('1 verse');
  await expect(page.getByText('Chapters total')).toHaveCount(0);
  expect(await cardProblems(page)).toEqual([]);
});

test('with no plan the card says so and leads to the plans', async ({ page }) => {
  await openProgress(page, []);
  const plan = page.getByTestId('bible-progress-plan');
  await expect(plan).toContainText('No plan');
  expect(await cardProblems(page)).toEqual([]);
  await plan.getByTestId('bible-progress-start-plan').click();
  await expect(page.getByTestId('bible-tab-today')).toHaveAttribute('aria-pressed', 'true');
});
