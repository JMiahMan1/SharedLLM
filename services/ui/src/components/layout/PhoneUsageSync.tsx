import { useEffect } from 'react';
import { Capacitor } from '@capacitor/core';

import { storageGetSync } from '../../lib/storage';
import { getDeviceKey } from '../../lib/deviceRegistration';
import PhoneUsage from '../../plugins/phoneUsage';
import Guard from '../../plugins/guard';
import { CHECK_MS, readReportingAccess, within } from '../../lib/reportingAccess';

const USAGE_SYNC_INTERVAL_MS = 5 * 60 * 1000; // the day moves slowly; five minutes is plenty

let usageSyncInFlight = false;
// The night before last is sent once per app start, in case the app was not
// opened yesterday; last night is sent on every tick until noon has passed.
let olderNightSent = false;

/** The local calendar day, matching what the rest of the app stores. */
function localDay(now = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

/** 18:00 the evening before to noon on the morning of `daysAgo`'s night, local. */
function nightWindow(daysAgo: number): { night: string; since: number; until: number } {
  const noon = new Date();
  noon.setDate(noon.getDate() - daysAgo);
  noon.setHours(12, 0, 0, 0);
  const evening = new Date(noon);
  evening.setDate(evening.getDate() - 1);
  evening.setHours(18, 0, 0, 0);
  return { night: localDay(noon), since: evening.getTime(), until: Math.min(noon.getTime(), Date.now()) };
}

/** The screen-on stretches overnight, for the sleep estimate (times only). */
async function syncNight(serverUrl: string, token: string, daysAgo: number): Promise<void> {
  const window = nightWindow(daysAgo);
  if (window.until <= window.since) return;
  const reading = await PhoneUsage.getNight({ since: window.since, until: window.until });
  if (!reading?.intervals?.length) return;
  await fetch(`${serverUrl}/api/usage/night`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ night: window.night, device_key: await getDeviceKey(), intervals: reading.intervals }),
  });
}

/** What was last reported about this phone's protection, so it is sent only when it changes. */
let lastGuardReport = '';

/**
 * Jarvis's protection on this phone as it stands (device admin, screen guard,
 * web filter, usage access), for the father's Devices section of the family
 * member page. Android only: elsewhere nothing is guarded and nothing is sent.
 */
async function syncGuardState(serverUrl: string, token: string, usageAccess: boolean): Promise<void> {
  if (Capacitor.getPlatform() !== 'android') return;
  // A guard check that fails or never answers is reported as such, so the
  // permissions below still reach the parents.
  const [guard, access] = await Promise.all([
    within<Record<string, unknown>>(
      Guard.status().then((g) => ({ ...g })),
      CHECK_MS,
      { guardStatusUnavailable: true },
    ),
    readReportingAccess(),
  ]);
  // Calls, texts and notifications too: without them those reports are empty
  // and nobody could tell why (no phone had ever sent a call or a text).
  const state = {
    ...guard,
    usageAccess,
    notificationAccess: access.notifications,
    callLogAccess: access.calls,
    smsAccess: access.sms,
    ...(access.activity === null ? {} : { activityAccess: access.activity }),
  };
  const report = JSON.stringify(state);
  if (report === lastGuardReport) return;
  const resp = await fetch(`${serverUrl}/api/user-panel/devices/${encodeURIComponent(await getDeviceKey())}/capabilities`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ capabilities: { guard: { ...state, reportedAt: new Date().toISOString() } } }),
  });
  if (resp.ok) lastGuardReport = report;
}

/** One reading of today's phone usage, posted for the person signed in here. */
async function syncPhoneUsage(): Promise<void> {
  if (usageSyncInFlight) return;
  usageSyncInFlight = true;
  try {
    const access = await within(PhoneUsage.hasAccess(), CHECK_MS, { granted: false });
    const token = storageGetSync('jarvis_api_key');
    if (!token) return;
    const serverUrl = storageGetSync('jarvis_server_url') || window.location.origin;
    // Reported even without usage access: that being off is itself worth knowing.
    await syncGuardState(serverUrl, token, Boolean(access?.granted)).catch(() => undefined);
    if (!access?.granted) return;
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
        // A person's phone and tablet each report their own day; the server adds them up.
        device_key: await getDeviceKey(),
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
    try {
      await syncNight(serverUrl, token, 0);
      if (!olderNightSent) {
        await syncNight(serverUrl, token, 1);
        olderNightSent = true;
      }
    } catch {
      // An APK older than getNight, or a device that has not registered yet:
      // the day's usage above is unaffected.
    }
  } catch {
    // Quiet by design: usage access or the network may be missing, and the
    // next tick tries again.
  } finally {
    usageSyncInFlight = false;
  }
}

/**
 * Posts the phone's own usage (screen time, when use started and stopped, and
 * the day's top apps by foreground time) as it changes, the overnight
 * screen-on stretches the sleep estimate reads, and the state of Jarvis's
 * protection when it changes.
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
