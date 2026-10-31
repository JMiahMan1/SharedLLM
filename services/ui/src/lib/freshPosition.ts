import { Capacitor } from '@capacitor/core';
import { Geolocation } from '@capacitor/geolocation';

/** A position the phone just took, for an SOS or a check-in. */
export interface FreshPosition {
  lat: number;
  lon: number;
  accuracy: number | null;
  /** When it was taken, epoch ms. */
  at: number;
}

/**
 * Ask the phone where it is now, giving up after ``timeoutMs`` (an SOS must
 * not wait on the GPS). A fix up to a minute old is fine. Null when the phone
 * cannot say: no permission, no signal, or too slow.
 */
export async function freshPosition(timeoutMs = 8000): Promise<FreshPosition | null> {
  const opts = { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 60_000 };
  const read = async (): Promise<FreshPosition | null> => {
    if (Capacitor.isNativePlatform()) {
      const pos = await Geolocation.getCurrentPosition(opts);
      return { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy ?? null, at: pos.timestamp };
    }
    if (typeof navigator === 'undefined' || !navigator.geolocation) return null;
    return new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy ?? null, at: pos.timestamp }),
        () => resolve(null),
        opts,
      );
    });
  };
  const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs + 500));
  try {
    return await Promise.race([read(), timeout]);
  } catch {
    return null;
  }
}
