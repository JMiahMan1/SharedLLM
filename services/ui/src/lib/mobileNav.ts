import { navPagesFor } from '../components/layout/navItems';

/**
 * Layout of the phone's pinned bottom-bar pages.
 *
 * The choice is per device, not per account: the same person may want five
 * different pages on a phone and a tablet. Only paths are stored, never the
 * page objects, so the app keeps one source of truth for icons, labels and
 * which pages each role may open.
 */
export const MOBILE_NAV_STORAGE_KEY = 'jarvis_mobile_nav_v1';

/** Fired in this tab after a write; `storage` covers other tabs. */
export const MOBILE_NAV_CHANGED_EVENT = 'jarvis-mobile-nav-changed';

export const MAX_MOBILE_PINS = 5;

function allowedPaths(isAdmin: boolean): Set<string> {
  return new Set(navPagesFor(isAdmin).map((page) => page.path));
}

/** The shipped five, in their original `pinnedOnMobile` order. */
export function defaultMobileNavPaths(isAdmin: boolean): string[] {
  return navPagesFor(isAdmin)
    .filter((page) => page.pinnedOnMobile !== undefined)
    .sort((a, b) => (a.pinnedOnMobile ?? 0) - (b.pinnedOnMobile ?? 0))
    .slice(0, MAX_MOBILE_PINS)
    .map((page) => page.path);
}

/**
 * Drop paths this build or this role no longer offers, duplicates and
 * anything past the fifth slot. A stored empty list is kept: it is how the
 * user asks for a bar with only the More button.
 */
export function sanitizeMobileNavPaths(value: unknown, isAdmin: boolean): string[] {
  if (!Array.isArray(value)) return defaultMobileNavPaths(isAdmin);

  const allowed = allowedPaths(isAdmin);
  const seen = new Set<string>();
  const paths: string[] = [];

  for (const path of value) {
    if (typeof path !== 'string' || !allowed.has(path) || seen.has(path)) continue;
    seen.add(path);
    paths.push(path);
    if (paths.length === MAX_MOBILE_PINS) break;
  }

  // A stored empty list is deliberate (a bar with only More). A non-empty
  // list that no longer resolves means every saved page was removed or is
  // admin-only for this user, so fall back to the shipped five.
  if (paths.length === 0 && value.length > 0) return defaultMobileNavPaths(isAdmin);
  return paths;
}

/** Parse the raw localStorage value; missing or malformed data means defaults. */
export function parseMobileNavPaths(raw: string | null, isAdmin: boolean): string[] {
  if (raw === null) return defaultMobileNavPaths(isAdmin);
  try {
    return sanitizeMobileNavPaths(JSON.parse(raw), isAdmin);
  } catch {
    return defaultMobileNavPaths(isAdmin);
  }
}

export function readMobileNavPaths(isAdmin: boolean): string[] {
  try {
    return parseMobileNavPaths(localStorage.getItem(MOBILE_NAV_STORAGE_KEY), isAdmin);
  } catch {
    // Storage can be blocked (private windows, hardened WebViews). The menu
    // still works for this session with its defaults.
    return defaultMobileNavPaths(isAdmin);
  }
}

function announceChange(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(MOBILE_NAV_CHANGED_EVENT));
}

/** Validate, persist and announce a new layout. Returns what was stored. */
export function writeMobileNavPaths(paths: readonly string[], isAdmin: boolean): string[] {
  const clean = sanitizeMobileNavPaths(paths, isAdmin);
  try {
    localStorage.setItem(MOBILE_NAV_STORAGE_KEY, JSON.stringify(clean));
  } catch {
    // Storage can be unavailable; still announce so listeners re-read.
  }
  announceChange();
  return clean;
}

/** Forget this device's choice and go back to the shipped five. */
export function resetMobileNavPaths(isAdmin: boolean): string[] {
  try {
    localStorage.removeItem(MOBILE_NAV_STORAGE_KEY);
  } catch {
    // Nothing persisted to remove.
  }
  announceChange();
  return defaultMobileNavPaths(isAdmin);
}

/** Pin an allowed page at the end, or unpin it. Returns an unchanged copy at the cap. */
export function toggleMobileNavPath(paths: readonly string[], path: string, isAdmin: boolean): string[] {
  const current = sanitizeMobileNavPaths(paths, isAdmin);
  if (current.includes(path)) return current.filter((item) => item !== path);
  if (current.length >= MAX_MOBILE_PINS || !allowedPaths(isAdmin).has(path)) return current;
  return [...current, path];
}

/** Move one pinned page by one slot. Edges are no-ops. */
export function moveMobileNavPath(paths: readonly string[], path: string, direction: -1 | 1): string[] {
  const next = [...paths];
  const index = next.indexOf(path);
  const target = index + direction;
  if (index === -1 || target < 0 || target >= next.length) return next;
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  return next;
}

/**
 * Notify for both this tab's custom event and other tabs' `storage` event.
 * A `storage` event with a null key means the whole store was cleared.
 */
export function subscribeMobileNav(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.key === MOBILE_NAV_STORAGE_KEY || event.key === null) listener();
  };
  window.addEventListener(MOBILE_NAV_CHANGED_EVENT, listener);
  window.addEventListener('storage', onStorage);
  return () => {
    window.removeEventListener(MOBILE_NAV_CHANGED_EVENT, listener);
    window.removeEventListener('storage', onStorage);
  };
}
