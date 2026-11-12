import { isAxiosError } from 'axios';
import toast from 'react-hot-toast';
import { api, apiClient } from '../services/api';

/**
 * The Rooms, Energy, History and Climate pages (services/gateway/dashboard_routes.py,
 * which reads homehub's registry, history and long-term statistics).
 *
 * Nothing here knows a room, a device or a circuit: every page is built from
 * what the hub's registry and the energy settings say at request time.
 */

export type Attrs = Record<string, unknown>;

export interface HubEntity {
  entity_id: string;
  domain: string;
  name: string;
  state: string;
  attributes: Attrs;
  device_class: string | null;
  unit: string | null;
  icon: string | null;
  area_id: string | null;
  device_id: string | null;
  last_changed: string;
  /** What it can do, from the hub's capability index (homehub capabilities.py); absent from older hubs. */
  abilities?: string[];
}

export interface RoomArea {
  area_id: string;
  name: string;
  floor_id: string | null;
  icon: string | null;
  entities: HubEntity[];
}

export interface Floor { floor_id: string; name?: string; level?: number | null }

export interface RoomsResponse { floors: Floor[]; areas: RoomArea[]; unassigned: HubEntity[] }

export interface Thermostat extends HubEntity {
  area_name: string | null;
  power: HubEntity[];
  power_source: string;
  sensors: HubEntity[];
}

export interface HistoryPoint { t: string; s: string; a?: Attrs }
export type HistorySeries = Record<string, HistoryPoint[]>;

export interface PickerEntity {
  entity_id: string;
  name: string;
  domain: string;
  unit: string | null;
  device_class: string | null;
  state: string;
}

export interface StatRow {
  start: number;
  end: number;
  mean?: number | null;
  min?: number | null;
  max?: number | null;
  change?: number | null;
  sum?: number | null;
  state?: number | null;
}

export interface SeriesPoint { start: string; value: number }

export interface EnergyDevice {
  name: string;
  stat: string;
  kwh: number;
  series: SeriesPoint[];
  power_entity: string | null;
  power_w: number | null;
}

export interface UtilitySummary {
  name: string;
  stat: string;
  kwh: number;
  series: SeriesPoint[];
  cost: number | null;
  cost_series?: SeriesPoint[];
  periods: { name: string; stat: string; kwh: number }[];
  data_until: string | null;
  rate: number | null;
}

export type CostSource = 'statistic' | 'fixed_price' | 'tariff' | 'flat_price' | null;

export interface EnergySummary {
  start: string;
  end: string;
  period: string;
  currency: string | null;
  time_zone?: string;
  grid: {
    kwh: number;
    series: SeriesPoint[];
    returned_kwh: number;
    cost: number | null;
    cost_source: CostSource;
    power_w: number | null;
    stats: string[];
  };
  solar: null | { kwh: number; series: SeriesPoint[] };
  tou: { source: 'tariff' | null; periods: { name: string; kwh: number; cost: number | null }[] };
  devices: EnergyDevice[];
  untracked_kwh: number | null;
  utility: UtilitySummary[];
}

export interface TariffPeriod {
  name: string;
  price: number | null;
  months: number[];
  weekdays: number[];
  start: string;
  end: string;
}

export interface Tariff {
  currency: string | null;
  flat_price: number | null;
  periods: TariffPeriod[];
  default_period: { name: string; price: number | null };
}

export interface EnergySource {
  type: 'grid' | 'solar' | 'battery' | 'gas' | 'water';
  stat_energy_from?: string | null;
  stat_energy_to?: string | null;
  stat_cost?: string | null;
  number_energy_price?: number | null;
  [key: string]: unknown;
}

export interface DeviceConsumption {
  stat_consumption: string;
  name?: string | null;
  stat_rate?: string | null;
  included_in_stat?: string | null;
}

export interface UtilitySource {
  name: string;
  stat_energy: string;
  stat_cost?: string | null;
  stat_periods?: Record<string, string> | null;
}

export interface EnergyPrefs {
  energy_sources: EnergySource[];
  device_consumption: DeviceConsumption[];
  device_consumption_water?: unknown[];
  utility_sources: UtilitySource[];
  tariff: Tariff;
}

export interface EnergyCandidate {
  kind: 'utility' | 'sensor';
  name: string | null;
  stat_energy: string;
  stat_cost?: string | null;
  stat_periods?: Record<string, string> | null;
}

export interface TariffSuggestion { periods: TariffPeriod[]; months_seen: number[]; hours_compared: number }

