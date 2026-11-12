import { apiClient } from '../services/api';
import { modeVisual } from './climate';

/**
 * Thermostat programs, hold/resume, away and energy settings, and the generic
 * scheduler (the Schedules page), all on the device hub through the gateway
 * (services/gateway/automation_routes.py).
 *
 * Every thermostat, program, person and power sensor comes from the API at
 * runtime; nothing here knows a household's rooms or rate windows. The pure
 * helpers (weekday expansion, slot normalisation, timeline geometry, hold
 * options) are what the tests pin.
 */

// --------------------------------------------------------------------------- types

export interface Setpoint {
  hvac_mode: string;
  temperature?: number;
  target_temp_low?: number;
  target_temp_high?: number;
  fan_mode?: string;
}

export interface ProgramSlot extends Setpoint {
  start: string;
  stop?: string | null;
}

export interface Program {
  id: string;
  entity_id?: string;
  name: string;
  entity_ids: string[];
  weekdays: string[];
  start_date: string | null;
  end_date: string | null;
  enabled: boolean;
  repeat_type?: string;
  slots: ProgramSlot[];
  editable?: boolean;
  reason?: string | null;
  next_trigger?: string | null;
}

export interface ThermostatCapabilities {
  hvac_modes: string[];
  fan_modes: string[];
  min_temp?: number;
  max_temp?: number;
  range?: boolean;
  unit?: string;
}

export interface Thermostat {
  entity_id: string;
  name: string;
  area?: string | null;
  state: string;
  current_temperature?: number | null;
  temperature?: number | null;
  target_temp_low?: number | null;
  target_temp_high?: number | null;
  fan_mode?: string | null;
  hvac_action?: string | null;
  capabilities: ThermostatCapabilities;
  current: { program_id: string; program_name: string; slot: number; start: string; end: string | null; setpoint: Setpoint } | null;
  next: { program_id: string; program_name: string; slot: number; at: string; setpoint: Setpoint } | null;
  hold: { setpoint: Setpoint; until: string | null; kind: 'until' | 'next_slot' | 'permanent'; set_at: string; by: string | null } | null;
  effective: { source: 'hold' | 'away' | 'energy' | 'schedule' | 'none'; setpoint: Setpoint | null };
  summary: string;
  schedule_entity?: string;
}

export interface EnergyWindow {
  name: string;
  months: number[];
  weekdays: string[];
  start: string;
  stop: string;
  limit_kw: number | null;
}

export interface ClimateSettings {
  away: {
    enabled: boolean;
    start: string | null;
    end: string | null;
    auto: boolean;
    persons: string[];
    setpoint: Setpoint;
    per_entity: Record<string, Setpoint>;
    /** Thermostats away applies to; empty means all. */
    thermostats?: string[];
  };
  energy: {
    enabled: boolean;
    power_sensor: string | null;
    windows: EnergyWindow[];
    shed_after_min: number;
    resume_below_kw: number | null;
    comfort_max_temp: number | null;
    comfort_setpoint: Setpoint | null;
    thermostats: string[];
    /** Per-thermostat override of comfort_max_temp. */
    comfort_max_temp_by_entity?: Record<string, number>;
  };
}

export interface ClimateOverview {
  thermostats: Thermostat[];
  programs: Program[];
  settings: ClimateSettings;
  away: { active: boolean; reason: string | null };
  energy: { in_window: boolean; window: string | null; power_kw: number | null; shed: string[]; comforted?: string[] };
  can_edit: boolean;
  now: string;
  time_zone: string;
}

export interface ScheduleCondition {
  entity_id: string;
  /** The attribute compared; "state" when absent. */
  attribute?: string;
  match_type: 'is' | 'not' | 'below' | 'above';
  value: string | number;
}

export interface ScheduleAction {
  service: string;
  entity_id?: string;
  service_data?: Record<string, unknown>;
}

