import { apiClient } from '../services/api';

/**
 * Automations, scripts and scenes on the device hub (homehub), reached through
 * the gateway at the same paths (services/gateway/automation_routes.py).
 *
 * Nothing here names a household's entities, rooms or people: every picker is
 * filled from the hub's catalog at runtime. Configs are Home Assistant's own
 * shape (what automations.yaml, scripts.yaml and scenes.yaml hold), so the
 * builder edits those dicts directly instead of a private model.
 */

export type ItemKind = 'automation' | 'script' | 'scene';
export const ITEM_KINDS: ItemKind[] = ['automation', 'script', 'scene'];

export interface AutomationItem {
  kind: ItemKind;
  id: string;
  entity_id: string;
  alias: string;
  description?: string | null;
  icon?: string | null;
  enabled: boolean;
  runnable: boolean;
  unsupported_reason: string | null;
  warnings: string[];
  last_triggered: string | null;
  last_error: string | null;
  mode?: string | null;
  running: number;
  entities: string[];
}

export type Config = Record<string, unknown>;
export type Step = Record<string, unknown>;

export interface ItemDetail {
  item: AutomationItem;
  config: Config;
  yaml: string;
  can_edit?: boolean;
}

export interface TraceStep {
  index: number;
  depth: number;
  kind: string;
  alias: string | null;
  summary: string;
  at: string;
  error: string | null;
}

export interface Trace {
  run_id: string;
  kind: ItemKind;
  item_id: string;
  started: string;
  finished: string | null;
  result: 'running' | 'finished' | 'error' | 'condition_failed' | 'stopped' | 'cancelled' | string;
  trigger: { description?: string; [key: string]: unknown } | null;
  conditions: { passed: boolean | null; detail: string | null } | null;
  steps: TraceStep[];
  error: string | null;
}

export type FieldKind =
  | 'entity' | 'entities' | 'text' | 'number' | 'time' | 'duration' | 'select' | 'template' | 'weekdays' | 'boolean';

export interface SpecField {
  name: string;
  label: string;
  kind: FieldKind;
  domain?: string;
  options?: string[];
  required?: boolean;
}

export interface Spec {
  type: string;
  label: string;
  fields: SpecField[];
}

export interface ServiceField {
  name?: string;
  description?: string;
  example?: unknown;
  required?: boolean;
  selector?: Record<string, unknown>;
}

export interface ServiceInfo {
  name?: string;
  description?: string;
  fields?: Record<string, ServiceField>;
}

export interface CatalogEntity {
  entity_id: string;
  name?: string | null;
  domain?: string;
  area?: string | null;
  device_class?: string | null;
  unit?: string | null;
}

export interface Catalog {
  triggers: Spec[];
  conditions: Spec[];
  actions: Spec[];
  services: { domain: string; services: Record<string, ServiceInfo> }[];
  entities: CatalogEntity[];
  areas: { area_id: string; name: string }[];
  zones: string[];
  persons: string[];
}

export interface ValidateResult {
  valid: boolean;
  errors: string[];
  unsupported_reason: string | null;
  warnings: string[];
}

export interface ImportResult {
  created: string[];
  updated: string[];
  skipped: { id: string; reason: string }[];
  errors: string[];
}

export interface Remap {
  entities?: Record<string, string>;
  registry_ids?: Record<string, string>;
  devices?: Record<string, string>;
  drop_actions?: { match: Record<string, unknown>; reason: string }[];
  only?: string[];
}

export interface RemapRow {
  kind: ItemKind;
  id: string;
  alias: string;
  changes: { path: string; old: unknown; new: unknown }[];
  dropped: { path: string; reason: string }[];
  reason_before: string | null;
  reason_after: string | null;
  enabled: boolean;
}

const BASE = '/api/homehub/automations';
const itemPath = (kind: ItemKind, id: string) => `${BASE}/${kind}/${encodeURIComponent(id)}`;

export async function listItems(): Promise<{ items: AutomationItem[]; can_edit: boolean }> {
  return (await apiClient.get(BASE)).data;
}

export async function getItem(kind: ItemKind, id: string): Promise<ItemDetail> {
  return (await apiClient.get(itemPath(kind, id))).data;
}

export async function createItem(kind: ItemKind, config: Config): Promise<{ item: AutomationItem; config: Config }> {
  return (await apiClient.post(`${BASE}/${kind}`, { config })).data;
}

export async function updateItem(kind: ItemKind, id: string, config: Config): Promise<{ item: AutomationItem; config: Config }> {
  return (await apiClient.put(itemPath(kind, id), { config })).data;
}

