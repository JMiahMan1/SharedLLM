import { describe, expect, it } from 'vitest';
import { formatState, groupByFloor, unavailableLabel, type RoomsResponse } from './dashboards';

describe('unavailableLabel', () => {
  it('says a light that alone stopped answering has no power', () => {
    expect(unavailableLabel({ domain: 'light', offline: { cause: 'device', integration: 'Matter' } }))
      .toBe('No power (wall switch off?)');
  });

  it('names the integration when all of it is out, not each bulb', () => {
    expect(unavailableLabel({ domain: 'light', offline: { cause: 'integration', integration: 'Matter' } }))
      .toBe('Matter not connected');
  });

  it('keeps other devices plain and older hubs unchanged', () => {
    expect(unavailableLabel({ domain: 'sensor', offline: { cause: 'device', integration: 'WiZ' } })).toBe('Not answering');
    expect(unavailableLabel({ domain: 'light' })).toBe('Unavailable');
    expect(formatState({ state: 'unavailable', unit: null, domain: 'sensor', offline: null })).toBe('Unavailable');
  });
});

describe('groupByFloor', () => {
  const lamp = { entity_id: 'light.lamp', domain: 'light', name: 'Lamp', state: 'on', attributes: {} } as unknown as RoomsResponse['unassigned'][number];
  const rooms: RoomsResponse = {
    floors: [{ floor_id: 'up', name: 'Upstairs', level: 1 }],
    areas: [
      { area_id: 'den', name: 'Den', floor_id: null, icon: null, entities: [lamp] },
      { area_id: 'loft', name: 'Loft', floor_id: 'up', icon: null, entities: [] },
    ],
    unassigned: [],
  };

  it('leaves out a room with nothing in it by default', () => {
    expect(groupByFloor(rooms).map((g) => [g.floor?.name ?? null, g.areas.map((a) => a.name)])).toEqual([[null, ['Den']]]);
  });

  it('keeps an empty room, on its floor, when asked to (an admin setting up rooms)', () => {
    expect(groupByFloor(rooms, true).map((g) => [g.floor?.name ?? null, g.areas.map((a) => a.name)]))
      .toEqual([['Upstairs', ['Loft']], [null, ['Den']]]);
  });
});
