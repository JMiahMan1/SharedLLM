import { registerPlugin } from '@capacitor/core';

/** One offline chat message as this phone holds it (opened: only rooms this person is in). */
export interface MeshMessage {
  id: string;
  t: 'text' | 'pin' | 'thumb';
  from: string;
  room: string;
  sent_at: number;
  text?: string;
  lat?: number;
  lon?: number;
  label?: string;
  mime?: string;
  /** A JPEG preview, base64. */
  data?: string;
  caption?: string;
  /** Written on this phone. */
  mine: boolean;
  /** Already in its chat room. */
  synced: boolean;
  /** How many phones it passed through to get here. */
  hops: number;
}

export interface MeshDiagnostics {
  enabled: boolean;
  connectPermission: boolean;
  /** How many of this person's chat rooms have an offline key on this phone. */
  rooms: number;
  /** This phone accepts offline chat connections (a GATT server is open). */
  server: boolean;
  /** The family member being synced with now, or null. */
  syncing: string | null;
  stored: number;
  /** This phone's own messages not yet in their chat room. */
  unsynced: number;
  hasMail: boolean;
  lastSentAt: number | null;
  lastReceivedAt: number | null;
  lastServerSyncAt: number | null;
  lastPeerSync?: { at: number; peer: string; sent: number; received: number };
  lastError: string | null;
}

/** A chat room this phone can use offline (its key stays on the phone). */
export interface MeshRoom {
  room: string;
  name: string;
  /** Talk room type: 1 one-to-one, 2 group, 3 public. */
  type: number;
}

export interface FamilyMeshPluginInterface {
  getDiagnostics(): Promise<MeshDiagnostics>;
  /** Turn the offline chat on (asks for Nearby devices' connect on Android 12+, only now). */
  enable(): Promise<MeshDiagnostics>;
  disable(): Promise<MeshDiagnostics>;
  /** The rooms that work offline, as last fetched (kept for when there is no internet). */
  rooms(): Promise<{ rooms: MeshRoom[] }>;
  /** Fetch the rooms and their keys again (online). */
  refreshRooms(): Promise<void>;
  send(options: { room: string; kind: 'text' | 'pin' | 'thumb'; fields: Record<string, unknown> }): Promise<{ id: string }>;
  /** What this phone holds and may read; one room's when `room` is given. */
  list(options?: { room?: string }): Promise<{ messages: MeshMessage[] }>;
  addListener(event: 'change', cb: () => void): Promise<{ remove: () => Promise<void> }>;
}

const NEEDS_APP = 'The offline chat needs the Jarvis Android app';
const FamilyMesh = registerPlugin<FamilyMeshPluginInterface>('JarvisFamilyMesh', {
  web: () => ({
    getDiagnostics: async () => Promise.reject(new Error(NEEDS_APP)),
    enable: async () => Promise.reject(new Error(NEEDS_APP)),
    disable: async () => Promise.reject(new Error(NEEDS_APP)),
    rooms: async () => ({ rooms: [] }),
    refreshRooms: async () => undefined,
    send: async () => Promise.reject(new Error(NEEDS_APP)),
    list: async () => ({ messages: [] }),
    addListener: async () => ({ remove: async () => undefined }),
  }),
});

export default FamilyMesh;