export interface Timeslot {
  start: string;
  stop: string | null;
  conditions: ScheduleCondition[];
  condition_type: 'and' | 'or' | null;
  track_conditions: boolean;
  actions: ScheduleAction[];
}

export interface Schedule {
  schedule_id: string;
  name: string;
  entity_id?: string;
  enabled: boolean;
  weekdays: string[];
  start_date: string | null;
  end_date: string | null;
  repeat_type: 'repeat' | 'pause' | 'single';
  timeslots: Timeslot[];
  tags: string[];
  next_trigger?: string | null;
  next_slot?: number | null;
  current_slot?: number | null;
  last_run?: string | null;
  last_error?: string | null;
  unsupported_reason?: string | null;
  climate?: boolean;
}

export type HoldUntil = 'next_slot' | 'permanent' | string;

// --------------------------------------------------------------------------- api

const CLIMATE = '/api/homehub/climate';
const SCHEDULES = '/api/homehub/schedules';

export async function getClimate(): Promise<ClimateOverview> {
  return (await apiClient.get(CLIMATE)).data;
}

export async function holdThermostat(entityId: string, setpoint: Setpoint, until: HoldUntil): Promise<Thermostat> {
  return (await apiClient.post(`${CLIMATE}/hold`, { entity_id: entityId, setpoint, until })).data;
}

export async function resumeThermostat(entityId: string): Promise<Thermostat> {
  return (await apiClient.post(`${CLIMATE}/resume`, { entity_id: entityId })).data;
}

export async function saveProgram(program: Partial<Program>): Promise<Program> {
  const body = { ...program, slots: normaliseSlots(program.slots ?? []) };
  if (program.id) return (await apiClient.put(`${CLIMATE}/programs/${encodeURIComponent(program.id)}`, body)).data;
  return (await apiClient.post(`${CLIMATE}/programs`, body)).data;
}

export async function deleteProgram(id: string): Promise<void> {
  await apiClient.delete(`${CLIMATE}/programs/${encodeURIComponent(id)}`);
}

export async function saveClimateSettings(settings: ClimateSettings): Promise<ClimateSettings> {
  return (await apiClient.put(`${CLIMATE}/settings`, settings)).data;
}

export async function listSchedules(): Promise<{ schedules: Schedule[]; can_edit: boolean }> {
  return (await apiClient.get(SCHEDULES)).data;
}

export async function saveSchedule(schedule: Partial<Schedule>): Promise<Schedule> {
  if (schedule.schedule_id) {
    return (await apiClient.put(`${SCHEDULES}/${encodeURIComponent(schedule.schedule_id)}`, schedule)).data;
  }
  return (await apiClient.post(SCHEDULES, schedule)).data;
}

export async function deleteSchedule(id: string): Promise<void> {
  await apiClient.delete(`${SCHEDULES}/${encodeURIComponent(id)}`);
}

export async function setScheduleEnabled(id: string, enabled: boolean): Promise<Schedule> {
  return (await apiClient.post(`${SCHEDULES}/${encodeURIComponent(id)}/enabled`, { enabled })).data;
}

// --------------------------------------------------------------------------- weekdays

export const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
export type Weekday = typeof WEEKDAYS[number];

export const WEEKDAY_LABELS: Record<string, string> = {
  mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun',
  daily: 'Every day', workday: 'Workdays', weekend: 'Weekend',
};

export const WEEKDAY_GROUPS = ['daily', 'workday', 'weekend'] as const;

/**
 * The days a weekday list covers. `workday` is taken as Monday to Friday and
 * `weekend` as Saturday and Sunday (the scheduler's defaults when no workday
 * sensor says otherwise).
 */
