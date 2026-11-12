import { apiClient } from '../services/api';
import type { HubEntity } from './dashboards';
import type { HubEntry } from './discoveredDevices';

/**
 * Devices > Hardware: the device hub's live registry (services/homehub/hardware.py through
 * services/gateway/discovery_routes.py, admins only). Nothing here is a fixed list of devices:
 * every row, connection and ability comes from the hub.
 */

export type DeviceStatus = 'online' | 'partial' | 'offline' | 'disabled' | 'not_loaded';

export interface Connection {
  type: 'ip' | 'mac' | 'bluetooth' | 'zigbee' | 'upnp' | string;
  value: string;
}

export interface HardwareDevice {
  id: string;
  name: string;
  original_name: string | null;
  name_by_user: string | null;
  manufacturer: string | null;
  model: string | null;
  model_id?: string | null;
  sw_version: string | null;
  hw_version: string | null;
  serial_number: string | null;
  area_id: string | null;
  area: string | null;
  domain: string | null;
  label: string | null;
  entry_id: string | null;
  entry_title: string | null;
  entry_state: string | null;
  entry_reason: string | null;
  connections: Connection[];
  configuration_url: string | null;
  entry_type?: string | null;
  via_device_id: string | null;
  via_device_name: string | null;
  disabled_by: string | null;
  entities: number;
  unavailable: number;
  status: DeviceStatus;
  last_seen: string | null;
  abilities: string[];
}

export interface EntityCapabilities {
  abilities: string[];
  services: string[];
  details: Record<string, unknown>;
  protocol: Record<string, unknown>;
}

export interface HardwareEntity {
  entity_id: string;
  domain: string;
  platform: string;
  name: string;
  name_by_user: string | null;
  original_name: string | null;
  entity_category: string | null;
  disabled_by: string | null;
  hidden_by: string | null;
  area_id: string | null;
  icon: string | null;
  /** The current state, in the shape the Rooms page's controls take; null while disabled. */
  state: HubEntity | null;
  capabilities: EntityCapabilities | null;
}

export interface HardwareDetail extends HardwareDevice {
  entity_list: HardwareEntity[];
  children: { id: string; name: string; model: string | null }[];
  entry: HubEntry | null;
}

export interface HardwareList {
  devices: HardwareDevice[];
  areas: { area_id: string; name: string }[];
}

export interface IntegrationRow {
  domain: string;
  label: string;
  has_driver: boolean;
  status: string;
  entries: { entry_id: string; title: string; state: string; reason: string | null; disabled_by: string | null }[];
  devices: number;
  entities: number;
  unavailable: number;
  discovery: string[];
  manual: boolean;
  self_added: string | null;
  syncs: boolean;
  new_found: number;
  moved: number;
}

export async function getHardware(): Promise<HardwareList> {
  const resp = await apiClient.get('/api/homehub/hardware');
  return resp.data;
}

export async function getHardwareDevice(id: string): Promise<HardwareDetail> {
  const resp = await apiClient.get(`/api/homehub/hardware/${encodeURIComponent(id)}`);
  return resp.data;
}

export async function getIntegrations(): Promise<IntegrationRow[]> {
  const resp = await apiClient.get('/api/homehub/integrations');
  return resp.data.integrations || [];
}

export async function setDeviceDisabled(id: string, disabled: boolean): Promise<HardwareDevice> {
  const resp = await apiClient.post(`/api/homehub/hardware/${encodeURIComponent(id)}/disable`, { disabled },
    { timeout: 60_000 });
  return resp.data;
}

export async function updateEntity(entityId: string,
  body: { name?: string | null; area_id?: string | null; hidden?: boolean; disabled?: boolean }): Promise<HardwareEntity> {
  const resp = await apiClient.patch(`/api/homehub/entities/${encodeURIComponent(entityId)}`, body, { timeout: 60_000 });
  return resp.data;
}

// -- pure helpers (unit-tested) ------------------------------------------------------

export const STATUS_LABEL: Record<string, string> = {
  online: 'Online', partial: 'Partly offline', offline: 'Offline', disabled: 'Disabled', not_loaded: 'Not loaded',
};

