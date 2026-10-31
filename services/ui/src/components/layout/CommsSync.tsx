import { useEffect } from 'react';

import { storageGetSync } from '../../lib/storage';
import Comms from '../../plugins/comms';

const COMMS_SYNC_INTERVAL_MS = 5 * 60 * 1000;

const callsSeen = new Set<number>();
const smsSeen = new Set<number>();

let commsSyncInFlight = false;

/** Whether this APK sends calls and texts itself (CommsCollector); false once an older one says no. */
let nativeSync = true;

/** One pass: pull new calls and SMS, post each as an activity event. */
async function syncComms(): Promise<void> {
  if (commsSyncInFlight) return;
  commsSyncInFlight = true;
  try {
    const access = await Comms.hasAccess();
    if (!access?.calls && !access?.sms) return;
    if (nativeSync) {
      // The APK reads oldest first and moves its place only after the server
      // took each one, the same pass its background worker runs while Jarvis
      // is closed, so nothing is sent twice or lost on a failed upload.
      try {
        await Comms.syncNow();
        return;
      } catch {
        // An APK from before syncNow: read the rows here, as it always did.
        nativeSync = false;
      }
    }
    const token = storageGetSync('jarvis_api_key');
    if (!token) return;
    const serverUrl = storageGetSync('jarvis_server_url') || window.location.origin;

    if (access.calls) {
      const result = await Comms.getNewCalls();
      for (const call of result?.calls ?? []) {
        if (callsSeen.has(call.id)) continue;
        callsSeen.add(call.id);
        await fetch(`${serverUrl}/api/activity`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            feature: 'calls',
            event: 'call',
            item: call.name || call.number,
            action: call.direction,
            duration_s: call.duration,
            detail: {
              number: call.number,
              direction: call.direction,
              at: call.at,
              source: 'phone',
            },
          }),
        });
      }
    }

    if (access.sms) {
      const result = await Comms.getNewSms();
      for (const message of result?.messages ?? []) {
        if (smsSeen.has(message.id)) continue;
        smsSeen.add(message.id);
        await fetch(`${serverUrl}/api/activity`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            feature: 'sms',
            event: 'message',
            item: message.name || message.number,
            action: message.direction,
            detail: {
              number: message.number,
              body: message.body,
              direction: message.direction,
              at: message.at,
              source: 'phone',
            },
          }),
        });
      }
    }
  } catch {
    // Quiet by design: permissions or the network may be missing; the next
    // tick tries again.
  } finally {
    commsSyncInFlight = false;
  }
}

/**
 * Sends new calls and SMS to the activity feed while Jarvis is open (the APK's
 * background worker covers the rest of the time).
 */
export default function CommsSync() {
  useEffect(() => {
    void syncComms();
    const timer = window.setInterval(() => {
      void syncComms();
    }, COMMS_SYNC_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  return null;
}
