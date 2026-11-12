/**
 * Managing Skylight chores across form factors (docs/ACHIEVEMENTS.md,
 * "Managing chores"), hermetic: every API call is mocked, nothing reaches a
 * server or Skylight.
 *
 * At each viewport (a small phone, a Pixel, an iPad both ways, a desktop) a
 * parent adds and removes chores from the dashboard widget (its card and its
 * full-screen view), the Guardian page and a child's member page. The rules
 * are the same everywhere: no horizontal page scroll, no card wider than its
 * cell, dialogs inside the screen and above the full-screen view, every new
 * control at least 44px to tap, and no page error.
 *
 * Runs once (desktop project or a local config); it sweeps its own viewports.
 */
import { expect, test, type Page, type Route } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';
import { shotPath } from '../fixtures/climateMocks';

const VIEWPORTS = [
  { name: 'phone-375', width: 375, height: 667, mobile: true },
  { name: 'pixel-412', width: 412, height: 915, mobile: true },
  { name: 'ipad-768', width: 768, height: 1024, mobile: true },
  { name: 'ipad-1180', width: 1180, height: 820, mobile: true },
  { name: 'desktop-1440', width: 1440, height: 900, mobile: false },
];

type Chore = { id: string; title: string; completed: boolean; assignees: string[]; reward?: number;
  recurrence?: string | null; start_time?: string | null; emoji_icon?: string | null };

interface Board { chores: Chore[]; added: Record<string, unknown>[]; removed: Array<{ id: string; applyTo: string | null }>; errors: string[] }

const WIDGETS = ['chores_progress', 'weather', 'climate', 'energy_insights', 'ambient_timer', 'quick_notes', 'active_media',
  'upcoming_events', 'quick_assistant', 'device_control', 'workspaces', 'health_activity', 'health_today', 'presence',
  'bible_daily', 'recommendations'];

