import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { Car, Gauge, OctagonAlert, Smartphone, TrendingUp, Zap } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../../services/api';
import MemberCard from './MemberCard';
import type { DrivingEventCounts, TripLocation } from '../../types/api';

const EVENT_ROWS: { key: keyof DrivingEventCounts; label: string; icon: LucideIcon }[] = [
  { key: 'hard_brake', label: 'Hard braking', icon: OctagonAlert },
  { key: 'rapid_accel', label: 'Rapid acceleration', icon: Zap },
  { key: 'high_speed', label: 'Over 80 mph', icon: Gauge },
  { key: 'phone_use', label: 'Phone in use', icon: Smartphone },
];

const placeName = (l: TripLocation | undefined | null) => l?.zone || l?.name || l?.address || 'somewhere';

function eventSummary(e: DrivingEventCounts): string {
  const parts = EVENT_ROWS.filter((r) => e[r.key]).map((r) => `${e[r.key]} ${r.label.toLowerCase()}`);
  return parts.length ? parts.join(' · ') : 'No events';
}

/**
 * The last week of driving (Life360's driver report): drives, miles, time,
 * top speed, and the events the phone noticed. Events need a phone on an app
 * build that detects them; drives come from any phone that shares location.
 */
export default function DriverReportCard({ username, name }: { username: string; name: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['driver-report', username],
    queryFn: () => api.getDriverReport(username, 7),
    retry: false,
    staleTime: 5 * 60_000,
  });
  const refused = isAxiosError(error) && (error.response?.status === 403 || error.response?.status === 404);
  return (
    <MemberCard
      title="Driving this week"
      icon={Car}
      tone="text-emerald-300"
      testId="member-driving"
      isLoading={isLoading}
      error={refused ? `${name}'s driving is not shared with you.` : error ? 'The driver report could not be loaded.' : null}
      empty={data && data.trips === 0 ? `${name} has not driven in the last 7 days.` : null}
    >
      {data && (
        <div className="space-y-3 text-xs">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="driving-totals">
            <Stat label="Drives" value={String(data.trips)} />
            <Stat label="Miles" value={data.miles.toFixed(1)} />
            <Stat label="Time" value={data.drive_minutes >= 60 ? `${Math.floor(data.drive_minutes / 60)} h ${data.drive_minutes % 60} min` : `${data.drive_minutes} min`} />
            <Stat label="Top speed" value={`${Math.round(data.top_speed_mph)} mph`} />
          </div>
          <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2" data-testid="driving-events">
            {EVENT_ROWS.map((row) => (
              <li key={row.key} className="flex min-w-0 items-center justify-between gap-2 rounded-xl bg-white/5 px-3 py-2">
                <span className="flex min-w-0 items-center gap-2 text-slate-300">
                  <row.icon size={14} className={data.events[row.key] ? 'text-amber-300' : 'text-slate-500'} />
                  <span className="truncate">{row.label}</span>
                </span>
                <span className="shrink-0 font-semibold text-slate-100">
                  {data.events[row.key]}
                  {row.key === 'phone_use' && data.events.phone_use_min ? ` (${data.events.phone_use_min} min)` : ''}
                </span>
              </li>
            ))}
          </ul>
          <ol className="space-y-1.5">
            {data.per_trip.slice(0, 5).map((t) => (
              <li key={t.id} className="rounded-xl border border-white/10 bg-white/5 p-2.5">
                <p className="break-words text-slate-200">
                  {placeName(t.start_location)} to {placeName(t.end_location)}
                </p>
                <p className="flex flex-wrap items-center gap-x-2 text-[10px] text-slate-500">
                  <span>{new Date(t.start_time * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</span>
                  <span>{(t.distance_miles ?? 0).toFixed(1)} mi</span>
                  {t.top_speed_mph ? <span className="flex items-center gap-0.5"><TrendingUp size={10} /> {Math.round(t.top_speed_mph)} mph</span> : null}
                  <span className={eventSummary(t.events) === 'No events' ? '' : 'text-amber-300'}>{eventSummary(t.events)}</span>
                </p>
              </li>
            ))}
          </ol>
        </div>
      )}
    </MemberCard>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-white/5 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="truncate text-sm font-semibold text-white">{value}</p>
    </div>
  );
}