export async function deleteItem(kind: ItemKind, id: string): Promise<void> {
  await apiClient.delete(itemPath(kind, id));
}

export async function setItemEnabled(id: string, enabled: boolean): Promise<{ item: AutomationItem }> {
  return (await apiClient.post(`${itemPath('automation', id)}/enabled`, { enabled })).data;
}

export async function runItem(kind: ItemKind, id: string, skipCondition = true): Promise<void> {
  await apiClient.post(`${itemPath(kind, id)}/run`, { skip_condition: skipCondition }, { timeout: 90_000 });
}

export async function getTraces(kind: ItemKind, id: string): Promise<Trace[]> {
  return ((await apiClient.get(`${itemPath(kind, id)}/traces`)).data?.traces ?? []) as Trace[];
}

export async function validateConfig(kind: ItemKind, config: Config): Promise<ValidateResult> {
  return (await apiClient.post(`${BASE}/validate`, { kind, config })).data;
}

export async function importYaml(kind: ItemKind, yaml: string, overwrite = false): Promise<ImportResult> {
  return (await apiClient.post(`${BASE}/import`, { kind, yaml, overwrite }, { timeout: 90_000 })).data;
}

export async function exportYaml(kind: ItemKind, id?: string): Promise<{ yaml: string; count: number; filename: string }> {
  return (await apiClient.get(`${BASE}/export`, { params: id ? { kind, id } : { kind } })).data;
}

export async function getCatalog(): Promise<Catalog> {
  return (await apiClient.get(`${BASE}/catalog`)).data;
}

export async function getRemap(): Promise<{ remap: Remap; report: RemapRow[] }> {
  return (await apiClient.get(`${BASE}/remap`)).data;
}

export async function saveRemap(remap: Remap): Promise<{ remap: Remap; report: RemapRow[] }> {
  return (await apiClient.put(`${BASE}/remap`, { remap })).data;
}

export async function previewRemap(remap: Remap): Promise<{ report: RemapRow[] }> {
  return (await apiClient.post(`${BASE}/remap/preview`, { remap })).data;
}

/** The hub's 400 carries `errors`; show them with its message. */
export function validationErrors(err: unknown): string[] {
  const data = (err as { response?: { data?: { errors?: unknown } } })?.response?.data;
  return Array.isArray(data?.errors) ? data.errors.map(String) : [];
}

// --------------------------------------------------------------------------- list helpers

export const KIND_LABELS: Record<ItemKind, string> = { automation: 'Automation', script: 'Script', scene: 'Scene' };

/** Items matching every word of the query, in alias, id, description or the entities they name. */
export function filterItems(items: AutomationItem[], query: string, kind: ItemKind | 'all' = 'all'): AutomationItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return items
    .filter((item) => kind === 'all' || item.kind === kind)
    .filter((item) => {
      if (!words.length) return true;
      const hay = [item.alias, item.id, item.description ?? '', ...(item.entities ?? [])].join(' ').toLowerCase();
      return words.every((w) => hay.includes(w));
    })
    .sort((a, b) => (a.alias || a.id).localeCompare(b.alias || b.id));
}

/** "3 min ago", "2 h ago", "4 d ago", or "never". */
export function relativeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'never';
  const minutes = Math.round((now.getTime() - then) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 90) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export const TRACE_RESULT_LABELS: Record<string, string> = {
  running: 'Running',
  finished: 'Finished',
  error: 'Error',
  condition_failed: 'Conditions not met',
  stopped: 'Stopped',
  cancelled: 'Cancelled',
};

// --------------------------------------------------------------------------- builder: step types

/** HA writes the trigger type as `trigger` (2024.10+) or `platform` (older files). */
export function triggerType(trigger: Step): string {
  return String(trigger.trigger ?? trigger.platform ?? 'raw');
}

export function conditionType(condition: Step): string {
  if (typeof condition.condition === 'string') return condition.condition;
  return 'raw';
}

/** An action's type from the keys HA uses to tell them apart. */
export function actionType(action: Step): string {
  if (typeof action.action === 'string' || typeof action.service === 'string') return 'service';
  for (const key of ['delay', 'wait_template', 'choose', 'if', 'repeat', 'variables', 'stop', 'scene', 'event']) {
    if (key in action) return key;
  }
  return 'raw';
}

/** A copy of an object without some keys (typed as the input, since the keys dropped are optional ones). */
export function omit<T extends object>(value: T, keys: string[]): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) if (!keys.includes(k)) out[k] = v;
  return out as T;
}

const ON_OFF = new Set(['on', 'off']);
const DEVICE_ONOFF_KEYS = new Set(['trigger', 'platform', 'entity_id', 'to', 'for', 'id', 'alias']);

