import { isAxiosError } from 'axios';
import { apiClient } from '../services/api';

/**
 * Devices > New devices: what the device hub has seen on the LAN that is not
 * set up yet (services/gateway/discovery_routes.py, admins only).
 */

export interface DiscoveryInput {
  key: string;
  label: string;
  type: 'text' | 'password';
  help?: string;
  optional?: boolean;
}

export interface DiscoveredDevice {
  id: string;
  integration: string;
  label: string;
  name: string | null;
  model: string | null;
  manufacturer: string | null;
  host: string | null;
  mac: string | null;
  source: string | null;
  first_seen: string | null;
  last_seen: string | null;
  known: boolean;
  entry_id: string | null;
  entry_title: string | null;
  ignored: boolean;
  new: boolean;
  adoptable: boolean;
  unsupported: string | null;
  needs: DiscoveryInput[];
}

export interface DiscoveredList {
  devices: DiscoveredDevice[];
  last_scan?: { at?: string; skipped?: string } | null;
}

export interface AdoptResult {
  /** `pending`: the device waits for a person (Allow on the TV); ask again with `session` after `poll_ms`. */
  status: 'adopted' | 'needs_input' | 'pending' | 'error' | 'unsupported';
  message?: string;
  error?: string | null;
  needs?: DiscoveryInput[];
  /** A pairing open on the device; sent back as `input.session`. */
  session?: string;
  step?: 'accept_on_tv' | 'accept_on_device' | string;
  poll_ms?: number;
  entry_id?: string;
  title?: string;
  name?: string;
  entities?: string[];
}

export interface AdoptRequest {
  name?: string;
  area_id?: string;
  input?: Record<string, string>;
}

const path = (id: string) => `/api/homehub/discovered/${encodeURIComponent(id)}`;

export async function getDiscovered(): Promise<DiscoveredList> {
  const resp = await apiClient.get('/api/homehub/discovered');
  return resp.data;
}

export async function scanForDevices(): Promise<DiscoveredList & { scan?: { skipped?: string } }> {
  const resp = await apiClient.post('/api/homehub/discovered/scan', {}, { timeout: 90_000 });
  return resp.data;
}

export async function adoptDevice(id: string, body: AdoptRequest): Promise<AdoptResult> {
  const resp = await apiClient.post(`${path(id)}/adopt`, body, { timeout: 240_000 });
  return resp.data;
}

export async function ignoreDevice(id: string, ignored = true): Promise<DiscoveredDevice> {
  const resp = await apiClient.post(`${path(id)}/ignore`, { ignored });
  return resp.data;
}

export async function commissionMatter(code: string, name?: string, areaId?: string): Promise<AdoptResult> {
  const resp = await apiClient.post('/api/homehub/matter/commission',
    { code, ...(name ? { name } : {}), ...(areaId ? { area_id: areaId } : {}) },
    { timeout: 240_000 });
  return resp.data;
}

/** The hub's own words for a failed call ("already set up", "did not answer"), else a plain fallback. */
/** Add a device by its address (Integrations > Add by address). Answers like `adoptDevice`. */
export interface ManualFlow {
  domain: string;
  label: string;
  fields: DiscoveryInput[];
}

export async function getManualFlows(): Promise<ManualFlow[]> {
  const resp = await apiClient.get('/api/homehub/flows');
  return resp.data.flows || [];
}

export async function addByAddress(domain: string, body: AdoptRequest & { host?: string; url?: string }): Promise<AdoptResult> {
  const resp = await apiClient.post(`/api/homehub/flows/${encodeURIComponent(domain)}`, body, { timeout: 240_000 });
  return resp.data;
}

// -- Integrations: what is set up --------------------------------------------------

export interface ReconfigureField {
  key: string;
  label: string;
  type: 'text' | 'password';
  /** Current value (never for secrets). */
  value?: string;
  /** For secrets: whether one is stored. */
  set?: boolean;
}

export interface HubDevice {
  id: string;
  name: string;
  original_name: string | null;
  name_by_user: string | null;
  area_id: string | null;
  manufacturer: string | null;
  model: string | null;
  via_device_id: string | null;
  entities: string[];
}

export interface HubEntry {
  entry_id: string;
  domain: string;
  label: string;
  title: string;
  state: string;
  reason: string | null;
  source: string;
  disabled_by: string | null;
  devices: HubDevice[];
  entities: number;
  reconfigure: ReconfigureField[];
  /** Removing it also unpairs it on the device (HomeKit). */
  unpairs: boolean;
  /** Shared by many devices (Matter, Cast): remove devices, not the entry. */
  shared: boolean;
}

export interface HubArea {
  area_id: string;
  name: string;
}

export interface EntriesList {
  entries: HubEntry[];
  areas: HubArea[];
}

export interface RemoveResult {
  status: 'removed';
  entry_id?: string;
  unpaired?: boolean | null;
  warning?: string;
}

export interface BuilderLink {
  configured: boolean;
  url?: string;
  state?: string;
  reason?: string | null;
  entry_id?: string;
  title?: string;
}

/**
 * Connections that are not working (any state but loaded, and not switched
 * off on purpose), worst first: a failed setup before one still starting.
 * This is how an admin sees at a glance that, say, the music server or a
 * camera hub has dropped.
 */
export function entriesNeedingAttention<T extends { state: string; disabled_by?: string | null; title: string }>(entries: T[]): T[] {
  const rank = (state: string) => (/error|failed/.test(state) ? 0 : state === 'not_loaded' ? 1 : 2);
  return entries
    .filter((e) => e.state !== 'loaded' && !e.disabled_by)
    .sort((a, b) => rank(a.state) - rank(b.state) || a.title.localeCompare(b.title));
}

export async function getEntries(): Promise<EntriesList> {
  const resp = await apiClient.get('/api/homehub/entries');
  return resp.data;
}

export async function reconfigureEntry(entryId: string, body: { title?: string; data?: Record<string, string> }): Promise<HubEntry> {
  const resp = await apiClient.patch(`/api/homehub/entries/${encodeURIComponent(entryId)}`, body, { timeout: 60_000 });
  return resp.data;
}

export async function removeEntry(entryId: string, force = false): Promise<RemoveResult> {
  const resp = await apiClient.delete(`/api/homehub/entries/${encodeURIComponent(entryId)}`,
    { params: force ? { force: true } : undefined, timeout: 60_000 });
  return resp.data;
}

export async function updateHubDevice(deviceId: string, body: { name_by_user?: string | null; area_id?: string | null }): Promise<HubDevice> {
  const resp = await apiClient.patch(`/api/homehub/devices/${encodeURIComponent(deviceId)}`, body);
  return resp.data;
}

export async function removeHubDevice(deviceId: string, force = false): Promise<RemoveResult> {
  const resp = await apiClient.delete(`/api/homehub/devices/${encodeURIComponent(deviceId)}`,
    { params: force ? { force: true } : undefined, timeout: 60_000 });
  return resp.data;
}

export async function getBuilderLink(): Promise<BuilderLink> {
  const resp = await apiClient.get('/api/homehub/esphome-builder');
  return resp.data;
}

/** HTTP status of a failed call (502 from the hub means "could not reach the device"). */
export function failureStatus(err: unknown): number | undefined {
  return isAxiosError(err) ? err.response?.status : undefined;
}

export function failureText(err: unknown): string {
  if (isAxiosError(err)) {
    const data = err.response?.data as { detail?: string; message?: string } | undefined;
    return data?.message || data?.detail || 'That did not work. Try again.';
  }
  return 'That did not work. Try again.';
}
