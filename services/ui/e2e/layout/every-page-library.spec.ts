import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockMaJsonRpc, mockSendspin, pushNowPlaying, seedPlayerId, WEB_PLAYER_ID } from '../fixtures/mediaMocks';
import { LONG_NOTE_TITLE, LONG_RECIPE_TITLE, mockLibraryPages } from '../fixtures/libraryPagesMocks';
import { openAllDetails, screenFindings, settle, shot, type Allowed, quietHotReload } from '../fixtures/pageAudit';

/**
 * Every "library" page (Notes, Recipes, Media, Knowledge, Workspaces,
 * Artifacts, Docs, the Lab) and the dialogs, sheets and editors they open, on
 * every form factor, with long made-up content: thumb-sized controls on a
 * phone, no hard-clipped text, nothing past its card or the screen, and no
 * sideways scroll. Hermetic: every call is answered (the catch-all stays empty)
 * and no page throws.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** Findings for deliberate designs, each with its reason. Nothing here hides a bug. */
const ALLOWED: Allowed[] = [];

interface Run {
  home: HomeMocks;
  errors: string[];
  findings: string[];
}

async function open(page: Page, path: string): Promise<Run> {
  await quietHotReload(page);
  const home = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockLibraryPages(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error' && /React UI error|Uncaught/.test(m.text())) errors.push(m.text().slice(0, 300)); });
  await page.goto(path);
  return { home, errors, findings: [] };
}

/** Audit one screen (the page's main, or a dialog on it), screenshot it, and keep its findings. */
async function check(page: Page, info: TestInfo, run: Run, scope: Locator, where: string): Promise<void> {
  await settle(page);
  await openAllDetails(scope);
  await settle(page);
  run.findings.push(...await screenFindings(page, scope, where, ALLOWED));
  await shot(page, info, where.replace(/[^a-z0-9]+/gi, '-'));
}

function finish(run: Run): void {
  expect(run.findings).toEqual([]);
  expect(run.home.unmocked).toEqual([]);
  expect(run.errors).toEqual([]);
}

const main = (page: Page) => page.locator('main');
const lastDialog = (page: Page) => page.getByRole('dialog').last();

test.describe('library pages on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('notes: the list and the editor with a long note', async ({ page }, info) => {
    const run = await open(page, '/notes');
    await expect(main(page).getByText(LONG_NOTE_TITLE).first()).toBeVisible();
    await check(page, info, run, main(page), 'notes list');

    await main(page).getByText(LONG_NOTE_TITLE).first().click();
    const editor = page.getByTestId('note-editor');
    await expect(editor).toBeVisible();
    await check(page, info, run, editor, 'note editor');
    for (const name of ['Share', 'Remind me', 'History']) {
      // The footer's panel toggles (a toolbar has its own Share-to-chat button).
      const button = editor.locator('button[aria-pressed]', { hasText: name }).first();
      if (!(await button.count())) continue;
      await button.scrollIntoViewIfNeeded();
      await button.click();
      await expect(editor, `the editor stays open with ${name} open`).toBeVisible();
      await check(page, info, run, editor, `note editor with ${name} open`);
      await button.scrollIntoViewIfNeeded();
      await button.click();
    }
    finish(run);
  });

  test('recipes: the shelf, a recipe, the forms, the week and the list', async ({ page }, info) => {
    const run = await open(page, '/recipes');
    await expect(main(page).getByText(LONG_RECIPE_TITLE).first()).toBeVisible();
    await check(page, info, run, main(page), 'recipes shelf');

    await page.getByTestId('recipe-suggestions-toggle').click();
    await check(page, info, run, main(page), 'recipes suggestions');
    await page.getByRole('button', { name: 'More cookbook actions' }).click();
    await check(page, info, run, main(page), 'recipes menu');
    await page.getByRole('button', { name: 'More cookbook actions' }).click();

    await page.getByRole('button', { name: 'This week', exact: true }).click();
    await expect(main(page).getByText('Beef tacos').first()).toBeVisible();
    await check(page, info, run, main(page), 'recipes week');

    await page.getByRole('button', { name: 'Shopping list', exact: true }).click();
    await expect(main(page).getByText('Tortillas').first()).toBeVisible();
    await check(page, info, run, main(page), 'recipes shopping list');

    await page.goto(`/recipes?recipe=${encodeURIComponent(`Recipes/${LONG_RECIPE_TITLE}.md`)}`);
    await expect(main(page).getByText('Ingredients').first()).toBeVisible();
    await check(page, info, run, main(page), 'recipe card');

    await page.goto(`/recipes?edit=${encodeURIComponent(`Recipes/${LONG_RECIPE_TITLE}.md`)}`);
    await expect(main(page).locator('textarea, input').first()).toBeVisible();
    await check(page, info, run, main(page), 'recipe editor');

    await page.goto('/recipes?add=1');
    await expect(main(page).locator('input, textarea').first()).toBeVisible();
    await check(page, info, run, main(page), 'add a recipe');
    finish(run);
  });

  test('media: listen now, search, library, an album and the player sheets', async ({ page }, info) => {
    await seedPlayerId(page);
    const ma = await mockMaJsonRpc(page);
    await mockSendspin(page);
    const run = await open(page, '/media');
    await expect(page.getByRole('heading', { name: 'Listen Now', level: 1 })).toBeVisible();
    await expect(page.getByRole('button', { name: /Midnight Drive/ }).first()).toBeVisible();
    await check(page, info, run, main(page), 'media listen now');

    for (const [path, name] of [['/media/search?q=neon', 'search'], ['/media/library', 'library'], ['/media/album/library/1', 'album']] as const) {
      await page.goto(path);
      await expect(main(page).getByText(/Neon|Midnight/).first()).toBeVisible();
      await check(page, info, run, main(page), `media ${name}`);
    }

    await page.goto('/media');
    await page.getByRole('button', { name: 'Play Midnight Drive' }).first().click();
    await expect.poll(() => ma.sentCommands.some((c) => c.command === 'player_queues/play_media')).toBe(true);
    pushNowPlaying(ma, WEB_PLAYER_ID, { title: 'Midnight Drive', artist: 'The Synthwave Collective', uri: 'library://track/1', duration: 210, position: 12 });
    await expect(page.getByText('Playing on this device', { exact: true })).toBeVisible();
    // The mini player shows away from the media page: move to the Docs page in the app, as a link would.
    await page.evaluate(() => { window.history.pushState({}, '', '/docs'); window.dispatchEvent(new PopStateEvent('popstate')); });
    await page.getByTestId('mini-player').getByRole('button', { name: 'Open now playing' }).click();
    const sheet = page.getByTestId('now-playing-sheet');
    await expect(sheet).toBeVisible();
    await check(page, info, run, sheet, 'media now playing');
    await sheet.getByRole('button', { name: 'Open queue' }).click();
    const queue = page.getByTestId('queue-sheet');
    await expect(queue).toBeVisible();
    await check(page, info, run, queue, 'media queue');
    await queue.getByRole('button', { name: 'Close queue' }).click();
    await expect(queue).toBeHidden();
    await sheet.getByRole('button', { name: 'Choose output' }).click();
    const output = page.getByTestId('output-sheet');
    await expect(output).toBeVisible();
    await check(page, info, run, output, 'media output');
    finish(run);
  });

  test('knowledge', async ({ page }, info) => {
    const run = await open(page, '/knowledge');
    await expect(main(page).getByText('48,210').first()).toBeVisible();
    await check(page, info, run, main(page), 'knowledge');
    finish(run);
  });

  test('workspaces: the list and the new and edit dialogs', async ({ page }, info) => {
    const run = await open(page, '/workspaces');
    await expect(main(page).getByText('School projects').first()).toBeVisible();
    await check(page, info, run, main(page), 'workspaces');

    await page.getByRole('button', { name: 'Add Workspace' }).click();
    await expect(lastDialog(page)).toBeVisible();
    await check(page, info, run, lastDialog(page), 'new workspace');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);

    await page.getByRole('button', { name: /^Configure The family website/ }).first().click();
    await expect(lastDialog(page)).toBeVisible();
    await check(page, info, run, lastDialog(page), 'edit workspace');
    finish(run);
  });

  test('artifacts: the list and a preview', async ({ page }, info) => {
    const run = await open(page, '/artifacts');
    await expect(main(page).getByText(/Morning briefing/).first()).toBeVisible();
    await check(page, info, run, main(page), 'artifacts');
    await main(page).getByRole('button', { name: /^Preview Supercali/ }).first().click();
    await expect(lastDialog(page)).toBeVisible();
    await expect(lastDialog(page).locator('img')).toBeVisible();
    await check(page, info, run, lastDialog(page), 'artifact preview');
    finish(run);
  });

  test('docs', async ({ page }, info) => {
    const run = await open(page, '/docs');
    await expect(main(page).getByRole('heading', { name: 'How the services fit together' })).toBeVisible();
    await check(page, info, run, main(page), 'docs');
    finish(run);
  });

  test('lab: missions and tests', async ({ page }, info) => {
    const run = await open(page, '/lab');
    await expect(main(page).getByText(/Look through the gateway logs/).first()).toBeVisible();
    await check(page, info, run, main(page), 'lab missions');
    await page.goto('/lab?tab=tests');
    await settle(page);
    await check(page, info, run, main(page), 'lab tests');
    finish(run);
  });
});