/**
 * A state trigger that is only "entity turns on/off" is shown as the
 * device_onoff sugar, so the editor offers the simple form. Anything with more
 * keys (from, attribute) stays a plain state trigger.
 */
export function toBuilderTrigger(trigger: Step): Step {
  if (triggerType(trigger) !== 'state') return trigger;
  const keys = Object.keys(trigger);
  if (!keys.every((k) => DEVICE_ONOFF_KEYS.has(k))) return trigger;
  if (typeof trigger.to !== 'string' || !ON_OFF.has(trigger.to)) return trigger;
  return { trigger: 'device_onoff', ...omit(trigger, ['platform', 'trigger']) };
}

/** device_onoff back to HA's own state trigger; everything else unchanged. */
export function fromBuilderTrigger(trigger: Step): Step {
  if (triggerType(trigger) !== 'device_onoff') return trigger;
  return { trigger: 'state', ...omit(trigger, ['platform', 'trigger']) };
}

/** The list a config keeps its triggers, conditions and actions in (new plural keys, or the old singular ones). */
export function listOf(config: Config, plural: string, singular: string): Step[] {
  const value = config[plural] ?? config[singular];
  if (Array.isArray(value)) return value.filter((v): v is Step => !!v && typeof v === 'object');
  if (value && typeof value === 'object') return [value as Step];
  return [];
}

/** Config to the builder's three lists (for an automation; scripts use `sequence`). */
export function toBuilder(kind: ItemKind, config: Config): { triggers: Step[]; conditions: Step[]; actions: Step[] } {
  if (kind === 'script') return { triggers: [], conditions: [], actions: listOf(config, 'sequence', 'sequence') };
  return {
    triggers: listOf(config, 'triggers', 'trigger').map(toBuilderTrigger),
    conditions: listOf(config, 'conditions', 'condition'),
    actions: listOf(config, 'actions', 'action'),
  };
}

/** The builder's lists back into the config, written with HA's current plural keys. */
export function fromBuilder(kind: ItemKind, config: Config, lists: { triggers: Step[]; conditions: Step[]; actions: Step[] }): Config {
  if (kind === 'script') return { ...config, sequence: lists.actions };
  return {
    ...omit(config, ['trigger', 'condition', 'action']),
    triggers: lists.triggers.map(fromBuilderTrigger),
    conditions: lists.conditions,
    actions: lists.actions,
  };
}

/** A fresh step of a type, with the spec's select fields at their first option. */
export function blankStep(group: 'trigger' | 'condition' | 'action', type: string, spec?: Spec): Step {
  const step: Step = {};
  if (group === 'trigger') step.trigger = type;
  else if (group === 'condition') {
    step.condition = type;
    if (type === 'and' || type === 'or' || type === 'not') step.conditions = [];
  } else {
    switch (type) {
      case 'service': step.action = ''; break;
      case 'delay': step.delay = '00:00:30'; break;
      case 'choose': step.choose = [{ conditions: [], sequence: [] }]; break;
      case 'if': step.if = []; step.then = []; break;
      case 'repeat': step.repeat = { count: 2, sequence: [] }; break;
      case 'stop': step.stop = 'Stopped'; break;
      case 'variables': step.variables = {}; break;
      case 'scene': step.scene = ''; break;
      case 'event': step.event = ''; break;
      case 'wait_template': step.wait_template = ''; break;
      default: break;
    }
  }
  if (group === 'trigger' && type === 'device_onoff') step.to = 'on';
  for (const field of spec?.fields ?? []) {
    if (field.kind === 'select' && field.options?.length && !(field.name in step)) step[field.name] = field.options[0];
  }
  return step;
}

/** The service an action calls (`action` in new configs, `service` in old ones). */
export function actionService(action: Step): string {
  return String(action.action ?? action.service ?? '');
}

/** The entities an action targets, from `target.entity_id`, `entity_id` or `data.entity_id`. */
export function actionEntities(action: Step): string[] {
  const target = (action.target ?? {}) as Record<string, unknown>;
  const data = (action.data ?? {}) as Record<string, unknown>;
  const raw = target.entity_id ?? action.entity_id ?? data.entity_id;
  if (Array.isArray(raw)) return raw.map(String);
  return typeof raw === 'string' && raw ? [raw] : [];
}

/** An action with its entities written to `target.entity_id`, the shape HA writes today. */
export function withActionEntities(action: Step, entities: string[]): Step {
  const rest = omit(action, ['entity_id']);
  const target = { ...((action.target ?? {}) as Record<string, unknown>) };
  if (entities.length) target.entity_id = entities.length === 1 ? entities[0] : entities;
  else delete target.entity_id;
  const out: Step = { ...rest };
  if (Object.keys(target).length) out.target = target;
  else delete out.target;
  return out;
}

