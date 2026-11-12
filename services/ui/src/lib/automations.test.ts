import { describe, expect, it } from 'vitest';
import {
  actionEntities, actionType, filterItems, fromBuilder, toBuilder, withActionEntities,
  type AutomationItem, type Config,
} from './automations';

const CONFIG: Config = {
  id: 'porch_at_dusk',
  alias: 'Porch at dusk',
  mode: 'single',
  triggers: [
    { trigger: 'state', entity_id: 'binary_sensor.porch_motion', to: 'on' },
    { trigger: 'state', entity_id: 'light.porch', from: 'off', to: 'on' },
    { trigger: 'sun', event: 'sunset', offset: '-00:15:00' },
  ],
  conditions: [{ condition: 'or', conditions: [{ condition: 'state', entity_id: 'person.someone', state: 'home' }] }],
  actions: [
    { action: 'light.turn_on', target: { entity_id: 'light.porch' }, data: { brightness_pct: 50 } },
    { delay: '00:05:00' },
    { choose: [{ conditions: [], sequence: [{ action: 'light.turn_off', target: { entity_id: 'light.porch' } }] }] },
    { something_new: true },
  ],
};

describe('builder round trip', () => {
  it('shows a plain on/off state trigger as the device sugar and writes it back unchanged', () => {
    const lists = toBuilder('automation', CONFIG);
    expect(lists.triggers[0]).toEqual({ trigger: 'device_onoff', entity_id: 'binary_sensor.porch_motion', to: 'on' });
    // from: makes it more than "turns on", so it stays a state trigger.
    expect(lists.triggers[1].trigger).toBe('state');
    expect(fromBuilder('automation', CONFIG, lists)).toEqual(CONFIG);
  });

  it('reads old singular keys and writes the plural ones', () => {
    const old: Config = { alias: 'x', trigger: [{ platform: 'time', at: '07:00:00' }], condition: [], action: { service: 'light.turn_on' } };
    const out = fromBuilder('automation', old, toBuilder('automation', old));
    expect(out).toEqual({ alias: 'x', triggers: [{ platform: 'time', at: '07:00:00' }], conditions: [], actions: [{ service: 'light.turn_on' }] });
  });

  it('keeps a script in its sequence', () => {
    const script: Config = { alias: 's', sequence: [{ delay: 5 }] };
    expect(fromBuilder('script', script, toBuilder('script', script))).toEqual(script);
  });

  it('types actions by their keys, unknown ones as raw', () => {
    expect((CONFIG.actions as Config[]).map(actionType)).toEqual(['service', 'delay', 'choose', 'raw']);
  });

  it('reads and writes action targets', () => {
    expect(actionEntities({ service: 'light.turn_on', entity_id: 'light.a' })).toEqual(['light.a']);
    expect(withActionEntities({ service: 'light.turn_on', entity_id: 'light.a' }, ['light.a', 'light.b']))
      .toEqual({ service: 'light.turn_on', target: { entity_id: ['light.a', 'light.b'] } });
    expect(withActionEntities({ action: 'x.y', target: { entity_id: 'light.a' } }, [])).toEqual({ action: 'x.y' });
  });
});

describe('filterItems', () => {
  const item = (id: string, kind: AutomationItem['kind'], entities: string[] = []): AutomationItem => ({
    kind, id, entity_id: `${kind}.${id}`, alias: id.replace(/_/g, ' '), enabled: true, runnable: true,
    unsupported_reason: null, warnings: [], last_triggered: null, last_error: null, running: 0, entities,
  });
  const items = [item('porch_lights', 'automation', ['light.porch']), item('bedtime', 'script'), item('movie', 'scene')];
  it('matches names and entities, and filters by kind', () => {
    expect(filterItems(items, 'porch').map((i) => i.id)).toEqual(['porch_lights']);
    expect(filterItems(items, 'light.porch').map((i) => i.id)).toEqual(['porch_lights']);
    expect(filterItems(items, '', 'scene').map((i) => i.id)).toEqual(['movie']);
    expect(filterItems(items, '').map((i) => i.id)).toEqual(['bedtime', 'movie', 'porch_lights']);
  });
});
