import { describe, expect, it } from 'vitest';
import { blankHelper, cleanConfig, describeValue, editableConfig, formatSeconds, type HelperRow, type HelperType } from './helpers';

const NUMBER: HelperType = {
  domain: 'input_number', label: 'Number', services: ['set_value'],
  fields: [
    { name: 'min', label: 'Minimum', kind: 'number', required: true, default: 0 },
    { name: 'max', label: 'Maximum', kind: 'number', required: true, default: 100 },
    { name: 'unit_of_measurement', label: 'Unit', kind: 'text' },
  ],
};

const row = (over: Partial<HelperRow>): HelperRow => ({
  domain: 'input_boolean', id: 'x', entity_id: 'input_boolean.x', name: 'X', config: { id: 'x', name: 'X' },
  state: { entity_id: 'input_boolean.x', state: 'on', attributes: {} }, disabled: false, ...over,
});

describe('helper configs', () => {
  it('starts from the type defaults and sends only what was filled', () => {
    expect(blankHelper(NUMBER)).toEqual({ name: '', min: 0, max: 100 });
    expect(blankHelper({ ...NUMBER, domain: 'input_select', fields: [] })).toEqual({ name: '', options: [] });
    expect(cleanConfig(NUMBER, { name: 'Fan', min: '1', max: '5', unit_of_measurement: '' })).toEqual({ name: 'Fan', min: 1, max: 5 });
    expect(editableConfig(row({ config: { id: 'x', name: 'X', initial: true } }))).toEqual({ name: 'X', initial: true });
  });
});

describe('helper values in words', () => {
  const now = new Date('2026-03-04T17:00:00Z');
  it('reads each kind', () => {
    expect(describeValue(row({}))).toBe('On');
    expect(describeValue(row({ domain: 'input_number', state: { entity_id: 'n', state: '2.5', attributes: { unit_of_measurement: 'lvl' } } }))).toBe('2.5 lvl');
    expect(describeValue(row({ domain: 'input_text', state: { entity_id: 't', state: 'unknown', attributes: {} } }))).toBe('Empty');
    expect(describeValue(row({ domain: 'input_text', state: { entity_id: 't', state: 'secret', attributes: { mode: 'password' } } }))).toBe('Hidden');
    expect(describeValue(row({ domain: 'input_button', state: { entity_id: 'b', state: 'unknown', attributes: {} } }))).toBe('Never pressed');
    expect(describeValue(row({ domain: 'timer', state: { entity_id: 't', state: 'active',
      attributes: { finishes_at: '2026-03-04T17:04:59Z' } } }), now)).toBe('4:59 left');
    expect(describeValue(row({ domain: 'timer', state: { entity_id: 't', state: 'paused', attributes: { remaining: '0:02:00' } } }))).toBe('Paused, 0:02:00 left');
    expect(describeValue(row({ domain: 'timer', state: { entity_id: 't', state: 'idle', attributes: {} } }))).toBe('Idle');
    expect(describeValue(row({ disabled: true }))).toBe('Disabled');
    expect(describeValue(row({ state: null }))).toBe('Not running');
  });

  it('formats seconds', () => {
    expect(formatSeconds(299.4)).toBe('4:59');
    expect(formatSeconds(3725)).toBe('1:02:05');
  });
});
