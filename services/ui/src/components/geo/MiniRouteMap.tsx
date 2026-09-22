import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

interface MiniRouteMapProps {
  points: Array<{ lat: number; lon: number }>;
  height?: number;
  className?: string;
  /** When set, the thumbnail is clickable and opens the full route map. */
  onClick?: () => void;
  title?: string;
}

/**
 * Lightweight static route preview using OpenStreetMap tiles.
 * Renders a polyline fitted to the breadcrumb bounds. Interaction is off —
 * it's a thumbnail — but an optional onClick opens the full Route Map modal.
 */
export default function MiniRouteMap({ points, height = 140, className = "", onClick, title }: MiniRouteMapProps) {
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

  const clickable = Boolean(onClick);
  return (
    <div
      ref={containerRef}
      className={`${className}${clickable ? " cursor-pointer hover:opacity-90 transition-opacity" : ""}`}
      style={{ height, width: "100%" }}
      onClick={clickable ? onClick : undefined}
      onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick?.(); } } : undefined}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      title={title || (clickable ? "Open route map" : undefined)}
      aria-label={clickable ? "Open full route map" : undefined}
    />
  );
}
