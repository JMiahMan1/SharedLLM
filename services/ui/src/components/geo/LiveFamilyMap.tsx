import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { api } from '../../services/api';
import {
  FRESHNESS_STYLE,
  RECENT_THRESHOLD_MS,
  ageLabel,
  buildLiveMembers,
} from './liveLocations';

interface LiveFamilyMapProps {
  height?: number;
  className?: string;
  /** Hide anyone whose fix is older than this (default 15 min). */
  maxAgeMs?: number;
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
  maxAgeMs = RECENT_THRESHOLD_MS,
}: LiveFamilyMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  const { data: members = [] } = useQuery({
    queryKey: ['user-locations', maxAgeMs],
    // Freshness is computed at fetch time so render stays pure.
    queryFn: async () => buildLiveMembers(await api.getAllUserLocations(), Date.now(), maxAgeMs),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  useEffect(() => {
    if (!containerRef.current) return;
    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: true,
    });
    mapRef.current = map;
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
    layerRef.current = L.layerGroup().addTo(map);

    return () => {
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();

    const points: L.LatLngExpression[] = [];
    for (const member of members) {
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

      L.circleMarker(latlng, {
        radius: member.freshness === 'live' ? 8 : 6,
        color: style.color,
        fillColor: style.fill,
        fillOpacity: style.opacity,
        weight: 2,
      })
        .bindPopup(
          `<strong>${member.userId}</strong><br/>${ageLabel(member.ageMs)}` +
            (member.accuracy ? `<br/>±${Math.round(member.accuracy)} m` : '')
        )
        .addTo(layer);
    }

    if (points.length > 0) {
      const bounds = L.latLngBounds(points);
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 16 });
    }
  }, [members]);

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
        <span data-testid="live-map-count">
          {members.length === 0
            ? 'No one is sharing location right now'
            : `${members.length} sharing location`}
        </span>
      </div>
    </div>
  );
}
