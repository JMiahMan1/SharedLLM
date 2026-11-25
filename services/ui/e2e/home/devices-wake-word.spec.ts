import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { HARDWARE, mockDeviceStates } from '../fixtures/hardwareMocks';
import { controlsOffScreen, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > an Echo (hassmic) > Wake word: the per-Echo sensitivity HomeHub keeps (services/homehub/hassmic.py),
 * its warning about accidental wake-ups, and the recent wake-ups with the probability each passed at. Every
 * project (desktop, phones, tablets). Hermetic: the Echo, its entities and its wake-ups are made up.
 */

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const WARNING = 'More sensitive means more accidental wake-ups, especially near a TV or radio. Start with More '
  + 'sensitive, and go to Most sensitive only if the children are still not heard.';
const OPTIONS = ['Standard', 'More sensitive', 'Most sensitive'];
const CUTOFFS: Record<string, [number, number]> = { Standard: [0.97, 5], 'More sensitive': [0.94, 5], 'Most sensitive': [0.91, 4] };

const ECHO = {
  id: 'hw-echo', name: 'Playroom Echo', manufacturer: 'Amazon', model: 'Echo Dot (donut)', sw_version: 'hassmic 1.0',
  area_id: 'den', area: 'Den', domain: 'esphome', label: 'ESPHome', entry_id: 'e-echo', entry_title: 'echo-playroom',
  kind: 'media', abilities: ['select_option'], connections: [{ type: 'mac', value: '02:00:00:00:00:61' }],
  original_name: null, name_by_user: null, model_id: null, hw_version: null, serial_number: null, entry_state: 'loaded',
  entry_reason: null, configuration_url: null, entry_type: null, via_device_id: null, via_device_name: null,
  disabled_by: null, entities: 3, unavailable: 0, status: 'online', last_seen: ago(1), labels: [],
};

const hub = (entity_id: string, name: string, state: string, attributes: Record<string, unknown>) => ({
  entity_id, domain: entity_id.split('.')[0], name, state, attributes, device_class: null, unit: null, icon: null,
  area_id: 'den', device_id: 'hw-echo', last_changed: ago(5),
});

const ent = (entity_id: string, name: string, category: string, state: ReturnType<typeof hub>) => ({
  entity_id, domain: entity_id.split('.')[0], platform: 'esphome', name, name_by_user: null, original_name: null,
  entity_category: category, disabled_by: null, hidden_by: null, area_id: null, icon: null, capabilities: null,
  labels: [], voice: { exposed: false, default: false, chosen: false }, state,
});

function detail(level: string) {
  const [cutoff, window] = CUTOFFS[level];
  return {
    ...ECHO, children: [], entry: null,
    entity_list: [
      ent('select.playroom_echo_wake_word_engine', 'Playroom Echo Wake word engine', 'config',
        hub('select.playroom_echo_wake_word_engine', 'Wake word engine', 'microWakeWord', { options: ['Amazon', 'microWakeWord'] })),
      ent('select.playroom_echo_wake_word_sensitivity', 'Playroom Echo Wake word sensitivity', 'config',
        hub('select.playroom_echo_wake_word_sensitivity', 'Wake word sensitivity', level, {
          options: OPTIONS, warning: WARNING, managed: true,
          models: { hey_jarvis: { cutoff, window }, alexa: { cutoff: Math.round((cutoff - 0.07) * 100) / 100, window } },
        })),
      ent('sensor.playroom_echo_wake_word_detections', 'Playroom Echo Wake word detections', 'diagnostic',
        hub('sensor.playroom_echo_wake_word_detections', 'Wake word detections', '3', {
          detections: [
            { at: '2026-11-20 18:02:11.417Z', wake_word: 'Hey Jarvis', probability: 0.944, cutoff: 0.94, model: 'hey_jarvis', second: false },
            { at: '2026-11-20 17:40:02.004Z', wake_word: 'Alexa', probability: 0.914, cutoff: 0.87, model: 'alexa', second: true },
            { at: 'boot+812.004', wake_word: 'Hey Jarvis', probability: 0.992, cutoff: 0.97, model: 'hey_jarvis', second: false },
          ],
        })),
    ],
  };
}

type Run = { mocks: HomeMocks; errors: string[]; selects: unknown[] };

async function open(page: Page): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  await mockDeviceStates(page);
  const errors: string[] = [];
  const selects: unknown[] = [];
  let level = 'Standard';
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route(/\/api\/homehub\/hardware(\?.*)?$/, (route: Route) =>
    route.fulfill({ json: { ...HARDWARE, devices: [...HARDWARE.devices, ECHO] } }));
  await page.route(/\/api\/homehub\/hardware\/hw-echo(\?.*)?$/, (route: Route) => route.fulfill({ json: detail(level) }));
  await page.route('**/execute/ha_service', (route: Route) => {
    const body = route.request().postDataJSON() as { entity_id: string; service_data?: { option?: string } };
    if (body.entity_id !== 'select.playroom_echo_wake_word_sensitivity') return route.fallback();
    selects.push(body);
    level = String(body.service_data?.option ?? level);
    return route.fulfill({ json: { status: 'SUCCESS', message: 'Done.' } });
  });
  await page.goto('/devices');
  await expect(page.getByTestId('device-tile').first()).toBeVisible();
  return { mocks, errors, selects };
}

test.describe('Devices: an Echo\'s wake word sensitivity', () => {
  test.describe.configure({ timeout: 90_000 });

  test('the level, its warning and the recent wake-ups; changing it goes to the hub', async ({ page }, info) => {
    const run = await open(page);
    await page.getByTestId('device-tile').filter({ hasText: 'Playroom Echo' }).getByTestId('hardware-device').click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('hardware-detail')).toBeVisible();
    const section = dialog.getByTestId('section-wake-word');
    await section.scrollIntoViewIfNeeded();
    await expect(section).toBeVisible();

    const select = section.getByRole('combobox', { name: 'Sensitivity' });
    await expect(select).toHaveValue('Standard');
    await expect(section.getByTestId('wake-sensitivity-warning')).toContainText('more accidental wake-ups, especially near a TV');
    const rows = section.getByTestId('wake-detection');
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText('Hey Jarvis');
    await expect(rows.nth(0)).toContainText('0.94 (cutoff 0.94)');  // only just passed: the setting is close
    await expect(rows.nth(1)).toContainText('Alexa (second)');
    await expect(rows.nth(2)).toContainText('14 min after it started');
    // The two entities are shown here, not again under Configuration or Diagnostics.
    await expect(dialog.getByTestId('section-config')).not.toContainText('Wake word sensitivity');
    await expectTapTargets(section);
    await expectNoHorizontalOverflow(page);
    expect(await controlsOffScreen(page)).toEqual([]);
    await snap(page, info, 'devices-wake-word');

    await select.selectOption('Most sensitive');
    await expect.poll(() => run.selects.length).toBe(1);
    expect(run.selects[0]).toMatchObject({
      domain: 'select', service: 'select_option', entity_id: 'select.playroom_echo_wake_word_sensitivity',
      service_data: { option: 'Most sensitive' },
    });
    await expect(select).toHaveValue('Most sensitive');
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-wake-word-most');

    expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
    expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
  });
});
