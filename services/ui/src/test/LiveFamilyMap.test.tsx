import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { server } from './setup';
import LiveFamilyMap from '../components/geo/LiveFamilyMap';
import {
  STALE_MAX_AGE_MS,
  ageLabel,
  buildLiveMembers,
  classifyLocation,
  clusterKey,
  clusterMembers,
  distanceMeters,
  placeKeyFor,
  spiderOffsets,
} from '../components/geo/liveLocations';

function renderMap() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LiveFamilyMap />
    </QueryClientProvider>
  );
}

describe('classifyLocation', () => {
  const now = 1_700_000_000_000;
  const nowSec = now / 1000;

  it('marks a fresh fix as live', () => {
    const m = classifyLocation(
      'jeremiah',
      { latitude: 33.4, longitude: -112.0, updated_at: nowSec - 30 },
      now
    );
    expect(m?.freshness).toBe('live');
  });

  it('marks a few-minutes-old fix as recent', () => {
    const m = classifyLocation(
      'jeremiah',
      { latitude: 33.4, longitude: -112.0, updated_at: nowSec - 300 },
      now
    );
    expect(m?.freshness).toBe('recent');
  });

  it('marks an old fix as stale without claiming a cause', () => {
    const m = classifyLocation(
      'jeremiah',
      { latitude: 33.4, longitude: -112.0, updated_at: nowSec - 3600 },
      now
    );
    expect(m?.freshness).toBe('stale');
  });

  it('ages a re-sent fix by when it was taken, not when it arrived', () => {
    const m = classifyLocation(
      'michele',
      { latitude: 33.4, longitude: -112.0, timestamp: nowSec - 3600, updated_at: nowSec - 5 },
      now
    );
    expect(m?.freshness).toBe('stale');
  });

  it('rejects invalid and null-island fixes', () => {
    expect(classifyLocation('x', { latitude: NaN, longitude: 1 })).toBeNull();
    expect(classifyLocation('x', { latitude: 0, longitude: 0 })).toBeNull();
  });
});

