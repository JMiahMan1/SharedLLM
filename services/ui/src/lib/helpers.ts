import { apiClient } from '../services/api';

/**
 * Helpers on the device hub: Home Assistant's toggles, numbers, texts,
 * dropdowns, dates, buttons, counters and timers, which automations read and
 * set. The hub keeps them (services/homehub/helpers.py); the gateway forwards
 * these admin-only calls (services/gateway/helper_routes.py).
 *
 * Configs are Home Assistant's own storage shape, so a helper imported from
 * Home Assistant edits exactly as it was. Nothing here names a household's
 * helpers: the list, the types and their fields all come from the hub.
 */

export type HelperDomain =
  | 'input_boolean' | 'input_number' | 'input_text' | 'input_select' | 'input_datetime' | 'input_button'
  | 'counter' | 'timer';

export interface HelperField {
  name: string;
  label: string;
  kind: 'boolean' | 'number' | 'text' | 'select' | 'options' | 'duration';
  required?: boolean;
  default?: unknown;
  options?: string[];
}

export interface HelperType {
  domain: HelperDomain;
  label: string;
  fields: HelperField[];
  services: string[];
}

export interface HelperState {
  entity_id: string;
  state: string;
  attributes: Record<string, unknown>;
  last_changed?: string;
}

export interface HelperRow {
  domain: HelperDomain;
  id: string;
  entity_id: string;
  name: string;
  config: Record<string, unknown>;
  state: HelperState | null;
  disabled: boolean;
}

export interface HelperList {
  helpers: HelperRow[];
  types: HelperType[];
  problems: string[];
}

const BASE = '/api/homehub/helpers';
const path = (domain: HelperDomain, id: string) => `${BASE}/${domain}/${encodeURIComponent(id)}`;

export async function listHelpers(): Promise<HelperList> {
  return (await apiClient.get(BASE)).data;
}

export async function createHelper(domain: HelperDomain, config: Record<string, unknown>): Promise<HelperRow> {
  return (await apiClient.post(`${BASE}/${domain}`, config)).data;
}

export async function updateHelper(domain: HelperDomain, id: string, config: Record<string, unknown>): Promise<HelperRow> {
  return (await apiClient.put(path(domain, id), config)).data;
}

export async function deleteHelper(domain: HelperDomain, id: string): Promise<void> {
  await apiClient.delete(path(domain, id));
}

export async function callHelper(domain: HelperDomain, id: string, service: string, data?: Record<string, unknown>): Promise<HelperRow> {
  return (await apiClient.post(`${path(domain, id)}/call`, { service, data: data ?? {} })).data;
}

/** A blank config for a new helper of a type: the type's defaults, nothing else. */
export function blankHelper(type: HelperType): Record<string, unknown> {
  const out: Record<string, unknown> = { name: '' };
  for (const f of type.fields) {
    if (f.default !== undefined) out[f.name] = f.default;
  }
  if (type.domain === 'input_select') out.options = [];
  return out;
}

/** The config the form shows for an existing helper: its stored config without the id. */
export function editableConfig(row: HelperRow): Record<string, unknown> {
  return Object.fromEntries(Object.entries(row.config).filter(([k]) => k !== 'id'));
}

/** A config ready to send: empty optional values dropped, so the hub applies its defaults. */
export function cleanConfig(type: HelperType, config: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(config)) {
    if (v === '' || v === undefined || v === null) continue;
    const field = type.fields.find((f) => f.name === k);
    if (field?.kind === 'number' && typeof v === 'string') {
      const n = Number(v);
      out[k] = Number.isFinite(n) ? n : v;
    } else {
      out[k] = v;
    }
  }
  return out;
}

/** How a helper's value reads in a list: "On", "3 laps", "4:59 left", "Never pressed". */
export function describeValue(row: HelperRow, now: Date = new Date()): string {
  const st = row.state;
  if (!st) return 'Not running';
  if (row.disabled) return 'Disabled';
  const a = st.attributes;
  switch (row.domain) {
    case 'input_boolean':
      return st.state === 'on' ? 'On' : st.state === 'off' ? 'Off' : st.state;
    case 'input_number': {
      const unit = typeof a.unit_of_measurement === 'string' ? ` ${a.unit_of_measurement}` : '';
      const n = Number(st.state);
      return Number.isFinite(n) ? `${n}${unit}` : st.state;
    }
    case 'input_text':
      if (st.state === 'unknown') return 'Empty';
      return a.mode === 'password' ? 'Hidden' : st.state || 'Empty';
    case 'input_button':
      return st.state === 'unknown' ? 'Never pressed' : `Pressed ${new Date(st.state).toLocaleString()}`;
    case 'timer':
      if (st.state === 'active' && typeof a.finishes_at === 'string') {
        return `${formatSeconds(Math.max(0, (new Date(a.finishes_at).getTime() - now.getTime()) / 1000))} left`;
      }
      if (st.state === 'paused' && typeof a.remaining === 'string') return `Paused, ${a.remaining} left`;
      return 'Idle';
    default:
      return st.state;
  }
}

/** 299.4 -> "4:59", 3725 -> "1:02:05". */
export function formatSeconds(total: number): string {
  const s = Math.floor(total);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}
