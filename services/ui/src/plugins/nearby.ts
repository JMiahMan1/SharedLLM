import { registerPlugin } from '@capacitor/core';

/**
 * Settings > Nearby & offline on this Android phone: which parts run, what
 * Android allows, and where the Bluetooth LE engine runs (inside location
 * sharing's service, its own small service, or nowhere).
 */
export interface NearbyDeviceState {
  nearby: boolean;
  chat: boolean;
  offline: boolean;
  car: boolean;
  /** A child's phone: a parent's offline lock, unlock and extra time always reach it. */
  parentLink: boolean;
  scanPermission: boolean;
  advertisePermission: boolean;
  connectPermission: boolean;
  bluetoothOn: boolean;
  host: 'none' | 'location' | 'own';
  apiLevel: number;
}

export interface NearbyPluginInterface {
  getState(): Promise<NearbyDeviceState>;
  /** Save the parts; with `ask`, Android 12+ asks for what a part just turned on needs. */
  apply(options: { nearby?: boolean; chat?: boolean; offline?: boolean; car?: boolean; ask?: boolean }): Promise<NearbyDeviceState>;
  /** Ask for what the parts that are on still lack. */
  requestPermissions(): Promise<NearbyDeviceState>;
}

const NEEDS_APP = 'Nearby & offline needs the Jarvis Android app';
const Nearby = registerPlugin<NearbyPluginInterface>('JarvisNearby', {
  web: () => ({
    getState: async () => Promise.reject(new Error(NEEDS_APP)),
    apply: async () => Promise.reject(new Error(NEEDS_APP)),
    requestPermissions: async () => Promise.reject(new Error(NEEDS_APP)),
  }),
});

export default Nearby;
