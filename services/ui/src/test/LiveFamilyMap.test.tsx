import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { server } from './setup';
import LiveFamilyMap from '../components/geo/LiveFamilyMap';
import {
  ageLabel,
  classifyLocation,
  clusterMembers,
  distanceMeters,
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

  it('marks an old fix as stale (tracking turned off)', () => {
    const m = classifyLocation(
      'jeremiah',
      { latitude: 33.4, longitude: -112.0, updated_at: nowSec - 3600 },
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

  it('ignores staleness when distance is what matters', () => {
    const m = classifyLocation(
      'jeremiah',
      { latitude: 33.4484, longitude: -112.074, updated_at: nowSec - 10 },
      now
    );
    expect(m).not.toBeNull();
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
      )
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

  it('explains when nobody is sharing location', async () => {
    server.use(http.get('/api/users/location/all', () => HttpResponse.json({})));
    renderMap();
    await waitFor(() => {
      expect(screen.getByTestId('live-map-count')).toHaveTextContent(
        'No one is sharing location right now'
      );
    });
  });

  it('drops members whose tracking stopped (stale fixes)', async () => {
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
      expect(screen.getByTestId('live-map-count')).toHaveTextContent(
        'No one is sharing location right now'
      );
    });
  });
});