async function mockAll(page: Page): Promise<Board> {
  const board: Board = {
    chores: [
      { id: '50-today', title: 'Feed the chickens', completed: false, assignees: ['Noah'], reward: 2, recurrence: 'RRULE:FREQ=DAILY', start_time: '07:00', emoji_icon: '🐔' },
      { id: '51', title: 'Trash and recycling to the curb', completed: true, assignees: ['Noah'] },
      { id: '52', title: 'Unload the dishwasher', completed: false, assignees: ['Gracie'], recurrence: 'RRULE:FREQ=DAILY', start_time: '18:30' },
    ],
    added: [], removed: [], errors: [],
  };
  page.on('pageerror', (e) => board.errors.push(e.message));

  const catchAll = async (route: Route) => {
    const req = route.request();
    if (req.method() !== 'GET') return route.fulfill({ json: { status: 'SUCCESS', message: 'e2e' } });
    const p = new URL(req.url()).pathname;
    if (/\/api\/(health|info)\b/.test(p)) return route.fulfill({ json: { status: 'ok', version: 'e2e', git_sha: 'e2e' } });
    if (/\/api\/(config|calendar\/settings)\b/.test(p)) return route.fulfill({ json: { status: 'SUCCESS', settings: {}, config: {} } });
    if (/\/api\/telemetry\//.test(p)) return route.fulfill({ json: { notifications: [], jobs: [] } });
    // The shapes the real server answers with (the Guardian page's other cards read them).
    if (/\/api\/screen-time\/[^/]+\/status/.test(p)) return route.fulfill({ json: {
      username: 'noah', day: '', rules: { daily_limit_min: 120 }, can_manage: true, technical: false,
      limit_min: 120, used_min: 30, bonus_min: 0, remaining_min: 90, locked: false } });
    if (/\/api\/(screen-time|guard)\/requests/.test(p)) return route.fulfill({ json: { requests: [] } });
    // The Guardian and member pages' other cards, empty (shapes from e2e/family-member.spec.ts).
    if (p.startsWith('/api/guardian/concerns/')) return route.fulfill({ json: { status: 'SUCCESS', concerns: [] } });
    if (p.startsWith('/api/guardian/guidance/')) return route.fulfill({ json: { status: 'SUCCESS', week: { start: '', end: '' }, prompts: [] } });
    if (p.startsWith('/api/guardian/recap/')) return route.fulfill({ json: { status: 'SUCCESS', recap: { week: { start: '', end: '' }, has_data: false,
      screen: null, top_apps: [], media: [], web: { top: [], blocked: [] }, comms: { calls: 0, call_contacts: 0, messages: 0, message_contacts: 0 },
      faith: { bible_days: 0 }, concerns: [], highlights: [] } } });
    if (p.startsWith('/api/guardian/journal/')) return route.fulfill({ json: { notes: [] } });
    if (p.startsWith('/api/guardian/activity/')) return route.fulfill({ json: { status: 'SUCCESS', events: [] } });
    if (p.startsWith('/api/usage/')) return route.fulfill({ json: { status: 'SUCCESS', days: [] } });
    if (p === '/api/guard/offline-code') return route.fulfill({ json: { set: false, updated_at: null, can_set: true } });
    if (p.startsWith('/api/recommendation-lanes/')) return route.fulfill({ json: { username: 'noah', lanes: {}, can_manage: true } });
    if (p === '/api/geo/zones') return route.fulfill({ json: { features: [] } });
    if (p === '/api/users/location/all') return route.fulfill({ json: {} });
    return route.fulfill({ json: [] });
  };
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/') || u.pathname === '/health', catchAll);

  await page.route('**/api/widgets/settings', (route) => route.fulfill({ json: {
    widgets: WIDGETS.map((key, i) => ({
      widget_key: key, visibility: key === 'chores_progress' ? 'visible' : 'hidden', order_index: i, size: 'medium',
      is_pinned: false, sort_mode: null, pinned_devices: [], config: {}, updated_at: 0,
    })),
    quick_assistant_enabled: false,
  } }));
  await page.route('**/api/users/family', (route) => route.fulfill({ json: [
    { username: 'noah', display_name: 'Noah', family_role: 'child', mother_username: null, is_minor: true, sharing_locked: true, is_admin: false, avatar: null },
  ] }));

  // Skylight, as the gateway would answer it.
  await page.route('**/api/integrations/skylight/members', (route) => route.fulfill({ json: { status: 'SUCCESS', members: [
    { id: '11', label: 'Noah', color: '#f97316' }, { id: '12', label: 'Gracie', color: '#22c55e' },
  ] } }));
  await page.route('**/api/integrations/skylight/chores**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() === 'GET' && url.pathname.endsWith('/chores')) {
      const scope = (url.searchParams.get('scope') || '').toLowerCase();
      const chores = scope && scope !== 'me' ? board.chores.filter((c) => c.assignees.some((a) => a.toLowerCase() === scope)) : board.chores;
      return route.fulfill({ json: { status: 'SUCCESS', chores, assignee_meta: { Noah: '#f97316', Gracie: '#22c55e' } } });
    }
    if (req.method() === 'POST' && url.pathname.endsWith('/chores')) {
      const body = req.postDataJSON() as { title: string; assignees: string[]; repeat?: string };
      board.added.push(body);
      for (const who of body.assignees) {
        const label = who.charAt(0).toUpperCase() + who.slice(1);
        board.chores.push({ id: `n${board.chores.length}`, title: body.title, completed: false, assignees: [label],
          recurrence: body.repeat ? 'RRULE:x' : null });
      }
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Chore added' } });
    }
    if (req.method() === 'DELETE') {
      const id = decodeURIComponent(url.pathname.split('/').pop() || '');
      board.removed.push({ id, applyTo: url.searchParams.get('apply_to') });
      board.chores = board.chores.filter((c) => c.id !== id);
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Chore removed' } });
    }
    return route.fulfill({ json: { status: 'SUCCESS' } });
  });
  await seedAuth(page);
  return board;
}

