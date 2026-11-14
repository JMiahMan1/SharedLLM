import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks, type Role } from '../fixtures/homeMocks';
import { controlsOffScreen, expectLayoutSound, expectNoHorizontalOverflow, expectTapTargets, isPhoneShell } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The Lists page (the hub's to-do lists) and History's Logbook tab, in every
 * project: desktop, Pixel 7, a small phone, and a tablet both ways round.
 * Hermetic: every call is answered by fixtures/homeMocks.ts, which also fails
 * the test on any call it does not know.
 */

async function open(page: Page, role: Role, path: string, ready: string): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, role);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(path);
  await expect(page.getByTestId(ready)).toBeVisible();
  return { mocks, errors };
}

function clean({ mocks, errors }: { mocks: HomeMocks; errors: string[] }) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('lists and logbook', () => {
  test.describe.configure({ timeout: 90_000 });

  test('Lists: a member adds, ticks off, reorders and clears on a household list', async ({ page }, info) => {
    const run = await open(page, 'member', '/lists', 'lists-page');
    const list = page.getByTestId('todo-list');
    await expect(list.getByRole('heading', { name: 'Reminders' })).toBeVisible();
    await expect(list.getByText(/^Due /)).toBeVisible();
    await expectLayoutSound(page, info, 'lists');
    await expectTapTargets(list);
    expect(await controlsOffScreen(page)).toEqual([]);

    await page.getByRole('tab', { name: /Shopping List/ }).click();
    await expect(page).toHaveURL(/list=todo\.shopping_list/);
    await expect(list.getByRole('heading', { name: 'Shopping List' })).toBeVisible();
    // The shopping list has no due dates.
    await expect(list.getByLabel('Due date (optional)')).toHaveCount(0);

    await list.getByRole('textbox', { name: 'Add to Shopping List' }).fill('Oat milk');
    await list.getByRole('button', { name: 'Add' }).click();
    await expect(list.getByRole('button', { name: 'Rename Oat milk' })).toBeVisible();
    await list.getByRole('button', { name: 'Mark Milk done' }).click();
    await expect(list.getByRole('button', { name: 'Mark Milk done' })).toHaveCount(0);
    await list.getByRole('button', { name: 'Move Oat milk up' }).click();
    await expect(list.getByRole('list', { name: 'To do' }).getByTestId('todo-item').first()).toContainText('Oat milk');

    const done = list.getByTestId('completed-items');
    await done.getByText(/Done \(2\)/).click();
    await expectTapTargets(done);
    await done.getByRole('button', { name: 'Clear done items' }).click();
    await expect(done).toHaveCount(0);

    const writes = run.mocks.posts.filter((p) => p.path.startsWith('/api/lists'));
    expect(writes.map((p) => p.path)).toEqual([
      '/api/lists/todo.shopping_list/items',
      '/api/lists/todo.shopping_list/items/s1',
      '/api/lists/todo.shopping_list/items/n4/move',
      '/api/lists/todo.shopping_list/clear_completed',
    ]);
    expect(writes[0].body).toEqual({ summary: 'Oat milk' });
    expect(writes[1].body).toEqual({ status: 'completed' });
    // Up past Bread: right after the done Milk the hub still keeps above it.
    expect(writes[2].body).toEqual({ previous_uid: 's1' });
    await expectNoHorizontalOverflow(page);
    clean(run);
  });

  test('Lists: the recipe shopping list is a link to Recipes, and the nav offers Lists', async ({ page }) => {
    const run = await open(page, 'member', '/lists', 'lists-page');
    await expect(page.getByTestId('recipe-list-link')).toHaveAttribute('href', '/recipes?tab=list');
    if (await isPhoneShell(page)) {
      await page.getByRole('button', { name: 'More pages' }).click();
      await expect(page.getByTestId('more-sheet').getByRole('link', { name: 'Lists' })).toBeVisible();
      await page.keyboard.press('Escape');
    } else {
      await expect(page.getByRole('link', { name: 'Lists', exact: true })).toBeVisible();
    }
    clean(run);
  });

  test('History: the Logbook tab, by room, for a member', async ({ page }, info) => {
    const run = await open(page, 'member', '/history?view=logbook', 'logbook');
    const book = page.getByTestId('logbook');
    await expect(book.getByText('Reading lamp')).toBeVisible();
    await expect(book.getByText('Today', { exact: true })).toBeVisible();
    await expect(book.getByTestId('logbook-entry')).toHaveCount(4);
    await expectLayoutSound(page, info, 'logbook');
    await expectTapTargets(book);
    expect(await controlsOffScreen(page)).toEqual([]);

    await book.getByRole('combobox', { name: 'Room' }).selectOption('living');
    await expect(page).toHaveURL(/area=living/);
    await expect(book.getByTestId('logbook-entry')).toHaveCount(2);
    await expect(book.getByText('Back door')).toHaveCount(0);

    await page.getByRole('tab', { name: 'Charts' }).click();
    await expect(page.getByText('Pick something to chart')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    clean(run);
  });
});
