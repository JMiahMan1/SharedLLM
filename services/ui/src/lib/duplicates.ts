import { apiClient } from '../services/api';

/**
 * Devices > Duplicates: the same physical device registered by two integrations (a bulb seen
 * through Matter and through WiZ), from the hub's duplicates API (services/homehub/duplicates.py,
 * admins only). Hiding a copy keeps it working; only what people and Jarvis browse leaves it out.
 */

export interface DuplicateCopy {
  id: string;
  name: string;
  manufacturer: string | null;
  model: string | null;
  domain: string | null;
  label: string | null;
  entry_id: string | null;
  entry_title: string | null;
  area_id: string | null;
  area: string | null;
  entity_ids: string[];
  hidden: boolean;
  hidden_by: string | null;
  last_updated: string | null;
}

export interface DuplicateGroup {
  id: string;
  /** How the copies were matched: a shared MAC, or the same name in the same room. */
  match: 'mac' | 'name';
  name: string;
  area: string | null;
  domains: string[];
  devices: DuplicateCopy[];
}

export interface DuplicateIntegration {
  domain: string;
  label: string;
  /** Duplicate groups it has a copy in. */
  groups: number;
  devices: number;
  hidden: number;
}

export interface Duplicates {
  groups: DuplicateGroup[];
  integrations: DuplicateIntegration[];
}

export interface DuplicatesChange extends Duplicates {
  changed: string[];
}

export async function getDuplicates(): Promise<Duplicates> {
  const resp = await apiClient.get('/api/homehub/devices/duplicates');
  return { groups: resp.data?.groups ?? [], integrations: resp.data?.integrations ?? [] };
}

/** Hide one integration's copy in every duplicate group that has another integration's copy too. */
export async function hideCopiesOf(domain: string): Promise<DuplicatesChange> {
  const resp = await apiClient.post('/api/homehub/devices/duplicates/hide', { hide_domain: domain });
  return resp.data;
}

/** Show one integration's hidden copies again. */
export async function showCopiesOf(domain: string): Promise<DuplicatesChange> {
  const resp = await apiClient.post('/api/homehub/devices/duplicates/unhide', { domain });
  return resp.data;
}

/** Hide or show one device (every entity on it follows). */
export async function setDeviceHidden(deviceId: string, hidden: boolean): Promise<{ id: string; hidden: boolean }> {
  const resp = await apiClient.patch(`/api/homehub/devices/${encodeURIComponent(deviceId)}`, { hidden });
  return resp.data;
}

// -- pure helpers (unit-tested) ------------------------------------------------------

/** What the bulk buttons offer per integration: hide its copies while some still show, show them once hidden. */
export function bulkActions(d: Duplicates): { domain: string; label: string; canHide: boolean; canShow: boolean }[] {
  return d.integrations.map((i) => ({
    domain: i.domain, label: i.label || i.domain, canHide: i.hidden < i.devices, canShow: i.hidden > 0,
  }));
}

/** "Same network address" / "Same name in Kitchen" / "Same name". */
export function matchLabel(g: DuplicateGroup): string {
  if (g.match === 'mac') return 'Same network address';
  return g.area ? `Same name in ${g.area}` : 'Same name';
}

/** Groups where every copy shows: the ones still to decide. */
export function undecided(d: Duplicates): number {
  return d.groups.filter((g) => g.devices.every((c) => !c.hidden)).length;
}