export function expandWeekdays(list: string[] | null | undefined): Set<Weekday> {
  const out = new Set<Weekday>();
  for (const raw of list ?? []) {
    const day = String(raw).toLowerCase();
    if (day === 'daily') WEEKDAYS.forEach((d) => out.add(d));
    else if (day === 'workday') (['mon', 'tue', 'wed', 'thu', 'fri'] as Weekday[]).forEach((d) => out.add(d));
    else if (day === 'weekend') { out.add('sat'); out.add('sun'); }
    else if ((WEEKDAYS as readonly string[]).includes(day)) out.add(day as Weekday);
  }
  return out;
}

/** "Every day", "Workdays", "Mon, Wed, Fri". */
export function weekdaysText(list: string[] | null | undefined): string {
  const days = expandWeekdays(list);
  if (days.size === 7) return 'Every day';
  if (!days.size) return 'No days';
  const key = WEEKDAYS.filter((d) => days.has(d)).join(',');
  if (key === 'mon,tue,wed,thu,fri') return 'Workdays';
  if (key === 'sat,sun') return 'Weekend';
  return WEEKDAYS.filter((d) => days.has(d)).map((d) => WEEKDAY_LABELS[d]).join(', ');
}

/**
 * Tap a chip. A group chip (daily/workday/weekend) replaces the list; a day
 * chip turns a group into its days first, so tapping Wed off "Workdays"
 * leaves Mon, Tue, Thu, Fri.
 */
export function toggleWeekday(list: string[], chip: string): string[] {
  if ((WEEKDAY_GROUPS as readonly string[]).includes(chip)) return list.length === 1 && list[0] === chip ? [] : [chip];
  const days = expandWeekdays(list);
  if (days.has(chip as Weekday)) days.delete(chip as Weekday);
  else days.add(chip as Weekday);
  return WEEKDAYS.filter((d) => days.has(d));
}

/** Monday-first index of a JS date's weekday. */
export function weekdayOf(date: Date): Weekday {
  return WEEKDAYS[(date.getDay() + 6) % 7];
}

// --------------------------------------------------------------------------- times