/** Layout facts that must hold at every width. */
async function layout(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const cards = Array.from(document.querySelectorAll<HTMLElement>('[data-testid="guardian-chores"], [role="dialog"]'));
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const box = dialog?.getBoundingClientRect();
    // Every chore control a parent taps, at least 44px each way.
    const small = Array.from(document.querySelectorAll<HTMLElement>(
      '[data-testid="chore-add"], [data-testid="chore-add-for"], [data-testid="chore-remove"], [data-testid="guardian-chores"] button, [role="dialog"] button, [role="dialog"] input, [role="dialog"] select'))
      .filter((el) => el.offsetParent !== null)
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.width > 0 && (r.height < 43.5 || r.width < 43.5))
      .map(({ el, r }) => `${el.getAttribute('aria-label') || el.textContent?.trim().slice(0, 30)} ${Math.round(r.width)}x${Math.round(r.height)}`);
    return {
      pageOverflow: doc.scrollWidth - doc.clientWidth,
      cardOverflow: cards.map((c) => ({ id: c.dataset.testid || c.getAttribute('role'), over: c.scrollWidth - c.clientWidth })).filter((c) => c.over > 1),
      dialogOffScreen: box ? box.left < -1 || box.right > window.innerWidth + 1 || box.top < -1 || box.bottom > window.innerHeight + 1 : false,
      dialogOnTop: box ? dialog!.contains(document.elementFromPoint(box.left + box.width / 2, box.top + 24)) : true,
      small,
      widgetFailed: document.body.innerText.includes('Widget failed'),
    };
  });
}

/** Wait for an opening dialog to finish sliding in (its box stops moving). */
async function settled(page: Page) {
  // CSS animations (the full view fading up) first, then the dialog's slide.
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running'), null, { timeout: 5000 }).catch(() => {});
  let last = '';
  for (let i = 0; i < 40; i++) {
    const box = await page.evaluate(() => {
      const d = document.querySelector('[role="dialog"]');
      const r = d?.getBoundingClientRect();
      return r ? `${Math.round(r.top)},${Math.round(r.bottom)},${getComputedStyle(d!.parentElement!).opacity}` : 'none';
    });
    if (box === last) return;
    last = box;
    await page.waitForTimeout(100);
  }
}

async function fits(page: Page, label: string) {
  await settled(page);
  const f = await layout(page);
  // 44px is a finger's target; a mouse on the desktop needs less (the shared dialog's close is 36px there).
  const touch = await page.evaluate(() => matchMedia('(pointer: coarse)').matches);
  expect(f.pageOverflow, `${label}: page scrolls sideways`).toBeLessThanOrEqual(1);
  expect(f.cardOverflow, `${label}: something spills out of its card`).toEqual([]);
  expect(f.dialogOffScreen, `${label}: dialog runs off the screen`).toBe(false);
  expect(f.dialogOnTop, `${label}: dialog is hidden behind something`).toBe(true);
  if (touch) expect(f.small, `${label}: controls under 44px`).toEqual([]);
  expect(f.widgetFailed).toBe(false);
}

