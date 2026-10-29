import type { UserLiveLocation } from '../types/api';
import { distanceMeters, fixTimeSeconds, STALE_MAX_AGE_MS } from '../components/geo/liveLocations';

/** One Wander member card. */
export interface FamilyMemberStatus {
  id: string;
  name: string;
  zone: string;
  isMoving: boolean;
  speedMph: number;
  battery: number | null;
  assignedVehicle?: string;
  lastUpdated?: string;
  /** GPS accuracy in metres, when the source reports it. */
  accuracy?: number | null;
  /** Zones the member is currently inside. */
  inZones?: string[];
  /** Where the position came from: the Jarvis app, or a Home Assistant person. */
  source: 'app' | 'home_assistant';
}

export interface Place {
  name: string;
  lat: number;
  lon: number;
  radius: number;
}

type HaFeature = { properties?: Record<string, unknown> };

/** Faster than walking pace counts as "in motion". */
const MOVING_MPS = 1.0;
const MPS_TO_MPH = 2.23694;

const titleCase = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** The users a HA entity belongs to, by underscore token (person.jeremiah_phone -> jeremiah). */
function entityTokens(entityId: string): Set<string> {
  const obj = entityId.split('.').slice(1).join('.').toLowerCase();
  return new Set(obj.replace(/-/g, '_').split('_').filter(Boolean));
}

/** Places (HA zones) that contain a point, nearest first. */
export function placesAt(lat: number, lon: number, places: Place[]): string[] {
  return places
    .map((p) => ({ p, d: distanceMeters(lat, lon, p.lat, p.lon) }))
    .filter(({ p, d }) => d <= p.radius)
    .sort((a, b) => a.d - b.d)
    .map(({ p }) => p.name);
}

/**
 * The Wander member cards: the freshest position for each person.
 *
 * They used to be built only from Home Assistant `person.*` entities. Those
 * follow whichever device trackers were assigned to the person in HA, and when
 * the phones changed the people kept pointing at trackers that had stopped
 * reporting, so the cards sat a day behind while the map (which reads the
 * Jarvis app's own fixes) was live. Now the app's fix wins whenever it is
 * newer, its place is worked out from the HA zones, and HA still supplies
 * anyone who has no app fix (and battery, when the app's fix lacks one).
 */
export function buildFamilyCards(
  haFeatures: HaFeature[],
  appLocations: Record<string, UserLiveLocation>,
  places: Place[],
  now: number = Date.now(),
): FamilyMemberStatus[] {
  const cards: FamilyMemberStatus[] = [];
  const tokensById = new Map<string, Set<string>>();

  for (const feat of haFeatures) {
    const props = feat.properties || {};
    const entityId = String(props.entity_id || '');
    if (!entityId.startsWith('person.')) continue;
    const speed = Number(props.speed || 0);
    const battery = props.battery != null ? Number(props.battery) : null;
    const accuracy = props.gps_accuracy != null ? Number(props.gps_accuracy) : null;
    cards.push({
      id: entityId,
      name: String(props.friendly_name || entityId.replace('person.', '')),
      zone: titleCase(String(props.state || 'Unknown')),
      isMoving: speed > MOVING_MPS,
      speedMph: Math.round(speed * MPS_TO_MPH),
      battery: Number.isFinite(battery as number) ? battery : null,
      accuracy: Number.isFinite(accuracy as number) ? accuracy : null,
      lastUpdated: props.last_updated != null ? String(props.last_updated) : undefined,
      inZones: Array.isArray(props.in_zones) ? (props.in_zones as string[]) : [],
      source: 'home_assistant',
    });
    tokensById.set(entityId, entityTokens(entityId));
  }

  for (const [userId, loc] of Object.entries(appLocations ?? {})) {
    const user = userId.toLowerCase();
    const { latitude, longitude } = loc;
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    if (latitude === 0 && longitude === 0) continue;
    const stamp = fixTimeSeconds(loc);
    if (!stamp) continue;

    const existing = cards.find((c) => c.source === 'home_assistant' && tokensById.get(c.id)?.has(user));
    const haTime = existing?.lastUpdated ? Date.parse(existing.lastUpdated) / 1000 : 0;
    if (existing && haTime >= stamp) continue; // HA is as fresh or fresher

    const inZones = placesAt(latitude, longitude, places);
    const speed = typeof loc.speed === 'number' ? loc.speed : 0;
    const update: Partial<FamilyMemberStatus> = {
      zone: inZones[0] ?? 'Away',
      inZones,
      isMoving: speed > MOVING_MPS,
      speedMph: Math.round(speed * MPS_TO_MPH),
      accuracy: typeof loc.accuracy === 'number' ? loc.accuracy : null,
      lastUpdated: new Date(stamp * 1000).toISOString(),
      source: 'app',
    };
    if (typeof loc.battery === 'number') update.battery = loc.battery;

    if (existing) {
      Object.assign(existing, update);
    } else if (now - stamp * 1000 <= STALE_MAX_AGE_MS) {
      // A card of its own only for a recent fix: an account that has not
      // reported in a day (the system account, a retired phone) is not family
      // to show.
      cards.push({
        id: `user.${user}`,
        name: titleCase(user),
        battery: null,
        ...(update as Omit<FamilyMemberStatus, 'id' | 'name' | 'battery'>),
      });
    }
  }
  return cards;
}

/** People who are in the same place, so the page can merge their cards. */
export interface MemberGroup {
  /** Stable identity of the place, or a per-person key when they cannot merge. */
  key: string;
  /** The place name to show on a merged card ("school", "Grandma's"). */
  label: string;
  members: FamilyMemberStatus[];
}

// "Away"/"Unknown" is not a shared place: two people both away are not together.
const NOT_A_PLACE = new Set(['away', 'unknown', '']);

/**
 * Group the family by the place they are in.
 *
 * People sharing a named zone (or the same named state) collapse into one
 * group; anyone who is not in a named place gets a group of their own, so the
 * page keeps showing them as an individual card.
 */
export function groupByPlace(members: FamilyMemberStatus[]): MemberGroup[] {
  const groups: MemberGroup[] = [];
  const byKey = new Map<string, MemberGroup>();
  for (const member of members) {
    const zoneId = member.inZones?.find((zone) => zone.startsWith('zone.'));
    const label = zoneId ? zoneId.replace(/^zone\./, '').replace(/_/g, ' ') : member.zone;
    const named = Boolean(zoneId) || !NOT_A_PLACE.has((member.zone ?? '').trim().toLowerCase());
    const key = named ? (zoneId ?? `place:${label.toLowerCase()}`) : `solo:${member.id}`;
    let group = named ? byKey.get(key) : undefined;
    if (!group) {
      group = { key, label, members: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    group.members.push(member);
  }
  return groups;
}
