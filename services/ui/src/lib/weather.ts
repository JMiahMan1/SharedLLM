/**
 * Weather: the forecast shape from `/api/weather`, its icons and wording, and
 * the household's weather settings (docs/WEATHER.md).
 *
 * The forecast is Open-Meteo's, cached by execution. Reminder rules live in
 * the `weather_reminders` global setting, a JSON list execution's weather
 * loop reads; `weather_location` and `weather_units` choose where and how.
 */
import {
  Cloud, CloudDrizzle, CloudFog, CloudLightning, CloudRain, CloudSnow, CloudSun, Moon, Sun,
  type LucideIcon,
} from 'lucide-react';
import { api } from '../services/api';

export type WeatherIcon = 'clear' | 'partly_cloudy' | 'cloudy' | 'fog' | 'drizzle' | 'rain' | 'sleet' | 'snow' | 'storm';

export interface WeatherHour {
  time: string;
  ts: number;
  temp: number | null;
  precip_prob: number | null;
  precip: number | null;
  snow: number | null;
  code: number | null;
  label: string;
  icon: WeatherIcon;
  wind: number | null;
  gust: number | null;
}

export interface WeatherDay {
  date: string;
  ts: number;
  high: number | null;
  low: number | null;
  precip: number | null;
  precip_prob: number | null;
  snow: number | null;
  gust: number | null;
  code: number | null;
  label: string;
  icon: WeatherIcon;
  sunrise: string | null;
  sunset: string | null;
}

export interface WeatherHeadsUp {
  id: string;
  kind: WeatherReminderKind;
  title: string;
  body: string;
  starts_at: number;
}

export interface WeatherResponse {
  source: string;
  location: { name: string; latitude: number | null; longitude: number | null };
  timezone: string | null;
  utc_offset_seconds: number;
  units: { system: WeatherUnits; temp: string; wind: string; precip: string; snow?: string };
  current: {
    ts: number | null;
    temp: number | null;
    feels_like: number | null;
    humidity: number | null;
    precip: number | null;
    code: number | null;
    label: string;
    icon: WeatherIcon;
    wind: number | null;
    gust: number | null;
    is_day: boolean;
  };
  hourly: WeatherHour[];
  daily: WeatherDay[];
  fetched_at: number;
  stale?: boolean;
  /** True when the forecast is for the person's own place, not the household's. */
  personal?: boolean;
  heads_up?: WeatherHeadsUp[];
}

export interface WeatherEnergyDay {
  date: string;
  outdoor_mean: number | null;
  outdoor_min: number | null;
  outdoor_max: number | null;
  indoor_mean: number | null;
  hdd: number;
  cdd: number;
  kwh: number | null;
  power_coverage: number;
  heat_hours: number;
  cool_hours: number;
}

export interface WeatherEnergyReport {
  units: string;
  days: WeatherEnergyDay[];
  model: { baseline_kwh: number; kwh_per_degree_day: number; r2: number | null; days: number } | null;
  projection: { date: string; hdd: number; cdd: number; kwh: number | null }[];
  insights: string[];
}

const ICONS: Record<WeatherIcon, LucideIcon> = {
  clear: Sun,
  partly_cloudy: CloudSun,
  cloudy: Cloud,
  fog: CloudFog,
  drizzle: CloudDrizzle,
  rain: CloudRain,
  sleet: CloudSnow,
  snow: CloudSnow,
  storm: CloudLightning,
};

/** The icon for a condition; a clear night is a moon. */
export function weatherIcon(icon: WeatherIcon | string | undefined, isDay = true): LucideIcon {
  if (icon === 'clear' && !isDay) return Moon;
  return ICONS[(icon as WeatherIcon)] ?? Cloud;
}

export const ICON_TONE: Record<WeatherIcon, string> = {
  clear: 'text-amber-300',
  partly_cloudy: 'text-amber-200',
  cloudy: 'text-slate-300',
  fog: 'text-slate-400',
  drizzle: 'text-sky-300',
  rain: 'text-sky-400',
  sleet: 'text-cyan-200',
  snow: 'text-cyan-100',
  storm: 'text-violet-300',
};

