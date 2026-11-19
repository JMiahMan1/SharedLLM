import { apiClient } from '../services/api';

/**
 * Devices > Rooms & places and Setup > People: the hub's areas, floors, labels, zones and persons
 * (services/homehub/entries_api.py and registries_api.py through services/gateway/hub_registry_routes.py,
 * admins only). Every row comes from the hub; nothing here names a room, a place or a person.
 */

export interface Floor {
  floor_id: string;
  name: string;
  level: number | null;
  icon: string | null;
  aliases: string[];
}

export interface Area {
  area_id: string;
  name: string;
  floor_id: string | null;
  icon: string | null;
  aliases: string[];
  labels: string[];
}

export interface Label {
  label_id: string;
  name: string;
  color: string | null;
  icon: string | null;
  description: string | null;
}

/** "home" is Home (the hub's own location); every other place is a "hub" zone. */
export type ZoneSource = 'home' | 'hub';

/**
 * A place on HomeHub, the only store of the family's places: Home, a zone drawn for the household, or a place
 * someone saved in Wander (it has an owner, and is shared with the family or private to them).
 */
export interface Zone {
  id: string;
  entity_id: string | null;
  name: string;
  latitude: number | null;
  longitude: number | null;
  radius: number | null;
  passive: boolean;
  icon: string | null;
  source: ZoneSource;
  editable: boolean;
  /** False only for Home: it moves, but there is always one. */
  removable?: boolean;
  /** The Jarvis username of whoever saved it in Wander; null for Home and household zones. */
  owner_name?: string | null;
  visibility?: 'family' | 'private';
  important?: boolean;
  notify?: string[];
}

export interface Person {
  id: string;
  entity_id: string | null;
  name: string;
  user_id: string | null;
  jarvis_user: string | null;
  device_trackers: string[];
  state: string | null;
  /** ``hub`` for the hub's own person; otherwise the login provider that keeps it (``jarvis_identity``). */
  source?: string;
  /** False for a person a login provider keeps: it is changed where its user is (Jarvis). */
  editable?: boolean;
  avatar?: string | null;
}

export interface Tracker {
  entity_id: string;
  name: string;
  state: string | null;
}

export type ZoneBody = { name: string; latitude: number; longitude: number; radius: number; passive: boolean };
/** What moving Home takes: no passive (Home is always where people are). */
export type HomeBody = Omit<ZoneBody, 'passive'>;
export type PersonBody = { name: string; jarvis_user: string | null; device_trackers: string[] };

/** The query key every view of the labels shares, so a change shows everywhere at once. */
export const LABELS_KEY = ['homehub-labels'];

const item = (kind: string, id: string) => `/api/homehub/${kind}/${encodeURIComponent(id)}`;

export async function getAreas(): Promise<Area[]> {
  return (await apiClient.get('/api/homehub/areas')).data.areas ?? [];
}
export async function createArea(body: { name: string; floor_id?: string | null }): Promise<Area> {
  return (await apiClient.post('/api/homehub/areas', body)).data;
}
export async function updateArea(id: string, body: Partial<Pick<Area, 'name' | 'floor_id' | 'labels'>>): Promise<Area> {
  return (await apiClient.patch(item('areas', id), body)).data;
}
export async function deleteArea(id: string): Promise<{ devices: number; entities: number }> {
  return (await apiClient.delete(item('areas', id))).data;
}

export async function getFloors(): Promise<Floor[]> {
  return (await apiClient.get('/api/homehub/floors')).data.floors ?? [];
}
export async function createFloor(body: { name: string; level: number | null }): Promise<Floor> {
  return (await apiClient.post('/api/homehub/floors', body)).data;
}
export async function updateFloor(id: string, body: { name?: string; level?: number | null }): Promise<Floor> {
  return (await apiClient.patch(item('floors', id), body)).data;
}
export async function deleteFloor(id: string): Promise<{ areas: string[] }> {
  return (await apiClient.delete(item('floors', id))).data;
}

export async function getLabels(): Promise<Label[]> {
  return (await apiClient.get('/api/homehub/labels')).data.labels ?? [];
}
export async function createLabel(body: { name: string; color: string | null }): Promise<Label> {
  return (await apiClient.post('/api/homehub/labels', body)).data;
}
export async function updateLabel(id: string, body: { name?: string; color?: string | null }): Promise<Label> {
  return (await apiClient.patch(item('labels', id), body)).data;
}
export async function deleteLabel(id: string): Promise<{ areas: number; devices: number; entities: number }> {
  return (await apiClient.delete(item('labels', id))).data;
}

