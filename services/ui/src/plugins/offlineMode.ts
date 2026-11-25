import { registerPlugin } from '@capacitor/core';

/** One of this phone's Jarvis requests waiting for (or answered through) a family phone that is online. */
export interface OfflineRequest {
  id: string;
  kind: 'ask' | 'command' | 'location' | 'shopping_add' | 'shopping_check';
  body: Record<string, unknown> | null;
  issued_at: number;
  expires_at: number;
  status: 'answered' | 'expired' | 'sending' | 'waiting_for_online_phone' | 'no_family_phone_nearby';
  answer?: string;
  ok?: boolean;
}

/** One cached item of household information, opened (only this member's own items open). */
export interface CachedItem {
  key: 'locations' | 'near' | 'trips' | 'shopping' | 'calendar' | 'reminders' | 'bible' | string;
  /** When the server last produced this content (ms): the "as of" time. */
  version: number;
  data: unknown;
}

/** A parent's command sent over Bluetooth and what became of it. */
export interface OfflineCommand {
  id: string;
  child: string;
  action: 'lock' | 'unlock' | 'bonus';
  arg: number;
  issued_at: number;
  /** Handed to the server (by any phone). */
  reported: boolean;
  /** The child's phone confirmed it (signed by that phone), seconds. */
  confirmed_at?: number;
  result?: string;
}

export interface OfflineDiagnostics {
  online: boolean;
  peerNearby: boolean;
  queued: number;
  carrying: number;
  cacheItems: number;
  commandsWaiting: number;
  snapshotAt: number | null;
  lastRelayAt: number | null;
  parentKeys: number;
  keysVersion: string;
  protectedPhone: boolean;
}

export interface OfflineModePluginInterface {
  queue(options: { kind: OfflineRequest['kind']; body: Record<string, unknown> }): Promise<{ id: string }>;
  requests(): Promise<{ requests: OfflineRequest[]; online: boolean; peerNearby: boolean }>;
  cache(): Promise<{ items: CachedItem[] }>;
  sendCommand(options: { child: string; action: OfflineCommand['action']; minutes?: number }): Promise<{ id: string }>;
  commands(): Promise<{ commands: OfflineCommand[] }>;
  getDiagnostics(): Promise<OfflineDiagnostics>;
}

const NEEDS_APP = 'Offline mode needs the Jarvis Android app';
const OfflineMode = registerPlugin<OfflineModePluginInterface>('JarvisOfflineMode', {
  web: () => ({
    queue: async () => Promise.reject(new Error(NEEDS_APP)),
    requests: async () => ({ requests: [], online: true, peerNearby: false }),
    cache: async () => ({ items: [] }),
    sendCommand: async () => Promise.reject(new Error(NEEDS_APP)),
    commands: async () => ({ commands: [] }),
    getDiagnostics: async () => Promise.reject(new Error(NEEDS_APP)),
  }),
});

export default OfflineMode;