export const round = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? '–' : String(Math.round(value));

/** Hour label in the forecast's own time zone ("3 PM"), whatever the device's zone. */
export function hourLabel(ts: number, offsetSeconds: number): string {
  const d = new Date((ts + offsetSeconds) * 1000);
  const h = d.getUTCHours();
  return `${h % 12 || 12} ${h < 12 ? 'AM' : 'PM'}`;
}

/** Weekday for a forecast date ("Tue"); "Today" for the first. */
export function dayLabel(date: string, index: number): string {
  if (index === 0) return 'Today';
  const d = new Date(`${date}T12:00:00Z`);
  return d.toLocaleDateString(undefined, { weekday: 'short', timeZone: 'UTC' });
}

// --------------------------------------------------------------------------
// Settings
// --------------------------------------------------------------------------

export const WEATHER_REMINDERS_SETTING = 'weather_reminders';
export const WEATHER_LOCATION_SETTING = 'weather_location';
export const WEATHER_UNITS_SETTING = 'weather_units';

export type WeatherUnits = 'imperial' | 'metric';
export type WeatherReminderKind = 'rain' | 'snow' | 'freeze' | 'heat' | 'wind';

export interface WeatherLocation {
  name?: string;
  latitude: number;
  longitude: number;
}

export interface WeatherReminder {
  id: string;
  kind: WeatherReminderKind;
  title: string;
  note?: string;
  people: string[];
  hours?: number;
  threshold?: number;
  /** The units `threshold` was set in; the server converts when the household's differ. */
  units?: WeatherUnits;
  location?: WeatherLocation | null;
  enabled?: boolean;
  /** false: run the actions only, send no notice. */
  notify?: boolean;
  actions?: WeatherAction[];
}

/** What else a reminder does when it starts (services/execution/weather_bus.py). */
export type WeatherAction =
  | { type: 'ha_service'; service: string; entity_id?: string; data?: Record<string, unknown> }
  | { type: 'call'; service: string; path: string; method?: 'GET' | 'POST' | 'PUT' };

export interface WeatherKindInfo {
  label: string;
  title: string;
  hint: string;
  hours: number;
  /** Threshold default per unit system, and what it measures. */
  threshold: Record<WeatherUnits, number>;
  measure: 'percent' | 'temp' | 'wind' | 'precip' | 'snow';
  thresholdLabel: string;
}

/** Mirrors `KINDS` in services/execution/weather.py. */
export const WEATHER_KINDS: Record<WeatherReminderKind, WeatherKindInfo> = {
  rain: {
    label: 'Rain',
    title: 'Rain on the way',
    hint: 'Any hour in the window with at least this chance of rain, or measurable rain forecast.',
    hours: 12,
    threshold: { imperial: 60, metric: 60 },
    measure: 'percent',
    thresholdLabel: 'Chance',
  },
  snow: {
    label: 'Snow',
    title: 'Snow on the way',
    hint: 'At least this much snow in total across the window.',
    hours: 24,
    threshold: { imperial: 0.5, metric: 1 },
    measure: 'snow',
    thresholdLabel: 'Total',
  },
  freeze: {
    label: 'Freeze',
    title: 'Freeze coming',
    hint: 'Any hour in the window at or below this temperature (pipes, animals, plants).',
    hours: 24,
    threshold: { imperial: 32, metric: 0 },
    measure: 'temp',
    thresholdLabel: 'At or below',
  },
  heat: {
    label: 'Heat',
    title: 'Heat coming',
    hint: 'Any hour in the window at or above this temperature.',
    hours: 24,
    threshold: { imperial: 95, metric: 35 },
    measure: 'temp',
    thresholdLabel: 'At or above',
  },
  wind: {
    label: 'High wind',
    title: 'High winds coming',
    hint: 'Any hour in the window with gusts at or above this speed.',
    hours: 12,
    threshold: { imperial: 40, metric: 65 },
    measure: 'wind',
    thresholdLabel: 'Gusts',
  },
};