export async function getZones(): Promise<Zone[]> {
  return (await apiClient.get('/api/homehub/zones')).data.zones ?? [];
}
export async function createZone(body: ZoneBody): Promise<Zone> {
  return (await apiClient.post('/api/homehub/zones', body)).data;
}
export async function updateZone(id: string, body: Partial<ZoneBody>): Promise<Zone> {
  return (await apiClient.patch(item('zones', id), body)).data;
}
export async function deleteZone(id: string): Promise<{ entity_id: string | null }> {
  return (await apiClient.delete(item('zones', id))).data;
}
/** Move, resize or rename Home (admins). It is the hub's location: zone.home, the forecast and arrivals follow. */
export async function updateHome(body: Partial<HomeBody>): Promise<Zone> {
  return (await apiClient.patch(item('zones', 'home'), body)).data;
}

export async function getPersons(): Promise<{ persons: Person[]; trackers: Tracker[] }> {
  const data = (await apiClient.get('/api/homehub/persons')).data;
  return { persons: data.persons ?? [], trackers: data.trackers ?? [] };
}
export async function createPerson(body: PersonBody): Promise<Person> {
  return (await apiClient.post('/api/homehub/persons', body)).data;
}
export async function updatePerson(id: string, body: Partial<PersonBody>): Promise<Person> {
  return (await apiClient.patch(item('persons', id), body)).data;
}
export async function deletePerson(id: string): Promise<{ entity_id: string | null }> {
  return (await apiClient.delete(item('persons', id))).data;
}

/** The colours a label can take (Home Assistant's names), with the swatch drawn for each. */
export const LABEL_COLORS: Record<string, string> = {
  red: '#f44336', pink: '#e91e63', purple: '#9c27b0', indigo: '#3f51b5', blue: '#2196f3', cyan: '#00bcd4',
  teal: '#009688', green: '#4caf50', lime: '#cddc39', amber: '#ffc107', orange: '#ff9800', brown: '#795548',
  grey: '#9e9e9e',
};

