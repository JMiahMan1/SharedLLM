import type { NearbyDeviceState } from '../plugins/nearby';

/** The parts of Settings > Nearby & offline, in the order shown. */
export type NearbyPart = 'nearby' | 'chat' | 'offline' | 'car' | 'mine';
export const NEARBY_PARTS: NearbyPart[] = ['nearby', 'chat', 'offline', 'car', 'mine'];

export type NearbyParts = Record<NearbyPart, boolean>;

/** The signed-in person's own parts in react-query. */
export const NEARBY_QUERY_KEY = ['nearby-settings', 'me'] as const;

/** What a person gets before choosing: car detection on, the rest off (as before these settings). */
export const NEARBY_DEFAULTS: NearbyParts = { nearby: false, chat: false, offline: false, car: true, mine: false };

/** One switch: its name and a one-line plain description. */
export const NEARBY_LABELS: Record<NearbyPart, { title: string; description: string }> = {
  nearby: {
    title: 'Find family nearby',
    description: 'Family phones notice each other over Bluetooth, even with no signal.',
  },
  chat: {
    title: 'Offline family chat',
    description: 'Messages reach family phones nearby with no Wi-Fi or data.',
  },
  offline: {
    title: 'Offline mode',
    description: 'Ask the Voice Assistant through a nearby family phone, and keep locations, shopping and calendar shared.',
  },
  car: {
    title: 'Car detection',
    description: 'Tells which linked car a trip was in from its Bluetooth.',
  },
  mine: {
    title: 'My devices',
    description: 'Your phone and tablet keep your location, steps, watch and offline information the same, with no internet.',
  },
};

/** The server's answer for a person (their own, or a child's for a parent). */
export interface NearbySettingsState {
  username: string;
  settings: NearbyParts;
  /** A minor's parts: set by their parents. */
  managed_by_parent: boolean;
  /** Whether a choice has been saved; until then `settings` are only the defaults. */
  chosen: boolean;
  /** Whether the caller may change them. */
  can_change: boolean;
  /** Always on for a minor: "parent_link". */
  always_on: string[];
}

export function nearbyState(value: unknown): NearbySettingsState {
  const o = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  const raw = (o.settings && typeof o.settings === 'object' ? o.settings : {}) as Record<string, unknown>;
  const settings = { ...NEARBY_DEFAULTS };
  for (const p of NEARBY_PARTS) if (typeof raw[p] === 'boolean') settings[p] = raw[p] as boolean;
  return {
    username: String(o.username ?? ''),
    settings,
    managed_by_parent: o.managed_by_parent === true,
    // An older server does not say: treat its answer as a saved choice, as before.
    chosen: o.chosen !== false,
    can_change: o.can_change === true,
    always_on: Array.isArray(o.always_on) ? o.always_on.filter((x): x is string => typeof x === 'string') : [],
  };
}

/** A line under the switches about what this phone can do now, or null when all is well. */
export function deviceLine(device: NearbyDeviceState | null, parts: NearbyParts, parentLink = false): string | null {
  if (!device) return null;
  const radio = parts.nearby || parts.chat || parts.offline || parts.mine || parentLink;
  if (!radio && !parts.car) return null;
  if (radio && !device.bluetoothOn) return 'Bluetooth is off on this phone. Turn it on for these to work.';
  if (radio && !device.scanPermission) return 'Allow Nearby devices for these to work on this phone.';
  if ((parts.chat || parts.offline || parts.mine || parentLink || parts.car) && !device.connectPermission) {
    return 'Allow Nearby devices so phones can pass messages to each other.';
  }
  if (radio && device.host === 'own') return 'Running on its own while location sharing is off (one quiet notification).';
  return null;
}
