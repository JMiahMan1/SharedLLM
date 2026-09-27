import { useEffect } from 'react';
import MiniRouteMap from './MiniRouteMap';
import type { RoutePoint } from '../../types/api';

interface RoutePreviewProps {
  id: string;
  completed: boolean;
  points: RoutePoint[] | undefined;
  loadRoute: (key: string, loader: () => Promise<RoutePoint[]>) => void;
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
  useEffect(() => {
    if (!points && completed) {
      loadRoute(id, fetcher);
    }
  }, [points, completed, id, loadRoute, fetcher]);

  if (hidden) return null;
  if (!points || points.length === 0) return null;
  return (
    <MiniRouteMap
      points={points}
      height={120}
      className="rounded-xl border border-white/10 overflow-hidden mt-3"
      onClick={onOpenMap}
      title={onOpenMap ? 'Open route map' : undefined}
    />
  );
};

export default RoutePreview;
