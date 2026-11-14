/**
 * The hub's registries for the hermetic specs (areas, floors, labels, zones, persons): a small in-memory
 * store, so adding, renaming and removing behave as the hub does and the next read shows the change.
 * Every name and coordinate is made up.
 */

type Row = Record<string, unknown>;

const KEYS: Record<string, string> = { areas: 'area_id', floors: 'floor_id', labels: 'label_id', zones: 'id', persons: 'id' };

function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'item';
}

export interface RegistryMocks {
  /** The answer to ``method path`` (``undefined`` when it is not a registry call). */
  answer(method: string, path: string, body: unknown): { status: number; body: unknown } | undefined;
}

export function registryMocks(): RegistryMocks {
  const rows: Record<string, Row[]> = {
    floors: [{ floor_id: 'upstairs', name: 'Upstairs', level: 1, icon: null, aliases: [] }],
    areas: [
      { area_id: 'living_room', name: 'Living room', floor_id: null, icon: null, aliases: [], labels: [] },
      { area_id: 'bedroom', name: 'Bedroom', floor_id: 'upstairs', icon: null, aliases: [], labels: [] },
    ],
    labels: [{ label_id: 'night_lights', name: 'Night lights', color: 'indigo', icon: null, description: null }],
    zones: [
      { id: 'home', entity_id: 'zone.home', name: 'Home', latitude: 10, longitude: 20, radius: 100, passive: false,
        icon: 'mdi:home', source: 'home', editable: false },
      { id: 'geo_' + 'a'.repeat(32), entity_id: 'zone.practice_field', name: 'Practice field with a long name',
        latitude: 10.5, longitude: 20.5, radius: 200, passive: false, icon: 'mdi:map-marker-star', source: 'geo', editable: false },
    ],
    persons: [{ id: 'p1', entity_id: 'person.robin', name: 'Robin', user_id: null, jarvis_user: 'robin',
      device_trackers: ['device_tracker.robin_s_phone'], state: 'home' }],
  };
  const trackers = [
    { entity_id: 'device_tracker.robin_s_phone', name: "Robin's Phone", state: 'home' },
    { entity_id: 'device_tracker.sky_tablet', name: 'Sky tablet', state: 'not_home' },
  ];
  let next = 1;

  return {
    answer(method, path, body) {
      if (method === 'POST' && path === '/api/homehub/zones/sync') return { status: 200, body: { places: 1, added: 0, removed: 0 } };
      const m = /^\/api\/homehub\/(areas|floors|labels|zones|persons)(?:\/([^/]+))?$/.exec(path);
      if (!m) return undefined;
      const [, kind, rawId] = m;
      const key = KEYS[kind];
      const list = rows[kind];
      const data = (body && typeof body === 'object' ? body : {}) as Row;
      if (method === 'GET' && !rawId) {
        return { status: 200, body: kind === 'persons' ? { persons: list, trackers } : { [kind]: list } };
      }
      if (method === 'POST' && !rawId) {
        const id = kind === 'zones' || kind === 'persons' ? `new${next++}` : slug(String(data.name || 'item'));
        const row: Row = { [key]: id, ...data };
        if (kind === 'zones') Object.assign(row, { entity_id: `zone.${slug(String(data.name))}`, source: 'hub', editable: true });
        if (kind === 'persons') Object.assign(row, { entity_id: `person.${slug(String(data.name))}`, state: null });
        if (kind === 'areas') Object.assign(row, { labels: [], aliases: [] });
        list.push(row);
        return { status: 200, body: row };
      }
      const id = decodeURIComponent(rawId || '');
      const at = list.findIndex((r) => r[key] === id);
      if (at < 0) return { status: 404, body: { detail: 'Not found' } };
      if (method === 'PATCH') {
        list[at] = { ...list[at], ...data };
        return { status: 200, body: list[at] };
      }
      if (method === 'DELETE') {
        list.splice(at, 1);
        return { status: 200, body: { status: 'removed' } };
      }
      return undefined;
    },
  };
}
