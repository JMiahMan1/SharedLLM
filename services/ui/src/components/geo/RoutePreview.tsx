import { useQuery } from '@tanstack/react-query';
import MiniRouteMap from './MiniRouteMap';
import { routeQueryKey } from '../../lib/wanderQueries';
import type { RoutePoint } from '../../types/api';

interface RoutePreviewProps {
  id: string;
  completed: boolean;
  fetcher: () => Promise<RoutePoint[]>;
  /** Opens the full Route Map modal when the thumbnail is clicked. */
  onOpenMap?: () => void;
  /**
   * Hide while a map modal is open. A Leaflet canvas left mounted behind the
   * modal can paint over the larger map (notably in the Android WebView).
   */
  hidden?: boolean;
}

/**
 * Lazily loads and renders an OSM mini-map for a trip or workout.
 *
 * The route is a query keyed by the trip/workout id: fetched once, shared with
 * the Route Map modal, and not asked for again. It used to be fetched from an
 * effect that depended on a fetcher recreated every render, so each refresh of
 * the page (live positions every 30 s) asked for every route again, and a
 * route with no breadcrumbs was asked for forever.
 */
const RoutePreview = ({ id, completed, fetcher, onOpenMap, hidden }: RoutePreviewProps) => {
  const { data: points } = useQuery({
    queryKey: routeQueryKey(id),
    // A route the server has no points for is an empty route, not undefined.
    queryFn: async () => (await fetcher()) ?? [],
    enabled: completed,
    staleTime: Infinity,
    retry: false,
  });

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
