import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

export interface TripEndpoint {
  name: string;
  lat: number | null;
  lon: number | null;
  source: string | null;
}

interface TripLocationsMapProps {
  start: TripEndpoint;
  end: TripEndpoint;
  /** Optional breadcrumb path between the two points */
  path?: Array<{ lat: number; lon: number }>;
}

/**
 * Interactive Leaflet map showing a trip's start and destination with the
 * actual GPS path between them. Fully draggable/zoomable (unlike MiniRouteMap).
 */
export default function TripLocationsMap({ start, end, path = [] }: TripLocationsMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const map = L.map(containerRef.current, {
      zoomControl: true,
      dragging: true,
      scrollWheelZoom: true,
      doubleClickZoom: true,
      boxZoom: true,
      keyboard: true,
      touchZoom: true,
      attributionControl: true,
    });
    mapRef.current = map;

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    const bounds: L.LatLngExpression[] = [];

    if (start.lat != null && start.lon != null) {
      L.marker([start.lat, start.lon], {
        icon: L.divIcon({
          className: "",
          html: `<div style="width:16px;height:16px;border-radius:50%;background:#16a34a;border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.6)"></div>`,
          iconSize: [16, 16],
          iconAnchor: [8, 8],
        }),
      }).addTo(map).bindPopup(`<b>Start</b><br/>${start.name}`);
      bounds.push([start.lat, start.lon]);
    }

    if (end.lat != null && end.lon != null) {
      L.marker([end.lat, end.lon], {
        icon: L.divIcon({
          className: "",
          html: `<div style="width:16px;height:16px;border-radius:50%;background:#dc2626;border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.6)"></div>`,
          iconSize: [16, 16],
          iconAnchor: [8, 8],
        }),
      }).addTo(map).bindPopup(`<b>Destination</b><br/>${end.name}`);
      bounds.push([end.lat, end.lon]);
    }

    const validPath = path.filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon));
    if (validPath.length > 1) {
      const latlngs = validPath.map((p) => [p.lat, p.lon] as L.LatLngExpression);
      L.polyline(latlngs, { color: "#3b82f6", weight: 3, opacity: 0.85 }).addTo(map);
      bounds.push(...latlngs);
    }

    if (bounds.length === 1) {
      map.setView(bounds[0], 15);
    } else if (bounds.length > 1) {
      map.fitBounds(L.latLngBounds(bounds), { padding: [40, 40] });
    }

    setTimeout(() => map.invalidateSize(), 150);

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [start, end, path]);

  return <div ref={containerRef} className="w-full rounded-xl overflow-hidden border border-white/10" style={{ height: 360 }} />;
}
