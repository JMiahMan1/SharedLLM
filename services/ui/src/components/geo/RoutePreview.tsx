import { useEffect, useRef, useState } from 'react';
import MiniRouteMap from './MiniRouteMap';
import type { RoutePoint } from '../../types/api';

interface RoutePreviewProps {
  id: string;
  completed: boolean;
  /**
   * Optional. Omit `loadRoute` and the component fetches and caches the route
   * itself, which is what every call site actually wanted -- the parent was
   * maintaining a `routePoints` map and a `routeCache` ref purely to satisfy
   * this prop. Passing them keeps working for any caller that still does.
   */
  points?: RoutePoint[] | undefined;
  loadRoute?: (key: string, loader: () => Promise<RoutePoint[]>) => void;
  fetcher: () => Promise<RoutePoint[]>;
  /** Opens the full Route Map modal when the thumbnail is clicked. */
  onOpenMap?: () => void;
  /**
   * Hide while a map modal is open. A Leaflet canvas left mounted behind the
   * modal can paint over the larger map (notably in the Android WebView).
   */
  hidden?: boolean;
}

/** Lazily loads and renders an OSM mini-map for a trip or workout. */
const RoutePreview = ({ id, completed, points, loadRoute, fetcher, onOpenMap, hidden }: RoutePreviewProps) => {
  const [owned, setOwned] = useState<RoutePoint[] | undefined>(undefined);
  const cache = useRef<Record<string, RoutePoint[]>>({});

  useEffect(() => {
    if (!completed) return;
    // Parent-managed: hand off to their loader and let their `points` drive.
    if (loadRoute) {
      if (!points) loadRoute(id, fetcher);
      return;
    }
    // Self-managed: serve from cache, otherwise fetch once.
    if (owned || cache.current[id]) {
      const cached = cache.current[id];
      if (cached) setOwned(cached);
      return;
    }
    let cancelled = false;
    void fetcher()
      .then((pts) => {
        cache.current[id] = pts;
        if (!cancelled) setOwned(pts);
      })
      .catch(() => {
        // Route unavailable (e.g. no breadcrumbs) — card renders without a map.
      });
    return () => {
      cancelled = true;
    };
  }, [id, completed, points, loadRoute, fetcher, owned]);

  const resolved = loadRoute ? points : owned;

  if (hidden) return null;
  if (!resolved || resolved.length === 0) return null;
  return (
    <MiniRouteMap
      points={resolved}
      height={120}
      className="rounded-xl border border-white/10 overflow-hidden mt-3"
      onClick={onOpenMap}
      title={onOpenMap ? 'Open route map' : undefined}
    />
  );
};

export default RoutePreview;