export function unitSuffix(measure: WeatherKindInfo['measure'], units: WeatherUnits): string {
  if (measure === 'percent') return '%';
  if (measure === 'temp') return units === 'metric' ? '°C' : '°F';
  if (measure === 'wind') return units === 'metric' ? 'km/h' : 'mph';
  // Open-Meteo gives snowfall in cm (not mm) with metric units.
  if (measure === 'snow') return units === 'metric' ? 'cm' : 'in';
  return units === 'metric' ? 'mm' : 'in';
}

/** metric = imperial * factor; mirrors `convert` in services/common/weather.py. */
const TO_METRIC: Partial<Record<WeatherKindInfo['measure'], number>> = { wind: 1.609344, precip: 25.4, snow: 2.54 };

/** A threshold of `measure` from one unit system to the other, to one decimal. */
export function convertThreshold(measure: WeatherKindInfo['measure'], value: number, from: WeatherUnits, to: WeatherUnits): number {
  if (from === to) return value;
  let out: number;
  if (measure === 'temp') out = to === 'metric' ? ((value - 32) * 5) / 9 : (value * 9) / 5 + 32;
  else if (TO_METRIC[measure]) out = to === 'metric' ? value * TO_METRIC[measure]! : value / TO_METRIC[measure]!;
  else return value;
  return Math.round(out * 10) / 10;
}

/** The rule with its threshold in `units` (and saying so). */
export function inUnits(rule: WeatherReminder, units: WeatherUnits): WeatherReminder {
  if (rule.threshold === undefined || !rule.units || rule.units === units) return { ...rule, units };
  return { ...rule, threshold: convertThreshold(WEATHER_KINDS[rule.kind].measure, rule.threshold, rule.units, units), units };
}

const isKind = (value: unknown): value is WeatherReminderKind =>
  typeof value === 'string' && value in WEATHER_KINDS;

const finite = (value: unknown): number | undefined => {
  const n = typeof value === 'string' && value.trim() === '' ? NaN : Number(value);
  return Number.isFinite(n) ? n : undefined;
};

