import { describe, it, expect } from 'vitest';
import { can, supportsBrightness, type HubEntity } from './dashboards';
import { entityTakesService, servicesForEntities, type Catalog } from './automations';

const light = (over: Partial<HubEntity>): HubEntity => ({
  entity_id: 'light.porch', domain: 'light', name: 'Porch', state: 'on', attributes: {}, device_class: null,
  unit: null, icon: null, area_id: null, device_id: null, last_changed: '', ...over,
});

describe('controls from capabilities', () => {
  it('uses the hub\'s abilities when it has them, the attributes when it does not', () => {
    expect(can(light({ abilities: ['power'] }), 'dim')).toBe(false);
    expect(can(light({}), 'dim')).toBe(true);
    expect(can(light({}), 'dim', false)).toBe(false);
    expect(supportsBrightness(light({ abilities: ['power', 'dim'], attributes: { supported_color_modes: ['onoff'] } }))).toBe(true);
    expect(supportsBrightness(light({ abilities: ['power'], attributes: { brightness: 100 } }))).toBe(false);
    expect(supportsBrightness(light({ attributes: { supported_color_modes: ['brightness'] } }))).toBe(true);
  });
});

describe('automation services from capabilities', () => {
  const catalog = {
    triggers: [], conditions: [], actions: [], areas: [], zones: [], persons: [],
    services: [],
    entities: [
      { entity_id: 'light.porch', domain: 'light', services: ['light.toggle', 'light.turn_off', 'light.turn_on'] },
      { entity_id: 'cover.gate', domain: 'cover', services: ['cover.close_cover', 'cover.open_cover'] },
      { entity_id: 'cover.blind', domain: 'cover', services: ['cover.close_cover', 'cover.open_cover', 'cover.set_cover_position'] },
      { entity_id: 'switch.old', domain: 'switch' },
    ],
  } as Catalog;

  it('offers only what every chosen device takes', () => {
    expect(servicesForEntities(catalog, ['cover.gate', 'cover.blind'])).toEqual(['cover.close_cover', 'cover.open_cover']);
    expect(servicesForEntities(catalog, ['cover.blind'])).toContain('cover.set_cover_position');
    expect(servicesForEntities(catalog, [])).toBeNull();
    expect(servicesForEntities(catalog, ['switch.old'])).toBeNull();
  });

  it('offers only devices that take the chosen service', () => {
    const [, gate, blind, old] = catalog.entities;
    expect(entityTakesService(blind, 'cover.set_cover_position')).toBe(true);
    expect(entityTakesService(gate, 'cover.set_cover_position')).toBe(false);
    expect(entityTakesService(old, 'switch.turn_on')).toBe(true);
    expect(entityTakesService(old, 'light.turn_on')).toBe(false);
  });
});
