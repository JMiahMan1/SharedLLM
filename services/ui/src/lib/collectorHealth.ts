/**
 * Whether a child's phone is still collecting calls, texts and screen time
 * (GET /api/guardian/collector-health/{user}), in plain words for the parents'
 * Calls & messages view.
 *
 * Two sources: what each phone reports about itself (a missing permission, a
 * background worker that is not scheduled, a collector that stopped), and the
 * server's own check (other activity kept arriving long after the last call,
 * text or screen-time day), which also covers phones too old to report.
 */

export interface CollectorRun {
  at?: number | null;
  ok?: boolean;
  outcome?: string;
  message?: string;
  last_ok_at?: number | null;
}

export interface DeviceHealthProblem {
  kind: 'permission' | 'worker' | 'collector';
  what: string;
  state?: string;
  outcome?: string;
  message?: string;
}

export interface DeviceHealthReport {
  device_id: string;
  device_name?: string;
  app_version?: string;
  received_at?: string;
  permissions?: Record<string, boolean>;
  workers?: Record<string, string>;
  collectors?: Record<string, CollectorRun>;
  problems?: DeviceHealthProblem[];
}

export interface CollectorStaleness {
  comms?: { stale?: boolean; last_at?: string | null };
  usage?: { stale?: boolean; last_at?: string | null };
  other_last_at?: string | null;
}

export interface CollectorHealth {
  devices?: DeviceHealthReport[];
  staleness?: CollectorStaleness;
}

export interface CollectionNotice {
  key: string;
  text: string;
}

/** What each permission lets the phone collect, as a parent would name it. */
export const PERMISSION_LABELS: Record<string, string> = {
  read_sms: 'Text messages',
  read_call_log: 'Call log',
  usage_access: 'Usage access (screen time)',
  notification_listener: 'Notification access (app messages)',
  location: 'Location',
  bluetooth: 'Nearby devices (which car a trip was in)',
  bluetooth_scan: 'Nearby devices (family phones nearby)',
};

const WORKER_LABELS: Record<string, string> = {
  comms_sync: 'calls and texts',
  usage_sync: 'screen time',
  photo_scan: 'photo checks',
};

/** A failed upload is only worth a parent's attention once it has lasted a while. */
export const UPLOAD_FAILING_MS = 6 * 60 * 60 * 1000;

function shortDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;
  return when.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function listWords(items: string[]): string {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/**
 * Every reason a child's phone may not be collecting, one line each. Empty
 * when all is well (or nothing is known). `name` and `noun` come from data
 * ("Sam", "phone" or "tablet"); nothing here names a person.
 */
export function collectionNotices(
  health: CollectorHealth | null | undefined,
  name: string,
  noun = 'phone',
  now: number = Date.now(),
): CollectionNotice[] {
  if (!health) return [];
  const out: CollectionNotice[] = [];
  const whose = `${name}'s ${noun}`;
  const restart = `Open Jarvis on the ${noun} to restart collection.`;
  const stale = health.staleness;

  if (stale?.comms?.stale) {
    const since = shortDate(stale.comms.last_at);
    out.push({
      key: 'stale-comms',
      text: `${whose} has not sent texts or calls${since ? ` since ${since}` : ' for a while'}. ${restart}`,
    });
  }
  if (stale?.usage?.stale) {
    const since = shortDate(stale.usage.last_at);
    out.push({
      key: 'stale-usage',
      text: `${whose} has not sent screen time${since ? ` since ${since}` : ' for a while'}. ${restart}`,
    });
  }

  const devices = health.devices ?? [];
  for (const device of devices) {
    const which = devices.length > 1 && device.device_name ? `${whose} (${device.device_name})` : whose;
    const problems = device.problems ?? [];
    const missing = problems
      .filter((p) => p.kind === 'permission')
      .map((p) => PERMISSION_LABELS[p.what] ?? p.what);
    if (missing.length) {
      out.push({
        key: `${device.device_id}-permissions`,
        text: `${which} is missing permission for ${listWords(missing)}. Open Jarvis on the ${noun} and allow ${missing.length === 1 ? 'it' : 'them'}.`,
      });
    }
    const signedOut = problems.some((p) => p.kind === 'collector' && p.outcome === 'signed_out');
    if (signedOut) {
      out.push({
        key: `${device.device_id}-signed-out`,
        text: `${which} is signed out of Jarvis, so nothing is collected. Sign in on the ${noun}.`,
      });
    }
    const off = problems
      .filter((p) => p.kind === 'worker')
      .map((p) => WORKER_LABELS[p.what] ?? p.what);
    if (off.length && !signedOut) {
      out.push({
        key: `${device.device_id}-workers`,
        text: `Background collection of ${listWords(off)} is not running on ${which}. ${restart}`,
      });
    }
    // Family nearby (Bluetooth LE): said plainly, e.g. a tablet that can only scan.
    const nearby = problems.find((p) => p.kind === 'collector' && p.what === 'proximity' && p.outcome !== 'no_permission');
    if (nearby?.message) {
      out.push({ key: `${device.device_id}-proximity`, text: `Family nearby on ${which}: ${nearby.message}.` });
    }
    for (const p of problems) {
      // A listener-started run is a comms run: the comms collector's line covers it.
      if (p.kind !== 'collector' || p.outcome !== 'upload_failed' || p.what === 'listener') continue;
      const run = device.collectors?.[p.what];
      const lastOk = typeof run?.last_ok_at === 'number' ? run.last_ok_at : null;
      if (lastOk !== null && now - lastOk < UPLOAD_FAILING_MS) continue;
      const what = p.what === 'usage' ? 'screen time' : 'calls and texts';
      const since = lastOk !== null ? shortDate(new Date(lastOk).toISOString()) : null;
      out.push({
        key: `${device.device_id}-upload-${p.what}`,
        text: `${which} could not upload ${what}${since ? ` since ${since}` : ''}${p.message ? ` (${p.message})` : ''}.`,
      });
    }
  }
  // The listener and the comms run fail together; one line is enough.
  const seen = new Set<string>();
  return out.filter((n) => (seen.has(n.text) ? false : (seen.add(n.text), true)));
}
