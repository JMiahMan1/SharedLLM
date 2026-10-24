import { registerPlugin } from '@capacitor/core';

/* Health Connect import on Android (HealthConnectPlugin). The phone reads
   sleep, resting heart rate, HRV, weight and walks/runs/rides from Health
   Connect and posts them itself (hourly, on app open, and on "Sync now");
   this is only the connect/disconnect control and its status. */

export type HealthConnectAvailability = 'available' | 'update_required' | 'unavailable';

export interface HealthConnectCounts {
  added: number;
  duplicates: number;
}

export interface HealthConnectResult {
  ok: boolean;
  at?: number;
  error?: string;
  detail?: string | string[];
  sleep?: HealthConnectCounts;
  metrics?: HealthConnectCounts;
  workouts?: HealthConnectCounts & { skipped: number; other_types: number };
  rejected?: number;
}

export interface HealthConnectStatus {
  availability: HealthConnectAvailability;
  connected: boolean;
  /** The Jarvis account that connected it; others signed in on the phone are never posted for. */
  owner: string | null;
  /** Short names of granted read permissions, e.g. "sleep", "weight". */
  granted: string[];
  backgroundAvailable: boolean;
  backgroundGranted: boolean;
  lastSync: number | null;
  lastResult: HealthConnectResult | null;
  permissionError?: string;
}

export interface HealthConnectPluginInterface {
  status(): Promise<HealthConnectStatus>;
  connect(options: { username: string }): Promise<HealthConnectStatus>;
  syncNow(): Promise<HealthConnectStatus & { result: HealthConnectResult }>;
  disconnect(options: { revoke: boolean }): Promise<HealthConnectStatus>;
  openHealthConnect(): Promise<void>;
}

const unavailable: HealthConnectStatus = {
  availability: 'unavailable',
  connected: false,
  owner: null,
  granted: [],
  backgroundAvailable: false,
  backgroundGranted: false,
  lastSync: null,
  lastResult: null,
};

/* On the web (and APKs without the plugin) there is no Health Connect: say
   so, never pretend a sync happened. */
const webFallback: HealthConnectPluginInterface = {
  status: async () => unavailable,
  connect: async () => {
    throw new Error('Health Connect is only on the Android app');
  },
  syncNow: async () => ({ ...unavailable, result: { ok: false, error: 'unavailable' } }),
  disconnect: async () => unavailable,
  openHealthConnect: async () => undefined,
};

const HealthConnect = registerPlugin<HealthConnectPluginInterface>('HealthConnect', {
  web: () => webFallback,
});

export default HealthConnect;