describe('clustering nearby members', () => {
  const now = 1_700_000_000_000;
  const nowSec = now / 1000;

  it('measures distance between coordinates', () => {
    // ~111 m per 0.001 degrees of latitude
    const d = distanceMeters(33.0, -112.0, 33.001, -112.0);
    expect(d).toBeGreaterThan(100);
    expect(d).toBeLessThan(125);
  });

  it('collapses one phone posting under two keys into a single pin', () => {
    const members = [
      { userId: 'jeremiah', lat: 33.4484, lon: -112.074, accuracy: 15, ageMs: 20_000, freshness: 'live' as const },
      { userId: 'default', lat: 33.44845, lon: -112.07403, accuracy: 60, ageMs: 200_000, freshness: 'recent' as const },
    ];
    const clusters = clusterMembers(members);
    expect(clusters).toHaveLength(1);
    // the freshest member leads
    expect(clusters[0].lead.userId).toBe('jeremiah');
    expect(clusters[0].members.map((m) => m.userId)).toContain('default');
  });

  it('keeps genuinely separate members apart', () => {
    const members = [
      { userId: 'a', lat: 33.4484, lon: -112.074, accuracy: 10, ageMs: 10_000, freshness: 'live' as const },
      { userId: 'b', lat: 33.5, lon: -112.1, accuracy: 10, ageMs: 10_000, freshness: 'live' as const },
    ];
    expect(clusterMembers(members)).toHaveLength(2);
  });

  it('brings people in the same place together however far the fixes scatter', () => {
    const home = { id: 'zone.home', name: 'Home', lat: 33.45, lon: -112.08, radius: 10000 };
    // Same as the "separate" pair above: ~7 km apart, so distance alone would
    // never merge them - but both are inside home.
    const members = [
      { userId: 'a', lat: 33.4484, lon: -112.074, accuracy: 10, ageMs: 10_000, freshness: 'live' as const },
      { userId: 'b', lat: 33.5, lon: -112.1, accuracy: 10, ageMs: 10_000, freshness: 'live' as const },
    ];
    expect(clusterMembers(members, 60, (m) => placeKeyFor(m, [home]))).toHaveLength(1);
  });

  it('names the smallest place a point is standing in', () => {
    const town = { id: 'zone.town', name: 'Town', lat: 33.45, lon: -112.08, radius: 5000 };
    const school = { id: 'zone.school', name: 'School', lat: 33.4484, lon: -112.074, radius: 100 };
    expect(placeKeyFor({ lat: 33.4484, lon: -112.074 }, [town, school])).toBe('zone.school');
    expect(placeKeyFor({ lat: 34.0, lon: -112.0 }, [town, school])).toBeNull();
  });

  it('groups pins that would overlap on screen, with no place and beyond the metre radius', () => {
    // ~400 m apart and no saved place covers them: metres alone keep them apart.
    const members = [
      { userId: 'a', lat: 33.4484, lon: -112.074, accuracy: 10, ageMs: 10_000, freshness: 'live' as const },
      { userId: 'b', lat: 33.4520, lon: -112.074, accuracy: 10, ageMs: 5_000, freshness: 'live' as const },
    ];
    expect(clusterMembers(members, 150)).toHaveLength(2);
    // Zoomed out, ~10 m to the pixel: 40 px apart, so the avatars would overlap.
    const zoomedOut = (m: { lat: number; lon: number }) => ({ x: m.lon * 1e4, y: -m.lat * 1e4 });
    const out = clusterMembers(members, 150, undefined, { project: zoomedOut, pixelRadius: 40 });
    expect(out).toHaveLength(1);
    expect(out[0].lead.userId).toBe('b');
    // Zoomed in, the same pair is hundreds of pixels apart and splits again.
    const zoomedIn = (m: { lat: number; lon: number }) => ({ x: m.lon * 1e5, y: -m.lat * 1e5 });
    expect(clusterMembers(members, 150, undefined, { project: zoomedIn, pixelRadius: 40 })).toHaveLength(2);
  });

  it('chains neighbours into one group instead of stacking a second pin on the first', () => {
    // a-b and b-c are each ~110 m apart; a-c is ~220 m. A greedy pass that saw
    // a, then c, then b left c on its own pin right next to the group.
    const members = [
      { userId: 'a', lat: 33.0, lon: -112.0, accuracy: 10, ageMs: 10_000, freshness: 'live' as const },
      { userId: 'c', lat: 33.002, lon: -112.0, accuracy: 10, ageMs: 10_000, freshness: 'live' as const },
      { userId: 'b', lat: 33.001, lon: -112.0, accuracy: 10, ageMs: 10_000, freshness: 'live' as const },
    ];
    const clusters = clusterMembers(members, 150);
    expect(clusters).toHaveLength(1);
    expect(clusterKey(clusters[0])).toBe('a|b|c');
  });

  it('fans a group out into pins that do not touch', () => {
    for (const count of [2, 3, 5, 8]) {
      const offsets = spiderOffsets(count);
      expect(offsets).toHaveLength(count);
      for (let i = 0; i < count; i++) {
        for (let j = i + 1; j < count; j++) {
          const gap = Math.hypot(offsets[i].x - offsets[j].x, offsets[i].y - offsets[j].y);
          expect(gap).toBeGreaterThanOrEqual(34);
        }
      }
    }
  });

  it('ignores staleness when distance is what matters', () => {
    const m = classifyLocation(
      'jeremiah',
      { latitude: 33.4484, longitude: -112.074, updated_at: nowSec - 10 },
      now
    );
    expect(m).not.toBeNull();
  });
});

describe('buildLiveMembers', () => {
  const now = 1_700_000_000_000;
  const nowSec = now / 1000;

  it('keeps a stale member as a dimmed last-seen pin', () => {
    const members = buildLiveMembers(
      { jeremiah: { latitude: 33.4, longitude: -112.0, updated_at: nowSec - 3600 } },
      now
    );
    expect(members).toHaveLength(1);
    expect(members[0].freshness).toBe('stale');
  });

  it('drops a member whose last fix is older than the 24 h cap', () => {
    const members = buildLiveMembers(
      { ghost: { latitude: 33.4, longitude: -112.0, updated_at: nowSec - STALE_MAX_AGE_MS / 1000 - 60 } },
      now
    );
    expect(members).toHaveLength(0);
  });
});

