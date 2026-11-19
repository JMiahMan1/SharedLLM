/**
 * The hub's registries for the hermetic specs (areas, floors, labels, zones, persons, users and login
 * providers): a small in-memory
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
      device_trackers: ['device_tracker.robin_s_phone'], state: 'home', source: 'hub', editable: true },
    { id: 'p9', entity_id: 'person.avery_longname', name: 'Avery Longname-Hyphenated', user_id: 'u-avery', jarvis_user: 'avery',
      device_trackers: [], state: 'not_home', source: 'jarvis_identity', editable: false }],
  };
  const trackers = [
    { entity_id: 'device_tracker.robin_s_phone', name: "Robin's Phone", state: 'home' },
    { entity_id: 'device_tracker.sky_tablet', name: 'Sky tablet', state: 'not_home' },
  ];
  let next = 1;
  // Users & logins (services/homehub/users_api.py): the hub's own users beside Jarvis's.
  const users: Row[] = [
    { id: 'u-avery', name: 'Avery Longname-Hyphenated', source: 'jarvis_identity', username: 'avery', is_admin: true,
      is_owner: false, is_active: true, hub_login: false, editable: false,
      provider: { type: 'jarvis_identity', username: 'avery', roles: ['mother'], avatar: null, removed: false } },
    { id: 'u-kit', name: 'Kit', source: 'jarvis_identity', username: 'kit', is_admin: false, is_owner: false,
      is_active: true, hub_login: false, editable: false,
      provider: { type: 'jarvis_identity', username: 'kit', roles: ['child'], avatar: null, removed: false } },
    { id: 'u-visitor', name: 'Visitor', source: 'local', username: 'visitor', is_admin: false, is_owner: false,
      is_active: true, hub_login: true, editable: true, provider: null },
    { id: 'u-music', name: 'Music server', source: 'service', username: null, is_admin: false, is_owner: false,
      is_active: true, hub_login: false, editable: false, provider: null },
  ];
  const login = { primary: 'jarvis_identity', local_logins: true };
  const lastSync = { ok: true, at: new Date().toISOString(), provider_users: 2, users_added: 1, users_matched: 1,
    persons_linked: 1, trackers_attached: 1 };
  const providers = () => ({
    providers: [
      { type: 'local', label: 'This hub', external: false, configured: true, active: true, primary: login.primary === 'local',
        entry_id: null, state: 'loaded', reason: null, users: 1, inactive_users: 0, last_sync: null },
      { type: 'jarvis_identity', label: 'Jarvis', external: true, configured: true, active: true,
        primary: login.primary === 'jarvis_identity', entry_id: 'e-jarvis', state: 'loaded', reason: null, users: 2,
        inactive_users: 0, last_sync: lastSync },
    ],
    primary: login.primary, local_logins: login.local_logins, local_logins_setting: login.local_logins,
    external_admins: ['avery'], standalone_users: ['Visitor'],
  });

  return {
    answer(method, path, body) {
      if (method === 'POST' && path === '/api/homehub/zones/sync') return { status: 200, body: { places: 1, added: 0, removed: 0 } };
      if (path === '/api/homehub/auth/providers') {
        if (method === 'GET') return { status: 200, body: providers() };
        if (method === 'PUT') { Object.assign(login, body as Row); return { status: 200, body: providers() }; }
      }
      if (method === 'POST' && path === '/api/homehub/auth/providers/jarvis_identity/sync') return { status: 200, body: lastSync };
      if (path === '/api/homehub/users') {
        if (method === 'GET') return { status: 200, body: { users } };
        if (method === 'POST') {
          const data = body as Row;
          const row: Row = { id: `u-new${next++}`, name: data.name, source: 'local', username: data.username,
            is_admin: Boolean(data.is_admin), is_owner: false, is_active: true, hub_login: true, editable: true, provider: null };
          users.push(row);
          return { status: 200, body: row };
        }
      }
      const u = /^\/api\/homehub\/users\/([^/]+)$/.exec(path);
      if (u && method === 'PATCH') {
        const row = users.find((r) => r.id === decodeURIComponent(u[1]));
        if (!row) return { status: 404, body: { detail: 'No such user' } };
        if (!row.editable) return { status: 409, body: { detail: `${row.name} comes from Jarvis; change them there` } };
        Object.assign(row, body as Row);
        return { status: 200, body: row };
      }
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
