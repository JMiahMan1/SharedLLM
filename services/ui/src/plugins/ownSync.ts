import { registerPlugin } from '@capacitor/core';

/** What each sync with another of my devices can bring, in the order shown. */
export type OwnSyncCategory = 'device' | 'steps' | 'watch' | 'rooms' | 'family' | 'chat' | 'requests' | 'guardian';

/** One kind of data after the last sync with a device: the same on both, or brought from it. */
export interface OwnSyncTypeStatus {
  status: 'in_sync' | 'updated';
  /** The device it came from (updated only). */
  from?: string;
  /** When that sync happened (ms). */
  at: number;
}

/** One of my other devices, as the last sync with it left things. */
export interface OwnSyncPeer {
  device: string;
  name: string;
  last_sync_at: number;
  first_seen_at?: number;
  types: Partial<Record<OwnSyncCategory, OwnSyncTypeStatus>>;
}

export interface OwnSyncDiagnostics {
  /** My devices is on and the phone-to-phone syncs can run. */
  enabled: boolean;
  /** The switch in Settings > Nearby & offline. */
  turnedOn: boolean;
  deviceId?: string;
  name?: string;
  /** My own items this device holds (all my devices'). */
  ownItems?: number;
  peers: OwnSyncPeer[];
}

export interface OwnSyncFix {
  lat: number;
  lon: number;
  acc?: number;
  at: number;
  device?: string;
  name?: string;
}

export interface OwnSyncState {
  enabled: boolean;
  deviceId?: string;
  name?: string;
  devices: Array<{ device: string; name?: string; me: boolean; battery?: number; charging?: boolean; fix?: OwnSyncFix; at: number }>;
  latestFix?: OwnSyncFix;
  /** Today's steps by the daily-maximum rule, and each device's own count. */
  steps?: { day: string; total: number; devices: Array<{ device: string; name: string; me: boolean; steps: number; at: number | null }> };
  watch?: { day: string; steps: number; goal: number; battery: number; charging: boolean; read_at: number; via?: string };
}

export interface OwnSyncPluginInterface {
  state(): Promise<OwnSyncState>;
  getDiagnostics(): Promise<OwnSyncDiagnostics>;
  /** The watch's stats this phone just read, shared with my other devices. */
  noteWatch(options: { stats: Record<string, unknown>; name?: string }): Promise<void>;
}

const NEEDS_APP = 'My devices needs the Jarvis Android app';
const OwnSync = registerPlugin<OwnSyncPluginInterface>('JarvisOwnSync', {
  web: () => ({
    state: async () => ({ enabled: false, devices: [] }),
    getDiagnostics: async () => Promise.reject(new Error(NEEDS_APP)),
    noteWatch: async () => undefined,
  }),
});

export default OwnSync;
