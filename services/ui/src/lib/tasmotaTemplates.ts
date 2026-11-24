import { apiClient } from '../services/api';

/**
 * Devices > a Tasmota device > Device template: the device's GPIO map ("template"), the catalog of widely used
 * devices' templates (copied from templates.blakadder.com), the hub's suggestions, and loading one onto the device
 * (services/homehub/tasmota_templates.py through services/gateway/discovery_routes.py, admins only).
 */

export type TemplateKind = 'bulb' | 'plug' | 'switch' | 'dimmer' | 'strip' | 'relay';

export interface CatalogTemplate {
  id: string;
  manufacturer: string;
  model: string;
  kind: TemplateKind;
  chip: string;
  /** The template text exactly as its source page has it. */
  template: string;
  commands: string[];
  source_url: string;
  notes: string;
  name: string;
  channels: number;
}

export interface Suggestion extends CatalogTemplate {
  score: number;
  reasons: string[];
}

export interface CurrentTemplate {
  device_name: string | null;
  friendly_name: string[];
  hostname: string | null;
  chip: string | null;
  firmware: string | null;
  module: number | null;
  module_name: string | null;
  template_name: string | null;
  template_json: string | null;
  /** Module 0: the template is what the device runs. */
  active: boolean;
  light: boolean;
  light_channels: number;
  relays: number;
  energy: boolean;
  catalog_matches: { id: string; manufacturer: string; model: string }[];
}

export interface DeviceTemplate {
  entry_id: string;
  title: string;
  current: CurrentTemplate;
  up_to_date: boolean;
  message: string | null;
  suggestions: Suggestion[];
  previous_template: string | null;
}

export interface ApplyResult {
  status: 'applied' | 'unchanged';
  verified: boolean;
  restarted?: boolean;
  sent: string | null;
  template: string;
  previous_template: string | null;
  message: string;
  entities_added?: string[];
  entities_stale?: string[];
  current: CurrentTemplate | null;
}

export type ApplyRequest = { catalog_id: string } | { template: string };

export const KIND_LABEL: Record<TemplateKind, string> = {
  bulb: 'Bulbs', plug: 'Plugs', switch: 'Switches', dimmer: 'Dimmers', strip: 'LED strips', relay: 'Relays',
};

export const deviceTemplateKey = (entryId: string) => ['homehub-tasmota-template', entryId];
export const catalogKey = ['homehub-tasmota-catalog'];

export async function getDeviceTemplate(entryId: string): Promise<DeviceTemplate> {
  const resp = await apiClient.get(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/template`);
  return resp.data;
}

export async function getTemplateCatalog(): Promise<CatalogTemplate[]> {
  const resp = await apiClient.get('/api/homehub/tasmota/templates');
  return resp.data?.templates ?? [];
}

/** Loading a template restarts the device; the hub waits for it to come back (up to about 30 s). */
export async function applyTemplate(entryId: string, what: ApplyRequest): Promise<ApplyResult> {
  const resp = await apiClient.post(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/template`,
    { ...what, confirm: true }, { timeout: 120_000 });
  return resp.data;
}

export async function restorePreviousTemplate(entryId: string): Promise<ApplyResult> {
  const resp = await apiClient.post(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/template/restore`,
    { confirm: true }, { timeout: 120_000 });
  return resp.data;
}

/** The NAME of a template's JSON, or null when it is not one. */
export function templateName(text: string | null | undefined): string | null {
  if (!text) return null;
  try {
    const parsed = JSON.parse(text) as { NAME?: unknown };
    return typeof parsed?.NAME === 'string' ? parsed.NAME : null;
  } catch {
    return null;
  }
}

/**
 * What is wrong with a pasted template, or null when it looks like one. The hub checks again and has the last
 * word; this only saves a round trip for the common mistakes.
 */
export function pasteProblem(text: string): string | null {
  const t = text.trim();
  if (!t) return 'Paste a template first.';
  let parsed: unknown;
  try {
    parsed = JSON.parse(t);
  } catch {
    return 'That is not valid JSON. Paste the whole {"NAME":...} text.';
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return 'A template is a JSON object.';
  const o = parsed as Record<string, unknown>;
  if (typeof o.NAME !== 'string' || !o.NAME.trim()) return 'The template has no NAME.';
  if (!Array.isArray(o.GPIO) || !o.GPIO.length) return 'The template has no GPIO list.';
  if (!o.GPIO.every((g) => Number.isInteger(g) && (g as number) >= 0)) return 'Every GPIO value must be a whole number.';
  if (typeof o.BASE !== 'number') return 'The template has no BASE module number.';
  return null;
}

/** Catalog entries matching every word of `text` (maker, model, name). */
export function filterCatalog(list: CatalogTemplate[], text: string, kind: TemplateKind | ''): CatalogTemplate[] {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  return list.filter((e) => (!kind || e.kind === kind)
    && words.every((w) => `${e.manufacturer} ${e.model} ${e.name} ${e.id}`.toLowerCase().includes(w)));
}
