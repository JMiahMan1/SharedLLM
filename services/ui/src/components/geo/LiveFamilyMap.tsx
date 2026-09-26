import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { api } from '../../services/api';
import {
  FRESHNESS_STYLE,
  STALE_MAX_AGE_MS,
  ageLabel,
  buildLiveMembers,
  clusterMembers,
} from './liveLocations';

/** Continental-US view used until there is a home zone or a member to centre on. */
const DEFAULT_CENTER: L.LatLngExpression = [39.5, -98.35];
const DEFAULT_ZOOM = 4;

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
}: LiveFamilyMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  /** Only auto-fit once: 30 s polls must not yank the view away from the user. */
  const hasFitMembersRef = useRef(false);

  const { data: members = [] } = useQuery({
    queryKey: ['user-locations', maxAgeMs],
    // Freshness is computed at fetch time so render stays pure.
    queryFn: async () => buildLiveMembers(await api.getAllUserLocations(), Date.now(), maxAgeMs),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

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
    for (const cluster of clusterMembers(members)) {
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

      const others = cluster.members.filter((m) => m.userId !== member.userId).map((m) => m.userId);
      const age = ageLabel(member.ageMs);
      const ageText = member.freshness === 'stale' ? `last seen ${age}` : age;
      L.circleMarker(latlng, {
        radius: member.freshness === 'live' ? 8 : 6,
        color: style.color,
        fillColor: style.fill,
        fillOpacity: style.opacity,
        weight: 2,
      })
        .bindPopup(
          `<strong>${member.userId}</strong><br/>${ageText}` +
            (member.accuracy ? `<br/>±${Math.round(member.accuracy)} m` : '') +
            (others.length ? `<br/><span style="opacity:.7">also here: ${others.join(', ')}</span>` : '')
        )
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
  }, [members, zones]);

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
  const countText =
    members.length === 0
      ? 'No one is sharing location right now'
      : freshCount === 0
        ? `${staleCount} last seen (sharing is off)`
        : staleCount > 0
          ? `${freshCount} sharing location · ${staleCount} last seen`
          : `${freshCount} sharing location`;

  return (
    <div className={`relative ${className}`} data-testid="live-family-map">
      <div ref={containerRef} style={{ height, width: '100%' }} className="rounded-xl" />
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
