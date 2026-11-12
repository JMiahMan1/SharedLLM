/**
 * The household time zone (gateway: GET/PUT /api/household-timezone, admin only).
 *
 * Precedence, highest first: the household setting, TZ in the server's .env,
 * then a zone derived from the home location (only when that is switched on).
 * With none of them the server reports an error naming all three instead of
 * guessing a zone.
 */
export type HouseholdTimezoneSource = 'household setting' | 'TZ in .env' | 'home location';

export interface HouseholdTimezone {
  /** The zone in effect, or null when no source supplies one. */
  zone: string | null;
  source: HouseholdTimezoneSource | null;
  /** Why there is no zone, when there is none. */
  error: string | null;
  /** The stored household zone; "" means unset. */
  setting: string;
  from_location: boolean;
  /** TZ from the server's .env; "" when unset. */
  env_tz: string;
  location_set: boolean;
  /** The three sources in precedence order. */
  order: string[];
}

export interface HouseholdTimezoneUpdate {
  /** "" clears the household setting. */
  timezone?: string;
  from_location?: boolean;
}

/** Every IANA zone this browser knows, or [] where Intl cannot list them. */
export function listTimeZones(): string[] {
  const intl = Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] };
  try {
    return typeof intl.supportedValuesOf === 'function' ? intl.supportedValuesOf('timeZone') : [];
  } catch {
    return [];
  }
}

/** The zone this browser runs in, or "" when it does not say. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
}

/** Zones whose name contains every word of the query (case and `_` ignored). */
export function filterTimeZones(zones: string[], query: string): string[] {
  const words = query.toLowerCase().replace(/_/g, ' ').split(/\s+/).filter(Boolean);
  if (!words.length) return zones;
  return zones.filter((zone) => {
    const name = zone.toLowerCase().replace(/_/g, ' ');
    return words.every((word) => name.includes(word));
  });
}