/** The swatch for a label's colour: a known name, a #rrggbb, or none. */
export function labelSwatch(color: string | null | undefined): string | null {
  if (!color) return null;
  if (/^#[0-9a-fA-F]{6}$/.test(color)) return color;
  return LABEL_COLORS[color] ?? null;
}

/** ``ids`` with ``id`` added, or taken out when it is there. */
export function toggled(ids: string[], id: string): string[] {
  return ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
}

/** Areas grouped under their floors (lowest level first), then the areas on no floor. */
export function areasByFloor(areas: Area[], floors: Floor[]): { floor: Floor | null; areas: Area[] }[] {
  const byName = (a: Area, b: Area) => a.name.localeCompare(b.name);
  const known = new Set(floors.map((f) => f.floor_id));
  const groups = floors.map((floor) => ({ floor, areas: areas.filter((a) => a.floor_id === floor.floor_id).sort(byName) }));
  const loose = areas.filter((a) => !a.floor_id || !known.has(a.floor_id)).sort(byName);
  return loose.length ? [...groups, { floor: null, areas: loose }] : groups;
}

export interface ZoneForm {
  name: string;
  latitude: string;
  longitude: string;
  radius: string;
  passive: boolean;
}

/** "12.5, -45.25" pasted from a map: both numbers, or null when it is not a pair. */
export function parseLatLon(text: string): { latitude: number; longitude: number } | null {
  const m = /^\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*$/.exec(text);
  if (!m) return null;
  const latitude = Number(m[1]);
  const longitude = Number(m[2]);
  return Math.abs(latitude) <= 90 && Math.abs(longitude) <= 180 ? { latitude, longitude } : null;
}

/** A zone form as the hub takes it, or what is wrong with it (the same limits the hub checks). */
export function zoneBody(form: ZoneForm): { body: ZoneBody } | { error: string } {
  const name = form.name.trim();
  if (!name) return { error: 'Give the zone a name.' };
  const num = (v: string) => (v.trim() === '' ? NaN : Number(v));
  const latitude = num(form.latitude);
  const longitude = num(form.longitude);
  const radius = num(form.radius);
  if (!Number.isFinite(latitude) || Math.abs(latitude) > 90) return { error: 'Latitude is a number from -90 to 90.' };
  if (!Number.isFinite(longitude) || Math.abs(longitude) > 180) return { error: 'Longitude is a number from -180 to 180.' };
  if (!Number.isFinite(radius) || radius < 1 || radius > 100000) return { error: 'The radius is 1 to 100000 metres.' };
  return { body: { name, latitude, longitude, radius, passive: form.passive } };
}

export function zoneForm(zone?: Zone): ZoneForm {
  return {
    name: zone?.name ?? '',
    latitude: zone?.latitude == null ? '' : String(zone.latitude),
    longitude: zone?.longitude == null ? '' : String(zone.longitude),
    radius: String(zone?.radius ?? 100),
    passive: zone?.passive ?? false,
  };
}

export const ZONE_SOURCE_LABEL: Record<ZoneSource, string> = {
  home: 'Home (where the hub is)', hub: 'For the household',
};

/** Where a place came from, in a few words: Home, a household zone, or who saved it in Wander. */
export function zoneOrigin(zone: Zone): string {
  if (zone.source === 'home') return ZONE_SOURCE_LABEL.home;
  if (zone.owner_name) {
    return `Saved in Wander by ${zone.owner_name}${zone.visibility === 'private' ? ' (private)' : ''}`;
  }
  return ZONE_SOURCE_LABEL.hub;
}

/** Where an admin edits the household's places (Devices > Rooms & places). */
export const PLACES_ADMIN_LINK = '/devices#devices-zones';
/** Where everyone keeps their own places (Wander > Places). */
export const WANDER_PLACES_LINK = '/wander#wander-places';

/** Setup > Users & logins: where the hub's users and logins come from (services/homehub/users_api.py). */
export interface ProviderSync {
  ok: boolean;
  at?: string;
  failed_at?: string;
  error?: string;
  provider_users?: number;
  users_added?: number;
  users_matched?: number;
  users_updated?: number;
  users_disabled?: number;
  users_reactivated?: number;
  persons_created?: number;
  persons_linked?: number;
  persons_renamed?: number;
  trackers_attached?: number;
  standalone_users?: string[];
}

export interface LoginProvider {
  type: string;
  label: string;
  external: boolean;
  configured: boolean;
  active: boolean;
  primary: boolean;
  entry_id: string | null;
  state: string | null;
  reason: string | null;
  users: number;
  inactive_users: number;
  last_sync: ProviderSync | null;
}

export interface LoginProviders {
  providers: LoginProvider[];
  primary: string;
  local_logins: boolean;
  local_logins_setting: boolean;
  external_admins: string[];
  standalone_users: string[];
}

export interface HubUser {
  id: string;
  name: string;
  /** ``local`` (the hub's own), ``service``, ``system`` or a login provider (``jarvis_identity``). */
  source: string;
  username: string | null;
  is_admin: boolean;
  is_owner: boolean;
  is_active: boolean;
  hub_login: boolean;
  editable: boolean;
  provider: { type: string; username: string; roles: string[]; avatar: string | null; removed: boolean } | null;
}

export type NewHubUser = { name: string; username: string; password: string; is_admin: boolean };

export async function getLoginProviders(): Promise<LoginProviders> {
  return (await apiClient.get('/api/homehub/auth/providers')).data;
}
export async function setLoginProviders(body: { primary?: string; local_logins?: boolean }): Promise<LoginProviders> {
  return (await apiClient.put('/api/homehub/auth/providers', body)).data;
}
export async function syncLoginProvider(type: string): Promise<ProviderSync> {
  return (await apiClient.post(`/api/homehub/auth/providers/${encodeURIComponent(type)}/sync`, {})).data;
}
export async function getHubUsers(): Promise<HubUser[]> {
  return (await apiClient.get('/api/homehub/users')).data.users ?? [];
}
export async function createHubUser(body: NewHubUser): Promise<HubUser> {
  return (await apiClient.post('/api/homehub/users', body)).data;
}
export async function updateHubUser(id: string, body: Partial<{ name: string; is_admin: boolean; is_active: boolean; password: string }>): Promise<HubUser> {
  return (await apiClient.patch(item('users', id), body)).data;
}

/** "From Jarvis" for a person or user a login provider keeps. */
export function providerLabel(source: string | undefined | null): string | null {
  if (!source || source === 'hub' || source === 'local' || source === 'service' || source === 'system') return null;
  return source === 'jarvis_identity' ? 'From Jarvis' : `From ${source}`;
}

/** What the last sync did, in a line: "3 users · 1 added · 2 people linked". */
export function syncSummary(sync: ProviderSync | null | undefined): string {
  if (!sync) return 'Not synced yet';
  if (!sync.ok) return `Last sync failed: ${sync.error || 'no answer'}`;
  const parts = [`${sync.provider_users ?? 0} users`];
  const add = (n: number | undefined, word: string) => { if (n) parts.push(`${n} ${word}`); };
  add(sync.users_added, 'added');
  add(sync.users_matched, 'linked');
  add(sync.users_updated, 'changed');
  add(sync.users_disabled, 'disabled');
  add(sync.users_reactivated, 'back');
  add(sync.persons_created, sync.persons_created === 1 ? 'person added' : 'people added');
  add(sync.persons_linked, sync.persons_linked === 1 ? 'person linked' : 'people linked');
  add(sync.trackers_attached, sync.trackers_attached === 1 ? 'phone attached' : 'phones attached');
  return parts.join(' · ');
}
