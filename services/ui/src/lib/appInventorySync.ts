// The device's app report for app vetting (docs/APP_VETTING.md): sent from a
// child's or teen's Android device when the list changes, and once a day.
import { storageGetSync } from './storage';
import { getDeviceKey } from './deviceRegistration';
import { inventoryFingerprint, shouldSendInventory } from './appVetting';
import PhoneUsage from '../plugins/phoneUsage';

const LAST_SENT_KEY = 'jarvis_app_inventory_sent';

let inFlight = false;

function lastSent(): { fingerprint: string; at: number; user: string } | null {
  try {
    const raw = localStorage.getItem(LAST_SENT_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed.fingerprint === 'string' && typeof parsed.at === 'number' ? parsed : null;
  } catch {
    return null;
  }
}

/** Report this device's installed apps if they changed, or once a day. Exported for tests. */
export async function syncAppInventory(username: string, now: number = Date.now()): Promise<boolean> {
  if (inFlight) return false;
  inFlight = true;
  try {
    const token = storageGetSync('jarvis_api_key');
    if (!token) return false;
    const reading = await PhoneUsage.getInstalledApps();
    const fingerprint = inventoryFingerprint(reading.apps);
    if (!shouldSendInventory(lastSent(), fingerprint, username, now)) return false;
    const serverUrl = storageGetSync('jarvis_server_url') || window.location.origin;
    const resp = await fetch(`${serverUrl}/api/apps/inventory`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ device_key: await getDeviceKey(), apps: reading.apps, usage_access: reading.usageAccess }),
    });
    if (!resp.ok) return false;
    localStorage.setItem(LAST_SENT_KEY, JSON.stringify({ fingerprint, at: now, user: username }));
    return true;
  } catch {
    // An APK older than app vetting, or no network: the next check tries again.
    return false;
  } finally {
    inFlight = false;
  }
}