export interface DashboardPrefs { climate_power: Record<string, string[]>; hidden: string[] }

export type EnergyRange = 'day' | 'week' | 'month' | 'year';

// -- API ---------------------------------------------------------------------------
export async function getRooms(): Promise<RoomsResponse> {
  return (await apiClient.get('/api/dashboard/rooms')).data;
}

export async function getClimate(): Promise<{ thermostats: Thermostat[] }> {
  return (await apiClient.get('/api/dashboard/climate')).data;
}

export async function getHistory(entityIds: string[], start: Date, end: Date, attributes = false): Promise<HistorySeries> {
  const params: Record<string, string> = {
    entity_ids: entityIds.join(','), start: start.toISOString(), end: end.toISOString(),
  };
  if (attributes) params.attributes = '1';
  return ((await apiClient.get('/api/dashboard/history', { params })).data?.series ?? {}) as HistorySeries;
}

export async function getEntities(domains?: string[]): Promise<PickerEntity[]> {
  const params = domains?.length ? { domains: domains.join(',') } : undefined;
  return (await apiClient.get('/api/dashboard/entities', { params })).data?.entities ?? [];
}

export async function getStatistics(ids: string[], start: Date, end: Date, period: string,
  types: string[], units?: string): Promise<Record<string, StatRow[]>> {
  const params: Record<string, string> = {
    statistic_ids: ids.join(','), start: start.toISOString(), end: end.toISOString(), period, types: types.join(','),
  };
  if (units) params.units = units;
  return (await apiClient.get('/api/dashboard/statistics', { params })).data ?? {};
}

export async function getEnergySummary(range: EnergyRange, date: Date): Promise<EnergySummary> {
  return (await apiClient.get('/api/dashboard/energy/summary', { params: { range, date: date.toISOString() } })).data;
}

export async function getEnergyPrefs(): Promise<EnergyPrefs> {
  return (await apiClient.get('/api/dashboard/energy/prefs')).data;
}

export async function saveEnergyPrefs(prefs: Partial<EnergyPrefs>): Promise<EnergyPrefs> {
  return (await apiClient.put('/api/dashboard/energy/prefs', prefs)).data;
}

export async function getEnergyCandidates(): Promise<EnergyCandidate[]> {
  return (await apiClient.get('/api/dashboard/energy/candidates')).data ?? [];
}

export async function suggestTariff(days?: number): Promise<TariffSuggestion> {
  return (await apiClient.post('/api/dashboard/energy/tariff/suggest', days ? { days } : {})).data;
}

export async function importEnergyFromHa(): Promise<Record<string, unknown>> {
  return (await apiClient.post('/api/dashboard/energy/import', {})).data;
}

export interface StatTableSpan { rows: number; first: string | null; last: string | null }

export interface StatisticsImportReport {
  source?: string;
  metadata?: number;
  statistics?: Record<string, StatTableSpan & { statistics?: number; rebased?: number }>;
  imported_at?: string;
}

export interface StatisticsStatus {
  metadata: number;
  statistics: StatTableSpan;
  statistics_short_term: StatTableSpan;
  compiled_until?: unknown;
  last_import: StatisticsImportReport | null;
  last_error: string | null;
}

/** Admins: what the hub's long-term statistics hold and when they were last imported. */
export async function getStatisticsStatus(): Promise<StatisticsStatus> {
  return (await apiClient.get('/api/dashboard/statistics/status')).data;
}

/** Admins: copy the long-term statistics from the old Home Assistant recorder. Slow (minutes). */
export async function importStatistics(): Promise<StatisticsImportReport> {
  return (await apiClient.post('/api/dashboard/statistics/import', {}, { timeout: 900_000 })).data;
}

/** "Mar 2024 to today" for a statistics table, or "" when it is empty. */
export function statSpanText(span: StatTableSpan | null | undefined, now: Date = new Date()): string {
  if (!span || !span.rows || !span.first) return '';
  const fmt = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', year: 'numeric' });
  const last = span.last ? new Date(span.last) : null;
  const recent = last && now.getTime() - last.getTime() < 36 * 3600 * 1000;
  return `${fmt(span.first)} to ${recent || !span.last ? 'today' : fmt(span.last)}`;
}

export async function getDashboardPrefs(): Promise<DashboardPrefs> {
  return (await apiClient.get('/api/dashboard/prefs')).data;
}