test.describe('managing chores across form factors', () => {
  test.beforeEach(({ browserName }, testInfo) => {
    void browserName;
    test.skip(!!testInfo.project.name && testInfo.project.name !== 'hermetic-desktop', 'runs in one project');
  });

  for (const vp of VIEWPORTS) {
    test.describe(vp.name, () => {
      test.use({ viewport: { width: vp.width, height: vp.height }, isMobile: vp.mobile, hasTouch: vp.mobile });

      test('dashboard widget: add from the card', async ({ page }) => {
        const board = await mockAll(page);
        await page.goto('/');
        await expect(page.getByText('Feed the chickens').first()).toBeVisible();
        const add = page.getByTestId('chore-add').first();
        await add.scrollIntoViewIfNeeded();
        await add.click();
        const dialog = page.getByRole('dialog');
        await expect(dialog.getByRole('button', { name: 'Gracie' })).toBeVisible();
        await dialog.getByLabel('Chore name').fill('Water the garden');
        await dialog.getByRole('button', { name: 'Gracie' }).click();
        await dialog.getByLabel('How often').selectOption('weekends');
        await fits(page, 'add dialog');
        await page.screenshot({ path: shotPath(`chores-${vp.name}-add-dialog`) });
        await dialog.getByRole('button', { name: /Add chore/ }).click();
        await expect(dialog).toHaveCount(0);
        expect(board.added).toEqual([{ title: 'Water the garden', assignees: ['Gracie'], repeat: 'weekends' }]);
        await expect(page.getByText('Water the garden').first()).toBeVisible();
        await fits(page, 'dashboard');
        expect(board.errors).toEqual([]);
        await page.screenshot({ path: shotPath(`chores-${vp.name}-dashboard`) });
      });

      test('dashboard widget: full view adds per person and removes', async ({ page }) => {
        const board = await mockAll(page);
        await page.goto('/');
        await expect(page.getByText('Feed the chickens').first()).toBeVisible();
        await page.getByRole('button', { name: 'Expand widget' }).first().click();
        await expect(page.getByTestId('chore-add-for').first()).toBeVisible();
        await fits(page, 'full view');
        await page.screenshot({ path: shotPath(`chores-${vp.name}-full`) });

        await page.getByRole('button', { name: 'Add for Gracie' }).click();
        const dialog = page.getByRole('dialog');
        await expect(dialog).toContainText('New chore for Gracie');
        await dialog.getByLabel('Chore name').fill('Fold towels');
        await fits(page, 'per-person add over the full view');
        await dialog.getByRole('button', { name: /Add chore/ }).click();
        await expect(dialog).toHaveCount(0);
        expect(board.added.at(-1)).toEqual({ title: 'Fold towels', assignees: ['Gracie'] });

        await page.getByRole('button', { name: 'Remove Feed the chickens' }).last().click();
        await expect(dialog).toContainText('repeats');
        await fits(page, 'remove dialog over the full view');
        await page.screenshot({ path: shotPath(`chores-${vp.name}-remove-dialog`) });
        await dialog.getByRole('button', { name: 'Every day' }).click();
        await expect(dialog).toHaveCount(0);
        expect(board.removed).toEqual([{ id: '50-today', applyTo: 'all' }]);
        await expect(page.getByText('Feed the chickens')).toHaveCount(0);
        expect(board.errors).toEqual([]);
      });

      test('guardian page and member page cards', async ({ page }) => {
        const board = await mockAll(page);
        await page.goto('/guardian');
        // Phones open on the Overview area; chores live under Time & Chores.
        await page.getByTestId('guardian-area-time').click();
        await expect(page.getByTestId('guardian-area-time')).toHaveAttribute('aria-selected', 'true');
        const card = page.getByTestId('guardian-chores');
        await expect(card).toContainText('Feed the chickens', { timeout: 15_000 });
        await card.scrollIntoViewIfNeeded();
        await expect(card).toContainText('1 of 2 left today');
        await expect(card).not.toContainText('Unload the dishwasher'); // Gracie's, not Noah's
        await card.getByLabel('Chore name').fill('Brush the dog');
        await card.getByLabel('Time (optional)').fill('16:30');
        await card.getByRole('button', { name: /Add chore/ }).click();
        await expect(card).toContainText('Brush the dog');
        expect(board.added).toEqual([{ title: 'Brush the dog', assignees: ['noah'], time: '16:30' }]);
        await card.getByRole('button', { name: 'Remove Trash and recycling to the curb' }).click();
        await expect(card).not.toContainText('Trash and recycling');
        expect(board.removed).toEqual([{ id: '51', applyTo: null }]);
        await card.getByRole('button', { name: 'Remove Feed the chickens' }).click();
        await expect(card.getByTestId('chore-remove-prompt')).toBeVisible();
        await fits(page, 'guardian');
        await card.screenshot({ path: shotPath(`chores-${vp.name}-guardian-card`) });
        await card.getByRole('button', { name: 'Keep it' }).click();
        await expect(card.getByTestId('chore-remove-prompt')).toHaveCount(0);

        await page.goto('/family/member/noah?section=time');
        const memberCard = page.getByTestId('guardian-chores');
        await expect(memberCard).toContainText('Feed the chickens', { timeout: 15_000 });
        await memberCard.scrollIntoViewIfNeeded();
        await fits(page, 'member page');
        expect(board.errors).toEqual([]);
      });
    });
  }
});
