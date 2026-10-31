/**
 * Light or dark map tiles, chosen per device in Settings > Appearance.
 *
 * "auto" (the default) is light by day and dark at night by this device's
 * clock: night runs from sunset to sunrise where the map is looking, or
 * 19:00-06:00 when there is no position to work it out from.
 */
export type MapStylePref = 'auto' | 'light' | 'dark';
export type MapScheme = 'light' | 'dark';

const KEY = 'jarvis_map_style';
const listeners = new Set<() => void>();
/** The choice made this session, for when storage cannot be read or written. */
let chosen: MapStylePref | null = null;

export function getMapStyle(): MapStylePref {
  let saved: string | null = chosen;
  try {
    saved = localStorage.getItem(KEY) ?? chosen;
  } catch {
    // Unreadable storage: the session's choice, if any.
  }
  return saved === 'light' || saved === 'dark' ? saved : 'auto';
}

export function setMapStyle(next: MapStylePref): void {
  chosen = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // Private mode or full storage: the choice holds for this session only.
  }
  listeners.forEach((listener) => listener());
}

export function subscribeMapStyle(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const RAD = Math.PI / 180;

/**
 * Sunrise and sunset (epoch ms) around `now` at a place, by the standard
 * sunrise equation; accurate to a minute or two, which is all a map needs.
 * Null in polar day or night.
 */
export function sunTimes(now: number, lat: number, lon: number): { sunrise: number; sunset: number } | null {
  const julianDay = now / 86_400_000 + 2440587.5;
  const n = Math.round(julianDay - 2451545 + 0.0008 + lon / 360);
  const meanSolarNoon = n - lon / 360;
  const anomaly = (357.5291 + 0.98560028 * meanSolarNoon) % 360;
  const centre =
    1.9148 * Math.sin(anomaly * RAD) + 0.02 * Math.sin(2 * anomaly * RAD) + 0.0003 * Math.sin(3 * anomaly * RAD);
  const longitude = (anomaly + centre + 180 + 102.9372) % 360;
  const transit =
    2451545 + meanSolarNoon + 0.0053 * Math.sin(anomaly * RAD) - 0.0069 * Math.sin(2 * longitude * RAD);
  const declination = Math.asin(Math.sin(longitude * RAD) * Math.sin(23.44 * RAD));
  const cosHour =
    (Math.sin(-0.833 * RAD) - Math.sin(lat * RAD) * Math.sin(declination)) /
    (Math.cos(lat * RAD) * Math.cos(declination));
  if (cosHour < -1 || cosHour > 1) return null;
  const hourAngle = Math.acos(cosHour) / RAD / 360;
  const toMs = (jd: number) => (jd - 2440587.5) * 86_400_000;
  return { sunrise: toMs(transit - hourAngle), sunset: toMs(transit + hourAngle) };
}

/** Whether it is night at `now` on this device, where the map is looking. */
export function isNight(now: number, where?: { lat: number; lon: number } | null): boolean {
  if (where && Number.isFinite(where.lat) && Number.isFinite(where.lon)) {
    const sun = sunTimes(now, where.lat, where.lon);
    if (sun) return now < sun.sunrise || now > sun.sunset;
  }
  const hour = new Date(now).getHours();
  return hour >= 19 || hour < 6;
}

export function resolveMapScheme(
  pref: MapStylePref,
  now: number,
  where?: { lat: number; lon: number } | null,
): MapScheme {
  if (pref !== 'auto') return pref;
  return isNight(now, where) ? 'dark' : 'light';
}
