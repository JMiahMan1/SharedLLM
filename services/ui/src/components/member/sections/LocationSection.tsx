import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { Flag, Route as RouteIcon } from 'lucide-react';
import { api } from '../../../services/api';
import MemberCard from '../MemberCard';
import FindPhoneCard from '../FindPhoneCard';
import { MiniMapCard } from './OverviewSection';
import { geoZonesQueryKey, zonePlaces } from '../../../lib/wanderQueries';
import { localDay } from '../../../lib/healthHub';
import type { Trip, TripLocation } from '../../../types/api';

const placeName = (l: TripLocation | undefined) => l?.zone || l?.name || l?.address || 'somewhere';
const clock = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

/** Today's trips as departures and arrivals, from geo (the child's sharing consent applies). */
function TripsCard({ username, name }: { username: string; name: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['member-trips', username], queryFn: () => api.getTrips(username), retry: false, staleTime: 60_000 });
  const today = localDay();
  const trips = (data?.trips || []).filter((t: Trip) => localDay(new Date(t.start_time * 1000)) === today).sort((a, b) => b.start_time - a.start_time);
  const refused = isAxiosError(error) && (error.response?.status === 403 || error.response?.status === 404);
  return (
    <MemberCard
      title="Today's trips"
      icon={RouteIcon}
      tone="text-emerald-300"
      testId="member-trips"
      isLoading={isLoading}
      error={refused ? `${name}'s trips are not shared with you.` : error ? 'Trips could not be loaded.' : null}
      empty={trips.length === 0 ? 'No trips today.' : null}
    >
      <ol className="space-y-2">
        {trips.map((t) => (
          <li key={t.id} className="rounded-xl border border-white/10 bg-white/5 p-2.5 text-xs">
            <p className="text-slate-200">
              Left {placeName(t.start_location)} at {clock(t.start_time)}
              {t.status === 'completed' ? `, arrived at ${placeName(t.end_location)} at ${clock(t.end_time)}` : ', on the way now'}
            </p>
            <p className="text-[10px] text-slate-500">{t.distance_miles.toFixed(1)} mi · {Math.round(t.duration_seconds / 60)} min{t.activity_type ? ` · ${t.activity_type}` : ''}</p>
          </li>
        ))}
      </ol>
    </MemberCard>
  );
}

/** The family's places (Home Assistant zones), the ones that tell people about arrivals marked. */
function PlacesCard() {
  const { data, isLoading, isError } = useQuery({ queryKey: geoZonesQueryKey(), queryFn: () => api.getGeoZones(), retry: false, staleTime: 5 * 60_000 });
  const places = zonePlaces(data);
  return (
    <MemberCard
      title="Family places"
      icon={Flag}
      tone="text-emerald-300"
      testId="member-places"
      isLoading={isLoading}
      error={isError ? 'Places could not be loaded.' : null}
      empty={places.length === 0 ? 'No family places set up yet.' : null}
    >
      <ul className="space-y-1.5 text-xs">
        {places.map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-2">
            <span className="truncate text-slate-200">{p.name}</span>
            <span className="shrink-0 text-[10px] text-slate-500">{p.important ? 'arrivals and departures are announced' : `${Math.round(p.radius)} m`}</span>
          </li>
        ))}
      </ul>
    </MemberCard>
  );
}

export default function LocationSection({ username, name }: { username: string; name: string }) {
  return (
    <>
      <div className="lg:col-span-2">
        <MiniMapCard username={username} height={340} />
      </div>
      <FindPhoneCard username={username} name={name} />
      <TripsCard username={username} name={name} />
      <PlacesCard />
    </>
  );
}