export function parseLocation(raw: unknown): WeatherLocation | null {
  let data = raw;
  if (typeof data === 'string') {
    try {
      data = data.trim() ? JSON.parse(data) : null;
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== 'object') return null;
  const row = data as Record<string, unknown>;
  const latitude = finite(row.latitude);
  const longitude = finite(row.longitude);
  if (latitude === undefined || longitude === undefined) return null;
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
  const name = typeof row.name === 'string' && row.name.trim() ? row.name.trim().slice(0, 60) : undefined;
  return { ...(name ? { name } : {}), latitude, longitude };
}

/** A short id for a new rule (lower-case, as the server expects). */
export function newReminderId(): string {
  return Math.random().toString(36).slice(2, 10);
}

/** Trim, lower-case people, drop blanks and defaults so the service's own apply. */
export function cleanReminder(rule: WeatherReminder): WeatherReminder {
  const out: WeatherReminder = {
    id: rule.id.trim().toLowerCase() || newReminderId(),
    kind: rule.kind,
    title: rule.title.trim().slice(0, 80) || WEATHER_KINDS[rule.kind].title,
    people: [...new Set(rule.people.map((p) => p.trim().toLowerCase()).filter(Boolean))],
  };
  const note = rule.note?.trim();
  if (note) out.note = note.slice(0, 200);
  const hours = finite(rule.hours);
  if (hours !== undefined && hours > 0) out.hours = Math.min(48, Math.round(hours));
  const threshold = finite(rule.threshold);
  if (threshold !== undefined) out.threshold = threshold;
  if (rule.units === 'imperial' || rule.units === 'metric') out.units = rule.units;
  const location = rule.location ? parseLocation(rule.location) : null;
  if (location) out.location = location;
  if (rule.enabled === false) out.enabled = false;
  if (rule.notify === false) out.notify = false;
  const actions = parseActions(rule.actions);
  if (actions.length) out.actions = actions;
  return out;
}

const HA_SERVICE = /^[a-z_]+\.[a-z0-9_]+$/;
const SERVICE_PATH = /^\/[A-Za-z0-9._~/%-]{0,200}$/;

/** Clean actions; mirrors `parse_actions` on the server. */
export function parseActions(raw: unknown): WeatherAction[] {
  if (!Array.isArray(raw)) return [];
  const out: WeatherAction[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    if (row.type === 'ha_service') {
      const service = String(row.service ?? '').trim().toLowerCase();
      const entity = String(row.entity_id ?? '').trim();
      if (!HA_SERVICE.test(service) || (entity && !entity.includes('.'))) continue;
      const action: WeatherAction = { type: 'ha_service', service };
      if (entity) action.entity_id = entity;
      if (row.data && typeof row.data === 'object' && !Array.isArray(row.data)) action.data = row.data as Record<string, unknown>;
      out.push(action);
    } else if (row.type === 'call') {
      const service = String(row.service ?? '').trim().toLowerCase();
      const path = String(row.path ?? '').trim();
      const method = String(row.method ?? 'POST').toUpperCase();
      if (!service || !SERVICE_PATH.test(path) || path.includes('..') || !['GET', 'POST', 'PUT'].includes(method)) continue;
      out.push({ type: 'call', service, path, method: method as 'GET' | 'POST' | 'PUT' });
    }
  }
  return out;
}

export function parseReminders(raw: string | undefined | null): WeatherReminder[] {
  let data: unknown;
  try {
    data = JSON.parse(raw || '[]');
  } catch {
    return [];
  }
  if (!Array.isArray(data)) return [];
  const out: WeatherReminder[] = [];
  const seen = new Set<string>();
  for (const item of data) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    if (!isKind(row.kind)) continue;
    const rule = cleanReminder({
      id: typeof row.id === 'string' ? row.id : '',
      kind: row.kind,
      title: typeof row.title === 'string' ? row.title : '',
      note: typeof row.note === 'string' ? row.note : undefined,
      people: Array.isArray(row.people) ? row.people.map(String) : [],
      hours: finite(row.hours),
      threshold: finite(row.threshold),
      units: row.units === 'metric' || row.units === 'imperial' ? row.units : undefined,
      location: parseLocation(row.location),
      enabled: row.enabled !== false,
      notify: row.notify !== false,
      actions: parseActions(row.actions),
    });
    if (seen.has(rule.id)) continue;
    seen.add(rule.id);
    out.push(rule);
  }
  return out;
}

export function upsertReminder(list: WeatherReminder[], rule: WeatherReminder): WeatherReminder[] {
  const clean = cleanReminder(rule);
  const index = list.findIndex((r) => r.id === clean.id);
  if (index < 0) return [...list, clean];
  return list.map((r, i) => (i === index ? clean : r));
}

export function removeReminder(list: WeatherReminder[], id: string): WeatherReminder[] {
  return list.filter((r) => r.id !== id);
}

/** A person's own weather preference; null fields follow the household default. */
export interface MyWeatherPrefs {
  location: WeatherLocation | null;
  units: WeatherUnits | null;
  /** Follow where their phone is (falling back to `location`, then the household's). */
  follow?: boolean;
  /** Read only: they follow their phone and it reported recently enough to use. */
  live?: boolean;
}

export interface WeatherSettings {
  reminders: WeatherReminder[];
  location: WeatherLocation | null;
  units: WeatherUnits;
}

