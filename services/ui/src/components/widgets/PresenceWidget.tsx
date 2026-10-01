import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Compass, Navigation, Radio } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { WidgetCard } from './WidgetCard';
import { useHaptics } from '../../hooks/useHaptics';
import {
  FRESHNESS_STYLE,
  ageLabel,
  buildLiveMembers,
  type LiveFamilyMember,
} from '../geo/liveLocations';
import { tripsQueryKey, tripsQueryOptions } from '../../lib/wanderQueries';
import {
  displayName,
  formatMiles,
  formatTripLocation,
  isTripLive,
  isTripOwner,
  relativeTime,
} from '../../lib/wanderTrips';

/**
 * Family presence at a glance: who is reporting, where they last were, and
 * the most recent trip.
 *
 * This is the dashboard's answer to the same question the Wander page answers
 * in full -- it deliberately shows *no* map. A Leaflet canvas inside a 280px
 * bento cell is unreadable, and `LiveFamilyMap` already owns the map
 * experience on the page itself.
 *
 * Freshness is the point of the card. A phone that stopped uploading must not
 * render as a calm "everyone's home" -- so each row carries its age, and an
 * offline member is visibly dimmed rather than quietly listed as present.
 */

const POLL_MS = 60_000;

const STATUS_TEXT: Record<LiveFamilyMember['freshness'], string> = {
  live: 'Live',
  recent: 'Recent',
  stale: 'Last seen',
};

export interface PresenceWidgetConfig {
  /** Show the most recent trip row beneath the member list. */
  showLatestTrip?: boolean;
}

const PresenceWidget = ({ settingsButton }: IWidgetProps) => {
  const navigate = useNavigate();
  const { trigger } = useHaptics();
  const { user } = useAuth();
  const currentUsername = (user?.username || '').toLowerCase() || undefined;

  const locationsQuery = useQuery({
    queryKey: ['user-locations', 'presence-widget'],
    // Freshness is computed at fetch time so render stays pure.
    queryFn: async () => buildLiveMembers(await api.getAllUserLocations()),
    refetchInterval: POLL_MS,
    staleTime: 30_000,
    retry: false,
  });

  const tripsQuery = useQuery({
    queryKey: tripsQueryKey(),
    queryFn: () => api.getTrips(undefined),
    ...tripsQueryOptions,
  });

  const members = useMemo(() => locationsQuery.data ?? [], [locationsQuery.data]);
  const latestTrip = useMemo(() => {
    const trips = tripsQuery.data?.trips ?? [];
    return trips.reduce<(typeof trips)[number] | null>((best, trip) => {
      if (!best) return trip;
      return (trip.start_time ?? 0) > (best.start_time ?? 0) ? trip : best;
    }, null);
  }, [tripsQuery.data]);

  const liveCount = members.filter((m) => m.freshness === 'live').length;
  const loading = locationsQuery.isLoading && members.length === 0;
  const canEditTrip = latestTrip ? isTripOwner(latestTrip, currentUsername) : false;

  const goToWander = () => {
    trigger('light');
    navigate('/wander');
  };

  return (
    <WidgetCard
      title="Family Presence"
      settingsButton={settingsButton}
      accentColor="#8b5cf6"
      expandedClassName="bg-black/80"
    >
      <div
        role="button"
        tabIndex={0}
        data-testid="presence-widget"
        onClick={goToWander}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            goToWander();
          }
        }}
        className="space-y-3 text-left cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/60 rounded-xl min-h-11 pointer-coarse:min-h-11"
      >
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold text-white">
            {loading ? '—' : liveCount}
          </span>
          <span className="text-xs text-slate-400">
            {liveCount === 1 ? 'person live' : 'people live'}
            {members.length > liveCount && !loading
              ? ` · ${members.length - liveCount} not reporting`
              : ''}
          </span>
        </div>

        {loading ? (
          <div className="space-y-2" aria-hidden>
            <div className="h-8 skeleton rounded-lg" />
            <div className="h-8 skeleton rounded-lg" />
          </div>
        ) : members.length === 0 ? (
          <p className="text-xs text-slate-400">
            No one is sharing location right now.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {members.map((member) => {
              const style = FRESHNESS_STYLE[member.freshness];
              const isYou = member.userId.toLowerCase() === currentUsername;
              return (
                <li
                  key={member.userId}
                  data-testid={`presence-row-${member.userId}`}
                  className="flex items-center gap-2 text-xs"
                >
                  <span
                    aria-hidden
                    className="h-2 w-2 rounded-full shrink-0"
                    style={{ backgroundColor: style.color }}
                  />
                  <span className="text-slate-200 truncate">
                    {member.userId}
                    {isYou && (
                      <span className="ml-1.5 text-[10px] text-purple-300">You</span>
                    )}
                  </span>
                  <span className="ml-auto text-slate-500 shrink-0 tabular-nums">
                    {STATUS_TEXT[member.freshness]} · {ageLabel(member.ageMs)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}

        {latestTrip && (
          <div className="pt-3 border-t border-white/5">
            <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1">
              Latest trip
            </p>
            <p className="text-xs text-slate-200 truncate">
              {formatTripLocation(latestTrip.start_location, 'Unknown start')}
              {' → '}
              {formatTripLocation(latestTrip.end_location, 'Unknown')}
            </p>
            <p className="mt-0.5 text-[11px] text-slate-500 flex items-center gap-1.5 flex-wrap">
              {isTripLive(latestTrip) && (
                <span className="inline-flex items-center gap-1 text-purple-300">
                  <Radio size={10} /> In progress
                </span>
              )}
              <span className="inline-flex items-center gap-1">
                <Navigation size={10} />
                {formatMiles(latestTrip.distance_miles ?? 0)} mi
              </span>
              <span>{relativeTime(latestTrip.start_time)}</span>
              <span>· {displayName(latestTrip)}</span>
            </p>
            {canEditTrip && (
              <p className="mt-1 text-[10px] text-slate-500">
                Tap to edit on Wander.
              </p>
            )}
          </div>
        )}

        <p className="text-[10px] text-slate-500 inline-flex items-center gap-1">
          <Compass size={10} /> Open Wander
        </p>
      </div>
    </WidgetCard>
  );
};

export default PresenceWidget;