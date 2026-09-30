import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import type { Page, Route } from '@playwright/test';
import { seedAuth } from './mediaMocks';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Screenshots land in the workspace .tmp/ dir (never /tmp). */
export const SHOTS_DIR = path.resolve(__dirname, '../../../../.tmp/shots');
fs.mkdirSync(SHOTS_DIR, { recursive: true });

export interface HaEntity {
  entity_id: string;
  friendly_name: string;
  state: string;
  domain: string;
  area_id?: string;
  attributes: Record<string, unknown>;
  last_updated: string;
}

const MODES = ['off', 'heat', 'cool', 'heat_cool', 'auto'];

/** A spread of real-world thermostat shapes, including the awkward ones. */
function baseEntities(): HaEntity[] {
  const now = new Date('2026-09-29T14:00:00Z').toISOString();
  return [
    {
      entity_id: 'climate.living_room', friendly_name: 'Living Room', state: 'heat', domain: 'climate',
      area_id: 'living_room', last_updated: now,
      attributes: {
        hvac_mode: 'heat', hvac_action: 'heating', current_temperature: 21.3, temperature: 22,
        humidity: 44, hvac_modes: MODES, min_temp: 7, max_temp: 35, target_temp_step: 0.5,
        preset_modes: ['none', 'away', 'home'], preset_mode: 'home',
        fan_modes: ['auto', 'low', 'medium', 'high'], fan_mode: 'auto',
        swing_modes: ['off', 'on', 'oscillate'], swing_mode: 'on',
      },
    },
    {
      // Dual-bound: only accepts low + high, never a single target.
      entity_id: 'climate.kitchen', friendly_name: 'Kitchen', state: 'heat_cool', domain: 'climate',
      area_id: 'kitchen', last_updated: now,
      attributes: {
        hvac_mode: 'heat_cool', hvac_action: 'idle', current_temperature: 21.8,
        target_temp_low: 19.5, target_temp_high: 24.5, humidity: 39,
        hvac_modes: MODES, min_temp: 7, max_temp: 35, target_temp_step: 0.5,
      },
    },
    {
      entity_id: 'climate.bedroom', friendly_name: 'Primary Bedroom', state: 'off', domain: 'climate',
      area_id: 'bedroom', last_updated: now,
      attributes: {
        hvac_mode: 'off', hvac_action: 'off', current_temperature: 19.1, temperature: 21,
        humidity: 51, hvac_modes: MODES, min_temp: 10, max_temp: 30,
      },
    },
    {
      // Advertises no min/max: the widget must derive a range from the target.
      entity_id: 'climate.nursery', friendly_name: 'Nursery', state: 'cool', domain: 'climate',
      area_id: 'nursery', last_updated: now,
      attributes: {
        hvac_mode: 'cool', hvac_action: 'cooling', current_temperature: 22.6, temperature: 22,
        target_temp_step: 1, humidity: 47, hvac_modes: ['off', 'cool', 'auto'],
      },
    },
    {
      // Integration dropped it: must render as Unavailable, never as a dial.
      entity_id: 'climate.office', friendly_name: 'Office', state: 'unavailable', domain: 'climate',
      area_id: 'office', last_updated: now,
      attributes: { friendly_name: 'Office' },
    },
  ];
}

const HIDDEN_KEYS = [
  'energy_insights', 'ambient_timer', 'quick_notes', 'active_media', 'chores_progress',
  'upcoming_events', 'quick_assistant', 'device_control', 'workspaces', 'health_activity',
];

export interface ClimateScenario {
  devices: string[];
  layout?: 'auto' | 'dial' | 'tiles';
  size?: 'small' | 'medium' | 'wide' | 'tall';
}

export interface ClimateMocks {
  /** Every ha_service call the UI made, in order. */
  commands: Array<{ domain: string; service: string; entity_id: string; service_data: Record<string, unknown> }>;
  /** Endpoints that fell through to the generic stub — keep this short. */
  unmocked: string[];
  entities: HaEntity[];
}

