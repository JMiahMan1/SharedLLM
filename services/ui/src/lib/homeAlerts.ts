/**
 * Home alerts: which Home Assistant entities send "home" phone notices.
 *
 * Stored as the `home_alert_entities` global setting, a JSON list that
 * execution's home_alerts loop reads (docs/INTEGRATIONS.md, Home alerts).
 * `people` empty means everyone; `minutes` and `watts` tune the events that
 * use them, and are left out when blank so the service default applies.
 */
import { api } from '../services/api';

export const HOME_ALERT_SETTING = 'home_alert_entities';

export type HomeAlertEvent =
  | 'doorbell'
  | 'motion_away'
  | 'garage_open'
  | 'door_unlocked_night'
  | 'appliance_done'
  | 'water_leak';

export interface HomeAlert {
  entity_id: string;
  event: HomeAlertEvent;
  people: string[];
  minutes?: number;
  watts?: number;
}

export interface HomeAlertEventInfo {
  label: string;
  hint: string;
  /** Default for `minutes` when the event uses it (the service's own default). */
  minutes?: number;
  watts?: number;
}

export const HOME_ALERT_EVENTS: Record<HomeAlertEvent, HomeAlertEventInfo> = {
  doorbell: { label: 'Doorbell pressed', hint: 'A doorbell button or event entity.' },
  motion_away: { label: 'Motion while nobody is home', hint: 'A motion sensor; only counts when everyone is away.' },
  garage_open: { label: 'Garage door left open', hint: 'A cover or door sensor; open longer than the minutes set.', minutes: 15 },
  door_unlocked_night: {
    label: 'Door unlocked at night',
    hint: 'An exterior lock; unlocked for the minutes set between 10 pm and 6 am.',
    minutes: 5,
  },
  appliance_done: {
    label: 'Washer or dryer finished',
    hint: 'A power sensor; done when power stays under the watts set for the minutes set.',
    minutes: 5,
    watts: 10,
  },
  water_leak: { label: 'Water leak (urgent)', hint: 'A moisture sensor.' },
};

const isEvent = (value: unknown): value is HomeAlertEvent =>
  typeof value === 'string' && value in HOME_ALERT_EVENTS;

const positive = (value: unknown): number | undefined => {
  const n = typeof value === 'string' && value.trim() === '' ? NaN : Number(value);
  return Number.isFinite(n) && n > 0 ? n : undefined;
};

/** The setting's value as clean entries; anything malformed is dropped. */
export function parseHomeAlerts(raw: string | undefined | null): HomeAlert[] {
  let data: unknown;
  try {
    data = JSON.parse(raw || '[]');
  } catch {
    return [];
  }
  if (!Array.isArray(data)) return [];
  const out: HomeAlert[] = [];
  for (const item of data) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    const entityId = String(row.entity_id ?? '').trim();
    if (!entityId.includes('.') || !isEvent(row.event)) continue;
    out.push(cleanHomeAlert({
      entity_id: entityId,
      event: row.event,
      people: Array.isArray(row.people) ? row.people.map(String) : [],
      minutes: positive(row.minutes),
      watts: positive(row.watts),
    }));
  }
  return out;
}

/** Drop tuning fields the event does not use and blanks, lower-case people. */
export function cleanHomeAlert(alert: HomeAlert): HomeAlert {
  const info = HOME_ALERT_EVENTS[alert.event];
  const out: HomeAlert = {
    entity_id: alert.entity_id.trim(),
    event: alert.event,
    people: [...new Set(alert.people.map((p) => p.trim().toLowerCase()).filter(Boolean))],
  };
  const minutes = positive(alert.minutes);
  const watts = positive(alert.watts);
  if (info.minutes !== undefined && minutes !== undefined) out.minutes = minutes;
  if (info.watts !== undefined && watts !== undefined) out.watts = watts;
  return out;
}

/** Add or replace the entry for this entity and event. */
export function upsertHomeAlert(list: HomeAlert[], alert: HomeAlert): HomeAlert[] {
  const clean = cleanHomeAlert(alert);
  const rest = list.filter((a) => !(a.entity_id === clean.entity_id && a.event === clean.event));
  return [...rest, clean];
}

export function removeHomeAlert(list: HomeAlert[], alert: Pick<HomeAlert, 'entity_id' | 'event'>): HomeAlert[] {
  return list.filter((a) => !(a.entity_id === alert.entity_id && a.event === alert.event));
}

export async function loadHomeAlerts(): Promise<HomeAlert[]> {
  const settings = await api.getSettings();
  return parseHomeAlerts(settings.find((s) => s.key === HOME_ALERT_SETTING)?.value);
}

export async function saveHomeAlerts(list: HomeAlert[]): Promise<void> {
  await api.updateSetting(HOME_ALERT_SETTING, JSON.stringify(list.map(cleanHomeAlert)));
}
