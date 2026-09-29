import {
  Droplet, Fan, Flame, PowerOff, Snowflake, SunSnow, Thermometer,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ClimateLayout, DeviceEntry } from '../types/widget';

/**
 * Pure climate (Home Assistant `climate.*`) helpers.
 *
 * Kept out of the widget file so the rules that decide what a setpoint ring
 * should look like are testable on their own, and so nothing here has to
 * guess: every bound, mode list and label comes from the entity itself.
 */

export const POLL_MS = 15000;
/** Coalesce rapid stepper taps into one service call. */
export const SETPOINT_DEBOUNCE_MS = 400;
/** Roll an optimistic value back if Home Assistant never echoes it. */
export const OPTIMISTIC_TTL_MS = 6000;
/** Only used when an entity advertises no `hvac_modes` of its own. */
export const MODE_FALLBACK = ['off', 'heat', 'cool', 'heat_cool', 'auto'];

export type ClimateAttributes = {
  friendly_name?: string;
  hvac_mode?: string;
  hvac_modes?: string[];
  hvac_action?: string;
  current_temperature?: number;
  temperature?: number;
  target_temp_step?: number;
  target_temp_high?: number;
  target_temp_low?: number;
  min_temp?: number;
  max_temp?: number;
  preset_mode?: string;
  preset_modes?: string[];
  fan_mode?: string;
  fan_modes?: string[];
  swing_mode?: string;
  swing_modes?: string[];
  humidity?: number;
};

export type ModeVisual = { label: string; Icon: LucideIcon; tone: string };

const MODE_VISUALS: Record<string, ModeVisual> = {
  off: { label: 'Off', Icon: PowerOff, tone: 'text-slate-400' },
  heat: { label: 'Heat', Icon: Flame, tone: 'text-amber-400' },
  cool: { label: 'Cool', Icon: Snowflake, tone: 'text-cyan-400' },
  heat_cool: { label: 'Heat/Cool', Icon: SunSnow, tone: 'text-violet-300' },
  auto: { label: 'Auto', Icon: Thermometer, tone: 'text-emerald-300' },
  dry: { label: 'Dry', Icon: Droplet, tone: 'text-sky-300' },
  fan_only: { label: 'Fan', Icon: Fan, tone: 'text-teal-300' },
};

const GENERIC_MODE: ModeVisual = { label: 'Mode', Icon: Thermometer, tone: 'text-slate-300' };

export function titleize(value?: string): string {
  if (!value) return '';
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function modeVisual(mode?: string): ModeVisual {
  if (!mode) return GENERIC_MODE;
  return MODE_VISUALS[mode] ?? { ...GENERIC_MODE, label: titleize(mode) };
}

/** Arc + text colour follows what the unit is actually doing, not the mode. */
export function actionTone(action?: string, mode?: string): {
  stroke: string; text: string; glow: string; label: string;
} {
  const key = (action && action !== 'off' ? action : mode) || 'off';
  if (key === 'cooling' || key === 'cool' || key === 'drying' || key === 'dry') {
    return { stroke: '#22d3ee', text: 'text-cyan-300', glow: 'rgba(34,211,238,0.35)', label: titleize(key) };
  }
  if (key === 'heating' || key === 'heat' || key === 'preheating') {
    return { stroke: '#fb923c', text: 'text-amber-300', glow: 'rgba(251,146,60,0.35)', label: titleize(key) };
  }
  if (key === 'fan' || key === 'fan_only') {
    return { stroke: '#2dd4bf', text: 'text-teal-300', glow: 'rgba(45,212,191,0.3)', label: titleize(key) };
  }
  if (key === 'idle') return { stroke: '#64748b', text: 'text-slate-400', glow: 'transparent', label: 'Idle' };
  return { stroke: '#475569', text: 'text-slate-500', glow: 'transparent', label: 'Off' };
}

/**
 * Setpoint bounds and granularity come from the entity, never from a literal.
 * Some integrations omit min/max, so fall back to the entity's own target plus
 * a step-derived span rather than inventing absolute temperatures.
 */
export function setpointRange(attrs: ClimateAttributes): { min: number; max: number; step: number } {
  const step = typeof attrs.target_temp_step === 'number' && attrs.target_temp_step > 0
    ? attrs.target_temp_step
    : 1;
  const targets = [attrs.temperature, attrs.target_temp_low, attrs.target_temp_high]
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  const lowKnown = targets.length ? Math.min(...targets) : 0;
  const highKnown = targets.length ? Math.max(...targets) : 0;
  const span = step * 10;
  const rawMin = typeof attrs.min_temp === 'number' ? attrs.min_temp : lowKnown - span;
  const rawMax = typeof attrs.max_temp === 'number' ? attrs.max_temp : highKnown + span;
  return { min: Math.min(rawMin, rawMax), max: Math.max(rawMin, rawMax), step };
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function snap(value: number, min: number, max: number, step: number): number {
  const snapped = Math.round((value - min) / step) * step + min;
  return clamp(Math.round(snapped * 100) / 100, min, max);
}

/** HA sends numbers, numeric strings, or nothing at all — normalise to a number. */
export function numberAttr(value: unknown): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

export function climateAttrs(device: DeviceEntry | undefined): ClimateAttributes {
  return (device?.attributes || {}) as ClimateAttributes;
}

export function statusLine(device: DeviceEntry, attrs: ClimateAttributes): string {
  if (device.state === 'unavailable' || device.state === 'unknown') return 'Unavailable';
  const parts = [attrs.hvac_action ? titleize(attrs.hvac_action) : modeVisual(attrs.hvac_mode).label];
  if (typeof attrs.humidity === 'number') parts.push(`${Math.round(attrs.humidity)}% RH`);
  return parts.join(' · ');
}

export function entityLabel(device: DeviceEntry): string {
  return climateAttrs(device).friendly_name || device.friendly_name || device.entity_id;
}

/** Mode list comes from the entity; only fall back when it advertises none. */
export function modesFor(device: DeviceEntry): string[] {
  const modes = climateAttrs(device).hvac_modes;
  return Array.isArray(modes) && modes.length ? modes : MODE_FALLBACK;
}

export function formatTemp(value: number | undefined): number | string {
  return typeof value === 'number' ? Math.round(value * 10) / 10 : '--';
}

export const LAYOUT_OPTIONS: { value: ClimateLayout; label: string; hint: string }[] = [
  { value: 'auto', label: 'Auto', hint: 'Dial for one device, room tiles for several' },
  { value: 'dial', label: 'Dial', hint: 'Always one dial; switch rooms along the top' },
  { value: 'tiles', label: 'Tiles', hint: 'Grid of rooms; tap a tile to open its dial' },
];

/** Is the entity actually a climate entity, by domain or by id? */
export function isClimateEntity(device: DeviceEntry): boolean {
  return device.domain === 'climate' || device.entity_id.startsWith('climate.');
}
