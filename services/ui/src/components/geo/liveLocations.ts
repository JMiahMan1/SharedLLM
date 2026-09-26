import type { UserLiveLocation } from '../../types/api';

export const LIVE_THRESHOLD_MS = 2 * 60 * 1000; // fresh fix
export const RECENT_THRESHOLD_MS = 15 * 60 * 1000; // still worth showing as "last seen"

export type LocationFreshness = 'live' | 'recent' | 'stale';

export interface LiveFamilyMember {
  userId: string;
  lat: number;
  lon: number;
  accuracy: number | null;
  ageMs: number;
  freshness: LocationFreshness;
}

export function classifyLocation(
  userId: string,
  location: UserLiveLocation,
  now: number = Date.now()
): LiveFamilyMember | null {
  const { latitude, longitude } = location;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  if (latitude === 0 && longitude === 0) return null; // null island = bad fix

  const stamp = location.updated_at || location.timestamp || 0;
  const ageMs = stamp > 0 ? Math.max(0, now - stamp * 1000) : Number.POSITIVE_INFINITY;
  const freshness: LocationFreshness =
    ageMs <= LIVE_THRESHOLD_MS ? 'live' : ageMs <= RECENT_THRESHOLD_MS ? 'recent' : 'stale';

  return {
    userId,
    lat: latitude,
    lon: longitude,
    accuracy: typeof location.accuracy === 'number' ? location.accuracy : null,
    ageMs,
    freshness,
  };
}

/** Users with a fix newer than `maxAgeMs`; a member with tracking off ages out. */
export function buildLiveMembers(
  locations: Record<string, UserLiveLocation> | undefined,
  now: number = Date.now(),
  maxAgeMs: number = RECENT_THRESHOLD_MS
): LiveFamilyMember[] {
  return Object.entries(locations ?? {})
    .map(([userId, loc]) => classifyLocation(userId, loc, now))
    .filter((m): m is LiveFamilyMember => m != null && m.ageMs <= maxAgeMs)
    .sort((a, b) => a.userId.localeCompare(b.userId));
}

export function ageLabel(ageMs: number): string {
  if (!Number.isFinite(ageMs)) return 'unknown';
  const mins = Math.round(ageMs / 60000);
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 min ago';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours === 1 ? '1 hr ago' : `${hours} hrs ago`;
}

export const FRESHNESS_STYLE: Record<
  LocationFreshness,
  { color: string; fill: string; opacity: number }
> = {
  live: { color: '#22c55e', fill: '#22c55e', opacity: 1 },
  recent: { color: '#f59e0b', fill: '#f59e0b', opacity: 0.85 },
  stale: { color: '#64748b', fill: '#64748b', opacity: 0.5 },
};

/** Metres between two coordinates (haversine). */
export function distanceMeters(
  aLat: number,
  aLon: number,
  bLat: number,
  bLon: number
): number {
  const R = 6371000;
  const dLat = ((bLat - aLat) * Math.PI) / 180;
  const dLon = ((bLon - aLon) * Math.PI) / 180;
  const lat1 = (aLat * Math.PI) / 180;
  const lat2 = (bLat * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

export interface MemberCluster {
  /** Freshest member in the cluster — the one the marker represents. */
  lead: LiveFamilyMember;
  members: LiveFamilyMember[];
}

/**
 * Group members standing together.
 *
 * One phone can post under more than one key (the app posts per-user and has a
 * legacy fallback), which showed up as duplicate pins stacked on the same spot.
 * Anything within `radiusMeters` collapses into one marker.
 */
export function clusterMembers(
  members: LiveFamilyMember[],
  radiusMeters = 60
): MemberCluster[] {
  const clusters: MemberCluster[] = [];
  for (const member of members) {
    const hit = clusters.find((c) =>
      c.members.some((m) => distanceMeters(m.lat, m.lon, member.lat, member.lon) <= radiusMeters)
    );
    if (hit) {
      hit.members.push(member);
      if (member.ageMs < hit.lead.ageMs) hit.lead = member;
    } else {
      clusters.push({ lead: member, members: [member] });
    }
  }
  return clusters;
}
