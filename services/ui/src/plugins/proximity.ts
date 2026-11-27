import { registerPlugin } from '@capacitor/core';

/** One family phone this device heard lately (diagnostics). */
export interface ProximityPeer {
  person: string;
  band: 'next_to' | 'same_room' | 'nearby' | 'too_far';
  rssi: number;
  at: number;
  /** Another device of this phone's own person (shown as "Your other device"; never a sighting). */
  ownDevice?: boolean;
}

/**
 * Wander's "Find family nearby" on this Android device: Bluetooth LE with no
 * network. `advertising` is `on`, `off`, `failed` or `unsupported` (a tablet
 * that can only scan).
 */
export interface ProximityDiagnostics {
  apiLevel?: number;
  enabled: boolean;
  scanPermission: boolean;
  advertisePermission: boolean;
  bluetoothOn: boolean;
  running: boolean;
  advertising: 'on' | 'off' | 'failed' | 'unsupported';
  advertiseError?: number;
  scanning: boolean;
  scanMode: 'low_power' | 'balanced';
  driving: boolean;
  lastScanStartAt: number | null;
  lastHeardAt: number | null;
  configured: boolean;
  keyId: string | null;
  members: number;
  /** Sightings waiting to be sent (kept while offline). */
  outbox: number;
  uploadedAt: number | null;
  peers: ProximityPeer[];
}

export interface ProximityPluginInterface {
  getDiagnostics(): Promise<ProximityDiagnostics>;
  /** Turn it on (asks for Nearby devices on Android 12+, only now). */
  enable(): Promise<ProximityDiagnostics>;
  disable(): Promise<ProximityDiagnostics>;
}

const NEEDS_APP = 'Family nearby needs the Jarvis Android app';
const Proximity = registerPlugin<ProximityPluginInterface>('JarvisProximity', {
  web: () => ({
    getDiagnostics: async () => Promise.reject(new Error(NEEDS_APP)),
    enable: async () => Promise.reject(new Error(NEEDS_APP)),
    disable: async () => Promise.reject(new Error(NEEDS_APP)),
  }),
});

export default Proximity;
