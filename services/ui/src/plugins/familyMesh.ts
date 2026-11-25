import { registerPlugin } from '@capacitor/core';

/** One offline family chat message as this phone holds it (opened). */
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
  /** Already in the family chat. */
  synced: boolean;
  /** How many phones it passed through to get here. */
  hops: number;
}

export interface MeshDiagnostics {
  enabled: boolean;
  connectPermission: boolean;
  /** A family chat is chosen for the messages to go to online. */
  room: boolean;
  /** This phone accepts offline chat connections (a GATT server is open). */
  server: boolean;
  /** The family member being synced with now, or null. */
  syncing: string | null;
  stored: number;
  /** This phone's own messages not yet in the family chat. */
  unsynced: number;
  hasMail: boolean;
  lastSentAt: number | null;
  lastReceivedAt: number | null;
  lastServerSyncAt: number | null;
  lastPeerSync?: { at: number; peer: string; sent: number; received: number };
  lastError: string | null;
}

export interface FamilyMeshPluginInterface {
  getDiagnostics(): Promise<MeshDiagnostics>;
  /** Turn the offline chat on (asks for Nearby devices' connect on Android 12+, only now). */
  enable(): Promise<MeshDiagnostics>;
  disable(): Promise<MeshDiagnostics>;
  setRoom(options: { room: string }): Promise<void>;
  send(options: { kind: 'text' | 'pin' | 'thumb'; fields: Record<string, unknown> }): Promise<{ id: string }>;
  list(): Promise<{ messages: MeshMessage[] }>;
  addListener(event: 'change', cb: () => void): Promise<{ remove: () => Promise<void> }>;
}

const NEEDS_APP = 'The offline chat needs the Jarvis Android app';
const FamilyMesh = registerPlugin<FamilyMeshPluginInterface>('JarvisFamilyMesh', {
  web: () => ({
    getDiagnostics: async () => Promise.reject(new Error(NEEDS_APP)),
    enable: async () => Promise.reject(new Error(NEEDS_APP)),
    disable: async () => Promise.reject(new Error(NEEDS_APP)),
    setRoom: async () => undefined,
    send: async () => Promise.reject(new Error(NEEDS_APP)),
    list: async () => ({ messages: [] }),
    addListener: async () => ({ remove: async () => undefined }),
  }),
});

export default FamilyMesh;
