import { apiClient } from '../services/api';

/**
 * Devices > a Tasmota device > Device template: the device's GPIO map ("template"), the catalog of widely used
 * devices' templates (copied from templates.blakadder.com), templates measured in this household, the hub's
 * suggestions, loading one onto the device, and the pin finder (services/homehub/tasmota_templates.py,
 * tasmota_measured.py and tasmota_pin_finder.py through services/gateway/discovery_routes.py, admins only).
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
  /** "measured": worked out in this household (the pin finder, a working device, or by hand). */
  source?: 'catalog' | 'measured';
  /** The device a measured template was found on, as the household names it. */
  measured_on?: string | null;
  measured_at?: string | null;
  method?: 'pin_finder' | 'saved_from_device' | 'by_hand' | null;
  how?: string | null;
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
  measured_matches?: { id: string; manufacturer: string; model: string; measured_on: string | null }[];
}

export interface DeviceTemplate {
  entry_id: string;
  title: string;
  current: CurrentTemplate;
  up_to_date: boolean;
  message: string | null;
  suggestions: Suggestion[];
  /** False when the device says nothing about itself: the page offers the pin finder first. */
  identified?: boolean;
  identify_note?: string | null;
  /** Templates that only match the kind its name suggests, when nothing identifies it. */
  guesses?: Suggestion[];
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

export type ApplyRequest = { catalog_id: string } | { measured_id: string } | { template: string };

export interface PinFinderAnswer { value: string; label: string }

export interface PinFinderQuestion {
  step: number;
  id: 'baseline' | 'single' | 'pair' | 'compare' | 'offer_rest' | 'verify';
  text: string;
  hint: string | null;
  pin: number | null;
  answers: PinFinderAnswer[];
  /** The test can be shown once more ("again"). */
  again: boolean;
  progress: { done: number; total: number };
}

export interface PinFinderResult {
  template: string;
  commands: string[];
  type: 'dimmer' | 'cw' | 'ct' | 'rgb' | 'rgbw' | 'rgbcw';
  pins: Record<string, number>;
  invert: boolean;
  kind?: TemplateKind;
  how?: string;
}

export interface PinFinderState {
  active: boolean;
  status: 'idle' | 'asking' | 'done' | 'failed' | 'cancelled';
  question: PinFinderQuestion | null;
  result: PinFinderResult | null;
  message: string | null;
  /** A run that did not finish left its saved template to put back. */
  interrupted?: boolean;
  restored?: boolean;
  manufacturer?: string;
  model?: string;
  kind?: TemplateKind;
  kinds?: TemplateKind[];
  lit?: Record<string, string>;
}

export interface PinFinderStart { manufacturer: string; model: string; kind: TemplateKind }

export interface MeasuredRequest {
  manufacturer: string;
  model: string;
  kind?: TemplateKind;
  notes?: string;
}

export interface SavedMeasured {
  status: 'saved';
  template: CatalogTemplate;
  message: string;
}

export const KIND_LABEL: Record<TemplateKind, string> = {
  bulb: 'Bulbs', plug: 'Plugs', switch: 'Switches', dimmer: 'Dimmers', strip: 'LED strips', relay: 'Relays',
};

export const deviceTemplateKey = (entryId: string) => ['homehub-tasmota-template', entryId];
export const catalogKey = ['homehub-tasmota-catalog'];
export const pinFinderKey = (entryId: string) => ['homehub-tasmota-pinfinder', entryId];

/** What the pin finder's test template does to the device while it runs. */
export const PIN_FINDER_LIGHT_KINDS: TemplateKind[] = ['bulb', 'strip', 'dimmer'];

export async function getDeviceTemplate(entryId: string): Promise<DeviceTemplate> {
  const resp = await apiClient.get(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/template`);
  return resp.data;
}

/** The household's measured templates first, then the bundled catalog. */
export async function getTemplateCatalog(): Promise<CatalogTemplate[]> {
  const resp = await apiClient.get('/api/homehub/tasmota/templates');
  const measured: CatalogTemplate[] = resp.data?.measured ?? [];
  const catalog: CatalogTemplate[] = resp.data?.templates ?? [];
  return [...measured.map((e) => ({ ...e, source: 'measured' as const })), ...catalog];
}

/** "Save this device's template": the hub reads the device (nothing is sent to change it). */
export async function saveMeasuredTemplate(entryId: string, what: MeasuredRequest): Promise<SavedMeasured> {
  const resp = await apiClient.post(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/measured`, what,
    { timeout: 60_000 });
  return resp.data;
}

export async function forgetMeasuredTemplate(id: string): Promise<void> {
  await apiClient.delete(`/api/homehub/tasmota/measured/${encodeURIComponent(id)}`);
}

export async function getPinFinder(entryId: string): Promise<PinFinderState> {
  const resp = await apiClient.get(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/pinfinder`);
  return resp.data;
}

/** Starting saves the device's template, then loads a test template: the device restarts (up to a minute). */
export async function startPinFinder(entryId: string, what: PinFinderStart): Promise<PinFinderState> {
  const resp = await apiClient.post(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/pinfinder/start`,
    { ...what, confirm: true }, { timeout: 150_000 });
  return resp.data;
}

/** Some answers load a template (the device restarts), so this can take up to a minute too. */
export async function answerPinFinder(entryId: string, step: number, answer: string): Promise<PinFinderState> {
  const resp = await apiClient.post(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/pinfinder/answer`,
    { step, answer }, { timeout: 150_000 });
  return resp.data;
}

export async function cancelPinFinder(entryId: string): Promise<PinFinderState> {
  const resp = await apiClient.post(`/api/homehub/tasmota/${encodeURIComponent(entryId)}/pinfinder/cancel`,
    { confirm: true }, { timeout: 150_000 });
  return resp.data;
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
    && words.every((w) => `${e.manufacturer} ${e.model} ${e.name} ${e.id} ${e.measured_on ?? ''}`.toLowerCase().includes(w)));
}

/** How a template is labelled: "Measured on Den lamp" for the household's own, else nothing. */
export function measuredLabel(e: Pick<CatalogTemplate, 'source' | 'measured_on'>): string | null {
  if (e.source !== 'measured') return null;
  return e.measured_on ? `Measured on ${e.measured_on}` : 'Measured here';
}

/** The request that loads this entry: a measured template by its id, a catalog one by its id. */
export function applyRequestFor(e: CatalogTemplate): ApplyRequest {
  return e.source === 'measured' ? { measured_id: e.id } : { catalog_id: e.id };
}