export async function loadWeatherSettings(): Promise<WeatherSettings> {
  const settings = await api.getSettings();
  const value = (key: string) => settings.find((s) => s.key === key)?.value;
  const units: WeatherUnits = value(WEATHER_UNITS_SETTING) === 'metric' ? 'metric' : 'imperial';
  return {
    // Shown and edited in the household's units, whatever they were set in.
    reminders: parseReminders(value(WEATHER_REMINDERS_SETTING)).map((r) => inUnits(r, units)),
    location: parseLocation(value(WEATHER_LOCATION_SETTING)),
    units,
  };
}

/** Saves the rules, each stamped with the units its threshold is in. */
export async function saveWeatherReminders(list: WeatherReminder[], units: WeatherUnits): Promise<void> {
  await api.updateSetting(WEATHER_REMINDERS_SETTING, JSON.stringify(list.map((r) => cleanReminder(inUnits(r, units)))));
}

/**
 * Saves the forecast place and units. Changing the units converts the saved
 * reminders' thresholds too, so a 32 °F freeze becomes 0 °C, not 32 °C.
 */
export async function saveWeatherPlace(
  location: WeatherLocation | null,
  units: WeatherUnits,
  reminders: WeatherReminder[] = [],
  previousUnits: WeatherUnits = units,
): Promise<void> {
  await api.updateSetting(WEATHER_LOCATION_SETTING, location ? JSON.stringify(location) : '');
  if (units !== previousUnits && reminders.length) {
    const converted = reminders.map((r) => inUnits({ ...r, units: r.units ?? previousUnits }, units));
    await api.updateSetting(WEATHER_REMINDERS_SETTING, JSON.stringify(converted.map(cleanReminder)));
  }
  await api.updateSetting(WEATHER_UNITS_SETTING, units);
}

// --------------------------------------------------------------------------
// Integrations: sensors tracked against the weather, and hooks to services
// --------------------------------------------------------------------------

export const WEATHER_METRICS_SETTING = 'weather_metrics';
export const WEATHER_HOOKS_SETTING = 'weather_hooks';

export type WeatherMetricMode = 'power' | 'counter' | 'mean' | 'on_time';
export type WeatherEvent = 'forecast.updated' | 'condition.changed' | 'alert.started' | 'day.completed';

export const METRIC_MODES: Record<WeatherMetricMode, { label: string; hint: string }> = {
  power: { label: 'Power (W to kWh)', hint: 'A power sensor in watts; the day is its energy in kWh.' },
  counter: { label: 'Meter (daily increase)', hint: 'A total that only goes up, like a water or gas meter.' },
  mean: { label: 'Reading (daily average)', hint: 'Any number, like barn humidity or a tank level.' },
  on_time: { label: 'Time on (hours)', hint: 'Hours a pump, fan or heater is on or running.' },
};

export const WEATHER_EVENTS: Record<WeatherEvent, string> = {
  'forecast.updated': 'Forecast refreshed',
  'condition.changed': 'Conditions changed (e.g. rain started)',
  'alert.started': 'A weather reminder started',
  'day.completed': 'A day finished (its weather and metrics)',
};

export interface WeatherSensorMetric {
  name: string;
  label: string;
  entity_id: string;
  mode: WeatherMetricMode;
  unit?: string;
  attribute?: string;
  enabled?: boolean;
}

export interface WeatherHook {
  id: string;
  events: WeatherEvent[];
  service: string;
  path: string;
  method?: 'GET' | 'POST' | 'PUT';
  filter?: { kinds?: string[]; rules?: string[] };
  enabled?: boolean;
}

export interface WeatherMetricInfo {
  name: string;
  label: string;
  unit: string;
  agg: string;
  source: 'builtin' | 'ha' | 'pushed';
}

export interface WeatherIntegrations {
  events: WeatherEvent[];
  services: string[];
  metric_modes: WeatherMetricMode[];
  metric_aggs: string[];
  kinds: WeatherReminderKind[];
  stream: string;
  metrics: WeatherMetricInfo[];
}