/** Minutes after midnight for `HH:MM` or `HH:MM:SS`; null for anything else. */
export function parseTime(value: string | null | undefined): number | null {
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(String(value ?? '').trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  const s = Number(m[3] ?? 0);
  if (h > 24 || min > 59 || s > 59 || (h === 24 && (min || s))) return null;
  return h * 60 + min + s / 60;
}

/** `HH:MM:SS` for minutes after midnight, wrapped into one day. */
export function formatTime(minutes: number): string {
  const total = ((Math.round(minutes * 60) % 86400) + 86400) % 86400;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

/** `HH:MM` for an `<input type="time">`. */
export function toTimeInput(value: string | null | undefined): string {
  const minutes = parseTime(value);
  return minutes === null ? '' : formatTime(minutes).slice(0, 5);
}

/** "7:30 AM" in the viewer's locale. */
export function clockText(value: string | null | undefined): string {
  const minutes = parseTime(value);
  if (minutes === null) return String(value ?? '');
  const date = new Date(2000, 0, 1, Math.floor(minutes / 60), Math.round(minutes % 60));
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/** An ISO moment as "Tue 2:00 PM" (or "2:00 PM" today). */
export function momentText(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay ? time : `${date.toLocaleDateString([], { weekday: 'short' })} ${time}`;
}

// --------------------------------------------------------------------------- slots and the timeline

/**
 * Sort a program's slots by start and give each a stop at the next slot's
 * start; the last one stops at the first one's start the next day (the hub's
 * rule). Slots without a readable start are dropped.
 */
export function normaliseSlots<T extends { start: string; stop?: string | null }>(slots: T[]): T[] {
  const valid = slots
    .map((slot) => ({ slot, at: parseTime(slot.start) }))
    .filter((row): row is { slot: T; at: number } => row.at !== null)
    .sort((a, b) => a.at - b.at);
  return valid.map((row, i) => ({
    ...row.slot,
    start: formatTime(row.at),
    stop: formatTime(valid[(i + 1) % valid.length].at),
  }));
}

export interface Segment {
  /** Index into the normalised slots. */
  slot: number;
  /** Minutes after midnight. */
  start: number;
  end: number;
  /** Percent of the day, for absolute positioning. */
  left: number;
  width: number;
  /** The early hours carried over from the previous day's last slot. */
  carried: boolean;
}

const DAY = 1440;

/**
 * Where each slot sits on one day's bar. Every slot runs to the next start, so
 * the last slot crosses midnight: its tail fills the start of the day (marked
 * `carried`) and its head runs to the end. One slot alone covers the whole day.
 */
export function daySegments(slots: { start: string }[]): Segment[] {
  const starts = slots.map((s) => parseTime(s.start)).filter((m): m is number => m !== null);
  if (!starts.length) return [];
  const sorted = starts.map((at, slot) => ({ at, slot })).sort((a, b) => a.at - b.at);
  const seg = (slot: number, start: number, end: number, carried = false): Segment => ({
    slot, start, end, carried,
    left: (start / DAY) * 100,
    width: ((end - start) / DAY) * 100,
  });
  const out: Segment[] = [];
  const last = sorted[sorted.length - 1];
  if (sorted[0].at > 0) out.push(seg(last.slot, 0, sorted[0].at, true));
  sorted.forEach((row, i) => {
    const end = i + 1 < sorted.length ? sorted[i + 1].at : DAY;
    if (end > row.at) out.push(seg(row.slot, row.at, end));
  });
  return out;
}

/** Local `YYYY-MM-DD`. */
export function isoDate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** The dates of this week, Monday first, around `now`. */
export function weekDates(now: Date = new Date()): Date[] {
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
  return WEEKDAYS.map((_, i) => new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i));
}

/** Does this program run on this date (enabled, on that weekday, inside its date range)? */
export function programCovers(program: Program, date: Date): boolean {
  if (!program.enabled) return false;
  if (!expandWeekdays(program.weekdays).has(weekdayOf(date))) return false;
  const day = isoDate(date);
  if (program.start_date && day < program.start_date) return false;
  if (program.end_date && day > program.end_date) return false;
  return true;
}

/** For each day of this week, the program a thermostat follows (the first that covers it), or null. */
export function weekPrograms(programs: Program[], entityId: string, now: Date = new Date()): (Program | null)[] {
  const mine = programs.filter((p) => p.entity_ids.includes(entityId));
  return weekDates(now).map((date) => mine.find((p) => programCovers(p, date)) ?? null);
}

// --------------------------------------------------------------------------- setpoints

/** "73° Cool", "68-74° Heat/Cool", "Off". */
export function setpointText(setpoint: Setpoint | null | undefined, unit = '°'): string {
  if (!setpoint) return '';
  const mode = modeVisual(setpoint.hvac_mode).label;
  if (setpoint.hvac_mode === 'off') return 'Off';
  const fmt = (n: number) => `${Math.round(n * 10) / 10}`;
  const deg = unit.startsWith('°') ? '°' : ` ${unit}`;
  if (typeof setpoint.target_temp_low === 'number' && typeof setpoint.target_temp_high === 'number') {
    return `${fmt(setpoint.target_temp_low)}-${fmt(setpoint.target_temp_high)}${deg} ${mode}`;
  }
  if (typeof setpoint.temperature === 'number') return `${fmt(setpoint.temperature)}${deg} ${mode}`;
  return mode;
}

const SLOT_TONES: Record<string, string> = {
  heat: 'bg-amber-500/70 text-amber-50',
  cool: 'bg-cyan-500/70 text-cyan-50',
  heat_cool: 'bg-violet-500/70 text-violet-50',
  auto: 'bg-emerald-500/70 text-emerald-50',
  dry: 'bg-sky-500/70 text-sky-50',
  fan_only: 'bg-teal-500/70 text-teal-50',
  off: 'bg-slate-600/80 text-slate-200',
};

/** A slot's colour, by its mode. */
export function slotTone(setpoint: Setpoint | null | undefined): string {
  return SLOT_TONES[setpoint?.hvac_mode ?? ''] ?? 'bg-slate-500/70 text-white';
}

/** What a thermostat will do next, in words: the hub's summary when it gave one. */
export function nextChangeText(thermostat: Thermostat, now: Date = new Date()): string {
  if (thermostat.summary) return thermostat.summary;
  if (thermostat.next) {
    const unit = thermostat.capabilities?.unit ?? '°';
    return `Goes to ${setpointText(thermostat.next.setpoint, unit)} at ${momentText(thermostat.next.at, now)}`;
  }
  return 'No scheduled changes';
}

export interface HoldOption {
  value: 'next_slot' | 'time' | 'permanent';
  label: string;
  disabled?: boolean;
}

/** How long a hold may last, for this thermostat (no "until next change" when nothing is next). */
export function holdUntilOptions(thermostat: Thermostat, now: Date = new Date()): HoldOption[] {
  return [
    thermostat.next
      ? { value: 'next_slot', label: `Until the next change (${momentText(thermostat.next.at, now)})` }
      : { value: 'next_slot', label: 'Until the next change', disabled: true },
    { value: 'time', label: 'Until a time' },
    { value: 'permanent', label: 'Until I resume it' },
  ];
}

/** The `until` the hub takes for a hold choice; null when a time is chosen but not a valid one. */
export function holdUntilValue(choice: HoldOption['value'], time = ''): HoldUntil | null {
  if (choice === 'next_slot' || choice === 'permanent') return choice;
  const minutes = parseTime(time);
  return minutes === null ? null : formatTime(minutes).slice(0, 5);
}

/** "Held at 70° Heat until 6:00 PM", "Held until you resume it". */
export function holdText(thermostat: Thermostat, now: Date = new Date()): string {
  const hold = thermostat.hold;
  if (!hold) return '';
  const what = setpointText(hold.setpoint, thermostat.capabilities?.unit ?? '°');
  if (hold.kind === 'permanent' || !hold.until) return `Held at ${what} until resumed`;
  return `Held at ${what} until ${momentText(hold.until, now)}`;
}

/** A blank setpoint in the thermostat's first non-off mode, at its current target. */
export function defaultSetpoint(thermostat: Thermostat | undefined): Setpoint {
  const modes = thermostat?.capabilities?.hvac_modes ?? [];
  const mode = modes.find((m) => m !== 'off') ?? modes[0] ?? 'heat';
  const sp: Setpoint = { hvac_mode: mode };
  if (typeof thermostat?.temperature === 'number') sp.temperature = thermostat.temperature;
  else if (typeof thermostat?.current_temperature === 'number') sp.temperature = Math.round(thermostat.current_temperature);
  return sp;
}

// --------------------------------------------------------------------------- scheduler slots

export type SlotStart =
  | { kind: 'time'; time: string }
  | { kind: 'sunrise' | 'sunset'; offset: number };

/** `07:00:00`, `sunrise`, `sunset-00:30`, `sunrise+01:15` into a form. Offsets are minutes. */
export function parseSlotStart(value: string | null | undefined): SlotStart {
  const raw = String(value ?? '').trim();
  const sun = /^(sunrise|sunset)(?:([+-])(\d{1,2}):(\d{2})(?::\d{2})?)?$/.exec(raw);
  if (sun) {
    const offset = sun[2] ? (Number(sun[3]) * 60 + Number(sun[4])) * (sun[2] === '-' ? -1 : 1) : 0;
    return { kind: sun[1] as 'sunrise' | 'sunset', offset };
  }
  return { kind: 'time', time: toTimeInput(raw) };
}

/** The form back into the scheduler's string. */
export function formatSlotStart(start: SlotStart): string {
  if (start.kind === 'time') {
    const minutes = parseTime(start.time);
    return minutes === null ? '' : formatTime(minutes);
  }
  if (!start.offset) return start.kind;
  const abs = Math.abs(start.offset);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${start.kind}${start.offset < 0 ? '-' : '+'}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** "7:00 AM", "Sunset", "30 min before sunrise". */
export function slotStartText(value: string | null | undefined): string {
  const start = parseSlotStart(value);
  if (start.kind === 'time') return start.time ? clockText(start.time) : String(value ?? '');
  const sun = start.kind === 'sunrise' ? 'sunrise' : 'sunset';
  if (!start.offset) return sun === 'sunrise' ? 'Sunrise' : 'Sunset';
  return `${Math.abs(start.offset)} min ${start.offset < 0 ? 'before' : 'after'} ${sun}`;
}

/** A catalog entity that reads power (device class power, or a W/kW unit). */
export function isPowerSensor(entity: { entity_id: string; device_class?: string | null; unit?: string | null }): boolean {
  if (!entity.entity_id.startsWith('sensor.')) return false;
  const unit = (entity.unit ?? '').trim();
  return entity.device_class === 'power' || unit === 'W' || unit === 'kW';
}

export const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** A blank scheduler timeslot. */
export function blankTimeslot(): Timeslot {
  return { start: '08:00:00', stop: null, conditions: [], condition_type: 'and', track_conditions: false, actions: [] };
}

/** A blank scheduler schedule. */
export function blankSchedule(): Partial<Schedule> {
  return {
    name: '', enabled: true, weekdays: ['daily'], start_date: null, end_date: null, repeat_type: 'repeat',
    timeslots: [blankTimeslot()], tags: [],
  };
}

/** A blank program for these thermostats. */
export function blankProgram(entityIds: string[], setpoint: Setpoint): Partial<Program> {
  return {
    name: '', entity_ids: entityIds, weekdays: ['daily'], start_date: null, end_date: null, enabled: true,
    slots: [{ start: '06:00:00', ...setpoint }, { start: '22:00:00', ...setpoint }],
  };
}

/** A blank settings object, for when the hub has none yet (everything off). */
export function blankSettings(): ClimateSettings {
  return {
    away: { enabled: false, start: null, end: null, auto: false, persons: [], setpoint: { hvac_mode: 'off' }, per_entity: {} },
    energy: {
      enabled: false, power_sensor: null, windows: [], shed_after_min: 5, resume_below_kw: null,
      comfort_max_temp: null, comfort_setpoint: null, thermostats: [],
    },
  };
}

/**
 * What a group of thermostats can do together (a program or the away setpoint
 * shared by several): the modes and fan modes any of them offers, and the
 * widest temperature bounds.
 */
export function mergeCapabilities(thermostats: Thermostat[]): ThermostatCapabilities {
  const modes = new Set<string>();
  const fans = new Set<string>();
  let min: number | undefined;
  let max: number | undefined;
  let range = false;
  let unit: string | undefined;
  for (const t of thermostats) {
    const c = t.capabilities;
    if (!c) continue;
    (c.hvac_modes ?? []).forEach((m) => modes.add(m));
    (c.fan_modes ?? []).forEach((m) => fans.add(m));
    if (typeof c.min_temp === 'number') min = min === undefined ? c.min_temp : Math.min(min, c.min_temp);
    if (typeof c.max_temp === 'number') max = max === undefined ? c.max_temp : Math.max(max, c.max_temp);
    range = range || !!c.range;
    unit = unit ?? c.unit;
  }
  return { hvac_modes: [...modes], fan_modes: [...fans], min_temp: min, max_temp: max, range, unit };
}

/** Does this setpoint use a low/high pair instead of one target? */
export function usesRange(setpoint: Setpoint, capabilities?: ThermostatCapabilities): boolean {
  if (typeof setpoint.target_temp_low === 'number' || typeof setpoint.target_temp_high === 'number') return true;
  return !!capabilities?.range && setpoint.hvac_mode === 'heat_cool';
}
