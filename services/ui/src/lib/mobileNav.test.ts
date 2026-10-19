import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_MOBILE_PINS,
  MOBILE_NAV_STORAGE_KEY,
  defaultMobileNavPaths,
  moveMobileNavPath,
  parseMobileNavPaths,
  readMobileNavPaths,
  resetMobileNavPaths,
  sanitizeMobileNavPaths,
  subscribeMobileNav,
  toggleMobileNavPath,
  writeMobileNavPaths,
} from './mobileNav';

const DEFAULTS = ['/', '/wander', '/family', '/media', '/fitness'];

describe('mobileNav', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to the shipped pinnedOnMobile order', () => {
    expect(defaultMobileNavPaths(true)).toEqual(DEFAULTS);
    expect(defaultMobileNavPaths(false)).toEqual(DEFAULTS);
  });

  it('falls back to defaults when nothing or junk is stored', () => {
    expect(parseMobileNavPaths(null, true)).toEqual(DEFAULTS);
    expect(parseMobileNavPaths('{not json', true)).toEqual(DEFAULTS);
    expect(readMobileNavPaths(false)).toEqual(DEFAULTS);
  });

  it('keeps a valid custom order and drops stale, duplicate or unknown paths', () => {
    expect(
      sanitizeMobileNavPaths(['/notes', '/', '/notes', '/removed-page', 7, '/calendar'], false),
    ).toEqual(['/notes', '/', '/calendar']);
  });

  it('caps the bar at five pages', () => {
    const six = ['/notes', '/calendar', '/bible', '/remote', '/docs', '/settings'];
    expect(sanitizeMobileNavPaths(six, false)).toEqual(six.slice(0, MAX_MOBILE_PINS));
  });

  it('only accepts admin pages for admins', () => {
    expect(parseMobileNavPaths(JSON.stringify(['/admin', '/lab', '/notes']), false))
      .toEqual(['/notes']);
    expect(parseMobileNavPaths(JSON.stringify(['/admin', '/lab']), false)).toEqual(DEFAULTS);
    expect(parseMobileNavPaths(JSON.stringify(['/admin', '/lab', '/notes']), true))
      .toEqual(['/admin', '/lab', '/notes']);
  });

  it('keeps a deliberately empty layout instead of restoring defaults', () => {
    expect(parseMobileNavPaths('[]', false)).toEqual([]);
  });

  it('persists, announces and reads the sanitized layout', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeMobileNav(listener);

    const saved = writeMobileNavPaths(['/notes', '/admin', '/'], false);

    expect(saved).toEqual(['/notes', '/']);
    expect(JSON.parse(localStorage.getItem(MOBILE_NAV_STORAGE_KEY)!)).toEqual(saved);
    expect(readMobileNavPaths(false)).toEqual(saved);
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    writeMobileNavPaths(['/calendar'], false);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('resets to the shipped order and removes the stored choice', () => {
    writeMobileNavPaths(['/notes'], true);
    const listener = vi.fn();
    const unsubscribe = subscribeMobileNav(listener);

    expect(resetMobileNavPaths(true)).toEqual(DEFAULTS);
    expect(localStorage.getItem(MOBILE_NAV_STORAGE_KEY)).toBeNull();
    expect(readMobileNavPaths(true)).toEqual(DEFAULTS);
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it('toggles pages up to the cap and refuses admin pages to non-admins', () => {
    expect(toggleMobileNavPath(DEFAULTS, '/notes', false)).toEqual(DEFAULTS);
    expect(toggleMobileNavPath(DEFAULTS, '/admin', false)).toEqual(DEFAULTS);
    expect(toggleMobileNavPath(DEFAULTS, '/admin', true)).toHaveLength(5);

    const withRoom = DEFAULTS.slice(0, 4);
    expect(toggleMobileNavPath(withRoom, '/notes', false)).toEqual([...withRoom, '/notes']);
    expect(toggleMobileNavPath([...withRoom, '/notes'], '/notes', false)).toEqual(withRoom);
  });

  it('moves a page one slot and treats edges as no-ops', () => {
    expect(moveMobileNavPath(DEFAULTS, '/family', -1))
      .toEqual(['/', '/family', '/wander', '/media', '/fitness']);
    expect(moveMobileNavPath(DEFAULTS, '/family', 1))
      .toEqual(['/', '/wander', '/media', '/family', '/fitness']);
    expect(moveMobileNavPath(DEFAULTS, '/', -1)).toEqual(DEFAULTS);
    expect(moveMobileNavPath(DEFAULTS, '/fitness', 1)).toEqual(DEFAULTS);
    expect(moveMobileNavPath(DEFAULTS, '/notes', 1)).toEqual(DEFAULTS);
  });
});