export interface WeatherMetricAnalysis {
  name: string;
  label: string;
  unit: string;
  source: string;
  days: number;
  mean: number | null;
  fit: { baseline: number; per_degree_day: number; r2: number | null; days: number } | null;
  r_temperature: number | null;
  r_precip: number | null;
  wet_mean: number | null;
  dry_mean: number | null;
  projection: { date: string; value: number }[];
  insights: string[];
}

export interface WeatherCorrelations {
  units: string;
  today: string;
  metrics: WeatherMetricAnalysis[];
  insights: string[];
}

/** A metric name the server accepts, from a label ("Solar panels" -> "solar_panels"). */
export function metricName(label: string): string {
  const slug = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40);
  return /^[a-z]/.test(slug) ? slug : `m_${slug}`.slice(0, 40);
}

const isMode = (v: unknown): v is WeatherMetricMode => typeof v === 'string' && v in METRIC_MODES;
const isEvent = (v: unknown): v is WeatherEvent => typeof v === 'string' && v in WEATHER_EVENTS;

export function parseSensorMetrics(raw: string | undefined | null): WeatherSensorMetric[] {
  let data: unknown;
  try {
    data = JSON.parse(raw || '[]');
  } catch {
    return [];
  }
  if (!Array.isArray(data)) return [];
  const out: WeatherSensorMetric[] = [];
  for (const item of data) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    const name = String(row.name ?? '');
    const entity = String(row.entity_id ?? '').trim();
    if (!/^[a-z][a-z0-9_]{0,39}$/.test(name) || !entity.includes('.') || !isMode(row.mode)) continue;
    out.push({
      name,
      label: String(row.label ?? '') || name,
      entity_id: entity,
      mode: row.mode,
      ...(row.unit ? { unit: String(row.unit) } : {}),
      ...(row.attribute ? { attribute: String(row.attribute) } : {}),
      ...(row.enabled === false ? { enabled: false } : {}),
    });
  }
  return out;
}

export function parseHooks(raw: string | undefined | null): WeatherHook[] {
  let data: unknown;
  try {
    data = JSON.parse(raw || '[]');
  } catch {
    return [];
  }
  if (!Array.isArray(data)) return [];
  const out: WeatherHook[] = [];
  for (const item of data) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    const events = Array.isArray(row.events) ? row.events.filter(isEvent) : [];
    const path = String(row.path ?? '');
    if (!events.length || !row.service || !SERVICE_PATH.test(path)) continue;
    const filter = (row.filter && typeof row.filter === 'object' ? row.filter : {}) as Record<string, unknown>;
    out.push({
      id: String(row.id ?? '') || newReminderId(),
      events,
      service: String(row.service),
      path,
      method: (['GET', 'PUT'].includes(String(row.method)) ? row.method : 'POST') as WeatherHook['method'],
      filter: {
        kinds: Array.isArray(filter.kinds) ? filter.kinds.map(String) : [],
        rules: Array.isArray(filter.rules) ? filter.rules.map(String) : [],
      },
      ...(row.enabled === false ? { enabled: false } : {}),
    });
  }
  return out;
}

export interface WeatherIntegrationSettings {
  metrics: WeatherSensorMetric[];
  hooks: WeatherHook[];
}

export async function loadIntegrationSettings(): Promise<WeatherIntegrationSettings> {
  const settings = await api.getSettings();
  const value = (key: string) => settings.find((s) => s.key === key)?.value;
  return {
    metrics: parseSensorMetrics(value(WEATHER_METRICS_SETTING)),
    hooks: parseHooks(value(WEATHER_HOOKS_SETTING)),
  };
}

export async function saveSensorMetrics(list: WeatherSensorMetric[]): Promise<void> {
  await api.updateSetting(WEATHER_METRICS_SETTING, JSON.stringify(list));
}

export async function saveHooks(list: WeatherHook[]): Promise<void> {
  await api.updateSetting(WEATHER_HOOKS_SETTING, JSON.stringify(list));
}

