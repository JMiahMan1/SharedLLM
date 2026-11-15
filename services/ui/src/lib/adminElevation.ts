/**
 * How long a passed admin check (phone PIN, fingerprint or the admin PIN)
 * holds in the Android app.
 *
 * It used to live only in the page's memory, so every web update (which
 * reloads the app in place) and every move between Admin and Lab asked for
 * the phone's credentials again, which read as "updates ask for my password".
 * The time is kept in sessionStorage: it survives a reload of the same app
 * session but not the app being closed or the phone restarting, so a phone
 * that was put down still asks.
 */
export const ADMIN_ELEVATION_MS = 15 * 60_000;
const KEY = 'jarvis_admin_elevated_until';

export function adminElevated(now: number = Date.now()): boolean {
  try {
    return Number(window.sessionStorage.getItem(KEY) || 0) > now;
  } catch {
    return false; // storage unavailable: ask, never assume
  }
}

export function markAdminElevated(now: number = Date.now()): void {
  try {
    window.sessionStorage.setItem(KEY, String(now + ADMIN_ELEVATION_MS));
  } catch {
    // the check still holds for this page; it is asked again after a reload
  }
}