describe('ageLabel', () => {  it('formats freshness in human terms', () => {
    expect(ageLabel(5_000)).toBe('just now');
    expect(ageLabel(60_000)).toBe('1 min ago');
    expect(ageLabel(5 * 60_000)).toBe('5 min ago');
    expect(ageLabel(60 * 60_000)).toBe('1 hr ago');
    expect(ageLabel(3 * 60 * 60_000)).toBe('3 hrs ago');
  });
});

describe('LiveFamilyMap', () => {
  const nowSec = Date.now() / 1000;

  beforeEach(() => {
    server.use(
      http.get('/api/users/location/all', () =>
        HttpResponse.json({
          jeremiah: { latitude: 33.4484, longitude: -112.074, accuracy: 15, updated_at: nowSec - 20 },
          work: { latitude: 33.5, longitude: -112.1, accuracy: 40, updated_at: nowSec - 300 },
        })
      ),
      http.get('/api/users/family-directory', () =>
        HttpResponse.json([
          {
            username: 'jeremiah',
            display_name: 'Jeremiah',
            nextcloud_user: 'jeremiah',
            avatar: 'preset:jonahs-whale',
          },
        ])
      ),
    );
  });

  afterEach(() => {
    server.resetHandlers();
    vi.restoreAllMocks();
  });

  it('shows only members with a recent fix', async () => {
    renderMap();
    await waitFor(() => {
      expect(screen.getByTestId('live-map-count')).toHaveTextContent('2 sharing location');
    });
  });

  it("draws the member's picture inside the pin, with initials for anyone else", async () => {
    renderMap();
    await waitFor(() => {
      expect(screen.getByTestId('live-map-count')).toHaveTextContent('2 sharing location');
    });

    const pins = await waitFor(() => {
      const found = [...document.querySelectorAll('[data-testid="map-avatar-marker"]')];
      expect(found).toHaveLength(2);
      expect(found.some((pin) => pin.getAttribute('data-avatar-kind') === 'preset')).toBe(true);
      return found;
    });
    const kinds = pins.map((pin) => pin.getAttribute('data-avatar-kind'));
    expect(kinds).toContain('preset');
    expect(kinds).toContain('initials');
    const picture = pins.find((pin) => pin.getAttribute('data-avatar-kind') === 'preset');
    expect(picture).toHaveAttribute('aria-label', 'Jeremiah');
    expect(picture?.textContent).toContain('🐋');
  });

  it('explains when nobody is sharing location', async () => {
    server.use(http.get('/api/users/location/all', () => HttpResponse.json({})));
    renderMap();
    await waitFor(() => {
      expect(screen.getByTestId('live-map-count')).toHaveTextContent(
        'No one is sharing location right now'
      );
    });
  });

  it('shows a last-seen pin for a member whose tracking stopped', async () => {
    server.use(
      http.get('/api/users/location/all', () =>
        HttpResponse.json({
          offline_user: {
            latitude: 33.4,
            longitude: -112.0,
            updated_at: nowSec - 60 * 60,
          },
        })
      )
    );
    renderMap();
    await waitFor(() => {
      expect(screen.getByTestId('live-map-count')).toHaveTextContent('1 last seen');
    });
  });

  it('does not claim sharing is off, because a stale fix has two causes', async () => {
    // A stale position means either the user turned sharing off OR their app
    // stopped reporting. This map only ever sees other people's devices, so it
    // cannot tell -- and it was wrong in production: sharing was demonstrably
    // on while the footer said otherwise.
    server.use(
      http.get('/api/users/location/all', () =>
        HttpResponse.json({
          offline_user: {
            latitude: 33.4,
            longitude: -112.0,
            updated_at: nowSec - 60 * 60,
          },
        })
      )
    );
    renderMap();
    // Gate on the text, not the element: live-map-count renders immediately
    // with "No one is sharing" before the query settles, so findByTestId alone
    // reads the pre-data state.
    await waitFor(() =>
      expect(screen.getByTestId('live-map-count')).toHaveTextContent('1 last seen')
    );
    expect(screen.getByTestId('live-map-count')).not.toHaveTextContent(/sharing is off/i);
  });

  it('counts sharing and last-seen members separately', async () => {
    server.use(
      http.get('/api/users/location/all', () =>
        HttpResponse.json({
          jeremiah: { latitude: 33.4484, longitude: -112.074, accuracy: 15, updated_at: nowSec - 20 },
          offline_user: { latitude: 33.4, longitude: -112.0, updated_at: nowSec - 60 * 60 },
        })
      )
    );
    renderMap();
    await waitFor(() => {
      expect(screen.getByTestId('live-map-count')).toHaveTextContent(
        '1 sharing location · 1 last seen'
      );
    });
  });
  it('shows both people when they share a location', async () => {
    server.use(
      http.get('/api/users/location/all', () => HttpResponse.json({
        jeremiah: { latitude: 33.4484, longitude: -112.074, accuracy: 15, updated_at: nowSec - 20 },
        michele: { latitude: 33.4484, longitude: -112.074, accuracy: 12, updated_at: nowSec - 30 },
      })),
      http.get('/api/users/family-directory', () => HttpResponse.json([
        { username: 'jeremiah', display_name: 'Jeremiah', nextcloud_user: 'jeremiah', avatar: 'preset:jonahs-whale' },
        { username: 'michele', display_name: 'Michele', nextcloud_user: 'michele', avatar: '' },
      ])),
    );
    renderMap();
    const cluster = await waitFor(() => {
      const found = document.querySelector('[data-testid="map-avatar-cluster"]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(cluster.getAttribute('data-avatar-count')).toBe('2');
    expect(cluster.querySelectorAll('[data-testid="map-avatar-marker"]')).toHaveLength(2);
    expect(screen.getByTestId('live-map-count').textContent).toContain('2 sharing location');
  });

  it('opens a group into separate pins on tap, and folds it back on zoom', async () => {
    server.use(
      http.get('/api/users/location/all', () => HttpResponse.json({
        jeremiah: { latitude: 33.4484, longitude: -112.074, accuracy: 15, updated_at: nowSec - 20 },
        michele: { latitude: 33.44842, longitude: -112.07401, accuracy: 12, updated_at: nowSec - 30 },
      })),
      http.get('/api/users/family-directory', () => HttpResponse.json([
        { username: 'jeremiah', display_name: 'Jeremiah', nextcloud_user: 'jeremiah', avatar: 'preset:jonahs-whale' },
        { username: 'michele', display_name: 'Michele', nextcloud_user: 'michele', avatar: '' },
      ])),
    );
    renderMap();
    const cluster = await waitFor(() => {
      const found = document.querySelector('[data-testid="map-avatar-cluster"]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    // No saved place covers this spot: the pair still shares one pin.
    expect(document.querySelectorAll('.leaflet-marker-icon')).toHaveLength(1);

    fireEvent.click(cluster.closest('.leaflet-marker-icon') as HTMLElement);
    await waitFor(() => {
      expect(document.querySelector('[data-testid="map-avatar-cluster"]')).toBeNull();
      expect(document.querySelectorAll('[data-testid="map-avatar-marker"]')).toHaveLength(2);
    });
    const pins = [...document.querySelectorAll('.leaflet-marker-icon')] as HTMLElement[];
    expect(pins).toHaveLength(2);
    // Fanned out: the two pins no longer sit on the same spot.
    const at = (pin: HTMLElement) => `${pin.style.left},${pin.style.top},${pin.style.transform}`;
    expect(at(pins[0])).not.toBe(at(pins[1]));

    act(() => {
      fireEvent.click(document.querySelector('.leaflet-control-zoom-out') as HTMLElement);
    });
    await waitFor(() => {
      expect(document.querySelector('[data-testid="map-avatar-cluster"]')).not.toBeNull();
    });
  });
});