/** The fields a service takes, from the catalog; empty when it is not listed. */
export function serviceFields(catalog: Catalog | undefined, service: string): Record<string, ServiceField> {
  const [domain, name] = service.split('.');
  const entry = catalog?.services.find((s) => s.domain === domain);
  return entry?.services?.[name]?.fields ?? {};
}

/** Every `domain.service` the catalog lists, sorted. */
export function serviceNames(catalog: Catalog | undefined): string[] {
  const out: string[] = [];
  for (const entry of catalog?.services ?? []) {
    for (const name of Object.keys(entry.services ?? {})) out.push(`${entry.domain}.${name}`);
  }
  return out.sort();
}

/** "light.porch" -> its catalog name, else the id. */
export function entityName(catalog: Catalog | undefined, entityId: string): string {
  return catalog?.entities.find((e) => e.entity_id === entityId)?.name || entityId;
}

/** A one-line description of a step for collapsed rows and summaries. */
export function describeStep(group: 'trigger' | 'condition' | 'action', step: Step, catalog?: Catalog): string {
  if (typeof step.alias === 'string' && step.alias) return step.alias;
  const names = (value: unknown) => (Array.isArray(value) ? value : value ? [value] : [])
    .map((v) => entityName(catalog, String(v))).join(', ');
  if (group === 'trigger') {
    const type = triggerType(step);
    if (type === 'device_onoff') return `When ${names(step.entity_id) || 'a device'} turns ${String(step.to ?? 'on')}`;
    if (type === 'state') return `When ${names(step.entity_id) || 'an entity'} changes${step.to !== undefined ? ` to ${String(step.to)}` : ''}`;
    if (type === 'numeric_state') {
      const bounds = [step.above !== undefined ? `above ${String(step.above)}` : '', step.below !== undefined ? `below ${String(step.below)}` : ''].filter(Boolean).join(' and ');
      return `When ${names(step.entity_id) || 'a value'} is ${bounds || 'out of range'}`;
    }
    if (type === 'time') return `At ${Array.isArray(step.at) ? step.at.join(', ') : String(step.at ?? '?')}`;
    if (type === 'sun') return `At ${String(step.event ?? 'sunset')}${step.offset ? ` ${String(step.offset)}` : ''}`;
    if (type === 'zone') return `When ${names(step.entity_id) || 'someone'} ${String(step.event ?? 'enter')}s ${names(step.zone)}`;
    if (type === 'template') return 'When a template becomes true';
    return `${type} trigger`;
  }
  if (group === 'condition') {
    const type = conditionType(step);
    if (type === 'and' || type === 'or' || type === 'not') {
      const count = Array.isArray(step.conditions) ? step.conditions.length : 0;
      return `${type.toUpperCase()} of ${count} condition${count === 1 ? '' : 's'}`;
    }
    if (type === 'state') return `${names(step.entity_id) || 'An entity'} is ${String(step.state ?? '?')}`;
    if (type === 'numeric_state') return `${names(step.entity_id) || 'A value'} is in range`;
    if (type === 'time') return `Time is ${step.after ? `after ${String(step.after)}` : ''}${step.after && step.before ? ' and ' : ''}${step.before ? `before ${String(step.before)}` : ''}`;
    return `${type} condition`;
  }
  const type = actionType(step);
  if (type === 'service') {
    const targets = names(actionEntities(step));
    return `${actionService(step) || 'Call a service'}${targets ? ` on ${targets}` : ''}`;
  }
  if (type === 'delay') return `Wait ${typeof step.delay === 'object' ? JSON.stringify(step.delay) : String(step.delay)}`;
  if (type === 'choose') return 'Choose';
  if (type === 'if') return 'If, then, else';
  if (type === 'repeat') return 'Repeat';
  if (type === 'stop') return `Stop: ${String(step.stop ?? '')}`;
  if (type === 'scene') return `Activate ${names(step.scene)}`;
  return type === 'raw' ? 'Custom step' : `${type} step`;
}

/** An empty config for a new item of a kind. */
export function blankConfig(kind: ItemKind): Config {
  if (kind === 'script') return { alias: '', mode: 'single', sequence: [] };
  if (kind === 'scene') return { name: '', entities: {} };
  return { alias: '', description: '', mode: 'single', triggers: [], conditions: [], actions: [] };
}

/** Parse a JSON object typed into a "raw step" or data box; null when it is not one. */
export function parseJsonObject(text: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(text);
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
  } catch {
    return null;
  }
}
