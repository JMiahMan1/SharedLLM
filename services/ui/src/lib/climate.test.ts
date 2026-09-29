import { describe, it, expect } from 'vitest';
import {
  actionTone, climateAttrs, entityLabel, formatTemp, isClimateEntity, modeVisual,
  modesFor, numberAttr, setpointRange, snap, statusLine, titleize,
} from './climate';
import type { ClimateAttributes } from './climate';
import type { DeviceEntry } from '../types/widget';

const device = (overrides: Partial<DeviceEntry> = {}): DeviceEntry => ({
  entity_id: 'climate.living_room',
  friendly_name: 'Living Room',
  domain: 'climate',
  state: 'heat',
  attributes: { current_temperature: 20, temperature: 22 },
  ...overrides,
});

const attrs = (overrides: Partial<ClimateAttributes> = {}): ClimateAttributes =>
  ({ current_temperature: 20, temperature: 22, ...overrides });

describe('titleize', () => {
  it('turns HA identifiers into words', () => {
    expect(titleize('heat_cool')).toBe('Heat Cool');
    expect(titleize('preheating')).toBe('Preheating');
  });

  it('returns an empty string for a missing value rather than "undefined"', () => {
    expect(titleize()).toBe('');
    expect(titleize('')).toBe('');
  });
});

describe('modeVisual', () => {
  it('knows the common modes and their icons', () => {
    expect(modeVisual('heat')).toMatchObject({ label: 'Heat', tone: 'text-amber-400' });
    expect(modeVisual('off').Icon).toBeDefined();
  });

  it('labels an unknown mode instead of dropping it', () => {
    expect(modeVisual('dehumidify')).toMatchObject({ label: 'Dehumidify', tone: 'text-slate-300' });
  });

  it('falls back to a generic label with no mode at all', () => {
    expect(modeVisual()).toMatchObject({ label: 'Mode' });
  });
});

describe('actionTone', () => {
  it('follows what the unit is doing, not the mode it is in', () => {
    expect(actionTone('heating', 'heat_cool').stroke).toBe('#fb923c');
    expect(actionTone('cooling', 'heat_cool').stroke).toBe('#22d3ee');
  });

  it('ignores an "off" action and colours by the mode instead', () => {
    expect(actionTone('off', 'cool')).toMatchObject({ stroke: '#22d3ee', label: 'Cool' });
  });

  it('greys an idle unit and a fully off one differently', () => {
    expect(actionTone('idle', 'heat')).toMatchObject({ stroke: '#64748b', label: 'Idle' });
    expect(actionTone('off', 'off')).toMatchObject({ stroke: '#475569', label: 'Off' });
  });

  it('degrades to off with no action and no mode rather than throwing', () => {
    expect(actionTone()).toMatchObject({ stroke: '#475569', label: 'Off' });
  });
});

describe('setpointRange', () => {
  it('uses the bounds and step the entity advertises', () => {
    expect(setpointRange(attrs({ min_temp: 16, max_temp: 30, target_temp_step: 0.5 })))
      .toEqual({ min: 16, max: 30, step: 0.5 });
  });

  it('derives a window from the entity target when the integration omits min/max', () => {
    const range = setpointRange(attrs({ temperature: 22, target_temp_step: 1 }));
    expect(range.min).toBe(12);
    expect(range.max).toBe(32);
  });

  it('derives from both bounds of a heat_cool entity', () => {
    const range = setpointRange(attrs({ target_temp_low: 18, target_temp_high: 24, target_temp_step: 0.5 }));
    expect(range.min).toBe(13);
    expect(range.max).toBe(29);
  });

  it('rejects a nonsense step instead of dividing by zero', () => {
    expect(setpointRange(attrs({ target_temp_step: 0, min_temp: 10, max_temp: 20 })).step).toBe(1);
    expect(setpointRange(attrs({ target_temp_step: -2, min_temp: 10, max_temp: 20 })).step).toBe(1);
  });

  it('normalises an inverted min/max pair instead of inverting the dial', () => {
    expect(setpointRange(attrs({ min_temp: 30, max_temp: 16 }))).toMatchObject({ min: 16, max: 30 });
  });

  it('never returns NaN for an entity that reports no temperature at all', () => {
    const range = setpointRange({});
    expect(Number.isFinite(range.min)).toBe(true);
    expect(Number.isFinite(range.max)).toBe(true);
  });
});

