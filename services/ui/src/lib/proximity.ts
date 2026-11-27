/**
 * Family phones near each other (geo's proximity.py): bands in words, and
 * who a trip was taken with. Bands, never metres.
 */

import type { ProximityDiagnostics } from '../plugins/proximity';

export type ProximityBand = 'next_to' | 'same_room' | 'nearby';

export interface TripCompanion {
  user_id: string;
  user_name?: string;
  trip_id?: string;
  /** How it is known: the phones were close, and/or both were in the same linked car. */
  via: Array<'nearby' | 'same_vehicle'>;
  vehicle_id?: string | null;
  vehicle_name?: string | null;
}

export interface NearPerson {
  user_id: string;
  band: ProximityBand;
  last_seen: number;
}

const BAND_WORDS: Record<string, string> = {
  next_to: 'Next to you',
  same_room: 'In the same room',
  nearby: 'Nearby',
  too_far: 'Too faint to place',
};

export function bandLabel(band: string | null | undefined): string {
  return BAND_WORDS[band ?? ''] ?? 'Unknown';
}

function listNames(names: string[]): string {
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/**
 * "with Gracie in the Odyssey": who a trip was taken with, and the car when
 * one is known (the same linked car, or the trip's own detected car). Names
 * come from `nameOf` (the family directory) and fall back to the trip's.
 */
export function togetherLine(
  together: TripCompanion[] | null | undefined,
  nameOf: (userId: string) => string | undefined = () => undefined,
): string | null {
  if (!together?.length) return null;
  const names = together.map((t) => nameOf(t.user_id) ?? t.user_name ?? t.user_id);
  const car = together.find((t) => t.vehicle_name)?.vehicle_name;
  return `with ${listNames(names)}${car ? ` in the ${car}` : ''}`;
}

/** The near-now entry for one person, if any. */
export function nearOf(near: NearPerson[] | undefined, userId: string): NearPerson | undefined {
  const id = userId.replace(/^(person|user)\./, '').toLowerCase();
  return near?.find((n) => n.user_id === id);
}

/** One line on what Family nearby is doing on this device now. */
export function nearbyStatusLine(d: ProximityDiagnostics): string {
  if (!d.enabled) return 'Off on this device.';
  if (!d.scanPermission) return 'Waiting for Nearby devices to be allowed for Jarvis.';
  if (!d.bluetoothOn) return 'Bluetooth is off; it starts again when Bluetooth is on.';
  if (!d.configured) return 'Getting the family key; this needs the internet once.';
  if (d.advertising === 'unsupported') {
    return 'On, listening only: this device cannot send the Bluetooth signal, so it sees family phones but they do not see it.';
  }
  return 'On. Family phones nearby are noticed without Wi-Fi or data.';
}
