/**
 * A hassmic Echo's wake word sensitivity and its recent wake-ups, read from the two entities HomeHub
 * publishes for it (services/homehub/hassmic.py): the `*_wake_word_sensitivity` select (Standard, More
 * sensitive, Most sensitive) and the `*_wake_word_detections` diagnostic sensor. Pure: it reads the
 * device's entities and never invents a value.
 */
import type { HardwareEntity } from './hardware';
import type { HubEntity } from './dashboards';

export interface WakeDetection {
  /** The Echo's own stamp: a UTC time once its clock is set, else seconds since it started. */
  at: string;
  wakeWord: string;
  /** The window's mean probability that passed the cutoff, 0 to 1. */
  probability: number;
  /** The cutoff the model had when HomeHub read the line, when known. */
  cutoff: number | null;
  /** The Echo's second wake word slot. */
  second: boolean;
}

export interface WakeWordInfo {
  sensitivity: HubEntity | null;
  detectionsEntityId: string | null;
  warning: string;
  reason: string | null;
  note: string | null;
  detections: WakeDetection[];
}

/** HomeHub sends its own text; this is only for a hub that predates it. */
export const DEFAULT_WARNING = 'More sensitive means more accidental wake-ups, especially near a TV or radio.';

const SENSITIVITY_SUFFIX = '_wake_word_sensitivity';
const DETECTIONS_SUFFIX = '_wake_word_detections';

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function find(entities: HardwareEntity[], domain: string, suffix: string): HardwareEntity | undefined {
  return entities.find((e) => e.domain === domain && e.entity_id.endsWith(suffix) && !e.disabled_by);
}

function detection(raw: unknown): WakeDetection | null {
  if (!raw || typeof raw !== 'object') return null;
  const d = raw as Record<string, unknown>;
  const at = text(d.at);
  const wakeWord = text(d.wake_word);
  if (!at || !wakeWord || typeof d.probability !== 'number' || !Number.isFinite(d.probability)) return null;
  return {
    at,
    wakeWord,
    probability: d.probability,
    cutoff: typeof d.cutoff === 'number' && Number.isFinite(d.cutoff) ? d.cutoff : null,
    second: d.second === true,
  };
}

/** The entities of the Echo's wake word, or null when the device has none (not a hassmic Echo). */
export function wakeWordInfo(entities: HardwareEntity[]): WakeWordInfo | null {
  const select = find(entities, 'select', SENSITIVITY_SUFFIX);
  const sensor = find(entities, 'sensor', DETECTIONS_SUFFIX);
  if (!select && !sensor) return null;
  const attrs = select?.state?.attributes ?? {};
  const raw = sensor?.state?.attributes?.detections;
  return {
    sensitivity: select?.state ?? null,
    detectionsEntityId: sensor?.entity_id ?? null,
    warning: text(attrs.warning) ?? DEFAULT_WARNING,
    reason: text(attrs.reason),
    note: text(attrs.note),
    detections: Array.isArray(raw) ? raw.map(detection).filter((d): d is WakeDetection => d !== null) : [],
  };
}

/** The entity ids the wake word section shows, so the sheet does not list them twice. */
export function wakeWordEntityIds(entities: HardwareEntity[]): Set<string> {
  return new Set(entities
    .filter((e) => (e.domain === 'select' && e.entity_id.endsWith(SENSITIVITY_SUFFIX))
      || (e.domain === 'sensor' && e.entity_id.endsWith(DETECTIONS_SUFFIX)))
    .map((e) => e.entity_id));
}

/** "18:02, 20 Nov" for a UTC stamp, "12 min after it started" for a boot-relative one. */
export function detectionTime(at: string, locale?: string): string {
  const boot = /^boot\+(\d+(?:\.\d+)?)$/.exec(at);
  if (boot) {
    const s = Math.round(Number(boot[1]));
    if (s < 120) return `${s} s after it started`;
    if (s < 7200) return `${Math.round(s / 60)} min after it started`;
    return `${Math.round(s / 3600)} h after it started`;
  }
  const when = new Date(at.replace(' ', 'T'));
  if (Number.isNaN(when.getTime())) return at;
  return when.toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** "0.95" with the margin over the cutoff when known: "0.95 (cutoff 0.94)". */
export function probabilityText(d: WakeDetection): string {
  const p = d.probability.toFixed(2);
  return d.cutoff == null ? p : `${p} (cutoff ${d.cutoff.toFixed(2)})`;
}