export const STATUS_TONE: Record<string, string> = {
  online: 'bg-emerald-400', partial: 'bg-amber-400', offline: 'bg-rose-400', disabled: 'bg-slate-500',
  not_loaded: 'bg-slate-500',
};

export type GroupBy = 'area' | 'integration' | 'status' | 'none';

export interface DeviceFilter {
  query?: string;
  integration?: string;
  status?: string;
  ability?: string;
}

/** Search names, makers, models, areas, integrations and addresses; then the chips. */
export function filterDevices(devices: HardwareDevice[], f: DeviceFilter): HardwareDevice[] {
  const q = (f.query || '').trim().toLowerCase();
  return devices.filter((d) => {
    if (f.integration && d.domain !== f.integration) return false;
    if (f.status && d.status !== f.status) return false;
    if (f.ability && !d.abilities.includes(f.ability)) return false;
    if (!q) return true;
    const hay = [d.name, d.original_name, d.manufacturer, d.model, d.area, d.label, d.entry_title, d.sw_version,
      ...d.connections.map((c) => c.value)];
    return hay.some((t) => typeof t === 'string' && t.toLowerCase().includes(q));
  });
}

/** Sections for the list; devices keep their order, "No area" / "Other" last. */
export function groupDevices(devices: HardwareDevice[], by: GroupBy): { key: string; label: string; devices: HardwareDevice[] }[] {
  if (by === 'none') return [{ key: 'all', label: 'All devices', devices }];
  const key = (d: HardwareDevice): [string, string] => {
    if (by === 'area') return d.area_id ? [d.area_id, d.area || d.area_id] : ['', 'No area'];
    if (by === 'integration') return d.domain ? [d.domain, d.label || d.domain] : ['', 'Other'];
    return [d.status, STATUS_LABEL[d.status] || d.status];
  };
  const groups = new Map<string, { key: string; label: string; devices: HardwareDevice[] }>();
  for (const d of devices) {
    const [k, label] = key(d);
    if (!groups.has(k)) groups.set(k, { key: k, label, devices: [] });
    groups.get(k)!.devices.push(d);
  }
  const statusOrder = ['offline', 'partial', 'not_loaded', 'online', 'disabled'];
  return [...groups.values()].sort((a, b) => {
    if (by === 'status') return statusOrder.indexOf(a.key) - statusOrder.indexOf(b.key);
    if (!a.key) return 1;
    if (!b.key) return -1;
    return a.label.localeCompare(b.label);
  });
}

/** The integrations present in a device list, for the filter chips. */
export function integrationsIn(devices: HardwareDevice[]): { domain: string; label: string; count: number }[] {
  const out = new Map<string, { domain: string; label: string; count: number }>();
  for (const d of devices) {
    if (!d.domain) continue;
    const row = out.get(d.domain) ?? { domain: d.domain, label: d.label || d.domain, count: 0 };
    row.count += 1;
    out.set(d.domain, row);
  }
  return [...out.values()].sort((a, b) => a.label.localeCompare(b.label));
}

/** "5 min ago", "3 h ago", "2 d ago"; empty when never. */
export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return '';
  const minutes = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (minutes < 2) return 'just now';
  if (minutes < 90) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

/** "play_media" -> "Play media". */
export function abilityLabel(ability: string): string {
  const s = ability.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Abilities worth a chip on a device row (readings and plumbing left out). */
const QUIET_ABILITIES = new Set(['sense', 'detect', 'toggle', 'update', 'event', 'press', 'set_value', 'select_option']);
export function headlineAbilities(abilities: string[], max = 4): string[] {
  return abilities.filter((a) => !QUIET_ABILITIES.has(a)).slice(0, max);
}

/** "Integration needs attention" rows first, then set-up ones, then the rest, by name. */
export function sortIntegrations(rows: IntegrationRow[]): IntegrationRow[] {
  const rank = (r: IntegrationRow) => (['setup_error', 'setup_retry'].includes(r.status) ? 0
    : r.status === 'not_set_up' ? 3 : r.status === 'unsupported' ? 2 : 1);
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
}
