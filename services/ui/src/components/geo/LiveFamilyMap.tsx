import { useEffect, useMemo, useRef } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { api } from '../../services/api';
import { userLocationsQuery } from '../../lib/wanderQueries';
import { avatarClusterMarkerHtml, avatarClusterMarkerLayout, avatarMarkerHtml, escapeHtml } from '../../lib/avatarMarker';
import { findFamilyMember, useFamilyDirectory, type FamilyLookup } from '../chat/familyDirectory';
import {
  FRESHNESS_STYLE,
  STALE_MAX_AGE_MS,
  ageLabel,
  buildLiveMembers,
  clusterMembers,
  placeKeyFor,
} from './liveLocations';

/** Continental-US view used until there is a home zone or a member to centre on. */
const DEFAULT_CENTER: L.LatLngExpression = [39.5, -98.35];
const DEFAULT_ZOOM = 4;

/**
 * How close two fixes must be to count as together when they are not in the
 * same named place. Indoor GPS scatters by a hundred metres or more, so this is
 * roomier than the 60 m `clusterMembers` defaults to.
 */
const NEARBY_CLUSTER_M = 150;

/**
 * Slack around a place's own radius when deciding who is "at" it. Home
 * Assistant zones are often drawn tight (100 m), while an indoor fix can sit a
 * couple of hundred metres off, which would otherwise leave two people at home
 * on separate pins.
 */
const PLACE_SLACK_M = 250;

export interface MapPlace {
  id: string;
  name: string;
  lat: number;
  lon: number;
  radius: number;
}

interface LiveFamilyMapProps {
  height?: number;
  className?: string;
  /** Hide anyone whose fix is older than this (default 15 min). */
  maxAgeMs?: number;
  /** Centre the map on this user (matches the location key, case-insensitive). */
  focusUserId?: string | null;
  /** HA zones rendered as "Places" geofences. */
  zones?: MapPlace[];
  /** The family directory, so markers can draw real profile pictures. */
  people?: FamilyLookup;
}

/**
 * Live map of family members whose app has location sharing on.
 *
 * A member with GPS off simply stops updating: their marker ages out and is
 * dropped rather than showing a stale position as if it were current.
 */
