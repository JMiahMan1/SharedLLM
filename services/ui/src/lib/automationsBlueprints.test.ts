import { describe, expect, it } from 'vitest';
import {
  actionType, blankStep, blueprintConfig, blueprintUse, describeStep, entitySelector, forEachText, fromBuilder,
  parseForEach, repeatMode, rowsToVariables, selectOptions, selectorKind, stripBlueprintSteps, toBuilder,
  variableRows, variableValue, withBlueprintInput, withRepeatMode,
  type Catalog, type Config,
} from './automations';

const BLUEPRINT_ITEM: Config = {
  id: 'hall_motion', alias: 'Hall motion', description: '',
  use_blueprint: { path: 'someone/motion.yaml', input: { sensor: 'binary_sensor.hall', light: 'light.hall' } },
};

describe('blueprint items', () => {
  it('never get step lists from the builder', () => {
    expect(toBuilder('automation', BLUEPRINT_ITEM)).toEqual({ triggers: [], conditions: [], actions: [] });
    const out = fromBuilder('automation', BLUEPRINT_ITEM, { triggers: [{ trigger: 'time', at: '07:00:00' }], conditions: [], actions: [] });
    expect(out).toEqual(BLUEPRINT_ITEM);
    expect('triggers' in out || 'actions' in out || 'sequence' in out).toBe(false);
    expect(fromBuilder('script', BLUEPRINT_ITEM, { triggers: [], conditions: [], actions: [] })).not.toHaveProperty('sequence');
  });

  it('drops the empty lists an older editor stored beside use_blueprint', () => {
    const damaged = { ...BLUEPRINT_ITEM, triggers: [], conditions: [], actions: [] };
    expect(stripBlueprintSteps(damaged)).toEqual(BLUEPRINT_ITEM);
    const plain = { alias: 'x', triggers: [], actions: [] };
    expect(stripBlueprintSteps(plain)).toBe(plain);
  });

  it('sets and clears inputs, so an empty one falls back to the default', () => {
    let c = withBlueprintInput(BLUEPRINT_ITEM, 'wait', 45);
    expect(blueprintUse(c)?.input).toEqual({ sensor: 'binary_sensor.hall', light: 'light.hall', wait: 45 });
    c = withBlueprintInput(c, 'light', '');
    c = withBlueprintInput(c, 'sensor', []);
    expect(blueprintUse(c)?.input).toEqual({ wait: 45 });
    expect(withBlueprintInput({ alias: 'no blueprint' }, 'x', 1)).toEqual({ alias: 'no blueprint' });
  });

  it('starts a new item from a blueprint', () => {
    expect(blueprintConfig('automation', 'a/b.yaml')).toEqual({ alias: '', description: '', use_blueprint: { path: 'a/b.yaml', input: {} } });
    expect(blueprintConfig('script', 'a/c.yaml')).toEqual({ alias: '', use_blueprint: { path: 'a/c.yaml', input: {} } });
    expect(blueprintUse({ use_blueprint: 'nope' })).toBeNull();
  });

  it('reads selectors', () => {
    expect(selectorKind({ entity: { domain: 'light' } })).toBe('entity');
    expect(selectorKind({ target: {} })).toBe('entity');
    expect(selectorKind({ action: {} })).toBe('action');
    expect(selectorKind({ number: { min: 0 } })).toBe('number');
    expect(selectorKind({})).toBe('text');
    expect(selectorKind({ color_rgb: {} })).toBe('text');
    expect(entitySelector({ entity: { domain: ['light', 'switch'], multiple: true } })).toEqual({ domains: ['light', 'switch'], multiple: true });
    expect(entitySelector({ entity: { filter: [{ domain: 'binary_sensor' }] } })).toEqual({ domains: ['binary_sensor'], multiple: false });
    expect(entitySelector({ target: { entity: { domain: 'media_player' } } })).toEqual({ domains: ['media_player'], multiple: true });
    expect(selectOptions({ select: { options: ['a', { value: 'b', label: 'Bee' }] } })).toEqual([
      { value: 'a', label: 'a' }, { value: 'b', label: 'Bee' }]);
  });
});

