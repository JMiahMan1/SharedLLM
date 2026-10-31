import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { api } from '../../services/api';
import { userLocationsQuery } from '../../lib/wanderQueries';
import { getMapStyle, resolveMapScheme, subscribeMapStyle } from '../../lib/mapStyle';
import {
  PLACE_PIN_SIZE,
  avatarClusterMarkerHtml,
  avatarClusterMarkerLayout,
  avatarMarkerHtml,
  escapeHtml,
  placeGroupHtml,
  placeGroupLayout,
  placeKind,
  placePinHtml,
  type AvatarMarkerOptions,
} from '../../lib/avatarMarker';
import { findFamilyMember, useFamilyDirectory, type FamilyLookup } from '../chat/familyDirectory';
import {
  FRESHNESS_STYLE,
  STALE_MAX_AGE_MS,
  ageLabel,
  buildLiveMembers,
  clusterKey,
  clusterMembers,
  placeKeyFor,
  spiderOffsets,
  type LiveFamilyMember,
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

/** Roomier fallback used only when no places are known at all (the zones call
 * failed or Home Assistant is unreachable): without a place to group by, the
 * distance rule is all that is left, and a family in one house should still
 * share a pin rather than sit on separate ones. */
const NO_PLACE_CLUSTER_M = 300;

/**
 * Slack around a place's own radius when deciding who is "at" it. Home
 * Assistant zones are often drawn tight (100 m), while an indoor fix can sit a
 * couple of hundred metres off, which would otherwise leave two people at home
 * on separate pins.
 */
const PLACE_SLACK_M = 250;

/**
 * Pins closer than this on screen merge into one group, whatever the distance
 * in metres. An avatar is 34 px across, so anything nearer overlaps; this is
 * what keeps a zoomed-out map from piling a family into an unreadable stack,
 * and it re-groups every time the zoom changes.
 */
const OVERLAP_CLUSTER_PX = 40;

export interface MapPlace {
  id: string;
  name: string;
  lat: number;
  lon: number;
  radius: number;
  /** Home Assistant's zone icon ("mdi:school"), which picks the pin's icon. */
  icon?: string;
}

/** Space between a place's pin and the pill of people above it. */
const PLACE_GROUP_LIFT_PX = 8;

/**
 * The standard map, drawn dark by a filter on the tile layer (index.css,
 * `.jarvis-map-dark`) rather than a second tile service: the free dark tile
 * sets now want an API key. Light or dark follows lib/mapStyle: dark only at
 * night by this device's clock, unless Settings says always light or dark.
 */
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const TILE_ATTRIBUTION = '&copy; OpenStreetMap contributors';
const DARK_CLASS = 'jarvis-map-dark';

/** How often "auto" re-checks whether the sun has set. */
const SCHEME_CHECK_MS = 60_000;

function currentScheme(map: L.Map): 'light' | 'dark' {
  const centre = map.getCenter();
  return resolveMapScheme(getMapStyle(), Date.now(), { lat: centre.lat, lon: centre.lng });
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
  /** Grouping depends on how far apart pins sit on screen, so it tracks zoom. */
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  /** The group fanned out into individual pins after a tap, by `clusterKey`. */
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  /** The marker whose popup is open, so a redraw (every poll) reopens it. */
  const openPopupRef = useRef<string | null>(null);
  const redrawingRef = useRef(false);

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
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: TILE_ATTRIBUTION }).addTo(map);
    // Dark at sunset, light at sunrise, or as the setting says.
    const followScheme = () => {
      container.classList.toggle(DARK_CLASS, currentScheme(map) === 'dark');
    };
    followScheme();
    const schemeTimer = window.setInterval(followScheme, SCHEME_CHECK_MS);
    const stopSchemeWatch = subscribeMapStyle(followScheme);
    map.on('moveend', followScheme);
    layerRef.current = L.layerGroup().addTo(map);
    // Zooming changes who overlaps whom, so re-group and fold any open group
    // back up; tapping the map itself closes an open group too.
    map.on('zoomend', () => {
      setZoom(map.getZoom());
      setExpandedKey(null);
    });
    map.on('click', () => setExpandedKey(null));

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
      window.clearInterval(schemeTimer);
      stopSchemeWatch();
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
    redrawingRef.current = true;
    layer.clearLayers();
    redrawingRef.current = false;
    // Every popup marker goes through here: it keeps track of which one is
    // open, and reopens it after the redraw that removed it.
    const withPopup = (marker: L.Marker, key: string, html: string) => {
      marker
        .bindPopup(html)
        .on('popupopen', () => {
          openPopupRef.current = key;
        })
        .on('popupclose', () => {
          if (!redrawingRef.current) openPopupRef.current = null;
        })
        .addTo(layer);
      if (openPopupRef.current === key) marker.openPopup();
      return marker;
    };

    // One object URL per uploaded picture for this render of the markers; the
    // blobs themselves come from the query cache UserAvatar already uses.
    const avatarUrls = new Map<string, string>();
    avatarTargets.forEach((target, index) => {
      const blob = avatarResultsRef.current[index]?.data;
      if (blob) avatarUrls.set(target.key, URL.createObjectURL(blob));
    });

    // Places first so member pins always sit on top: a faint outline of the
    // place's area and a round pin at its centre.
    const placeById = new Map(zones.map((place) => [place.id ?? place.name, place]));
    for (const place of zones) {
      L.circle([place.lat, place.lon], {
        radius: place.radius,
        color: '#22d3ee',
        weight: 1,
        dashArray: '4 4',
        fillColor: '#22d3ee',
        fillOpacity: 0.05,
        interactive: false,
      }).addTo(layer);
      L.marker([place.lat, place.lon], {
        icon: L.divIcon({
          className: '',
          html: placePinHtml({ name: place.name, kind: placeKind(place) }),
          iconSize: [PLACE_PIN_SIZE, PLACE_PIN_SIZE],
          iconAnchor: [PLACE_PIN_SIZE / 2, PLACE_PIN_SIZE / 2],
        }),
        keyboard: true,
        title: place.name,
        alt: place.name,
      })
        .bindTooltip(place.name, { permanent: false, direction: 'bottom', offset: [0, PLACE_PIN_SIZE / 2] })
        .addTo(layer);
    }

    // Fit first, so the grouping below is worked out at the zoom the family
    // will actually see.
    const points: L.LatLngTuple[] = members.map((m) => [m.lat, m.lon]);
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
            points.reduce((acc, p) => acc + L.latLng(p).distanceTo(center), 0) / points.length;
          if (spreadMeters < 50) {
            map.setView(center, 16);
          } else {
            map.fitBounds(bounds, { padding: [30, 30], maxZoom: 16 });
          }
        } catch {
          // Never let a bad bounds calculation leave the map blank
          map.setView(points[0], 15);
        }
      }
    } else if (!hasFitMembersRef.current && zones.length > 0) {
      // No one is sharing yet, but we know where home is: start there.
      map.setView([zones[0].lat, zones[0].lon], 13);
    }

    const pinFor = (m: LiveFamilyMember): AvatarMarkerOptions => {
      const person = personByUser.get(m.userId);
      const label = person?.display_name || m.userId;
      const avatar = person?.avatar ?? null;
      const imageUrl = person && avatar?.startsWith('upload:')
        ? avatarUrls.get(`${person.username}:${avatar}`) ?? null
        : null;
      return {
        label,
        avatar: avatar ?? undefined,
        imageUrl: imageUrl ?? undefined,
        ringColor: FRESHNESS_STYLE[m.freshness].color,
        size: m.freshness === 'stale' ? 30 : 34,
        opacity: m.freshness === 'stale' ? 0.7 : 1,
      };
    };
    const popupFor = (m: LiveFamilyMember) => {
      const age = ageLabel(m.ageMs);
      const ageText = m.freshness === 'stale' ? `last seen ${age}` : age;
      return `<strong>${escapeHtml(pinFor(m).label)}</strong><br/>${ageText}` + (m.accuracy ? `<br/>±${Math.round(m.accuracy)} m` : '');
    };
    const singleIcon = (pin: AvatarMarkerOptions) => {
      const size = pin.size ?? 34;
      return L.divIcon({
        className: '',
        html: avatarMarkerHtml(pin),
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
        popupAnchor: [0, -size / 2],
      });
    };

    // Everyone at a saved place sits in one pill above that place's pin,
    // whatever their own fixes say (an indoor fix scatters; the place is where
    // they are). The pill lists each of them on tap.
    const atPlace = new Map<string, LiveFamilyMember[]>();
    const elsewhere: LiveFamilyMember[] = [];
    for (const member of members) {
      const key = placeKeys.get(member.userId);
      if (key && placeById.has(key)) atPlace.set(key, [...(atPlace.get(key) ?? []), member]);
      else elsewhere.push(member);
    }
    for (const [key, group] of atPlace) {
      const place = placeById.get(key)!;
      const unique = [...new Map(group.map((m) => [m.userId, m])).values()];
      const people = unique.map(pinFor);
      const layout = placeGroupLayout(people.length);
      const names = people.map((person) => person.label).join(', ');
      const list = unique
        .map((m, index) => {
          const age = ageLabel(m.ageMs);
          return `<strong>${escapeHtml(people[index].label)}</strong> · ${m.freshness === 'stale' ? `last seen ${age}` : age}`;
        })
        .join('<br/>');
      const groupMarker = L.marker([place.lat, place.lon], {
        icon: L.divIcon({
          className: '',
          html: placeGroupHtml({ people, place: place.name }),
          iconSize: [layout.width, layout.height],
          // Bottom-centre of the pill just above the top of the place's pin.
          iconAnchor: [layout.width / 2, layout.height + PLACE_PIN_SIZE / 2 + PLACE_GROUP_LIFT_PX],
          popupAnchor: [0, -(layout.height + PLACE_PIN_SIZE / 2 + PLACE_GROUP_LIFT_PX)],
        }),
        keyboard: true,
        title: `${names} at ${place.name}`,
        alt: `${names} at ${place.name}`,
        riseOnHover: true,
        zIndexOffset: 500,
      });
      withPopup(groupMarker, `place:${key}`, `<strong>${escapeHtml(place.name)}</strong><br/>${list}`);
    }

    // Everyone else: grouped by distance and by whether the pins would overlap
    // on screen at this zoom, so people at the same spot share one pin even
    // when no saved place covers it.
    const viewZoom = map.getZoom();
    const clusters = clusterMembers(
      elsewhere,
      zones.length ? NEARBY_CLUSTER_M : NO_PLACE_CLUSTER_M,
      undefined,
      { project: (m) => map.project([m.lat, m.lon], viewZoom), pixelRadius: OVERLAP_CLUSTER_PX }
    );
    for (const cluster of clusters) {
      const member = cluster.lead;
      const style = FRESHNESS_STYLE[member.freshness];
      const latlng: L.LatLngTuple = [member.lat, member.lon];

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
      const people = uniqueMembers.map(pinFor);
      const title = people.map((person) => person.label).join(', ');

      if (people.length === 1) {
        withPopup(
          L.marker(latlng, { icon: singleIcon(people[0]), keyboard: true, title, alt: title, riseOnHover: true }),
          `user:${uniqueMembers[0].userId}`,
          popupFor(uniqueMembers[0]),
        );
        continue;
      }

      const key = clusterKey(cluster);
      if (key === expandedKey) {
        // Fan the group out around its spot, each pin tied back to where that
        // person's own fix is, so everyone can be read and tapped on their own.
        const origin = map.project(latlng, viewZoom);
        spiderOffsets(uniqueMembers.length).forEach((offset, index) => {
          const m = uniqueMembers[index];
          const spot = map.unproject(origin.add([offset.x, offset.y]), viewZoom);
          L.polyline([[m.lat, m.lon], spot], {
            color: FRESHNESS_STYLE[m.freshness].color,
            weight: 1.5,
            opacity: 0.8,
            interactive: false,
          }).addTo(layer);
          withPopup(
            L.marker(spot, { icon: singleIcon(people[index]), keyboard: true, title: people[index].label, alt: people[index].label, riseOnHover: true, zIndexOffset: 1000 }),
            `user:${m.userId}`,
            popupFor(m),
          );
        });
        continue;
      }

      // One stacked pin with a count; tapping it fans the group out.
      const layout = avatarClusterMarkerLayout(people);
      const icon = L.divIcon({
        className: '',
        html: avatarClusterMarkerHtml({ people }),
        iconSize: [layout.width, layout.height],
        iconAnchor: [layout.width / 2, layout.height / 2],
      });
      L.marker(latlng, { icon, keyboard: true, title: `${title} - tap to see each person`, alt: title, riseOnHover: true })
        .on('click', () => setExpandedKey(key))
        .addTo(layer);
    }

    return () => {
      redrawingRef.current = true;
      layer.clearLayers();
      redrawingRef.current = false;
      for (const url of avatarUrls.values()) URL.revokeObjectURL(url);
    };
  }, [members, zones, personByUser, avatarTargets, avatarDataKey, placeKeys, zoom, expandedKey]);

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
