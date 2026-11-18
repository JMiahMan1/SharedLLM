import { apiClient } from '../services/api';

/**
 * Devices > Duplicates: the same physical device registered by two integrations (a bulb seen
 * through Matter and through WiZ), from the hub's duplicates API (services/homehub/duplicates.py,
 * admins only). Hiding a copy keeps it working; only what people and Jarvis browse leaves it out.
 * Combining makes the two copies one device: the main's commands fall back to the other copy when the
 * main does not answer, and the light is unavailable only when both copies are.
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
  /** In a combined device: which copy this is. */
  role?: 'main' | 'fallback' | null;
}

export interface Combined {
  main: string;
  fallback: string;
  main_domain: string | null;
  main_label: string | null;
  fallback_domain: string | null;
  fallback_label: string | null;
  entities: { main: string; fallback: string }[];
  /** Whether each copy answers (null: it has not said yet). */
  main_available: boolean | null;
  fallback_available: boolean | null;
  /** Which copy the combined device is using now. */
  using: 'main' | 'fallback' | null;
}

export interface DuplicateRule {
  domains: string[];
  main_domain: string;
  main_label: string;
  fallback_domain: string;
  fallback_label: string;
}

export interface DuplicateGroup {
  id: string;
  /** How the copies were matched: a shared MAC or serial number, or the same name in the same room. */
  match: 'mac' | 'serial' | 'name' | 'combined';
  name: string;
  area: string | null;
  domains: string[];
  devices: DuplicateCopy[];
  combined?: Combined | null;
  /** Two copies with a light, switch or other control to pair. */
  can_combine?: boolean;
  /** The household rule's main for these two integrations. */
  suggested_main?: string | null;
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
  rules?: DuplicateRule[];
}

export interface DuplicatesChange extends Duplicates {
  changed: string[];
}

export async function getDuplicates(): Promise<Duplicates> {
  const resp = await apiClient.get('/api/homehub/devices/duplicates');
  return { groups: resp.data?.groups ?? [], integrations: resp.data?.integrations ?? [], rules: resp.data?.rules ?? [] };
}

/** Combine two copies into one device: commands go to the main, and to the fallback when the main fails. */
export async function combineCopies(main: string, fallback: string): Promise<Duplicates> {
  const resp = await apiClient.post('/api/homehub/devices/duplicates/combine', { main, fallback });
  return resp.data;
}

/** Combine every pair of these two integrations this way, and keep it as the household rule. */
export async function combineAllLike(mainDomain: string, fallbackDomain: string): Promise<Duplicates> {
  const resp = await apiClient.post('/api/homehub/devices/duplicates/combine',
    { main_domain: mainDomain, fallback_domain: fallbackDomain });
  return resp.data;
}

/** Split a combined device back into its two copies, as they were. */
export async function uncombine(deviceId: string): Promise<Duplicates> {
  const resp = await apiClient.post('/api/homehub/devices/duplicates/uncombine', { device: deviceId });
  return resp.data;
}

/** Make the fallback the main. */
export async function swapMain(deviceId: string): Promise<Duplicates> {
  const resp = await apiClient.post('/api/homehub/devices/duplicates/swap', { device: deviceId });
  return resp.data;
}

/** Forget the household rule for two integrations. */
export async function clearRule(domains: string[]): Promise<Duplicates> {
  const resp = await apiClient.post('/api/homehub/devices/duplicates/rule', { clear: domains });
  return resp.data;
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

/** "Same network address" / "Same serial number" / "Same name in Kitchen" / "Same name". */
export function matchLabel(g: DuplicateGroup): string {
  if (g.match === 'mac') return 'Same network address';
  if (g.match === 'serial') return 'Same serial number';
  if (g.match === 'combined') return 'Combined';
  return g.area ? `Same name in ${g.area}` : 'Same name';
}

/** Groups where every copy shows: the ones still to decide. */
export function undecided(d: Duplicates): number {
  return d.groups.filter((g) => !g.combined && g.devices.every((c) => !c.hidden)).length;
}

/**
 * The copy offered as the main before a person picks: the household rule's, else the copy that shows
 * (the one people already use), else the first.
 */
export function defaultMain(g: DuplicateGroup): string {
  const byRule = g.devices.find((c) => g.suggested_main && c.domain === g.suggested_main);
  return (byRule ?? g.devices.find((c) => !c.hidden) ?? g.devices[0]).id;
}

/** "Main: Matter, fallback: WiZ". */
export function combinedLabel(c: Combined): string {
  return `Main: ${c.main_label || c.main_domain}, fallback: ${c.fallback_label || c.fallback_domain}`;
}

/** What the combined device is doing now, in plain words. */
export function combinedStatus(c: Combined): string {
  const main = c.main_label || c.main_domain || 'main';
  const fallback = c.fallback_label || c.fallback_domain || 'fallback';
  if (c.using === 'main') return c.fallback_available === false ? `Using ${main}; ${fallback} is not answering` : `Using ${main}`;
  if (c.using === 'fallback') return `${main} is not answering, so ${fallback} is used`;
  return 'Neither copy is answering';
}

