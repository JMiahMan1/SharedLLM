import { registerPlugin } from '@capacitor/core';

/**
 * The phone's paired Bluetooth devices, for linking a car to Wander's
 * vehicles (Android only). Linking keys on the hardware address; the name is
 * for people to read and may change.
 */
export interface VehicleBtStatus {
  /** The phone has Bluetooth at all. */
  supported: boolean;
  /** Nearby devices (BLUETOOTH_CONNECT, Android 12+) is granted; always true before Android 12. */
  permissionGranted: boolean;
  bluetoothOn: boolean;
}

export interface BondedDevice {
  name: string;
  /** Normalised hardware address, AA:BB:CC:DD:EE:FF. */
  address: string;
  type: 'classic' | 'le' | 'dual' | 'unknown';
  connected: boolean;
  /** A stable address that can be linked. */
  linkable: boolean;
  /** classic | le_public | le_static, when linkable. */
  kind: 'classic' | 'le_public' | 'le_static' | null;
  /** Why it cannot be linked, in words a person can act on. */
  reason: string | null;
  /** The vehicle it is linked to already, by name, if any (from the last vehicles read). */
  linkedTo: string | null;
}

/** What Wander's admin diagnostics show for vehicle detection on this phone. */
export interface VehicleBtDiagnostics extends VehicleBtStatus {
  apiLevel: number;
  /** Linked devices connected now: what the next location fix carries. */
  linkedConnected: Array<{ address: string; vehicle: string | null }>;
  /** Other connected devices, as a count only. */
  otherConnectedCount: number;
  /** Linked addresses the phone knows about (the whole household's). */
  linkedCount: number;
  /** When the phone last read the household's links (ms), or null. */
  linksRefreshedAt: number | null;
}

export interface VehicleBluetoothPluginInterface {
  getDiagnostics(): Promise<VehicleBtDiagnostics>;
  getStatus(): Promise<VehicleBtStatus>;
  requestPermission(): Promise<VehicleBtStatus>;
  listDevices(): Promise<VehicleBtStatus & { devices: BondedDevice[] }>;
  /** Hand the phone the vehicles just read (or changed) so it knows the linked addresses at once. */
  setVehicles(answer: { vehicles: unknown[] }): Promise<void>;
  openSettings(): Promise<void>;
  openBluetoothSettings(): Promise<void>;
}

const NEEDS_APP = 'Linking a car needs the Jarvis Android app';

const webFallback: VehicleBluetoothPluginInterface = {
  getDiagnostics: async () => Promise.reject(new Error(NEEDS_APP)),
  getStatus: async () => Promise.reject(new Error(NEEDS_APP)),
  requestPermission: async () => Promise.reject(new Error(NEEDS_APP)),
  listDevices: async () => Promise.reject(new Error(NEEDS_APP)),
  setVehicles: async () => undefined,
  openSettings: async () => undefined,
  openBluetoothSettings: async () => undefined,
};

const VehicleBluetooth = registerPlugin<VehicleBluetoothPluginInterface>('JarvisVehicleBluetooth', {
  web: () => webFallback,
});

export default VehicleBluetooth;
