import { registerPlugin } from '@capacitor/core';

/**
 * The phone's room-detection beacon (Android only): an iBeacon the hub's
 * Bluetooth listeners hear, so the hub can tell which room the phone is in.
 *
 * State, as the native side reports it:
 * - `advertising`: the beacon is on the air.
 * - `starting`: waiting for Android to confirm the start (a moment; never reported to the hub).
 * - `bluetooth_off`: switched on in Jarvis, waiting for Bluetooth to come back.
 * - `disabled`: switched off in Jarvis.
 * - `unavailable`: it cannot run; `reason` says why (no permission, the
 *   chipset cannot advertise, advertising failed). Never silent.
 */
export type BeaconState = 'advertising' | 'starting' | 'bluetooth_off' | 'disabled' | 'unavailable';

export interface BeaconStatus {
  enabled: boolean;
  state: BeaconState;
  /** Why the beacon is not advertising, in words a person can act on. */
  reason?: string | null;
  /** The per-install beacon id; generated once and kept. */
  uuid: string;
  major: number;
  minor: number;
  /** "ultra_low" | "low" | "medium" | "high" (Android's AdvertiseSettings TX power levels). */
  txPowerLevel: BeaconTxPower;
  /** Calibrated RSSI at 1 m, in dBm, carried in the iBeacon frame. */
  measuredPower: number;
  /** Whether BLUETOOTH_ADVERTISE (Android 12+) is granted; true on older Android. */
  permissionGranted: boolean;
  /** False on a child's phone: room detection is a safety feature there and stays on. */
  canDisable?: boolean;
  /** Whether the hub registered this beacon on the phone at the last report (null before one). */
  hubRegistered?: boolean | null;
  /** What the gateway or hub said at the last report, or why it could not be sent. */
  hubDetail?: string | null;
}

export type BeaconTxPower = 'ultra_low' | 'low' | 'medium' | 'high';

export interface BleBeaconPluginInterface {
  getStatus(): Promise<BeaconStatus>;
  /** Turns the beacon on (asks for the permission first when needed) or off. */
  setEnabled(options: { enabled: boolean }): Promise<BeaconStatus>;
  setTxPower(options: { level: BeaconTxPower }): Promise<BeaconStatus>;
  requestPermission(): Promise<BeaconStatus>;
  /** Android's app settings page, for a permission denied for good. */
  openSettings(): Promise<void>;
  /** The state changed (Bluetooth switched, the advertiser answered, a report went). */
  addListener(event: 'statusChange', cb: (status: BeaconStatus) => void): Promise<{ remove: () => Promise<void> }>;
}

/* On the web there is no beacon; callers check the platform first and treat
   a rejection as "not on this device". */
const webFallback: BleBeaconPluginInterface = {
  getStatus: async () => Promise.reject(new Error('Room detection needs the Jarvis Android app')),
  setEnabled: async () => Promise.reject(new Error('Room detection needs the Jarvis Android app')),
  setTxPower: async () => Promise.reject(new Error('Room detection needs the Jarvis Android app')),
  requestPermission: async () => Promise.reject(new Error('Room detection needs the Jarvis Android app')),
  openSettings: async () => undefined,
  addListener: async () => ({ remove: async () => undefined }),
};

const BleBeacon = registerPlugin<BleBeaconPluginInterface>('JarvisBleBeacon', {
  web: () => webFallback,
});

export default BleBeacon;
