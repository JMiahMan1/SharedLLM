import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

interface MiniRouteMapProps {
  points: Array<{ lat: number; lon: number }>;
  height?: number;
  className?: string;
}

/**
 * Lightweight static route preview using OpenStreetMap tiles.
 * Renders a polyline fitted to the breadcrumb bounds. No interaction
 * (dragging/zoom disabled) — it's a thumbnail, not an explorer.
 */
export default function MiniRouteMap({ points, height = 140, className = "" }: MiniRouteMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const valid = points.filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon));
    if (valid.length === 0) return;

    const map = L.map(containerRef.current, {
      zoomControl: false,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      touchZoom: false,
      attributionControl: false,
    });
    mapRef.current = map;

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
    }).addTo(map);

    const latlngs: L.LatLngExpression[] = valid.map((p) => [p.lat, p.lon]);
    const polyline = L.polyline(latlngs, { color: "#3b82f6", weight: 3, opacity: 0.9 }).addTo(map);
    map.fitBounds(polyline.getBounds(), { padding: [12, 12] });

    // Start (green) / end (red) markers
    L.circleMarker(latlngs[0], { radius: 5, color: "#16a34a", fillColor: "#16a34a", fillOpacity: 1, weight: 2 }).addTo(map);
    if (latlngs.length > 1) {
      L.circleMarker(latlngs[latlngs.length - 1], { radius: 5, color: "#dc2626", fillColor: "#dc2626", fillOpacity: 1, weight: 2 }).addTo(map);
    }

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [points]);

  if (points.length === 0) {
    return null;
  }

  return <div ref={containerRef} className={className} style={{ height, width: "100%" }} />;
}