/** The fake HA is stateful: an echoed state change settles the optimistic paint. */
function applyService(entities: HaEntity[], body: {
  domain: string; service: string; entity_id: string; service_data?: Record<string, unknown> | null;
}): void {
  const entity = entities.find((e) => e.entity_id === body.entity_id);
  if (!entity) return;
  const data = body.service_data || {};
  const attrs = entity.attributes as Record<string, unknown>;
  switch (body.service) {
    case 'set_temperature':
      if (typeof data.temperature === 'number') attrs.temperature = data.temperature;
      if (typeof data.target_temp_low === 'number') attrs.target_temp_low = data.target_temp_low;
      if (typeof data.target_temp_high === 'number') attrs.target_temp_high = data.target_temp_high;
      break;
    case 'set_hvac_mode': {
      const mode = String(data.hvac_mode);
      attrs.hvac_mode = mode;
      entity.state = mode;
      attrs.hvac_action = mode === 'off' ? 'off'
        : mode === 'heat' ? 'heating'
        : mode === 'cool' ? 'cooling'
        : 'idle';
      break;
    }
    case 'set_preset_mode': attrs.preset_mode = data.preset_mode; break;
    case 'set_fan_mode': attrs.fan_mode = data.fan_mode; break;
    case 'set_swing_mode': attrs.swing_mode = data.swing_mode; break;
    default: break;
  }
  entity.last_updated = new Date().toISOString();
}

/**
 * Boots the dashboard with only the climate widget visible. Every other widget
 * is `hidden` so it never mounts (and therefore never fetches), which keeps the
 * screenshot honest about the widget under test.
 */
export async function mockClimateApi(page: Page, scenario: ClimateScenario): Promise<ClimateMocks> {
  const entities = baseEntities();
  const commands: ClimateMocks['commands'] = [];
  const layout = scenario.layout ?? 'auto';
  const size = scenario.size ?? 'medium';

  // Registered first so it is consulted last: anything unmocked gets a benign
  // payload instead of a network error against a host that is not running.
  // Lists are the common shape, so GET defaults to [] — an object there makes
  // the app's `x.filter(...)` blow up and the error boundary eats the page.
  const objectResponses: Array<[RegExp, unknown]> = [
    [/\/api\/health\b/, { status: 'ok' }],
    [/\/api\/info\b/, { service: 'jarvis-ui', version: 'e2e', git_sha: 'e2e', git_branch: 'e2e' }],
    [/\/api\/(config|settings)\b/, {}],
    [/\/api\/telemetry\/(notifications|schedules)\b/, { notifications: [], jobs: [] }],
  ];
  const unmocked: string[] = [];
  const catchAll = async (route: Route): Promise<void> => {
    const request = route.request();
    const url = request.url();
    unmocked.push(`${request.method()} ${new URL(url).pathname}`);
    if (request.method() !== 'GET') return route.fulfill({ json: { status: 'ok', message: 'e2e' } });
    const hit = objectResponses.find(([re]) => re.test(url));
    return route.fulfill({ json: hit ? hit[1] : [] });
  };

  await page.route('**/execute/**', catchAll);
  await page.route('**/api/**', catchAll);

  await page.route('**/api/widgets/settings', (route) =>
    route.fulfill({
      json: {
        widgets: [
          {
            widget_key: 'climate', visibility: 'visible', order_index: 0, size,
            is_pinned: false, sort_mode: null, pinned_devices: [],
            config: { devices: scenario.devices, layout }, updated_at: 0,
          },
          ...HIDDEN_KEYS.map((key, i) => ({
            widget_key: key, visibility: 'hidden', order_index: i + 1, size: 'medium',
            is_pinned: false, sort_mode: null, pinned_devices: [], config: {}, updated_at: 0,
          })),
        ],
        quick_assistant_enabled: false,
      },
    }),
  );
  await page.route('**/api/widgets/settings/**', (route) =>
    route.fulfill({ json: { status: 'ok', message: 'saved' } }),
  );
  await page.route('**/api/users/devices', (route) => route.fulfill({ json: [] }));
  await page.route('**/execute/entity/search', (route) => {
    const body = route.request().postDataJSON() as { domain?: string | null };
    const domain = body?.domain ? String(body.domain).split(',')[0] : null;
    return route.fulfill({
      json: { status: 'ok', result: domain ? entities.filter((e) => e.domain === domain) : entities },
    });
  });
  await page.route('**/execute/ha_service', (route) => {
    const body = route.request().postDataJSON() as {
      domain: string; service: string; entity_id: string; service_data?: Record<string, unknown> | null;
    };
    commands.push({
      domain: body.domain, service: body.service, entity_id: body.entity_id,
      service_data: body.service_data || {},
    });
    applyService(entities, body);
    return route.fulfill({ json: { status: 'ok', message: 'called' } });
  });

  // Registered last so it wins over the catch-all for /api/users/me.
  await seedAuth(page);

  return { commands, unmocked, entities };
}

export function shotPath(name: string): string {
  return path.join(SHOTS_DIR, `${name}.png`);
}