describe('snap', () => {
  it('snaps to the entity step and stays inside the range', () => {
    expect(snap(22.3, 16, 30, 0.5)).toBe(22.5);
    expect(snap(99, 16, 30, 1)).toBe(30);
    expect(snap(-99, 16, 30, 1)).toBe(16);
  });

  it('does not leave floating point dust behind', () => {
    expect(snap(22.1, 16, 30, 0.5)).toBe(22);
    expect(snap(20.3, 16, 30, 0.1)).toBe(20.3);
  });
});

describe('numberAttr', () => {
  it('accepts the numbers HA sends', () => {
    expect(numberAttr(21.5)).toBe(21.5);
  });

  it('parses the numeric strings some integrations send', () => {
    expect(numberAttr('21.5')).toBe(21.5);
  });

  it('returns undefined for junk instead of NaN', () => {
    expect(numberAttr('warm')).toBeUndefined();
    expect(numberAttr('')).toBeUndefined();
    expect(numberAttr('  ')).toBeUndefined();
    expect(numberAttr(null)).toBeUndefined();
    expect(numberAttr(Number.NaN)).toBeUndefined();
    expect(numberAttr(Number.POSITIVE_INFINITY)).toBeUndefined();
  });
});

describe('climateAttrs', () => {
  it('tolerates a device with no attributes at all', () => {
    expect(climateAttrs(undefined)).toEqual({});
    expect(climateAttrs({ ...device(), attributes: undefined as never })).toEqual({});
  });
});

describe('statusLine', () => {
  it('reports the live action plus humidity', () => {
    expect(statusLine(device(), attrs({ hvac_action: 'heating', humidity: 44.4 })))
      .toBe('Heating · 44% RH');
  });

  it('falls back to the mode when the entity reports no action', () => {
    expect(statusLine(device(), attrs({ hvac_mode: 'heat_cool' }))).toBe('Heat/Cool');
  });

  it('says so when the unit is unavailable, instead of implying it is idle', () => {
    expect(statusLine(device({ state: 'unavailable' }), attrs())).toBe('Unavailable');
    expect(statusLine(device({ state: 'unknown' }), attrs())).toBe('Unavailable');
  });
});

describe('entityLabel', () => {
  it('prefers the entity friendly name, then the row name, then the id', () => {
    expect(entityLabel(device({ attributes: { friendly_name: 'Downstairs' } }))).toBe('Downstairs');
    expect(entityLabel(device({ friendly_name: 'Hall' }))).toBe('Hall');
    expect(entityLabel(device({ friendly_name: '' }))).toBe('climate.living_room');
  });
});

describe('modesFor', () => {
  it('uses the modes the entity advertises', () => {
    expect(modesFor(device({ attributes: { hvac_modes: ['off', 'fan_only'] } }))).toEqual(['off', 'fan_only']);
  });

  it('falls back to a conservative list when the entity advertises none', () => {
    expect(modesFor(device())).toContain('heat');
  });
});

describe('isClimateEntity', () => {
  it('accepts a climate domain or a climate id', () => {
    expect(isClimateEntity(device())).toBe(true);
    expect(isClimateEntity(device({ domain: 'sensor', entity_id: 'climate.attic' }))).toBe(true);
    expect(isClimateEntity(device({ domain: 'light', entity_id: 'light.lamp' }))).toBe(false);
  });
});

describe('formatTemp', () => {
  it('drops decimals below a tenth and shows a placeholder for a missing value', () => {
    expect(formatTemp(21.46)).toBe(21.5);
    expect(formatTemp(undefined)).toBe('--');
  });
});
