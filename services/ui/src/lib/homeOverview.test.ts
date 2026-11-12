import { describe, expect, it } from 'vitest';
import type { HubEntity, RoomsResponse } from './dashboards';
import { camerasIn, lightsOnByRoom, lightsSummary, roomAnchor } from './homeOverview';

const ent = (entity_id: string, state = 'off', area_id: string | null = null): HubEntity => ({
  entity_id, domain: entity_id.split('.')[0], name: entity_id, state, attributes: {}, device_class: null, unit: null,
  icon: null, area_id, device_id: null, last_changed: '2026-01-01T00:00:00Z',
});

const rooms: RoomsResponse = {
  floors: [],
  areas: [
    { area_id: 'a', name: 'Alpha', floor_id: null, icon: null, entities: [ent('light.a1', 'on'), ent('light.a2', 'off'), ent('camera.a')] },
    { area_id: 'b', name: 'Beta', floor_id: null, icon: null, entities: [ent('light.b1', 'on'), ent('light.b2', 'on'), ent('light.b3', 'unavailable')] },
    { area_id: 'c', name: 'Gamma', floor_id: null, icon: null, entities: [ent('light.c1', 'off'), ent('switch.c', 'on')] },
  ],
  unassigned: [ent('light.x', 'on'), ent('camera.x')],
};

describe('home overview', () => {
  it('lists rooms with lights on, busiest first, and leaves dark rooms out', () => {
    const groups = lightsOnByRoom(rooms);
    expect(groups.map((g) => [g.name, g.on.length, g.total])).toEqual([
      ['Beta', 2, 2], ['Alpha', 1, 2], ['Other', 1, 1],
    ]);
  });

  it('counts unavailable lights as neither on nor part of the room total', () => {
    expect(lightsOnByRoom(rooms).find((g) => g.area_id === 'b')?.total).toBe(2);
  });

  it('sums the lights for the headline', () => {
    expect(lightsSummary(lightsOnByRoom(rooms))).toBe('4 lights on in 3 rooms');
    expect(lightsSummary([])).toBe('No lights on');
  });

  it('finds cameras in rooms and outside them', () => {
    expect(camerasIn(rooms).map((c) => c.entity_id)).toEqual(['camera.a', 'camera.x']);
    expect(camerasIn(undefined)).toEqual([]);
  });

  it('gives lights in no room the anchor of the Other card', () => {
    expect(roomAnchor('')).toBe('room-other');
    expect(roomAnchor('a')).toBe('room-a');
  });
});
