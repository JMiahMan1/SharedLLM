import { useEffect } from 'react';

import { storageGetSync } from '../../lib/storage';
import PhoneUsage from '../../plugins/phoneUsage';

const USAGE_SYNC_INTERVAL_MS = 5 * 60 * 1000; // the day moves slowly; five minutes is plenty

let usageSyncInFlight = false;

/** The local calendar day, matching what the rest of the app stores. */
function localDay(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

/** One reading of today's phone usage, posted for the person signed in here. */
async function syncPhoneUsage(): Promise<void> {
  if (usageSyncInFlight) return;
  usageSyncInFlight = true;
  try {
    const access = await PhoneUsage.hasAccess();
    if (!access?.granted) return;
    const token = storageGetSync('jarvis_api_key');
    if (!token) return;
    const serverUrl = storageGetSync('jarvis_server_url') || window.location.origin;
    const today = await PhoneUsage.getToday();
    if (!today || !today.millis) return;
    await fetch(`${serverUrl}/api/usage`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        day: localDay(),
        millis: today.millis,
        first_use: today.firstUse,
        last_use: today.lastUse,
        sessions: today.sessions,
        apps: (today.apps ?? []).map((app) => ({
          package: app.package,
          millis: app.millis,
          last_use: app.lastUse,
        })),
        source: 'phone',
      }),
    });
  } catch {
    // Quiet by design: usage access or the network may be missing, and the
    // next tick tries again.
  } finally {
    usageSyncInFlight = false;
  }
}

/**
 * Posts the phone's own usage (screen time, when use started and stopped, and
 * the day's top apps by foreground time) as it changes.
 */
export default function PhoneUsageSync() {
  useEffect(() => {
    void syncPhoneUsage();
    const timer = window.setInterval(() => {
      void syncPhoneUsage();
    }, USAGE_SYNC_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  return null;
}
