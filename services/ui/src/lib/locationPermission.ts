import { Geolocation } from '@capacitor/geolocation';
import type { PermissionStatus } from '@capacitor/geolocation';

/**
 * What the app may read: precise GPS, approximate only, or nothing.
 *
 * The Geolocation plugin asks for permission itself inside every
 * `watchPosition` and `getCurrentPosition` call that is not yet allowed, and
 * with `enableHighAccuracy` it asks for PRECISE location. So starting a watch
 * and a first fix back to back opened two dialogs, any other screen reading the
 * position opened another, and a phone allowed only "Approximate" was asked
 * again on every high-accuracy read. Everything now goes through
 * `ensureLocationAccess`, which asks at most once at a time, and callers pass
 * `highAccuracy(access)` so a read never asks for more than was allowed.
 */
export type LocationAccess = 'precise' | 'approximate' | 'denied';

export function accessFrom(status: Pick<PermissionStatus, 'location' | 'coarseLocation'>): LocationAccess | null {
  if (status.location === 'granted') return 'precise';
  if (status.coarseLocation === 'granted') return 'approximate';
  if (status.location === 'denied' && status.coarseLocation === 'denied') return 'denied';
  return null; // not answered yet
}

let inFlight: Promise<LocationAccess> | null = null;

/**
 * The access the app has, asking once if the person has not answered yet.
 *
 * Concurrent callers share one request. An approximate grant is accepted as
 * it is: asking again for precise would show the dialog the person just
 * answered. `ask: false` only reads the current state and never shows a dialog
 * (for background work that must not interrupt).
 */
export async function ensureLocationAccess(opts: { ask?: boolean } = {}): Promise<LocationAccess> {
  const current = accessFrom(await Geolocation.checkPermissions());
  if (current === 'precise' || current === 'approximate') return current;
  if (opts.ask === false) return 'denied';
  // Ask even when the state reads "denied": Capacitor reports any permission
  // it has asked for before as denied, so treating that as final meant turning
  // location back on in Settings could never show the dialog again. After a
  // permanent "Don't allow" Android answers without showing anything.
  if (!inFlight) {
    inFlight = Geolocation.requestPermissions()
      .then((status) => accessFrom(status) ?? 'denied')
      .finally(() => {
        inFlight = null;
      });
  }
  return inFlight;
}

/** Whether a read may use high accuracy without the plugin re-asking for precise location. */
export function highAccuracy(access: LocationAccess): boolean {
  return access === 'precise';
}

/** Test hook: forget a request left in flight. */
export function resetLocationAccessForTests(): void {
  inFlight = null;
}
