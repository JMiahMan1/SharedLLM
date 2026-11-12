import { apiClient } from '../services/api';

/**
 * Adding a new Wi-Fi Matter device over Bluetooth: whether the Matter Server has
 * a Bluetooth adapter, and the household Wi-Fi new devices are given
 * (services/homehub/matter_ble.py through the gateway, admins only). The hub
 * never sends the password back: only whether it is set, and the network name.
 */

export interface MatterWifiStatus {
  configured: boolean;
  ssid: string | null;
  updated_at: string | null;
}

export interface MatterSettings {
  /** `enabled` is null when the Matter Server could not be asked. `setting` names the .env key. */
  bluetooth: { enabled: boolean | null; setting: string };
  wifi: MatterWifiStatus;
}

export const MATTER_SETTINGS_KEY = ['homehub-matter-settings'];

export async function getMatterSettings(): Promise<MatterSettings> {
  const resp = await apiClient.get('/api/homehub/matter/settings');
  return resp.data;
}

export async function setMatterWifi(ssid: string, password: string): Promise<MatterWifiStatus> {
  const resp = await apiClient.put('/api/homehub/matter/wifi', { ssid, password });
  return resp.data;
}

export async function clearMatterWifi(): Promise<MatterWifiStatus> {
  const resp = await apiClient.delete('/api/homehub/matter/wifi');
  return resp.data;
}

/** What is wrong with a network name and password, the way the hub checks them; null when they are fine. */
export function wifiProblem(ssid: string, password: string): string | null {
  if (!ssid) return 'Enter the network name.';
  if (new TextEncoder().encode(ssid).length > 32) return 'A network name is at most 32 bytes.';
  if (!password) return 'Enter the password.';
  const hexKey = /^[0-9A-Fa-f]{64}$/.test(password);
  const printable = /^[\x20-\x7E]+$/.test(password);
  if (!hexKey && !(printable && password.length >= 8 && password.length <= 63)) {
    return 'A Wi-Fi password is 8 to 63 characters (or a 64 digit hex key).';
  }
  return null;
}

/** The progress steps the hub reports while it adds a device over Bluetooth. */
export const MATTER_BLE_STEPS: Record<string, string> = {
  matter_ble_prepare: 'Getting ready',
  matter_ble_commission: 'Adding over Bluetooth',
  matter_ble_register: 'Adding its controls',
};
