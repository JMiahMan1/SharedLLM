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
  status: 'adopted' | 'needs_input' | 'error' | 'unsupported';
  message?: string;
  error?: string | null;
  needs?: DiscoveryInput[];
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

export async function commissionMatter(code: string, name?: string): Promise<AdoptResult> {
  const resp = await apiClient.post('/api/homehub/matter/commission', { code, ...(name ? { name } : {}) },
    { timeout: 240_000 });
  return resp.data;
}

/** The hub's own words for a failed call ("already set up", "did not answer"), else a plain fallback. */
export function failureText(err: unknown): string {
  if (isAxiosError(err)) {
    const data = err.response?.data as { detail?: string; message?: string } | undefined;
    return data?.message || data?.detail || 'That did not work. Try again.';
  }
  return 'That did not work. Try again.';
}