const CATALOG = { devices: [{ id: 'dev1', name: 'Desk lamp' }] } as unknown as Catalog;

describe('the new step types', () => {
  it('tells them apart', () => {
    expect(actionType({ device_id: 'dev1', domain: 'light', type: 'turn_on', entity_id: 'x' })).toBe('device');
    expect(actionType({ condition: 'state', entity_id: 'light.a', state: 'on' })).toBe('condition');
    expect(actionType({ sequence: [] })).toBe('sequence');
    expect(actionType({ parallel: [] })).toBe('parallel');
    expect(actionType({ wait_for_trigger: [], timeout: '00:01:00' })).toBe('wait_for_trigger');
    expect(actionType({ action: 'light.turn_on', response_variable: 'r' })).toBe('service');
  });

  it('starts each with the keys HA writes', () => {
    expect(blankStep('action', 'device')).toEqual({ device_id: '', domain: '', type: '' });
    expect(blankStep('trigger', 'device')).toEqual({ trigger: 'device', device_id: '', domain: '', type: '' });
    expect(blankStep('condition', 'device')).toEqual({ condition: 'device', device_id: '', domain: '', type: '' });
    expect(blankStep('action', 'parallel')).toEqual({ parallel: [] });
    expect(blankStep('action', 'condition')).toEqual({ condition: 'template', value_template: '' });
    expect(blankStep('action', 'wait_for_trigger')).toEqual({ wait_for_trigger: [] });
  });

  it('describes them in words', () => {
    expect(describeStep('trigger', { trigger: 'device', device_id: 'dev1', type: 'turned_on' }, CATALOG)).toBe('When Desk lamp turned on');
    expect(describeStep('action', { device_id: 'gone', domain: 'light', type: 'toggle' }, CATALOG)).toBe('a device: toggle');
    expect(describeStep('action', { parallel: [{}, {}] })).toBe('Run 2 steps at once');
    expect(describeStep('action', { condition: 'template', value_template: '{{ x }}' })).toBe('Continue only if: template condition');
    expect(describeStep('action', { repeat: { count: 3, sequence: [] } })).toBe('Repeat 3 times');
    expect(describeStep('action', { repeat: { until: [], sequence: [] } })).toBe('Repeat until conditions hold');
  });

  it('switches a repeat between its modes, keeping the steps', () => {
    const seq = [{ action: 'light.toggle' }];
    expect(repeatMode({ count: 2, sequence: seq })).toBe('count');
    expect(withRepeatMode({ count: 2, sequence: seq }, 'while')).toEqual({ sequence: seq, while: [] });
    expect(withRepeatMode({ while: [], sequence: seq }, 'for_each')).toEqual({ sequence: seq, for_each: [] });
    expect(withRepeatMode({ for_each: ['a'], sequence: seq }, 'count')).toEqual({ sequence: seq, count: 2 });
    expect(forEachText(['kitchen', 'hall'])).toBe('kitchen\nhall');
    expect(parseForEach('kitchen\n hall \n')).toEqual(['kitchen', 'hall']);
    expect(parseForEach('[1, 2]')).toEqual([1, 2]);
    expect(parseForEach('{{ states.light | list }}')).toBe('{{ states.light | list }}');
    expect(forEachText([{ a: 1 }])).toBe('[{"a":1}]');
  });

  it('edits variables as rows and keeps JSON values as JSON', () => {
    const rows = variableRows({ greeting: 'hi', level: 3, rooms: ['a', 'b'], tpl: '{{ x }}' });
    expect(rows).toEqual([{ key: 'greeting', value: 'hi' }, { key: 'level', value: '3' }, { key: 'rooms', value: '["a","b"]' },
      { key: 'tpl', value: '{{ x }}' }]);
    expect(rowsToVariables([...rows, { key: ' ', value: 'dropped' }])).toEqual({ greeting: 'hi', level: 3, rooms: ['a', 'b'], tpl: '{{ x }}' });
    expect(variableValue('true')).toBe(true);
    expect(variableValue('{"a": 1}')).toEqual({ a: 1 });
    expect(variableValue('[not json')).toBe('[not json');
  });
});
