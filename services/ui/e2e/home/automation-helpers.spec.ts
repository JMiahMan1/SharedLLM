import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { expectFits, expectLayoutSound, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The Automations page's Helpers section, a new automation from a blueprint,
 * and the visual editor's repeat, parallel, wait-for-trigger and
 * condition steps, in every hermetic project (desktop, phone, small phone,
 * tablet both ways round). Every call is answered by fixtures/homeMocks.ts;
 * names are made up.
 */

async function open(page: Page): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/automations');
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors };
}

function clean({ mocks, errors }: { mocks: HomeMocks; errors: string[] }) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('automations: helpers, blueprints, more steps', () => {
  test.describe.configure({ timeout: 90_000 });

  test('Helpers: list, control, add, edit, delete', async ({ page }, info) => {
    const run = await open(page);
    await page.getByRole('button', { name: 'Helpers' }).click();
    const panel = page.getByTestId('helpers-panel');
    await expect(panel.getByTestId('helper-row')).toHaveCount(3);
    await expect(panel.getByText('input_number Old level: max must be greater than min')).toBeVisible();
    await expectTapTargets(panel.getByTestId('helper-list'));
    await expectLayoutSound(page, info, 'helpers');

    await panel.getByRole('switch', { name: 'Guest mode on' }).click();
    await expect(panel.getByTestId('helper-row').first().getByTestId('helper-value')).toHaveText('On');
    await panel.getByRole('button', { name: 'Start Pasta' }).click();
    await expect(panel.getByRole('button', { name: 'Pause Pasta' })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    expect(run.mocks.posts.some((p) => p.path === '/api/homehub/helpers/timer/pasta/call')).toBe(true);

    await panel.getByRole('button', { name: /Add helper/ }).click();
    const types = page.getByTestId('helper-types');
    // A bottom sheet slides up on phones: measure it once it has arrived.
    await expect(async () => expectFits(page, page.getByRole('dialog'))).toPass();
    await types.getByRole('button', { name: /Dropdown/ }).click();
    const form = page.getByTestId('helper-form');
    await form.getByRole('textbox', { name: 'Name' }).fill('House mode');
    await form.getByRole('textbox', { name: 'New option' }).fill('home');
    await form.getByRole('button', { name: /Add option/ }).click();
    await form.getByRole('textbox', { name: 'New option' }).fill('away');
    await form.getByRole('button', { name: /Add option/ }).click();
    // A bottom sheet slides up on phones: measure it once it has arrived.
    await expect(async () => expectFits(page, page.getByRole('dialog'))).toPass();
    await expectTapTargets(form);
    await snap(page, info, 'helpers-new-dropdown');
    await form.getByRole('button', { name: 'Save' }).click();
    await expect(panel.getByTestId('helper-row')).toHaveCount(4);
    const created = run.mocks.posts.find((p) => p.path === '/api/homehub/helpers/input_select');
    expect(created?.body).toEqual({ name: 'House mode', options: ['home', 'away'] });

    await panel.getByRole('button', { name: 'Edit Guest mode' }).click();
    await page.getByTestId('helper-form').getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('button', { name: 'Delete' }).last().click();
    await expect(panel.getByTestId('helper-row')).toHaveCount(3);
    clean(run);
  });

  test('New from a blueprint: pick it, fill its inputs, save without steps', async ({ page }, info) => {
    const run = await open(page);
    await page.getByRole('button', { name: 'New' }).click();
    await page.getByRole('button', { name: 'From a blueprint' }).click();
    const list = page.getByTestId('blueprint-list');
    await expect(list).toBeVisible();
    // A bottom sheet slides up on phones: measure it once it has arrived.
    await expect(async () => expectFits(page, page.getByRole('dialog'))).toPass();
    await expectTapTargets(list);
    await list.getByRole('button', { name: /Motion-activated light/ }).click();
    const form = page.getByTestId('blueprint-form');
    await expect(form.getByText('Timing')).toBeVisible();
    await expect(page.getByText('Add trigger')).toHaveCount(0);
    await page.getByTestId('automation-editor').getByRole('textbox', { name: 'Name' }).fill('Hall motion');
    await form.getByRole('spinbutton', { name: /Wait after the last motion/ }).fill('300');
    await expectNoHorizontalOverflow(page);
    // A bottom sheet slides up on phones: measure it once it has arrived.
    await expect(async () => expectFits(page, page.getByRole('dialog'))).toPass();
    await snap(page, info, 'automation-from-blueprint');
    await page.getByRole('button', { name: /Check and save/ }).click();
    await expect.poll(() => run.mocks.posts.some((p) => p.path === '/api/homehub/automations/automation')).toBe(true);
    const saved = run.mocks.posts.find((p) => p.path === '/api/homehub/automations/automation')?.body as { config: Record<string, unknown> };
    expect(saved.config).toEqual({ alias: 'Hall motion', description: '',
      use_blueprint: { path: 'someone/motion_light.yaml', input: { wait: 300 } } });
    clean(run);
  });

  test('Visual editor: repeat until, parallel, wait for a trigger, a condition step, variables', async ({ page }, info) => {
    const run = await open(page);
    await page.getByRole('button', { name: 'Open Porch light at dusk' }).click();
    const editor = page.getByTestId('automation-editor');
    await expect(editor).toBeVisible();
    const addAction = editor.getByRole('combobox', { name: 'Add action' }).last();
    for (const type of ['repeat', 'parallel', 'wait_for_trigger', 'condition']) {
      await editor.getByRole('combobox', { name: 'Add action' }).last().selectOption(type);
      await addAction.locator('xpath=following-sibling::button').click();
    }
    await editor.getByRole('combobox', { name: 'Repeat', exact: true }).selectOption('until');
    await expect(editor.getByText('Until all of these hold')).toBeVisible();
    await expect(editor.getByText('All at the same time')).toBeVisible();
    await expect(editor.getByText('Until one of these happens')).toBeVisible();
    await expect(editor.getByRole('combobox', { name: 'Continue only if' })).toBeVisible();
    await editor.getByRole('button', { name: /^Variables/ }).click();
    await editor.getByRole('button', { name: 'Add variable' }).click();
    await editor.getByRole('textbox', { name: 'Name' }).last().fill('greeting');
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(editor.getByTestId('step-action').first());
    await snap(page, info, 'automation-editor-steps');
    await page.getByRole('tab', { name: 'YAML' }).click();
    const yaml = await editor.getByRole('textbox').first().inputValue();
    expect(JSON.parse(yaml)).toMatchObject({
      actions: [{ repeat: { until: [] } }, { parallel: [] }, { wait_for_trigger: [] }, { condition: 'template' }],
      variables: { greeting: '' },
    });
    clean(run);
  });

  test('Helpers: a value being typed survives the 5 s poll, even one that fails', async ({ page }) => {
    const run = await open(page);
    await page.getByRole('button', { name: 'Helpers' }).click();
    const panel = page.getByTestId('helpers-panel');
    await expect(panel.getByTestId('helper-row')).toHaveCount(3);
    // The hub stops answering for a while: every poll fails until it is back.
    let failing = false;
    let failed = 0;
    await page.route(/\/api\/homehub\/helpers$/, (route) => {
      if (failing && route.request().method() === 'GET') {
        failed += 1;
        // A 500 (the API client retries a 504 by itself, so that would not reach the page).
        return route.fulfill({ status: 500, json: { detail: 'hub timed out' } });
      }
      return route.fallback();
    });
    const box = panel.getByRole('textbox', { name: /Run Pasta for/ });
    await box.click();
    await page.keyboard.type('0:0');
    failing = true;
    await expect.poll(() => failed, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
    await page.waitForTimeout(500);  // the failed answer has reached the page
    await page.keyboard.type('0:45');
    await expect(box).toBeFocused();
    await expect(box).toHaveValue('0:00:45');
    await expect(panel.getByText('hub timed out')).toHaveCount(0);
    failing = false;
    expect(run.errors).toEqual([]);
  });
});