export default function LiveFamilyMap({
  height = 320,
  className = '',
  maxAgeMs = STALE_MAX_AGE_MS,
  focusUserId = null,
  zones = [],
  people,
}: LiveFamilyMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  /** Only auto-fit once: 30 s polls must not yank the view away from the user. */
  const hasFitMembersRef = useRef(false);

  // The one shared location query (see wanderQueries); freshness is worked out
  // in `select`, against the time the data arrived, so render stays pure.
  // A failed feed must not read as "nobody is sharing": that is a claim
  // about the family, and the map cannot know it when the request failed.
  const { data: members = [], isError: locationsError } = useQuery({
    ...userLocationsQuery,
    select: (raw) => buildLiveMembers(raw, Date.now(), maxAgeMs),
  });

  // Which place each member is standing in, so two people at home share one pin
  // however far their indoor fixes scatter.
  const placeKeys = useMemo(() => {
    const keys = new Map<string, string>();
    if (zones.length) {
      for (const member of members) {
        const key = placeKeyFor(member, zones, PLACE_SLACK_M);
        if (key) keys.set(member.userId, key);
      }
    }
    return keys;
  }, [members, zones]);

  // Wander already has the directory; the fallback keeps the map self-contained
  // for any other caller. Same query key, so this never fetches twice.
  const fallbackPeople = useFamilyDirectory();
  const directory = people ?? fallbackPeople;

  /** Each live location key to its household account, when we know one. */
  const personByUser = useMemo(() => {
    const byUser = new Map<string, ReturnType<typeof findFamilyMember>>();
    for (const member of members) {
      byUser.set(member.userId, findFamilyMember(directory, member.userId, member.userId));
    }
    return byUser;
  }, [members, directory]);

  /** Uploaded photos only: presets are drawn from code and need no fetch. */
  const avatarTargets = useMemo(() => {
    const seen = new Set<string>();
    const targets: Array<{ key: string; username: string; version: string }> = [];
    for (const person of personByUser.values()) {
      if (!person?.avatar?.startsWith('upload:')) continue;
      const key = `${person.username}:${person.avatar}`;
      if (seen.has(key)) continue;
      seen.add(key);
      targets.push({ key, username: person.username, version: person.avatar });
    }
    return targets;
  }, [personByUser]);

  // Same query keys as UserAvatar, so chat and the map share one cached blob
  // per picture instead of downloading it again for every marker.
  const avatarResults = useQueries({
    queries: avatarTargets.map((target) => ({
      queryKey: ['avatar-image', target.username, target.version],
      queryFn: () => api.getAvatarImage(target.username, target.version),
      staleTime: Infinity,
      retry: false,
    })),
  });
  const avatarResultsRef = useRef(avatarResults);
  const avatarDataKey = avatarResults.map((result) => result.dataUpdatedAt || 0).join('|');
  useEffect(() => {
    avatarResultsRef.current = avatarResults;
  }, [avatarResults]);

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    const map = L.map(container, {
      zoomControl: true,
      attributionControl: true,
    });
    mapRef.current = map;
    // A map with no view never draws tiles. Start at a real view so the map
    // is usable (and visibly a map) even when nobody has a fresh fix yet.
    map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
    layerRef.current = L.layerGroup().addTo(map);

    // Android WebViews can settle the container size after the map mounts;
    // without this the canvas stays blank/garbled until a manual resize.
    const invalidate = () => map.invalidateSize();
    const raf = requestAnimationFrame(invalidate);
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(invalidate);
      observer.observe(container);
    }

    return () => {
      cancelAnimationFrame(raf);
      observer?.disconnect();
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      hasFitMembersRef.current = false;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();

    // One object URL per uploaded picture for this render of the markers; the
    // blobs themselves come from the query cache UserAvatar already uses.
    const avatarUrls = new Map<string, string>();
    avatarTargets.forEach((target, index) => {
      const blob = avatarResultsRef.current[index]?.data;
      if (blob) avatarUrls.set(target.key, URL.createObjectURL(blob));
    });

    // Places (HA zones) first so member pins always sit on top.
    for (const place of zones) {
      L.circle([place.lat, place.lon], {
        radius: place.radius,
        color: '#818CF8',
        weight: 1,
        dashArray: '4 4',
        fillColor: '#818CF8',
        fillOpacity: 0.06,
      })
        .bindTooltip(place.name, { permanent: false, direction: 'top' })
        .addTo(layer);
    }

    const points: L.LatLngExpression[] = [];
    for (const cluster of clusterMembers(members, NEARBY_CLUSTER_M, (m) => placeKeys.get(m.userId) ?? null)) {
      const member = cluster.lead;
      const style = FRESHNESS_STYLE[member.freshness];
      const latlng: L.LatLngExpression = [member.lat, member.lon];
      points.push(latlng);

      if (member.accuracy && member.accuracy > 0 && member.freshness !== 'stale') {
        L.circle(latlng, {
          radius: member.accuracy,
          color: style.color,
          weight: 1,
          fillColor: style.color,
          fillOpacity: 0.12,
        }).addTo(layer);
      }

      const uniqueMembers = [...new Map(cluster.members.map((m) => [m.userId, m])).values()];
      const people = uniqueMembers.map((m) => {
        const person = personByUser.get(m.userId);
        const label = person?.display_name || m.userId;
        const avatar = person?.avatar ?? null;
        const imageUrl = person && avatar?.startsWith('upload:')
          ? avatarUrls.get(`${person.username}:${avatar}`) ?? null
          : null;
        const size = m.freshness === 'stale' ? 30 : 34;
        return {
          label,
          avatar: avatar ?? undefined,
          imageUrl: imageUrl ?? undefined,
          ringColor: FRESHNESS_STYLE[m.freshness].color,
          size,
          opacity: m.freshness === 'stale' ? 0.7 : 1,
        };
      });
      const isCluster = people.length > 1;
      const layout = isCluster
        ? avatarClusterMarkerLayout(people)
        : { width: people[0]?.size ?? 34, height: people[0]?.size ?? 34 };
      const title = people.map((person) => person.label).join(', ');
      const icon = L.divIcon({
        className: '',
        html: isCluster ? avatarClusterMarkerHtml({ people }) : avatarMarkerHtml(people[0]!),
        iconSize: [layout.width, layout.height],
        iconAnchor: [layout.width / 2, layout.height / 2],
        popupAnchor: [0, -layout.height / 2],
      });

      const popup = uniqueMembers
        .map((m) => {
          const person = personByUser.get(m.userId);
          const label = person?.display_name || m.userId;
          const age = ageLabel(m.ageMs);
          const ageText = m.freshness === 'stale' ? `last seen ${age}` : age;
          return `<strong>${escapeHtml(label)}</strong><br/>${ageText}` + (m.accuracy ? `<br/>±${Math.round(m.accuracy)} m` : '');
        })
        .join('<hr style="border-color:rgba(255,255,255,.12);margin:6px 0" />');
      L.marker(latlng, { icon, keyboard: true, title, alt: title, riseOnHover: true })
        .bindPopup(popup)
        .addTo(layer);
    }

    if (!hasFitMembersRef.current && points.length > 0) {
      hasFitMembersRef.current = true;
      if (points.length === 1) {
        map.setView(points[0], 15);
      } else {
        try {
          // Two people can be only metres apart, which produces a degenerate
          // bounds that Leaflet renders as a blank canvas (or rejects outright).
          // Only fit when there is a real spread; otherwise centre and zoom in.
          const bounds = L.latLngBounds(points);
          const center = bounds.getCenter();
          const spreadMeters =
            points.reduce((acc, p) => {
              const ll = L.latLng(p as L.LatLngTuple);
              return acc + ll.distanceTo(center);
            }, 0) / points.length;
          if (spreadMeters < 50) {
            map.setView(center, 16);
          } else {
            map.fitBounds(bounds, { padding: [30, 30], maxZoom: 16 });
          }
        } catch {
          // Never let a bad bounds calculation leave the map blank
          map.setView(points[0] as L.LatLngTuple, 15);
        }
      }
    } else if (!hasFitMembersRef.current && zones.length > 0) {
      // No one is sharing yet, but we know where home is: start there.
      map.setView([zones[0].lat, zones[0].lon], 13);
    }

    return () => {
      layer.clearLayers();
      for (const url of avatarUrls.values()) URL.revokeObjectURL(url);
    };
  }, [members, zones, personByUser, avatarTargets, avatarDataKey, placeKeys]);

  // "Show on map" from a family card.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusUserId) return;
    const target = members.find(
      (m) => m.userId.toLowerCase() === focusUserId.toLowerCase()
    );
    if (target) {
      map.flyTo([target.lat, target.lon], 16, { duration: 0.8 });
    }
  }, [focusUserId, members]);

  const freshCount = members.filter((m) => m.freshness !== 'stale').length;
  const staleCount = members.length - freshCount;
  // Deliberately does not claim *why* a member is stale. A stale position
  // means one of two very different things -- they turned sharing off, or their
  // app simply stopped reporting (phone off, app killed, no network) -- and a
  // timestamp cannot tell us which. This map reports on other people's devices,
  // so it has no way to know, and asserting "sharing is off" is a guess that
  // was demonstrably wrong: sharing can be on and the app still silent. Say
  // what we observe and let the per-pin "last seen N hrs ago" carry the rest.
  const countText = locationsError
    ? 'Could not read the location feed'
    : members.length === 0
      ? 'No one is sharing location right now'
      : freshCount === 0
        ? `${staleCount} last seen`
        : staleCount > 0
          ? `${freshCount} sharing location · ${staleCount} last seen`
          : `${freshCount} sharing location`;

  return (
    <div className={`relative ${className}`} data-testid="live-family-map">
      <div ref={containerRef} style={{ height, width: '100%' }} className="relative z-0 rounded-xl" />
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full bg-green-500" /> live (2 min)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-500" /> recent (15 min)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full bg-slate-500" /> last seen (24 h)
        </span>
        <span data-testid="live-map-count">{countText}</span>
      </div>
    </div>
  );
}
