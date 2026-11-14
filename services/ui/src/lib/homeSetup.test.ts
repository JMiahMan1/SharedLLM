import { describe, expect, it } from 'vitest';
import { areasByFloor, labelSwatch, parseLatLon, toggled, zoneBody, zoneForm, type Area, type Floor } from './homeSetup';

const area = (area_id: string, name: string, floor_id: string | null = null): Area =>
  ({ area_id, name, floor_id, icon: null, aliases: [], labels: [] });
const floor = (floor_id: string, name: string, level: number | null): Floor => ({ floor_id, name, level, icon: null, aliases: [] });

describe('home setup helpers', () => {
  it('groups rooms under their floors, then the rooms on no floor or a floor that is gone', () => {
    const groups = areasByFloor(
      [area('b', 'Bedroom', 'up'), area('a', 'Attic', 'up'), area('k', 'Kitchen'), area('x', 'Lost', 'gone')],
      [floor('up', 'Upstairs', 1), floor('down', 'Downstairs', 0)],
    );
    expect(groups.map((g) => [g.floor?.name ?? null, g.areas.map((a) => a.name)])).toEqual([
      ['Upstairs', ['Attic', 'Bedroom']], ['Downstairs', []], [null, ['Kitchen', 'Lost']],
    ]);
    expect(areasByFloor([area('k', 'Kitchen', 'up')], [floor('up', 'Upstairs', 1)])).toHaveLength(1);
  });

  it('toggles a label in or out', () => {
    expect(toggled(['a'], 'b')).toEqual(['a', 'b']);
    expect(toggled(['a', 'b'], 'a')).toEqual(['b']);
  });

  it('draws a swatch for a colour name or #rrggbb, and none otherwise', () => {
    expect(labelSwatch('indigo')).toBe('#3f51b5');
    expect(labelSwatch('#12ab34')).toBe('#12ab34');
    expect(labelSwatch('not-a-colour')).toBeNull();
    expect(labelSwatch(null)).toBeNull();
  });

  it('reads a "lat, lon" pair pasted from a map', () => {
    expect(parseLatLon('12.5, -45.25')).toEqual({ latitude: 12.5, longitude: -45.25 });
    expect(parseLatLon(' -1 2 ')).toEqual({ latitude: -1, longitude: 2 });
    expect(parseLatLon('12.5')).toBeNull();
    expect(parseLatLon('95, 10')).toBeNull();
  });

  it('checks a zone with the limits the hub uses', () => {
    const ok = { name: ' Field ', latitude: '1.5', longitude: '-2.5', radius: '150', passive: true };
    expect(zoneBody(ok)).toEqual({ body: { name: 'Field', latitude: 1.5, longitude: -2.5, radius: 150, passive: true } });
    expect(zoneBody({ ...ok, name: ' ' })).toEqual({ error: 'Give the zone a name.' });
    expect(zoneBody({ ...ok, latitude: '' })).toEqual({ error: 'Latitude is a number from -90 to 90.' });
    expect(zoneBody({ ...ok, longitude: '181' })).toEqual({ error: 'Longitude is a number from -180 to 180.' });
    expect(zoneBody({ ...ok, radius: '0' })).toEqual({ error: 'The radius is 1 to 100000 metres.' });
  });

  it('fills the zone form from a zone, or with a 100 m default', () => {
    expect(zoneForm()).toEqual({ name: '', latitude: '', longitude: '', radius: '100', passive: false });
    expect(zoneForm({ id: 'z', entity_id: 'zone.z', name: 'Z', latitude: 1, longitude: 2, radius: 30, passive: true,
      icon: null, source: 'hub', editable: true })).toEqual({ name: 'Z', latitude: '1', longitude: '2', radius: '30', passive: true });
  });
});
