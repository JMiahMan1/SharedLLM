import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The visual editor's voice steps: a sentence trigger, the reply to it, a voice
 * satellite's timer, and a spoken message in a cloned voice, in every hermetic
 * project (desktop, phone, small phone, tablet both ways round). Every call is
 * answered by fixtures/homeMocks.ts; names are made up.
 */

async function open(page: Page): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/automations');
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors };
}

test.describe('automations: voice steps', () => {
  test.describe.configure({ timeout: 90_000 });

  test('Sentence trigger, reply, satellite timer and a cloned voice', async ({ page }, info) => {
    const { mocks, errors } = await open(page);
    await page.getByRole('button', { name: 'Open Porch light at dusk' }).click();
    const editor = page.getByTestId('automation-editor');
    await expect(editor).toBeVisible();

    const addTrigger = editor.getByRole('combobox', { name: 'Add trigger' });
    await addTrigger.selectOption('conversation');
    await addTrigger.locator('xpath=following-sibling::button').click();
    const sentences = editor.getByRole('textbox', { name: /Sentences/ });
    await sentences.fill('good night\n[it is] bed time');

    for (const type of ['set_conversation_response', 'satellite_timer']) {
      const addAction = editor.getByRole('combobox', { name: 'Add action' }).last();
      await addAction.selectOption(type);
      await addAction.locator('xpath=following-sibling::button').click();
    }
    await editor.getByRole('textbox', { name: /^Reply/ }).fill('Sleep well');
    await editor.getByRole('textbox', { name: 'Voice satellite' }).click();
    await editor.getByRole('button', { name: /Den voice/ }).click();
    await editor.getByRole('combobox', { name: /^Event/ }).selectOption('finished');
    await editor.getByRole('textbox', { name: /^Timer id/ }).fill('t1');

    // A spoken message: tts.speak, in a cloned voice.
    const addAction = editor.getByRole('combobox', { name: 'Add action' }).last();
    await addAction.selectOption('service');
    await addAction.locator('xpath=following-sibling::button').click();
    await editor.getByRole('combobox', { name: 'Service' }).last().selectOption('tts.speak');
    const voice = editor.getByTestId('tts-voice').getByRole('combobox', { name: /Voice/ });
    await expect(voice.locator('optgroup[label="Cloned voices"] option')).toHaveText(['Pat']);
    await voice.selectOption('pat-0a1b2c');

    await expect(editor.getByText('When someone says "good night" or 1 more')).toBeVisible();
    await expect(editor.getByText('Timer finished on Den voice')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(editor.getByTestId('step-trigger').last());
    await snap(page, info, 'automation-editor-voice-steps');

    await page.getByRole('tab', { name: 'YAML' }).click();
    const yaml = await editor.getByRole('textbox').first().inputValue();
    expect(JSON.parse(yaml)).toMatchObject({
      triggers: [{ trigger: 'conversation', command: ['good night', '[it is] bed time'] }],
      actions: [
        { set_conversation_response: 'Sleep well' },
        { action: 'assist_satellite.timer_event', target: { entity_id: 'assist_satellite.den_voice' },
          data: { event: 'finished', timer_id: 't1' } },
        { action: 'tts.speak', data: { options: { voice: 'pat-0a1b2c' } } },
      ],
    });
    expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
    expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
  });
});