export async function saveDashboardPrefs(prefs: Partial<DashboardPrefs>): Promise<DashboardPrefs> {
  return (await apiClient.put('/api/dashboard/prefs', prefs)).data;
}

/** Run a service; on failure say so (the execution service's words) and let the next poll correct the tile. */
export async function runService(domain: string, service: string, entityId: string,
  data?: Record<string, unknown>): Promise<boolean> {
  try {
    await api.callHaService(domain, service, entityId, data ?? null);
    return true;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : `${domain}.${service} failed`);
    return false;
  }
}

/** Watts from a power reading in whatever unit the sensor uses. */
export function toWatts(value: number | null, unit: string | null | undefined): number | null {
  if (value == null) return null;
  if (unit === 'kW') return value * 1000;
  if (unit === 'MW') return value * 1e6;
  return value;
}

/** The gateway's own words for a failure ("Admin required", a validation message), else a fallback. */
export function failureText(err: unknown, fallback = 'Something went wrong'): string {
  if (isAxiosError(err)) {
    const detail = err.response?.data?.detail ?? err.response?.data?.message;
    if (typeof detail === 'string' && detail) return detail;
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

// -- rooms ------------------------------------------------------------------------------
/** The order a room card lists its things: what people touch first, then what they glance at. */
export const DOMAIN_ORDER = [
  'climate', 'light', 'switch', 'fan', 'media_player', 'remote', 'cover', 'lock', 'vacuum', 'humidifier',
  'water_heater', 'valve', 'siren', 'scene', 'camera', 'binary_sensor', 'sensor',
];

export const DOMAIN_LABELS: Record<string, string> = {
  climate: 'Climate', light: 'Lights', switch: 'Switches', fan: 'Fans', media_player: 'Media', remote: 'Remotes',
  cover: 'Covers', lock: 'Locks', vacuum: 'Vacuums', humidifier: 'Humidifiers', water_heater: 'Water heaters',
  valve: 'Valves', siren: 'Sirens', scene: 'Scenes', camera: 'Cameras', binary_sensor: 'Status', sensor: 'Sensors',
};

export interface DomainGroup { domain: string; label: string; entities: HubEntity[] }

/** A room's entities grouped by domain, in DOMAIN_ORDER (unknown domains last, alphabetically). */
export function groupByDomain(entities: HubEntity[]): DomainGroup[] {
  const byDomain = new Map<string, HubEntity[]>();
  for (const e of entities) {
    const list = byDomain.get(e.domain) ?? [];
    list.push(e);
    byDomain.set(e.domain, list);
  }
  const rank = (d: string) => {
    const i = DOMAIN_ORDER.indexOf(d);
    return i === -1 ? DOMAIN_ORDER.length : i;
  };
  return [...byDomain.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([domain, list]) => ({
      domain,
      label: DOMAIN_LABELS[domain] ?? domain.replace(/_/g, ' '),
      entities: [...list].sort((x, y) => x.name.localeCompare(y.name)),
    }));
}

/** Areas grouped under their floor (registry order by level), or one unnamed group when there are no floors. */
export function groupByFloor(rooms: RoomsResponse): { floor: Floor | null; areas: RoomArea[] }[] {
  const areas = rooms.areas.filter((a) => a.entities.length > 0);
  if (!rooms.floors?.length) return [{ floor: null, areas }];
  const floors = [...rooms.floors].sort((a, b) => (a.level ?? 0) - (b.level ?? 0)
    || (a.name ?? '').localeCompare(b.name ?? ''));
  const out: { floor: Floor | null; areas: RoomArea[] }[] = floors
    .map((floor) => ({ floor, areas: areas.filter((a) => a.floor_id === floor.floor_id) }))
    .filter((g) => g.areas.length > 0);
  const known = new Set(floors.map((f) => f.floor_id));
  const loose = areas.filter((a) => !a.floor_id || !known.has(a.floor_id));
  if (loose.length) out.push({ floor: null, areas: loose });
  return out;
}

export const isUnavailable = (state: string | undefined) => state === 'unavailable' || state === 'unknown';

/** Is the entity "on" in the sense its toggle shows? */
export function isActive(e: HubEntity): boolean {
  if (e.domain === 'lock') return e.state === 'unlocked';
  if (e.domain === 'cover') return e.state === 'open' || e.state === 'opening';
  if (e.domain === 'media_player') return e.state === 'playing';
  if (e.domain === 'vacuum') return e.state === 'cleaning';
  return e.state === 'on';
}

/** Light brightness as a 0-100 percentage, or null when the light has none (off or not dimmable). */
export function brightnessPct(e: HubEntity): number | null {
  const b = e.attributes?.brightness;
  if (typeof b !== 'number') return null;
  return Math.round((b / 255) * 100);
}

/**
 * Whether an entity can do something, by the hub's capability index. Controls are drawn from
 * this. An entity from a hub without the index (no `abilities`) gets `fallback`, which callers
 * set from the state's own attributes.
 */
export function can(e: Pick<HubEntity, 'abilities'>, ability: string, fallback = true): boolean {
  return Array.isArray(e.abilities) ? e.abilities.includes(ability) : fallback;
}

export function supportsBrightness(e: HubEntity): boolean {
  if (Array.isArray(e.abilities)) return e.abilities.includes('dim');
  const modes = e.attributes?.supported_color_modes;
  if (Array.isArray(modes)) return modes.some((m) => m !== 'onoff');
  return typeof e.attributes?.brightness === 'number';
}

/** "21.3 °F" for a sensor; states that are not numbers pass through. */
export function formatState(e: Pick<HubEntity, 'state' | 'unit'>): string {
  if (isUnavailable(e.state)) return 'Unavailable';
  const n = Number(e.state);
  if (e.state !== '' && Number.isFinite(n)) {
    const rounded = Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10;
    return e.unit ? `${rounded} ${e.unit}` : String(rounded);
  }
  return e.state.replace(/_/g, ' ');
}

/** A binary sensor's state in words its device class implies ("Open", "Motion", "Clear"). */
export function binaryLabel(e: HubEntity): string {
  if (isUnavailable(e.state)) return 'Unavailable';
  const on = e.state === 'on';
  switch (e.device_class) {
    case 'door': case 'garage_door': case 'window': case 'opening': return on ? 'Open' : 'Closed';
    case 'motion': case 'occupancy': case 'presence': return on ? 'Detected' : 'Clear';
    case 'moisture': return on ? 'Wet' : 'Dry';
    case 'lock': return on ? 'Unlocked' : 'Locked';
    case 'smoke': case 'gas': case 'carbon_monoxide': case 'problem': case 'safety': case 'tamper':
      return on ? 'Alert' : 'OK';
    default: return on ? 'On' : 'Off';
  }
}

// -- history ----------------------------------------------------------------------------
export const MAX_HISTORY_ENTITIES = 12;

export const HISTORY_PRESETS = [
  { key: '6h', label: '6 h', hours: 6 },
  { key: '24h', label: '24 h', hours: 24 },
  { key: '7d', label: '7 d', hours: 24 * 7 },
  { key: '30d', label: '30 d', hours: 24 * 30 },
] as const;

/** Whether every known state of a series is a number (unavailable gaps allowed). */
export function isNumericSeries(points: HistoryPoint[]): boolean {
  const known = points.filter((p) => !isUnavailable(p.s));
  return known.length > 0 && known.every((p) => p.s.trim() !== '' && Number.isFinite(Number(p.s)));
}

export interface ChartRow { t: number; [series: string]: number | null }

/**
 * Line-chart rows for several entities, merged on time. Each series holds
 * its value until it next changes (history only records changes), and an
 * unavailable state breaks the line rather than drawing to zero.
 */
export function mergeNumericSeries(series: Record<string, { t: number; v: number | null }[]>, endMs?: number): ChartRow[] {
  const times = new Set<number>();
  for (const pts of Object.values(series)) for (const p of pts) times.add(p.t);
  if (endMs) times.add(endMs);
  const sorted = [...times].sort((a, b) => a - b);
  const keys = Object.keys(series);
  const idx: Record<string, number> = Object.fromEntries(keys.map((k) => [k, 0]));
  const last: Record<string, number | null | undefined> = {};
  return sorted.map((t) => {
    const row: ChartRow = { t };
    for (const k of keys) {
      const pts = series[k];
      while (idx[k] < pts.length && pts[idx[k]].t <= t) {
        last[k] = pts[idx[k]].v;
        idx[k] += 1;
      }
      row[k] = last[k] === undefined ? null : (last[k] as number | null);
    }
    return row;
  });
}

/** History points as numbers (null for unavailable), optionally read from an attribute. */
export function numericPoints(points: HistoryPoint[], attribute?: string): { t: number; v: number | null }[] {
  return points.map((p) => {
    const raw = attribute ? p.a?.[attribute] : p.s;
    const n = typeof raw === 'number' ? raw : typeof raw === 'string' && raw.trim() !== '' ? Number(raw) : NaN;
    return { t: Date.parse(p.t), v: Number.isFinite(n) && !(attribute ? false : isUnavailable(p.s)) ? n : null };
  });
}

export interface Segment { state: string; from: number; to: number }

/** A non-numeric entity's timeline: one segment per state, clipped to the range. */
export function timelineSegments(points: HistoryPoint[], startMs: number, endMs: number): Segment[] {
  const out: Segment[] = [];
  const sorted = [...points].sort((a, b) => Date.parse(a.t) - Date.parse(b.t));
  sorted.forEach((p, i) => {
    const from = Math.max(startMs, Date.parse(p.t));
    const to = Math.min(endMs, i + 1 < sorted.length ? Date.parse(sorted[i + 1].t) : endMs);
    if (to <= from) return;
    const prev = out[out.length - 1];
    if (prev && prev.state === p.s && prev.to === from) prev.to = to;
    else out.push({ state: p.s, from, to });
  });
  return out;
}

/** A stable colour per state value, so "on" is the same colour in every row. */
const STATE_COLOURS: Record<string, string> = {
  on: '#f59e0b', off: '#334155', unavailable: '#7f1d1d', unknown: '#475569', home: '#10b981',
  not_home: '#64748b', open: '#f97316', closed: '#334155', playing: '#8b5cf6', paused: '#6366f1',
  idle: '#475569', heat: '#fb923c', cool: '#22d3ee', heating: '#fb923c', cooling: '#22d3ee',
};
const PALETTE = ['#38bdf8', '#a78bfa', '#34d399', '#fbbf24', '#f472b6', '#60a5fa', '#f87171', '#2dd4bf'];

export function stateColour(state: string): string {
  if (STATE_COLOURS[state]) return STATE_COLOURS[state];
  let h = 0;
  for (const ch of state) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export const seriesColour = (i: number) => PALETTE[i % PALETTE.length];

/** The range a History URL asks for: a preset (`r=24h`) or a custom `from`/`to`. */
export function rangeFromParams(params: URLSearchParams, now: Date = new Date()): { start: Date; end: Date; key: string } {
  const from = params.get('from');
  const to = params.get('to');
  if (from && to) {
    const s = new Date(from);
    const e = new Date(to);
    if (!Number.isNaN(s.getTime()) && !Number.isNaN(e.getTime()) && e > s) return { start: s, end: e, key: 'custom' };
  }
  const preset = HISTORY_PRESETS.find((p) => p.key === params.get('r')) ?? HISTORY_PRESETS[1];
  return { start: new Date(now.getTime() - preset.hours * 3600 * 1000), end: now, key: preset.key };
}

/** Entity ids from the URL (`?e=a,b`), cleaned and capped. */
export function parseEntityParam(raw: string | null): string[] {
  if (!raw) return [];
  const ids = raw.split(',').map((x) => x.trim()).filter((x) => /^[a-z0-9_]+\.[a-z0-9_]+$/.test(x));
  return [...new Set(ids)].slice(0, MAX_HISTORY_ENTITIES);
}

// -- energy ---------------------------------------------------------------------------------
export const RANGES: { key: EnergyRange; label: string }[] = [
  { key: 'day', label: 'Day' }, { key: 'week', label: 'Week' }, { key: 'month', label: 'Month' },
  { key: 'year', label: 'Year' },
];

/** The same range one step earlier or later. */
export function shiftDate(range: EnergyRange, date: Date, dir: -1 | 1): Date {
  const d = new Date(date);
  if (range === 'day') d.setDate(d.getDate() + dir);
  else if (range === 'week') d.setDate(d.getDate() + 7 * dir);
  else if (range === 'month') d.setMonth(d.getMonth() + dir, 1);
  else d.setFullYear(d.getFullYear() + dir, 0, 1);
  return d;
}

export function rangeTitle(range: EnergyRange, date: Date): string {
  if (range === 'day') return date.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  if (range === 'week') {
    const start = new Date(date);
    start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    return `Week of ${start.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })}`;
  }
  if (range === 'month') return date.toLocaleDateString([], { month: 'long', year: 'numeric' });
  return String(date.getFullYear());
}

/** Is the range the one containing now (so "next" makes no sense)? */
export function isCurrentRange(range: EnergyRange, date: Date, now: Date = new Date()): boolean {
  return shiftDate(range, date, 1) > now && date <= now;
}

export function formatKwh(kwh: number | null | undefined): string {
  if (kwh == null || !Number.isFinite(kwh)) return '--';
  if (Math.abs(kwh) >= 1000) return `${(kwh / 1000).toFixed(2)} MWh`;
  return `${kwh >= 100 ? Math.round(kwh) : kwh.toFixed(1)} kWh`;
}

export function formatMoney(value: number | null | undefined, currency: string | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '--';
  if (currency) {
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value);
    } catch {
      // An unknown currency code: fall through to the plain number.
    }
  }
  return value.toFixed(2);
}

export function formatPower(w: number | null | undefined): string {
  if (w == null || !Number.isFinite(w)) return '--';
  return Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(2)} kW` : `${Math.round(w)} W`;
}

export const COST_SOURCE_LABEL: Record<string, string> = {
  statistic: 'from the cost statistic',
  fixed_price: 'at the grid source\'s price',
  tariff: 'at the time-of-use rates',
  flat_price: 'at the flat rate',
};

/** `on_peak` -> "On peak". */
export function periodLabel(name: string): string {
  const words = name.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A bucket's axis label for the summary's period (hour, day or month). */
export function bucketLabel(iso: string, period: string): string {
  const d = new Date(iso);
  if (period === 'hour') return d.toLocaleTimeString([], { hour: 'numeric' });
  if (period === 'month') return d.toLocaleDateString([], { month: 'short' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export interface TouSplit { source: 'tariff' | 'utility'; label: string; periods: { name: string; kwh: number; cost: number | null }[] }

/** The on-peak / off-peak splits the summary can offer: the tariff's, then each utility's own. */
export function touSplits(summary: EnergySummary): TouSplit[] {
  const out: TouSplit[] = [];
  if (summary.tou?.periods?.length) {
    out.push({ source: 'tariff', label: 'Whole house, by your rate periods', periods: summary.tou.periods });
  }
  for (const u of summary.utility ?? []) {
    if (u.periods?.some((p) => p.kwh > 0)) {
      out.push({ source: 'utility', label: `${u.name}, as billed`,
        periods: u.periods.map((p) => ({ name: periodLabel(p.name), kwh: p.kwh, cost: null })) });
    }
  }
  return out;
}

export const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const HHMM = /^([01]\d|2[0-4]):([0-5]\d)$/;

/** What is wrong with a tariff period as entered, or null when it is fine (mirrors the hub's checks). */
export function tariffPeriodError(p: TariffPeriod): string | null {
  if (!p.name?.trim()) return 'Give the period a name';
  for (const [label, v] of [['Start', p.start], ['End', p.end]] as const) {
    const m = HHMM.exec(v ?? '');
    if (!m || (m[1] === '24' && m[2] !== '00')) return `${label} must be a time between 00:00 and 24:00`;
  }
  if (p.start === p.end) return 'Start and end must differ';
  if (p.price != null && (!Number.isFinite(p.price) || p.price < 0)) return 'Price must be a positive number';
  if (p.months.some((m) => m < 1 || m > 12)) return 'Months run 1 to 12';
  if (p.weekdays.some((d) => d < 0 || d > 6)) return 'Weekdays run Monday (0) to Sunday (6)';
  return null;
}

/** Toggle a value in a sorted number list (month or weekday chips). */
export function toggleIn(list: number[], value: number): number[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value].sort((a, b) => a - b);
}

/** "Mon-Fri, May-Oct, 14:00-20:00" for a period, from its own data. */
export function describePeriod(p: TariffPeriod): string {
  const span = (vals: number[], names: string[], offset: number) => {
    if (!vals.length) return '';
    const runs: string[] = [];
    let s = vals[0];
    let prev = vals[0];
    for (const v of [...vals.slice(1), Number.NaN]) {
      if (v === prev + 1) { prev = v; continue; }
      runs.push(s === prev ? names[s - offset] : `${names[s - offset]}-${names[prev - offset]}`);
      s = v;
      prev = v;
    }
    return runs.join(', ');
  };
  const days = span(p.weekdays, WEEKDAY_NAMES, 0) || 'Every day';
  const months = span(p.months, MONTH_NAMES, 1) || 'all year';
  return `${days}, ${months}, ${p.start}-${p.end}`;
}

export const emptyPeriod = (): TariffPeriod => ({
  name: '', price: null, months: [], weekdays: [0, 1, 2, 3, 4], start: '00:00', end: '01:00',
});
